"""Credential value-shape scanning shared by raw attempts and receipt tooling.

The three outcomes are deliberately distinct: a scanner error is not a clean
scan.  The byte scanner is suitable for opaque output because it imposes no
line-oriented text limit; the line-oriented scanner retains the historical
receipt and repository-audit behavior.
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
"""Maximum bytes in one line, including its terminator when present."""


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


# ``[[:space:]]`` in the C locale, pinned as bytes so locale cannot widen it.
_SPACE = rb"[ \t\n\r\f\v]"

# Match credential-shaped values, not merely sensitive variable names. This
# intentionally ignores empty credential-starvation settings and pagination
# cursors while detecting the shapes exercised by the historical audit corpus.
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
    """Clean, flagged, and scanner-error outcomes and their conventional codes."""

    CLEAN = 0
    FLAGGED = 1
    ERROR = 2


def scan_bytes(data: bytes) -> bool:
    """Return whether ``data`` carries a credential-shaped value."""
    return SCAN_SECRET_RE.search(data) is not None


def scan_file(path: Path) -> Outcome:
    """Classify one line-oriented file; unreadable is ERROR, never CLEAN."""
    try:
        with open(path, "rb") as handle:
            for line in bounded_lines(handle):
                if SCAN_SECRET_RE.search(line):
                    return Outcome.FLAGGED
    except (OSError, LineTooLongError):
        return Outcome.ERROR
    return Outcome.CLEAN


def scan_binary_file(path: Path) -> Outcome:
    """Classify an opaque binary file without imposing a text-line limit."""
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
    """Scan regular files under ``root`` and return ``(flagged, scanned)``.

    Only ``.git`` is skipped. Symlinks are not followed. Any traversal or file
    scan error fails the whole operation closed.
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
