"""The credential value-shape scan, against the fixtures that prove it has teeth.

The dirty fixtures stay base64-obfuscated on disk so a whole-repo tree scan
needs no name-based exclusion — a name-based exclusion is a bypass anything else
can hide behind — and are decoded into a tmp dir here, at test time.

The third outcome is the point of the file. ``grep`` exits 2 on error, and
treating that as "no match" turns a broken scanner into a pass; that bug shipped
once. So an unreadable file is asserted to classify as ERROR, not CLEAN.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from s3_listing_study.manager.receipt.scan import (
    LINE_SIZE_LIMIT,
    Outcome,
    TreeScanError,
    scan_binary_file,
    scan_file,
    scan_tree,
)

HARNESS = Path(__file__).resolve().parents[1] / "harness"
FIXTURES = HARNESS / "tests" / "scan-fixtures"
CLEAN = sorted(p for p in (FIXTURES / "clean").iterdir() if p.is_file())
OBFUSCATED = sorted((FIXTURES / "dirty").glob("*.b64"))


@pytest.fixture(scope="module")
def dirty(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """The dirty corpus, decoded — never on disk in the repo."""
    out = tmp_path_factory.mktemp("dirty")
    decoded = {}
    for source in OBFUSCATED:
        target = out / source.stem
        target.write_bytes(base64.b64decode(source.read_bytes()))
        decoded[target.name] = target
    return decoded


def test_fixture_corpus_is_present() -> None:
    """A vanished corpus must fail, not silently parametrize into zero tests."""
    assert CLEAN
    assert OBFUSCATED


@pytest.mark.parametrize("path", CLEAN, ids=lambda p: p.name)
def test_clean_stays_clean(path: Path) -> None:
    """No false positive — including the wrapper's own empty starving flag,
    redacted text, and a paginating tool's ContinuationToken."""
    assert scan_file(path) is Outcome.CLEAN


@pytest.mark.parametrize("name", [p.stem for p in OBFUSCATED])
def test_dirty_flags(dirty: dict[str, Path], name: str) -> None:
    assert scan_file(dirty[name]) is Outcome.FLAGGED


@pytest.mark.parametrize("path", OBFUSCATED, ids=lambda p: p.name)
def test_stored_obfuscated_fixture_is_clean_on_disk(path: Path) -> None:
    assert scan_file(path) is Outcome.CLEAN


def test_unreadable_file_is_scanner_error_not_clean(tmp_path: Path) -> None:
    """rc=2 is its own outcome. A scanner that cannot read a file has not passed it."""
    if os.geteuid() == 0:
        pytest.skip("root reads a 0000 file, so the unreadable case cannot be staged")
    unreadable = tmp_path / "unreadable.txt"
    unreadable.write_text("harmless\n")
    unreadable.chmod(0o000)
    try:
        assert scan_file(unreadable) is Outcome.ERROR
    finally:
        unreadable.chmod(0o600)


def test_missing_file_is_scanner_error(tmp_path: Path) -> None:
    assert scan_file(tmp_path / "absent.txt") is Outcome.ERROR


def test_scan_line_size_boundary_fails_closed(tmp_path: Path) -> None:
    boundary = tmp_path / "boundary.txt"
    boundary.write_bytes(b"x" * LINE_SIZE_LIMIT)
    assert scan_file(boundary) is Outcome.CLEAN
    boundary.write_bytes(b"x" * (LINE_SIZE_LIMIT + 1))
    assert scan_file(boundary) is Outcome.ERROR


def test_binary_scan_accepts_long_newline_free_content_and_still_flags(tmp_path: Path) -> None:
    payload = tmp_path / "listing.parquet"
    payload.write_bytes(b"PAR1" + b"x" * (LINE_SIZE_LIMIT + 1) + b"PAR1")
    assert scan_binary_file(payload) is Outcome.CLEAN
    payload.write_bytes(payload.read_bytes() + b"AKIA" + b"Q" * 16)
    assert scan_binary_file(payload) is Outcome.FLAGGED


def test_tree_clean(tmp_path: Path) -> None:
    flagged, scanned = scan_tree(FIXTURES / "clean")
    assert flagged == []
    assert scanned == len(CLEAN)


def test_tree_flags_decoded_dirty(dirty: dict[str, Path]) -> None:
    root = next(iter(dirty.values())).parent
    flagged, scanned = scan_tree(root)
    assert sorted(p.name for p in flagged) == sorted(dirty)
    assert scanned == len(dirty)


def test_tree_stored_b64_stays_clean() -> None:
    flagged, scanned = scan_tree(FIXTURES / "dirty")
    assert flagged == []
    assert scanned == len(OBFUSCATED)


def test_tree_bad_argument_is_scanner_error(tmp_path: Path) -> None:
    with pytest.raises(TreeScanError):
        scan_tree(tmp_path / "no-such-dir")


def test_tree_skips_only_dot_git(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "blob").write_bytes(base64.b64decode(OBFUSCATED[0].read_bytes()))
    (tmp_path / "scan-fixtures").mkdir()
    (tmp_path / "scan-fixtures" / "blob").write_bytes(base64.b64decode(OBFUSCATED[0].read_bytes()))
    flagged, scanned = scan_tree(tmp_path)
    # .git is pruned; a directory NAMED like the scanner's own corpus is not.
    assert [p.name for p in flagged] == ["blob"]
    assert scanned == 1


def test_unreadable_file_in_tree_is_not_a_clean_pass(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads a 0000 file, so the unreadable case cannot be staged")
    blocked = tmp_path / "blocked.txt"
    blocked.write_text("harmless\n")
    blocked.chmod(0o000)
    try:
        with pytest.raises(TreeScanError):
            scan_tree(tmp_path)
    finally:
        blocked.chmod(0o600)
