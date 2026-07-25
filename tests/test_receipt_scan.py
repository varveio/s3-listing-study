"""The credential value-shape scan, against the fixtures that prove it has teeth.

Ports ``harness/tests/scan-fixtures-run.sh`` onto the Python scanner, keeping
the fixtures exactly as they are. The dirty fixtures stay base64-obfuscated on
disk so a whole-repo tree scan needs no name-based exclusion — the very bypass
that removal closed — and are decoded into a tmp dir here, at test time.

The third outcome is the point of the file. ``grep`` exits 2 on error, and
treating that as "no match" turns a broken scanner into a pass; that bug shipped
once. So an unreadable file is asserted to classify as ERROR, not CLEAN.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path

import pytest

from s3_listing_study.receipt.scan import (
    SCAN_SECRET_RE,
    Outcome,
    TreeScanError,
    scan_file,
    scan_tree,
)

HARNESS = Path(__file__).resolve().parents[1] / "harness"
FIXTURES = HARNESS / "tests" / "scan-fixtures"
SCAN_LIB = HARNESS / "scan-lib.sh"
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


def test_pattern_is_the_one_in_scan_lib_sh() -> None:
    """The two live scanners' regex, bound rather than trusted to stay in step.

    ``harness/scan-tree.sh`` is still the CI tree scanner and still sources
    ``harness/scan-lib.sh``, so the pattern now exists in two places. This repo's
    own history is that a regex drifting between two shell callers is exactly why
    ``scan-lib.sh`` was created; a comment would not have caught that either.

    One documented translation, and only one: the shell writes the POSIX class
    ``[[:space:]]``, which in the C locale (``scan-lib.sh`` and ``scan-tree.sh``
    both ``export LC_ALL=C``) is exactly ``[ \\t\\n\\r\\f\\v]``. Everything else
    must match character for character. Case-insensitivity is the shell's
    ``grep -aEi``.
    """
    shell = re.search(r"^SCAN_SECRET_RE='(.*)'$", SCAN_LIB.read_text(), re.MULTILINE)
    assert shell is not None, "scan-lib.sh no longer defines SCAN_SECRET_RE on one line"
    translated = shell.group(1).replace("[[:space:]]", r"[ \t\n\r\f\v]")
    assert translated == SCAN_SECRET_RE.pattern.decode()
    assert SCAN_SECRET_RE.flags & re.IGNORECASE


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
