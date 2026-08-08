"""The ported verifier's own tests, plus tier 2 driven against it.

Tier 2 is `harness/tests/run-regressions.sh`, which is offline by design and
drives this package's `--scope union` path over synthetic fixtures it builds at
runtime, so tier 2 exercises the union path only and never the single-receipt
path — whose FAIL/DRIFT/ERROR branches are tier 3's job and whose refusals are
covered here. The suite is run from a staged copy of the tree so nothing it does
touches `harness/`, and it is pointed at THIS checkout's interpreter and sources
rather than whatever `s3_listing_study` the box has installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from s3_listing_study.verify import cli, common, compare, report, security, union
from s3_listing_study.verify.errors import ERROR_EXIT, VerifierError
from s3_listing_study.verify.registry_source import RegistrySource

REPO = Path(__file__).resolve().parents[1]
REGRESSIONS = REPO / "harness" / "tests" / "run-regressions.sh"
REGISTRY_FIXTURE = REPO / "tests" / "fixtures" / "registry-254c8cfe.md"


def test_the_registry_fixture_digests_to_the_sha_every_receipt_cites() -> None:
    """Identity is the raw bytes, so the markdown fixture reproduces `254c8cfe…`.

    All 85 committed `run.meta` cite that digest and the verifier refuses to
    judge a run against a registry it never saw. Digesting a parse instead of
    the file would make every committed verdict unreplayable.
    """
    source = RegistrySource.load(REGISTRY_FIXTURE)
    assert source.digest == "254c8cfedd06b1b8671c5bbabc753bfe45462124821eacf44bd27b43c67bbced"
    assert "@sha256:" in source.harness_image()


def test_the_toml_registry_is_read_for_fields_and_digested_for_identity() -> None:
    """Field parsing dispatches on file type; the digest never does."""
    import hashlib

    path = REPO / "data" / "registry.toml"
    source = RegistrySource.load(path)
    assert source.digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "@sha256:" in source.harness_image()


def test_a_final_record_without_a_newline_still_counts() -> None:
    """Counting newline characters is how a duplicate PASSes."""
    data = b"a\t1\te\t-\tSTANDARD\na\t1\te\t-\tSTANDARD"
    assert len(compare.split_records(data)) == 2


def test_a_malformed_mtime_is_caught_on_a_row_the_join_would_drop() -> None:
    rows = compare.split_records(b"kX\t9\tetagx\tgarbage-mtime\tSTANDARD\n")
    assert compare.bad_mtimes(rows) == [b"kX\tgarbage-mtime"]


def test_the_sentinel_exempts_a_field_and_a_zone_spelling_is_not_a_mismatch(
    tmp_path: Path,
) -> None:
    """A field is asserted only where the adapter emitted a non-`-` value.

    `…Z` and `…+00:00` denote the same second and must compare equal; the size
    the mode did not expose must not be compared at all, while the one it did
    expose wrongly must be.
    """
    actual = compare.split_records(
        b"a\t-\t-\t2026-07-17T00:00:00+00:00\t-\nb\t99\t-\t2026-07-17T00:00:00Z\t-\n"
    )
    expected = compare.split_records(
        b"a\t10\tETAGA\t2026-07-17T00:00:00Z\tSTANDARD\n"
        b"b\t20\tETAGB\t2026-07-17T00:00:00Z\tSTANDARD\n"
    )
    with compare.staged(actual, expected, tmp_path) as sides:
        assert sides.field_mismatches() == [b"size\tb\ttool=99\tmanifest=20"]


def test_keys_that_are_not_utf8_survive_the_set_math(tmp_path: Path) -> None:
    """A key is bytes copied from the listing response and is never decoded."""
    key = b"\xff\xfe/odd"
    actual = compare.split_records(key + b"\t1\te\t-\tSTANDARD\n")
    expected = compare.split_records(b"aaa\t1\te\t-\tSTANDARD\n")
    with compare.staged(actual, expected, tmp_path) as sides:
        assert sides.anti_join("actual", "expected") == [key]
        assert sides.anti_join("expected", "actual") == [b"aaa"]


def test_key_order_is_byte_order_not_locale_order(tmp_path: Path) -> None:
    """The hazard `LC_ALL=C` existed to hold: a collation that disagrees invents keys."""
    keys = [b"B/1", b"a/1", b"_x", b"\x7f"]
    actual = compare.split_records(b"".join(k + b"\t1\te\t-\tS\n" for k in keys))
    with compare.staged(actual, [], tmp_path) as sides:
        assert sides.anti_join("actual", "expected") == sorted(keys)


def test_duplicates_are_counted_before_dedup(tmp_path: Path) -> None:
    actual = compare.split_records(b"a\t1\te\t-\tS\na\t1\te\t-\tS\nb\t1\te\t-\tS\n")
    with compare.staged(actual, [], tmp_path) as sides:
        assert sides.duplicated("actual") == [b"a"]
        assert sides.distinct("actual") == 2


def test_the_verdict_stamp_is_a_literal_splice() -> None:
    """The placeholder carries `(`, `)` and `.`; a regex spelling matches nothing."""
    text = f"- Verdict: {report.PLACEHOLDER}\n"
    stamped, hits = report.stamp(text, "FAIL")
    assert hits == 1
    assert stamped == "- Verdict: **FAIL** — see `verify.md`\n"
    assert report.PLACEHOLDER not in stamped


# ------------------------------------------------------- the security boundary

# Both re-list constructors must carry the explicit evidence-log and no-pull
# arguments. Asserted against the constructed argv rather than by grepping a
# script's text, so the guard holds against what actually runs.

PRODUCTION_RUN_PREFIX = [
    "timeout",
    "-k",
    "2s",
    "30s",
    "docker",
    "run",
    "--rm",
    "--name",
    "N",
    "--log-driver=json-file",
    "--log-opt",
    "max-size=-1",
    "--pull=never",
    "--network",
    "s3-listing-study-subjects",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges:true",
    "-e",
    "AWS_EC2_METADATA_DISABLED=true",
    "-e",
    "TZ=UTC",
    "IMG",
]


def test_the_re_list_argv_is_the_production_one_element_by_element() -> None:
    """Not a subset check: a dropped element is a different security posture.

    `--log-opt max-size=-1` is the load-bearing one — Docker's default json-file
    driver rotates at 10 MiB, so without it the evidence log an ERROR verdict is
    read from would be silently truncated. `--pull=never` is the other: campaign
    execution never pulls.
    """
    assert security.SecurityBoundary().run_prefix("N", "IMG") == PRODUCTION_RUN_PREFIX


def test_relist_builds_its_docker_argv_only_through_the_boundary(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A second argv construction is a second place to lose `--pull=never`."""
    captured: list[list[str]] = []

    class Sentinel(security.SecurityBoundary):
        def run_prefix(self, name: str, image: str) -> list[str]:
            return ["SENTINEL", name, image]

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc, _records, _stderr = common.relist(Sentinel(), "IMG", "bkt", "us-east-1", "pfx/")

    assert rc == 0
    assert captured[0][:3] == ["SENTINEL", captured[0][1], "IMG"]
    assert "docker" not in captured[0][3:]
    assert captured[0][3:5] == ["s3api", "list-objects-v2"]


