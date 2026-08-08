"""Redaction, truncation, hashing and placement of a captured stream.

The order is fixed and load-bearing: **redact, then scan, then truncate, then hash** — redaction and
the scan over the FULL stream, so a credential the redaction did not catch is
still flagged when it sits beyond the 64 MiB cap instead of being silently
dropped with the truncated tail; and hashing last, because the hash freezes the
bytes and redaction after it would redact nothing.

Account IDs are redacted explicitly: the installed gitleaks default rules do NOT
flag a bare 12-digit ARN account, so an ``arn:aws:iam::123456789012:role/X`` in
debug output would otherwise be hashed and published. Deliberately narrow —
matched only in ARN position or as an ``Owner`` field, because 12-digit numbers
are also legitimate object sizes.
"""

from __future__ import annotations

import hashlib
import mmap
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .errors import ReceiptError
from .scan import LineTooLongError, bounded_lines

PAYLOAD_CAP = 64 * 1024 * 1024
"""Bytes kept per stream. A run emitting gigabytes of retry noise is evidence of
the retrying, not worth publishing byte-for-byte, and an unbounded capture can
fill the disk mid-study."""

INLINE_MAX = 102400
"""At or below this, the payload travels with ``run.meta`` in the receipt dir."""

_REDACTIONS: tuple[tuple[re.Pattern[bytes], bytes], ...] = (
    (re.compile(rb"(AKIA|ASIA)[A-Z0-9]{12,}"), rb"<REDACTED-AWS-KEY-ID>"),
    (re.compile(rb"([Xx]-[Aa]mz-[Ss]ignature=)[A-Fa-f0-9]+"), rb"\1<REDACTED>"),
    (re.compile(rb"([Xx]-[Aa]mz-[Cc]redential=)[^&\s\"]+"), rb"\1<REDACTED>"),
    (re.compile(rb"([Xx]-[Aa]mz-[Ss]ecurity-[Tt]oken=)[^&\s\"]+"), rb"\1<REDACTED>"),
    (re.compile(rb"([Aa]uthorization:[ \t\r\f\v]*).*"), rb"\1<REDACTED>"),
    (re.compile(rb"(AWS_SECRET_ACCESS_KEY=)[^\s]+"), rb"\1<REDACTED>"),
    (re.compile(rb"(AWS_SESSION_TOKEN=)[^\s]+"), rb"\1<REDACTED>"),
    (re.compile(rb"(Signature=)[A-Fa-f0-9]{32,}"), rb"\1<REDACTED>"),
    (
        re.compile(rb"(arn:aws[a-z-]*:[a-z0-9-]*:[a-z0-9-]*:)[0-9]{12}:"),
        rb"\1<REDACTED-ACCOUNT-ID>:",
    ),
    (
        re.compile(rb"(\"?[Oo]wner\"?[ \t\r\f\v]*[:=][ \t\r\f\v]*\"?)[0-9]{12}(\"?)"),
        rb"\1<REDACTED-ACCOUNT-ID>\2",
    ),
)


def redact_line(line: bytes) -> bytes:
    """Apply every substitution to one line, in order — as ``sed -e … -e …`` does."""
    for pattern, replacement in _REDACTIONS:
        line = pattern.sub(replacement, line)
    return line


def redact_file(src: Path, dst: Path) -> bool:
    """Redact ``src`` into ``dst``; return whether any byte changed.

    Line by line: no expression here may span a line break. Each line is bounded
    before regex processing; the separate 64 MiB payload cap still governs how
    many successfully processed bytes are retained afterward.
    """
    changed = False
    with open(src, "rb") as reader, open(dst, "wb") as writer:
        try:
            for line in bounded_lines(reader):
                out = redact_line(line)
                changed = changed or out != line
                writer.write(out)
        except LineTooLongError as exc:
            raise ReceiptError(f"cannot redact {src}: {exc}") from None
    return changed


def preserve_binary_file(src: Path, dst: Path) -> bool:
    """Copy an opaque binary stream unchanged, or refuse bytes needing redaction.

    Applying text substitutions to Parquet would silently corrupt the evidence.
    Search the memory-mapped source with every redaction expression first; a
    match therefore blocks publication for inspection instead of either leaking
    it or rewriting the binary payload. The map bounds Python-side memory even
    when the file contains no newlines.
    """
    try:
        with open(src, "rb") as reader:
            size = src.stat().st_size
            if size:
                with mmap.mmap(reader.fileno(), 0, access=mmap.ACCESS_READ) as payload:
                    if any(pattern.search(payload) for pattern, _ in _REDACTIONS):
                        raise ReceiptError(
                            f"cannot safely redact opaque binary payload {src}: "
                            "credential-shaped bytes require operator inspection"
                        )
            with open(dst, "wb") as writer:
                shutil.copyfileobj(reader, writer, length=1 << 20)
    except OSError as exc:
        raise ReceiptError(f"cannot preserve opaque binary payload {src}: {exc}") from None
    return False


