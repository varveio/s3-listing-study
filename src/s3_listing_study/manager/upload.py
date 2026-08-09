"""Upload a finalized attempt directory to an orchestrator-supplied GCS prefix.

Deliberately not part of the attempt engine. The engine's atomic
``os.replace()`` of ``result.json`` is what makes a completed attempt
directory locally trustworthy; mounting the destination bucket with gcsfuse
would turn that into a non-atomic copy-and-delete, letting a partial
``result.json`` become visible at the destination. This module runs after an
attempt directory is already finalized on local disk, as a wholly separate
process — its own runtime never touches ``result.json``'s ``elapsed_ns``.

The destination is a complete ``gs://bucket/prefix/`` the caller (the
orchestrator) supplies outright: this module has no opinion on bucket choice
or layout (per-campaign bucket, shared bucket with a campaign prefix, keyed
or not by attempt ID) — that is the orchestrator's decision, not this
uploader's.

Uploads every raw file present by default: ``result.json``,
``stdout.raw.gz``, ``stderr.raw.gz``, ``native/**`` (if any),
``collected.json`` and ``normalized.parquet`` (if ``collect-attempt`` already
ran). ``--metadata-only`` uploads only the small, non-bulk files
(``result.json``, ``collected.json``) and skips the raw listing bytes.

``result.json`` uploads last: its presence at the destination prefix is what
lets an external reader treat "this attempt's upload is complete" as legible,
the same ordering discipline the local engine already uses for the same
reason. Every object uploads with ``if_generation_match=0`` — create-only —
so a retry can never silently overwrite a previous upload; the study's rule
that an attempt is never overwritten is enforced here, not just documented.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

BULK_FILES = ("stdout.raw.gz", "stderr.raw.gz", "normalized.parquet")
METADATA_FILES = ("collected.json",)
RESULT_FILE = "result.json"


class UploadError(RuntimeError):
    """A finalized attempt directory could not be uploaded."""


def _iter_upload_files(attempt_dir: Path, *, metadata_only: bool) -> list[Path]:
    files = [attempt_dir / name for name in METADATA_FILES if (attempt_dir / name).is_file()]
    if not metadata_only:
        files += [attempt_dir / name for name in BULK_FILES if (attempt_dir / name).is_file()]
        native_dir = attempt_dir / "native"
        if native_dir.is_dir():
            files += sorted(p for p in native_dir.rglob("*") if p.is_file())
    return files


def parse_destination(destination: str) -> tuple[str, str]:
    if not destination.startswith("gs://"):
        raise UploadError(f"destination must be a gs:// URL: {destination!r}")
    without_scheme = destination[len("gs://") :]
    bucket_name, _, prefix = without_scheme.partition("/")
    if not bucket_name:
        raise UploadError("destination must name a bucket: gs://bucket/prefix/")
    return bucket_name, prefix.rstrip("/")


def _upload_one(bucket: Any, blob_path: str, local_path: Path) -> None:
    from google.api_core.exceptions import PreconditionFailed

    blob = bucket.blob(blob_path)
    try:
        blob.upload_from_filename(str(local_path), if_generation_match=0)
    except PreconditionFailed as exc:
        raise UploadError(
            f"{blob_path} already exists at the destination — an attempt is never overwritten"
        ) from exc


def upload_attempt(
    attempt_dir: Path, destination: str, *, metadata_only: bool, client: Any | None = None
) -> list[str]:
    """Upload one finalized attempt directory; return the blob paths uploaded, in order."""
    result_path = attempt_dir / RESULT_FILE
    if not result_path.is_file():
        raise UploadError(f"not a finalized attempt directory: {attempt_dir}")

    bucket_name, prefix = parse_destination(destination)
    if client is None:
        from google.cloud import storage  # type: ignore[import-untyped]

        client = storage.Client()
    bucket = client.bucket(bucket_name)

    uploaded: list[str] = []
    for local_path in _iter_upload_files(attempt_dir, metadata_only=metadata_only):
        relative = local_path.relative_to(attempt_dir)
        blob_path = f"{prefix}/{relative.as_posix()}" if prefix else relative.as_posix()
        _upload_one(bucket, blob_path, local_path)
        uploaded.append(blob_path)

    result_blob_path = f"{prefix}/{RESULT_FILE}" if prefix else RESULT_FILE
    _upload_one(bucket, result_blob_path, result_path)
    uploaded.append(result_blob_path)
    return uploaded


def upload_attempt_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="s3-listing-study upload-attempt")
    parser.add_argument("--attempt-dir", required=True, type=Path)
    parser.add_argument(
        "--destination",
        required=True,
        help="orchestrator-supplied gs://bucket/prefix/ this attempt's files land directly under",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="upload only result.json and collected.json; skip raw listing output",
    )
    args = parser.parse_args(argv)

    try:
        uploaded = upload_attempt(
            args.attempt_dir, args.destination, metadata_only=args.metadata_only
        )
    except UploadError as exc:
        print(f"upload-attempt: {exc}", file=sys.stderr)
        return 2

    for blob_path in uploaded:
        print(f"upload-attempt: uploaded {blob_path}", file=sys.stderr)
    return 0