def test_normalize_timeout_is_a_verifier_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = tmp_path / "payload"
    payload.write_bytes(b"")

    def expire(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert kwargs["timeout"] == common.NORMALIZE_TIMEOUT_S
        raise subprocess.TimeoutExpired(argv, common.NORMALIZE_TIMEOUT_S)

    monkeypatch.setattr(subprocess, "run", expire)
    with pytest.raises(VerifierError, match="normalize adapter timed out"):
        common.normalize("adapter", "mode", "", payload)


def test_unexpected_union_error_still_writes_the_durable_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(options: union.Options, work: Path) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(union, "_run", crash)
    options = union.Options(
        receipts=[],
        normalize="adapter",
        out=str(tmp_path),
        registry=RegistrySource.load(REGISTRY_FIXTURE),
        security=security.SecurityBoundary(),
    )
    with pytest.raises(RuntimeError, match="boom"):
        union.run(options)
    durable = (tmp_path / "union-verify.md").read_text()
    assert "**Verdict: ERROR**" in durable
    assert "unexpected RuntimeError: boom" in durable


def test_the_preflight_seam_cannot_widen() -> None:
    """`preflight` is the ONLY thing tier 3 is allowed to replace.

    Runner readiness is a property of the box; the docker argv, the timeouts and
    the status strings are what a verdict is read from, so a seam that grew a
    second override would make tier 3 stop testing what ships.
    """
    class_metadata = {
        "__doc__",
        "__module__",
        "__qualname__",
        "__firstlineno__",
        "__static_attributes__",
    }
    overrides = set(security.PreflightSkipped.__dict__) - class_metadata
    assert overrides == {"preflight"}


def test_no_argv_can_disable_the_preflight() -> None:
    """The gate is `docs/operating/runner-security.md`'s activation gate, not an option."""
    assert "--skip-preflight" not in cli._TAKES_VALUE
    assert cli.main(["--skip-preflight"]) == ERROR_EXIT


# ----------------------------------------------------------------- the refusals


def test_a_tab_inside_a_key_on_a_later_row_is_error_not_fail(tmp_path: Path) -> None:
    """The field-count gate reads row 1 only, in both implementations.

    A literal TAB in a key on any later row reached the unpack and raised
    `ValueError`, which leaves the interpreter at exit 1 — FAIL, a finding
    about a tool — where the shell's `die` exits 3.
    """
    rows = compare.split_records(b"a\t1\te\t-\tS\nkey\twith\ttab\t1\te\t-\tS\n")
    with pytest.raises(VerifierError) as caught, compare.staged([], rows, tmp_path) as sides:
        sides.distinct("expected")
    assert "manifest record 2" in str(caught.value)
    assert "not a tool finding" in str(caught.value)


def test_a_crash_is_reported_as_error_and_never_as_fail(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Exit 1 is a published finding about a tool. A verifier that crashed checked nothing."""

    def boom(argv: object, security: object = None) -> int:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(cli, "run", boom)
    assert cli.main([]) == ERROR_EXIT


def test_a_duplicated_registry_row_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    """A duplicated row resolving to whichever came first puts a plausible wrong
    value in a receipt — worse than no receipt at all."""
    path = tmp_path / "registry.md"
    path.write_text("## Harness client\n\n| Image | `a@sha256:1` |\n| Image | `b@sha256:2` |\n")
    with pytest.raises(VerifierError) as caught:
        RegistrySource.load(path).harness_image()
    assert "appears 2 times" in str(caught.value)
    assert "refusing to guess" in str(caught.value)


# ------------------------------------------------------------------------ tier 2


def _stage_regressions(root: Path) -> Path:
    """A copy of the shipped harness tree, run against this checkout's sources."""
    harness = root / "harness"
    shutil.copytree(REPO / "harness", harness)
    # The suite stages a hermetic fake-Docker copy of smoke-run.sh, and resolves
    # the verifier itself, from its own ../src. Copied here too, so this staged
    # tree resolves both exactly as the repo does and never through whatever
    # `s3_listing_study` the ambient interpreter happens to have.
    shutil.copytree(REPO / "src", root / "src")
    return harness / "tests" / "run-regressions.sh"


@pytest.mark.skipif(shutil.which("gzip") is None, reason="the suite builds gzip fixtures")
def test_tier2_regressions_pass_against_the_ported_verifier(tmp_path: Path) -> None:
    """The shipped offline suite, unedited, driven against the port.

    It asserts exit codes and message text for the union plan-defect refusals,
    the stream selection heuristic and `--stream` override, the mtime cases, and
    the redaction/truncation refusals — the paths no committed receipt reaches.
    """
    script = _stage_regressions(tmp_path)
    env = dict(os.environ)
    # The suite would otherwise pick an interpreter off the box; this pins it to
    # the one running the tests, which is the one whose dependencies are locked.
    env["S3STUDY_PYTHON"] = sys.executable
    proc = subprocess.run(
        ["bash", str(script)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        check=False,
    )
    assert proc.returncode == 0, proc.stdout
    assert "ALL REGRESSIONS PASS" in proc.stdout