def truncate_head(path: Path, cap: int = PAYLOAD_CAP) -> int:
    """Keep the first ``cap`` bytes; return how many were dropped (0 if none)."""
    size = path.stat().st_size
    if size <= cap:
        return 0
    with open(path, "rb+") as handle:
        handle.truncate(cap)
    return size - cap


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Payload:
    """One placed stream: where it is, what it hashes to, what was lost."""

    stream: str
    recorded_path: str
    size: int
    sha256: str
    truncated: str
    dropped_bytes: int
    note: str


def scope_tag(prefix: str) -> str:
    """``full``, or the prefix with everything outside ``[A-Za-z0-9._-]`` as ``_``.

    Scope belongs in the identity: the same mode runs full-bucket AND against
    each designated prefix, so mode+auth+stream alone makes the second >100KB
    payload collide with the first and abort a legitimate run.
    """
    if not prefix:
        return "full"
    return re.sub(r"[^A-Za-z0-9._-]", "_", prefix)


def place(
    *,
    stream: str,
    source: Path,
    out: Path,
    data_dir: Path,
    tool: str,
    mode: str,
    bucket: str,
    prefix: str,
    auth: str,
    truncated: bool,
    dropped_bytes: int,
    self_contained: bool = False,
) -> Payload:
    """Hash the redacted, scanned, truncated stream and put it where it belongs.

    The filename carries the auth mode: anonymous and credentialed runs of the
    same mode are both required, and ``<mode>.<stream>.txt`` alone lets the
    second silently overwrite bytes the first receipt already cited by hash.
    """
    size = source.stat().st_size
    sha = sha256_file(source)
    if size <= INLINE_MAX or self_contained:
        destination = out / f"{stream}.txt"
        if self_contained and destination.exists():
            raise ReceiptError(
                f"attempt payload {destination} already exists — refusing to overwrite evidence"
            )
        if self_contained:
            created = False
            try:
                with source.open("rb") as reader, destination.open("xb") as writer:
                    created = True
                    shutil.copyfileobj(reader, writer)
            except Exception:
                if created:
                    destination.unlink(missing_ok=True)
                raise
        else:
            shutil.copyfile(source, destination)
        if self_contained and sha256_file(destination) != sha:
            raise ReceiptError("payload hash changed during placement — refusing to cite it")
        # Inline payloads travel with run.meta. Record only the sibling
        # filename and declare its base in run.meta; embedding the caller's
        # relative --out spelling made old paths depend on an undeclared
        # invocation working directory.
        return Payload(
            stream=stream,
            recorded_path=f"{stream}.txt",
            size=size,
            sha256=sha,
            truncated="yes" if truncated else "no",
            dropped_bytes=dropped_bytes,
            note=(
                f"attempt-local — `{stream}.txt` ({size} bytes, sha256 `{sha}`)"
                if self_contained
                else f"inline — `{stream}.txt` ({size} bytes, sha256 `{sha}`)"
            ),
        )

    ext_dir = data_dir / "receipts" / tool
    ext_dir.mkdir(parents=True, exist_ok=True)
    ext = ext_dir.resolve() / f"{mode}.{bucket}.{scope_tag(prefix)}.{auth}.{stream}.txt"
    if ext.exists() and sha256_file(ext) != sha:
        raise ReceiptError(
            f"external payload {ext} already exists with different content — "
            "refusing to clobber evidence another receipt may cite."
        )
    shutil.copyfile(source, ext)
    # Re-hash at the destination: the receipt cites bytes at a path, so the
    # bytes at that path are what must be verified, not the ones we copied from.
    dest_sha = sha256_file(ext)
    if dest_sha != sha:
        raise ReceiptError("payload hash changed during placement — refusing to cite it")
    note = (
        f"external — `{ext}` ({size} bytes, sha256 `{dest_sha}`) — redacted and "
        "scanned before hashing; published as a release asset at publication"
    )
    return Payload(
        stream=stream,
        recorded_path=str(ext),
        size=size,
        sha256=sha,
        truncated="yes" if truncated else "no",
        dropped_bytes=dropped_bytes,
        note=note,
    )
