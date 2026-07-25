"""Tests for the registry: drift against the prose, and the TOML contract itself.

Two sources of bucket facts exist — ``data/registry.toml``, which code reads, and
``docs/smoke-bucket.md``, which people read. Two sources that must agree, can
disagree, so the drift guard here parses both independently and fails on the
first field where they differ. It uses its own markdown reader on purpose: a
guard that shared the reader under test would agree with it by construction.

That the registry resolves to the bytes the receipts name is proved elsewhere,
by the differential replay, which re-issues all 67 committed verdicts against
those bytes.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

from s3_listing_study.registry import FIELDS, Registry, RegistryError, default_path

REPO = Path(__file__).resolve().parents[1]
MARKDOWN = REPO / "docs/smoke-bucket.md"
FIXTURE = REPO / "tests/fixtures/registry-254c8cfe.md"

REGISTRY = Registry.load()
BUCKETS = REGISTRY.bucket_names()


# --- an independent reader for the prose side -----------------------------


def _sections(text: str) -> list[str]:
    """Split on ``## `` headings, dropping the preamble."""
    return re.split(r"^## ", text, flags=re.MULTILINE)[1:]


def _first_code(cell: str) -> str:
    match = re.search(r"`([^`]*)`", cell)
    return match.group(1) if match else ""


def _label_value(section: str, label: str) -> str:
    """The value stated for ``label``, from a table row or a prose ``Label:`` line.

    Requires exactly one statement of the label. Taking the first match is the
    ambiguity that matters: a duplicated ``Manifest sha256`` resolving to whichever
    came first puts a plausible wrong digest in a receipt.
    """
    row = re.compile(rf"^\|\s*{re.escape(label)}\s*\|(.*?)\|?\s*$", re.IGNORECASE | re.MULTILINE)
    prose = re.compile(rf"^{re.escape(label)}:\s*(.*?)\s*$", re.IGNORECASE | re.MULTILINE)
    found = [m.group(1).strip() for m in row.finditer(section)]
    found += [m.group(1).strip() for m in prose.finditer(section)]
    if len(found) > 1:
        raise AssertionError(f"{MARKDOWN} states {label!r} {len(found)} times — ambiguous")
    return found[0] if found else ""


def _shape(section: str) -> str:
    block = re.split(r"^### ", section, flags=re.MULTILINE)
    for part in block:
        if part.startswith("Measured shape"):
            body = part.split("\n", 1)[1]
            return "\n".join(line for line in body.splitlines() if line.strip())
    return ""


