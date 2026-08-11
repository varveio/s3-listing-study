"""The payload pipeline: redact → scan → truncate → hash, in that order.

The order is the behaviour. Redaction runs over the whole raw stream so a
legitimate object key shaped like a credential is scrubbed rather than falsely
flagged; the scan runs over the whole *redacted* stream, before truncation, so a
credential redaction missed is caught even when it sits beyond the cap; the hash
comes last, because it freezes the bytes and redaction after it would redact
nothing. Each of those is asserted here by its consequence, not by inspection.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from s3_listing_study.manager.receipt import cli
from s3_listing_study.manager.receipt.errors import ReceiptError
from s3_listing_study.manager.receipt.redact import (
    INLINE_MAX,
    PAYLOAD_CAP,
    preserve_binary_file,
    redact_file,
    redact_line,
    scope_tag,
    truncate_head,
)
from s3_listing_study.manager.receipt.scan import LINE_SIZE_LIMIT

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "receipt"

# Synthetic and shape-only: the value halves are placeholders, never a
# credential, and the file they are written into never leaves a tmp dir.
KEY_ID = "AKIA" + "Q" * 16
SECRET = "AWS_SECRET_ACCESS_KEY=" + "Z" * 40
# Split for the same reason: the tree scan covers the whole repo, this scheme
# name followed by a space IS the value-shape the scanner looks for, and a
# tracked test file that trips the tree scan makes the gate cry wolf about
# itself.
SIGV4 = "Authorization: AWS4-HMAC" + "-SHA256 stuff"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"id=" + KEY_ID.encode() + b"\n", b"id=<REDACTED-AWS-KEY-ID>\n"),
        (b"X-Amz-Signature=deadBEEF0123\n", b"X-Amz-Signature=<REDACTED>\n"),
        (b"X-Amz-Credential=abc%2Fdef&next=1\n", b"X-Amz-Credential=<REDACTED>&next=1\n"),
        (SIGV4.encode() + b"\n", b"Authorization: <REDACTED>\n"),
        (SECRET.encode() + b" tail\n", b"AWS_SECRET_ACCESS_KEY=<REDACTED> tail\n"),
        (b"arn:aws:iam::123456789012:role/X\n", b"arn:aws:iam::<REDACTED-ACCOUNT-ID>:role/X\n"),
        (b'"Owner": "123456789012"\n', b'"Owner": "<REDACTED-ACCOUNT-ID>"\n'),
        # A 12-digit object size is not an account id.
        (b"key.txt\t123456789012\n", b"key.txt\t123456789012\n"),
        (b"ordinary line\n", b"ordinary line\n"),
    ],
)
def test_redaction(raw: bytes, expected: bytes) -> None:
    assert redact_line(raw) == expected


def test_redaction_reports_whether_it_changed_anything(tmp_path: Path) -> None:
    clean = tmp_path / "clean.raw"
    clean.write_bytes(b"a/b/c.txt\n")
    assert redact_file(clean, tmp_path / "clean.txt") is False
    dirty = tmp_path / "dirty.raw"
    dirty.write_bytes(KEY_ID.encode() + b"\n")
    assert redact_file(dirty, tmp_path / "dirty.txt") is True


def test_redaction_refuses_a_line_over_the_shared_safety_limit(tmp_path: Path) -> None:
    source = tmp_path / "huge.raw"
    source.write_bytes(b"x" * (LINE_SIZE_LIMIT + 1))
    with pytest.raises(ReceiptError, match="line exceeds"):
        redact_file(source, tmp_path / "huge.txt")


def test_redaction_accepts_a_line_at_the_shared_safety_limit(tmp_path: Path) -> None:
    source = tmp_path / "boundary.raw"
    source.write_bytes(b"x" * LINE_SIZE_LIMIT)
    target = tmp_path / "boundary.txt"
    assert redact_file(source, target) is False
    assert target.stat().st_size == LINE_SIZE_LIMIT


def test_binary_payload_preserves_a_long_newline_free_stream(tmp_path: Path) -> None:
    source = tmp_path / "listing.parquet.raw"
    payload = b"PAR1" + b"x" * (LINE_SIZE_LIMIT + 1) + b"PAR1"
    source.write_bytes(payload)
    target = tmp_path / "listing.parquet"
    assert preserve_binary_file(source, target) is False
    assert target.read_bytes() == payload


def test_binary_payload_blocks_bytes_that_would_need_redaction(tmp_path: Path) -> None:
    source = tmp_path / "listing.parquet.raw"
    source.write_bytes(b"PAR1" + KEY_ID.encode() + b"PAR1")
    with pytest.raises(ReceiptError, match="cannot safely redact opaque binary"):
        preserve_binary_file(source, tmp_path / "listing.parquet")


def test_truncate_keeps_the_head(tmp_path: Path) -> None:
    path = tmp_path / "x.txt"
    path.write_bytes(b"0123456789")
    assert truncate_head(path, 4) == 6
    assert path.read_bytes() == b"0123"
    assert truncate_head(path, 4) == 0


def test_scope_tag() -> None:
    assert scope_tag("") == "full"
    assert scope_tag("normals-hourly/") == "normals-hourly_"
    assert scope_tag("a b/c|d") == "a_b_c_d"


def _facts_argv(tmp: Path, out: Path, data_dir: Path) -> list[str]:
    doc = json.loads((FIXTURES / "plain.json").read_text())
    facts = doc["facts"]
    argv = ["finish", f"--tmp={tmp}", f"--out={out}", f"--data-dir={data_dir}"]
    for name in cli._FACT_ARGS:
        argv.append(f"--{name}={facts[name.replace('-', '_')]}")
    return argv


@pytest.fixture
def run(tmp_path: Path) -> Iterator[tuple[Path, Path, Path, list[str]]]:
    tmp = tmp_path / "tmp"
    out = tmp_path / "out"
    data_dir = tmp_path / "data"
    tmp.mkdir()
    out.mkdir()
    yield tmp, out, data_dir, _facts_argv(tmp, out, data_dir)


def test_finish_places_a_clean_run(run: tuple[Path, Path, Path, list[str]]) -> None:
    tmp, out, _, argv = run
    (tmp / "stdout.raw").write_bytes(b"alpha/one.txt\nalpha/two.txt\n")
    (tmp / "stderr.raw").write_bytes(b"")
    assert cli.main(argv) == 0
    meta = dict(
        line.split("=", 1) for line in (out / "run.meta").read_text().splitlines() if "=" in line
    )
    assert meta["stdout_path"] == "stdout.txt"
    assert meta["redaction_changed_bytes"] == "no"
    assert meta["stdout_truncated"] == "no"
    assert meta["stdout_sha256"] == hashlib.sha256((out / "stdout.txt").read_bytes()).hexdigest()
    assert "# Smoke receipt" in (out / "receipt.md").read_text()
    assert not (out / "quarantine").exists()


def test_finish_uses_the_binary_path_for_s3_fast_list_parquet(
    run: tuple[Path, Path, Path, list[str]],
) -> None:
    tmp, _out, data_dir, argv = run
    argv = ["--tool=s3-fast-list" if arg.startswith("--tool=") else arg for arg in argv]
    argv = ["--mode=list" if arg.startswith("--mode=") else arg for arg in argv]
    payload = b"PAR1" + b"x" * (LINE_SIZE_LIMIT + 1) + b"PAR1"
    (tmp / "stdout.raw").write_bytes(payload)
    (tmp / "stderr.raw").write_bytes(b"")

    assert cli.main(argv) == 0
    placed = list((data_dir / "receipts" / "s3-fast-list").glob("*.stdout.txt"))
    assert len(placed) == 1
    assert placed[0].read_bytes() == payload


def test_finish_hashes_the_redacted_bytes_not_the_raw_ones(
    run: tuple[Path, Path, Path, list[str]],
) -> None:
    """Redact before hash: the hash freezes the bytes, so the order is the point."""
    tmp, out, _, argv = run
    raw = b"owner=" + KEY_ID.encode() + b"\n"
    (tmp / "stdout.raw").write_bytes(raw)
    (tmp / "stderr.raw").write_bytes(b"")
    assert cli.main(argv) == 0
    placed = (out / "stdout.txt").read_bytes()
    assert placed == b"owner=<REDACTED-AWS-KEY-ID>\n"
    meta = (out / "run.meta").read_text()
    assert f"stdout_sha256={hashlib.sha256(placed).hexdigest()}" in meta
    assert hashlib.sha256(raw).hexdigest() not in meta
    assert "redaction_changed_bytes=yes" in meta


def test_a_credential_beyond_the_cap_is_still_flagged_and_quarantined(
    run: tuple[Path, Path, Path, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scan the FULL redacted stream, then truncate — never the other way round.

    The scan runs before truncation precisely so the dropped tail cannot hide a
    secret. With the cap lowered, a secret past it must still stop the run, and
    the offending stream must land in ``$OUT/quarantine`` rather than be removed
    by the very failure that found it.
    """
    tmp, out, _, argv = run
    monkeypatch.setattr(cli, "PAYLOAD_CAP", 16)
    # Redaction does not catch a bare secret-key assignment with this spelling,
    # which is exactly the case the scan exists to backstop.
    (tmp / "stdout.raw").write_bytes(b"x" * 64 + b"\naws_secret_access_key = " + b"Z" * 40 + b"\n")
    (tmp / "stderr.raw").write_bytes(b"")
    assert cli.main(argv) == 2
    quarantined = out / "quarantine" / "stdout"
    assert quarantined.exists()
    assert b"Z" * 40 in quarantined.read_bytes()
    assert not (out / "run.meta").exists()
    assert not (out / "receipt.md").exists()


