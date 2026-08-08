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

import gzip
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


def _redact_line_with_count(line: bytes) -> tuple[bytes, int]:
    """Return the redacted line and the number of source bytes matched.

    Counting matched source bytes gives the machine result a stable, useful
    redaction measure even when a replacement changes length. The public
    ``redact_line`` and ``redact_file`` APIs retain their historical return
    types for the production receipt path.
    """
    changed_bytes = 0
    for pattern, replacement in _REDACTIONS:
        def replace(match: re.Match[bytes], replacement: bytes = replacement) -> bytes:
            nonlocal changed_bytes
            rendered = match.expand(replacement)
            if rendered != match.group(0):
                changed_bytes += len(match.group(0))
            return rendered

        line = pattern.sub(replace, line)
    return line, changed_bytes


def redact_line(line: bytes) -> bytes:
    """Apply every substitution to one line, in order — as ``sed -e … -e …`` does."""
    return _redact_line_with_count(line)[0]


def redact_file_count(src: Path, dst: Path) -> int:
    """Redact ``src`` into ``dst``; return matched source bytes changed.

    Line by line: no expression here may span a line break. Each line is bounded
    before regex processing; the separate 64 MiB payload cap still governs how
    many successfully processed bytes are retained afterward.
    """
    changed_bytes = 0
    with open(src, "rb") as reader, open(dst, "wb") as writer:
        try:
            for line in bounded_lines(reader):
                out, line_changed = _redact_line_with_count(line)
                changed_bytes += line_changed
                writer.write(out)
        except LineTooLongError as exc:
            raise ReceiptError(f"cannot redact {src}: {exc}") from None
    return changed_bytes


def redact_file(src: Path, dst: Path) -> bool:
    """Redact ``src`` into ``dst``; return whether any byte changed."""
    return redact_file_count(src, dst) > 0


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


@dataclass(frozen=True)
class CompressedPayload:
    """Identity of an uncompressed safe stream and its stored gzip bytes."""

    raw_bytes: int
    raw_sha256: str
    stored_bytes: int
    stored_sha256: str


def gzip_exclusive(source: Path, destination: Path) -> CompressedPayload:
    """Store ``source`` as deterministic gzip without ever replacing evidence.

    A failed write deliberately leaves its partial destination in place. That
    makes the failure inspectable and ensures a retry refuses to mix attempts.
    """
    raw_bytes = source.stat().st_size
    raw_sha256 = sha256_file(source)
    try:
        with (
            destination.open("xb") as stored,
            gzip.GzipFile(filename="", mode="wb", fileobj=stored, mtime=0) as archive,
            source.open("rb") as reader,
        ):
            shutil.copyfileobj(reader, archive, length=1 << 20)
    except FileExistsError:
        raise ReceiptError(
            f"attempt artifact {destination} already exists — refusing to overwrite evidence"
        ) from None
    return CompressedPayload(
        raw_bytes=raw_bytes,
        raw_sha256=raw_sha256,
        stored_bytes=destination.stat().st_size,
        stored_sha256=sha256_file(destination),
    )


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
) -> Payload:
    """Hash the redacted, scanned, truncated stream and put it where it belongs.

    The filename carries the auth mode: anonymous and credentialed runs of the
    same mode are both required, and ``<mode>.<stream>.txt`` alone lets the
    second silently overwrite bytes the first receipt already cited by hash.
    """
    size = source.stat().st_size
    sha = sha256_file(source)
    if size <= INLINE_MAX:
        destination = out / f"{stream}.txt"
        shutil.copyfile(source, destination)
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
            note=f"inline — `{stream}.txt` ({size} bytes, sha256 `{sha}`)",
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