def _markdown_buckets() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for section in _sections(MARKDOWN.read_text(encoding="utf-8")):
        name = _first_code(_label_value(section, "Bucket"))
        if not name:
            continue
        assert name not in out, f"{MARKDOWN} registers {name!r} twice"
        manifest = _first_code(_label_value(section, "Manifest"))
        if manifest.startswith("~/"):
            manifest = f"{Path.home()}/{manifest[2:]}"
        keys = re.search(r"[0-9][0-9,]*", _label_value(section, "Keys"))
        date = re.search(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", _label_value(section, "Snapshot date"))
        assert keys and date, f"{MARKDOWN} states no key count or snapshot date for {name!r}"
        out[name] = {
            "region": _first_code(_label_value(section, "Region")),
            "manifest": manifest,
            "manifest_sha256": _first_code(_label_value(section, "Manifest sha256")),
            "snapshot_date": date.group(0),
            "keys": keys.group(0).replace(",", ""),
            "shape": _shape(section),
        }
    return out


def _markdown_harness_image() -> str:
    for section in _sections(MARKDOWN.read_text(encoding="utf-8")):
        if section.startswith("Harness client"):
            return _first_code(_label_value(section, "Image"))
    raise AssertionError(f"{MARKDOWN} has no '## Harness client' section")


# --- drift guard: the two sources must not diverge -------------------------


def test_both_sources_register_the_same_buckets() -> None:
    assert sorted(_markdown_buckets()) == BUCKETS


@pytest.mark.parametrize("bucket", BUCKETS)
@pytest.mark.parametrize("field", FIELDS)
def test_toml_and_markdown_agree(bucket: str, field: str) -> None:
    assert REGISTRY.field(bucket, field) == _markdown_buckets()[bucket][field]


def test_toml_and_markdown_agree_on_the_harness_image() -> None:
    assert REGISTRY.harness_image == _markdown_harness_image()


def test_path_and_digest_bind_a_receipt_to_exact_bytes() -> None:
    # What must hold is that the pair names bytes a reader can check: the path
    # resolves and the digest is of exactly those bytes.
    assert REGISTRY.path == default_path()
    assert REGISTRY.digest == hashlib.sha256(REGISTRY.path.read_bytes()).hexdigest()


# --- strictness: every ambiguity is fatal ---------------------------------


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "registry.toml"
    path.write_text(text, encoding="utf-8")
    return path


BUCKET_BLOCK = """
[harness_client]
image = "amazon/aws-cli@sha256:{sha}"

[buckets."b"]
region = "us-east-1"
manifest = "~/m/b.tsv.gz"
manifest_sha256 = "{sha}"
snapshot_date = "2026-07-17"
keys = 7
shape = "one prefix"
""".format(sha="a" * 64)


def test_a_minimal_registry_loads(tmp_path: Path) -> None:
    registry = Registry.load(_write(tmp_path, BUCKET_BLOCK))
    assert registry.bucket("b").manifest == f"{Path.home()}/m/b.tsv.gz"
    assert registry.field("b", "keys") == "7"


def test_a_registered_bucket_may_hold_no_keys(tmp_path: Path) -> None:
    """0 is a fact, not a malformation — the shell resolver states it too."""
    registry = Registry.load(_write(tmp_path, BUCKET_BLOCK.replace("keys = 7", "keys = 0")))
    assert registry.field("b", "keys") == "0"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        pytest.param(
            lambda t: t + '\n[buckets."b"]\nregion = "x"\n', "valid TOML", id="dup-bucket"
        ),
        pytest.param(lambda t: t + 'region = "eu-west-1"\n', "valid TOML", id="dup-field"),
        pytest.param(lambda t: t + 'regoin = "eu-west-1"\n', "unknown key", id="misspelled-field"),
        pytest.param(
            lambda t: t.replace('region = "us-east-1"\n', ""), "no 'region'", id="missing"
        ),
        pytest.param(
            lambda t: t.replace('shape = "one prefix"', 'shape = ""'), "shape", id="shape"
        ),
        pytest.param(lambda t: t.replace("keys = 7", "keys = -1"), "keys", id="negative-keys"),
        pytest.param(lambda t: t.replace("keys = 7", 'keys = "7"'), "keys", id="string-keys"),
        pytest.param(
            lambda t: t.replace('"2026-07-17"', '"July 2026"'), "snapshot_date", id="date"
        ),
        pytest.param(lambda t: t.replace('_sha256 = "aaa', '_sha256 = "zzz'), "digest", id="sha"),
        pytest.param(lambda t: t.replace("@sha256:", ":latest-"), "digest-pinned", id="unpinned"),
        pytest.param(
            lambda t: t.replace("[harness_client]", "[harness-client]"), "unknown", id="hc"
        ),
        pytest.param(lambda t: t.replace("image = ", "imgae = "), "unknown key", id="hc-key"),
    ],
)
def test_a_malformed_registry_is_refused(
    tmp_path: Path, mutation: Callable[[str], str], message: str
) -> None:
    with pytest.raises(RegistryError, match=message):
        Registry.load(_write(tmp_path, mutation(BUCKET_BLOCK)))


def test_a_registry_with_no_buckets_is_refused(tmp_path: Path) -> None:
    text = BUCKET_BLOCK.split('[buckets."b"]')[0] + "[buckets]\n"
    with pytest.raises(RegistryError, match="registers no buckets"):
        Registry.load(_write(tmp_path, text))


def test_an_unreadable_registry_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="not readable"):
        Registry.load(tmp_path / "absent.toml")


def test_an_unregistered_bucket_is_refused() -> None:
    with pytest.raises(RegistryError, match="register it before running anything against it"):
        REGISTRY.bucket("not-a-bucket")


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(RegistryError, match="unknown field"):
        REGISTRY.field(BUCKETS[0], "regoin")


def test_the_committed_registry_is_the_one_the_toml_parser_sees() -> None:
    # Cheap tripwire: the file on disk parses as TOML at all, independently of the
    # accessor's validation.
    assert tomllib.loads(default_path().read_text(encoding="utf-8"))["buckets"]
