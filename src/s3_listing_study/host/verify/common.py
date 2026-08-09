"""Pieces both verdict paths need: the manifest, the adapter, the re-list.

Nothing here forms a verdict. It is the plumbing the single-receipt path and
the union path share, kept in one place so the two cannot drift apart on the
things that decide a published finding — how the reference is re-listed, how its
output is canonicalised, and how a payload is bound to the bytes its receipt
cites.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import random
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from .errors import VerifierError
from .meta import resolve_payload_path
from .security import SecurityBoundary, docker_status

Stream = tuple[str, str, str, str]
"""One recorded payload stream: name, resolved path, sha256, truncated (yes|no)."""

NORMALIZE_TIMEOUT_S = 300
"""Maximum wall time for one host-side adapter normalization."""


def say(message: str) -> None:
    sys.stderr.write(f"verify-listing: {message}\n")


def generated_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(source: str | Path, destination: Path) -> str:
    """Copy first, then hash the copy, then check the copy — no TOCTOU window.

    Hashing a path and reopening it lets the file be replaced, or a symlink
    retargeted, in between; the verdict would then be derived from bytes whose
    cited digest was never checked.
    """
    shutil.copyfile(source, destination)
    return sha256_file(destination)


def bind_streams(run_meta: Path, meta: dict[str, str]) -> list[Stream]:
    """Every payload stream one ``run.meta`` records, resolved and unchecked.

    BOTH streams: mapping only stdout made any tool that emits its listing to
    stderr unverifiable. A stream missing either its path or its digest is not
    bound at all — there is nothing to bind it to.

    One copy, because the single-receipt path and the union path resolve the
    same three facts about the same file and would bind different bytes for the
    same receipt if they ever drifted on how a recorded path is resolved.
    :func:`~s3_listing_study.host.verify.meta.resolve_payload_path` raises
    :class:`VerifierError` on a ``payload_path_base`` this build does not
    implement; each caller phrases its own refusal around that.
    """
    bound: list[Stream] = []
    for stream in ("stdout", "stderr"):
        recorded = meta.get(f"{stream}_path", "")
        sha = meta.get(f"{stream}_sha256", "")
        if not recorded or not sha:
            continue
        path = resolve_payload_path(run_meta, recorded)
        bound.append((stream, path, sha, meta.get(f"{stream}_truncated", "") or "no"))
    return bound


def read_manifest(snapshot_gz: Path) -> bytes:
    try:
        with gzip.open(snapshot_gz, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise VerifierError(f"manifest could not be decompressed: {exc}") from None


def manifest_field_count(data: bytes) -> int:
    """Fields in the manifest's first record. Contract v2 expects 5."""
    if not data:
        return 0
    return len(data.split(b"\n", 1)[0].split(b"\t"))


def normalize(adapter: str, mode: str, prefix: str, payload: Path) -> bytes:
    """Run a mode's normalize adapter over one payload and return its records.

    The run's prefix (from ``run.meta``) is the second argument so a mode that
    prints path-relative names can reconstruct full keys; empty for a
    full-bucket run.
    """
    with payload.open("rb") as handle:
        try:
            proc = subprocess.run(
                [adapter, mode, prefix],
                stdin=handle,
                stdout=subprocess.PIPE,
                check=False,
                timeout=NORMALIZE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            raise VerifierError(
                f"normalize adapter timed out after {NORMALIZE_TIMEOUT_S}s on {payload} "
                f"(mode {mode})"
            ) from None
    if proc.returncode != 0:
        raise VerifierError(f"normalize adapter failed on {payload} (mode {mode})")
    return proc.stdout


def canonicalise_reference(raw: bytes) -> list[bytes]:
    """The manifest's exact canonicalisation of an ``s3api ... --output text`` listing.

    ETags come back wrapped in literal quotes and are stripped; ``LastModified``
    comes back as ``…+00:00`` (the container runs ``TZ=UTC``, so it is genuinely
    UTC) and is rewritten to the contract-v2 ``…Z``. The full five-field record
    is captured, because a key-only — or even key/size/etag — drift check misses
    an identical-byte overwrite that changes ONLY the mtime, and the tool then
    eats a FAIL for correctly reporting what the bucket now holds.
    """
    if not raw:
        return []
    body = raw[:-1] if raw.endswith(b"\n") else raw
    rows = []
    for line in body.split(b"\n"):
        fields = line.replace(b'"', b"").split(b"\t")
        fields += [b""] * (5 - len(fields))
        if fields[3].endswith(b"+00:00"):
            fields[3] = fields[3][: -len(b"+00:00")] + b"Z"
        rows.append(b"\t".join(fields[:5]))
    return rows


def comm_split(left: list[bytes], right: list[bytes]) -> tuple[int, int, int]:
    """``comm`` over two sorted multisets: (left-only, right-only, common) counts."""
    i = j = 0
    only_left = only_right = both = 0
    while i < len(left) and j < len(right):
        if left[i] < right[j]:
            only_left += 1
            i += 1
        elif left[i] > right[j]:
            only_right += 1
            j += 1
        else:
            both += 1
            i += 1
            j += 1
    only_left += len(left) - i
    only_right += len(right) - j
    return only_left, only_right, both


def relist(
    security: SecurityBoundary,
    image: str,
    bucket: str,
    region: str,
    prefix: str,
) -> tuple[int, list[bytes], bytes]:
    """Re-list the reference with the pinned client. Returns (rc, records, stderr).

    The verifier's first move on ANY discrepancy. A third-party bucket can move
    under us mid-campaign, and recording drift as a tool failure would report a
    defect that isn't there.
    """
    name = f"s3study-reference-{os.getpid()}-{random.randint(0, 32767)}"
    argv = security.run_prefix(name, image)
    argv += [
        "s3api",
        "list-objects-v2",
        "--bucket",
        bucket,
        "--region",
        region,
        "--no-sign-request",
    ]
    if prefix:
        argv += ["--prefix", prefix]
    argv += [
        "--query",
        "Contents[].[Key,Size,ETag,LastModified,StorageClass]",
        "--output",
        "text",
    ]
    proc = subprocess.run(argv, capture_output=True, check=False)
    if proc.returncode != 0 and not security.reconcile_container_absent(name):
        raise VerifierError(
            f"reference re-list {docker_status(proc.returncode)} and bounded "
            "cleanup/absence could not be confirmed; discard this runner"
        )
    return proc.returncode, canonicalise_reference(proc.stdout), proc.stderr


def head(text: bytes, lines: int) -> str:
    """``$(head -N file)``: the first N lines, with the trailing newline dropped."""
    return "\n".join(text.decode("utf-8", errors="replace").splitlines()[:lines])