def test_truncation_is_recorded_loudly(
    run: tuple[Path, Path, Path, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp, out, _, argv = run
    monkeypatch.setattr(cli, "PAYLOAD_CAP", 8)
    (tmp / "stdout.raw").write_bytes(b"0123456789abcdef\n")
    (tmp / "stderr.raw").write_bytes(b"")
    assert cli.main(argv) == 0
    meta = (out / "run.meta").read_text()
    assert "stdout_truncated=yes" in meta
    assert "stdout_dropped_bytes=9" in meta
    receipt = (out / "receipt.md").read_text()
    # The receipt states the shipped cap; only the trigger is lowered here.
    assert f"stdout TRUNCATED at the {PAYLOAD_CAP}-byte (64 MiB) cap" in receipt
    assert "9 bytes dropped (head kept)" in receipt
    assert "**Truncation warning.**" in receipt


def test_an_oversized_payload_is_placed_outside_the_receipt_directory(
    run: tuple[Path, Path, Path, list[str]],
) -> None:
    tmp, out, data_dir, argv = run
    (tmp / "stdout.raw").write_bytes(b"k\n" * (INLINE_MAX // 2 + 1))
    (tmp / "stderr.raw").write_bytes(b"")
    assert cli.main(argv) == 0
    external = data_dir / "receipts" / "fixture-tool"
    placed = sorted(external.iterdir())
    assert [p.name for p in placed] == [
        "recursive.fixture-bucket.full.anonymous.stdout.txt",
    ]
    assert f"stdout_path={placed[0]}" in (out / "run.meta").read_text()
    assert not (out / "stdout.txt").exists()


def test_an_external_payload_never_clobbers_different_bytes(
    run: tuple[Path, Path, Path, list[str]],
) -> None:
    """Another receipt may already cite those bytes by hash."""
    tmp, out, data_dir, argv = run
    external = data_dir / "receipts" / "fixture-tool"
    external.mkdir(parents=True)
    (external / "recursive.fixture-bucket.full.anonymous.stdout.txt").write_bytes(b"other\n")
    (tmp / "stdout.raw").write_bytes(b"k\n" * (INLINE_MAX // 2 + 1))
    (tmp / "stderr.raw").write_bytes(b"")
    assert cli.main(argv) == 2
    assert not (out / "run.meta").exists()


def test_a_control_byte_in_a_field_stops_the_receipt(
    run: tuple[Path, Path, Path, list[str]],
) -> None:
    tmp, out, _, argv = run
    (tmp / "stdout.raw").write_bytes(b"ok\n")
    (tmp / "stderr.raw").write_bytes(b"")
    argv = [a for a in argv if not a.startswith("--tool-version=")]
    argv.append("--tool-version=1.0\nredaction_changed_bytes=no")
    assert cli.main(argv) == 2
    assert not (out / "run.meta").exists()
    assert not (out / "receipt.md").exists()
