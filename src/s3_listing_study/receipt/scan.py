"""Secret scanning: classify a redacted stream as clean, flagged, or broken.

The three outcomes must never be conflated: clean / flagged / scanner-broke.
A scanner that broke is not a clean scan. Validated against
``harness/tests/scan-fixtures/``.

gitleaks is deliberately NOT used (owner's call, 2026-07-16): its entropy rules
fire on S3 pagination cursors, so every paginating tool's ``--debug`` receipt
trips it. This scan matches credential VALUES by shape — AKIA/ASIA key ids, hex
signatures, long base64 assignments — not variable names. Two consequences:

* a receipt legitimately containing ``-e AWS_SECRET_ACCESS_KEY=`` with an EMPTY
  value is the wrapper's credential starvation made visible, NOT a leak, and the
  shape requirement (a value of real length must follow) keeps it clean;
* it does not fire on the ContinuationToken entropy that blocked real evidence.

Re-enable gitleaks — with the pagination-cursor allowlist that was tested and
works — before ever setting CREDS, where signatures and account IDs appear.
"""

from __future__ import annotations

import mmap
import os
import re
from collections.abc import Iterator
from enum import IntEnum
from pathlib import Path
from typing import BinaryIO

LINE_SIZE_LIMIT = 1024 * 1024
"""Maximum bytes in one line, including its terminator when present.

Both redaction and scanning use this bound before applying a regular expression,
so a newline-free or huge-line payload cannot force an unbounded allocation.
"""


class LineTooLongError(Exception):
    """A line exceeded :data:`LINE_SIZE_LIMIT`; processing must fail closed."""


def bounded_lines(handle: BinaryIO) -> Iterator[bytes]:
    """Yield binary lines without ever reading more than the shared line limit."""
    while True:
        line = handle.readline(LINE_SIZE_LIMIT + 1)
        if not line:
            return
        if len(line) > LINE_SIZE_LIMIT:
            raise LineTooLongError(f"line exceeds the {LINE_SIZE_LIMIT}-byte receipt safety limit")
        yield line


# ``[[:space:]]`` in the C locale, spelled out rather than left to ``\s``: the
# byte set is pinned here, so it cannot widen under a locale or an interpreter.
_SPACE = rb"[ \t\n\r\f\v]"

# The pattern requires a credential-SHAPED VALUE, not merely "something after
# =". Match the shape (AKIA + 16, a hex signature, 20+ base64 chars) and the two
# historic false positives — the wrapper's own empty starving flag, and a ``-``
# of the next argv element being read as a value — both disappear without losing
# teeth.
SCAN_SECRET_RE = re.compile(
    rb"AKIA[A-Z0-9]{16}"
    rb"|ASIA[A-Z0-9]{16}"
    rb"|X-Amz-Signature=[A-Fa-f0-9]{16,}"
    rb"|X-Amz-Credential=[A-Za-z0-9%/+-]{10,}"
    rb"|X-Amz-Security-Token=[A-Za-z0-9%/+=]{20,}"
    rb"|(AWS_SESSION_TOKEN|AWS_SECRET_ACCESS_KEY)=[A-Za-z0-9/+=]{16,}"
    rb"|aws_secret_access_key" + _SPACE + rb"*=" + _SPACE + rb"*[A-Za-z0-9/+=]{20,}"
    rb"|Authorization:" + _SPACE + rb"*(AWS4-HMAC-SHA256|Bearer|Basic)" + _SPACE,
    re.IGNORECASE,
)


class Outcome(IntEnum):
    """The three outcomes, never conflated — and the exit codes that carry them.

    ``grep`` exits 1 on no-match and 2 on error; treating rc=2 as "no match"
    turns a broken scan into a pass. That bug shipped in the first draft of the
    wrapper's scanner and must not return, so the error case is a value of its
    own rather than the absence of a match.
    """

    CLEAN = 0
    FLAGGED = 1
    ERROR = 2


def scan_bytes(data: bytes) -> bool:
    """True when ``data`` carries a credential-shaped value."""
    return SCAN_SECRET_RE.search(data) is not None


def scan_file(path: Path) -> Outcome:
    """Classify ONE file. Unreadable is :attr:`Outcome.ERROR`, never CLEAN.

    Scanned line by line, as ``grep`` scans it: a pattern's ``[[:space:]]`` can
    never span a line break, so a header split across two lines must not match
    here either.
    """
    try:
        with open(path, "rb") as handle:
            for line in bounded_lines(handle):
                if SCAN_SECRET_RE.search(line):
                    return Outcome.FLAGGED
    except (OSError, LineTooLongError):
        return Outcome.ERROR
    return Outcome.CLEAN


def scan_binary_file(path: Path) -> Outcome:
    """Classify an opaque binary payload without imposing a text-line limit.

    Binary listing formats such as Parquet legitimately contain arbitrarily
    long newline-free regions. Memory-map the file so the same credential-shape
    expression sees every byte without first allocating the whole payload.
    """
    try:
        with open(path, "rb") as handle:
            if os.fstat(handle.fileno()).st_size == 0:
                return Outcome.CLEAN
            with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as payload:
                if SCAN_SECRET_RE.search(payload):
                    return Outcome.FLAGGED
    except (OSError, ValueError):
        return Outcome.ERROR
    return Outcome.CLEAN


class TreeScanError(Exception):
    """The tree scan could not run. Never a pass, never a leak."""


def scan_tree(root: Path) -> tuple[list[Path], int]:
    """Scan every regular file under ``root``; return (flagged, scanned).

    Skips only ``.git``. There is NO name-based exclusion: a ``scan-fixtures``
    prune would let anyone bypass the scan by naming a directory
    ``scan-fixtures``. The scanner's own dirty corpus does not need excluding
    because it carries no credential-shaped bytes on disk — the dirty fixtures
    are stored base64-obfuscated and decoded only at test time.

    A traversal failure is a :class:`TreeScanError`, not a clean pass: a scan
    that cannot read a directory has not cleared the tree under it. Symlinks are
    not followed, matching ``find -type f`` under its default ``-P``.
    """
    if not root.is_dir():
        raise TreeScanError(f"not a directory: {root}")
    flagged: list[Path] = []
    scanned = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise TreeScanError(
                f"cannot traverse {directory} ({exc.strerror}) "
                "— a traversal failure is not a clean pass"
            ) from None
        for entry in entries:
            if entry.name == ".git":
                continue
            path = Path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError as exc:
                raise TreeScanError(
                    f"cannot stat {path} ({exc.strerror}) — a traversal failure is not a clean pass"
                ) from None
            scanned += 1
            outcome = scan_file(path)
            if outcome is Outcome.FLAGGED:
                flagged.append(path)
            elif outcome is Outcome.ERROR:
                raise TreeScanError(
                    f"scanner error on {path} — a scan that cannot read a file is not a pass"
                )
    return flagged, scanned
