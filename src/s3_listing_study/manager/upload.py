"""Host-side CLI for uploading a finalized attempt directory to GCS.

The uploader itself lives in ``s3_listing_study.worker.upload``, because an
attempt uploads its own directory from inside the image now. This module is the
host-side entry point for the same code: uploading an attempt that was produced
locally, or re-uploading one by hand.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from s3_listing_study.worker.upload import UploadError, parse_destination, upload_attempt

__all__ = ["UploadError", "parse_destination", "upload_attempt", "upload_attempt_main"]


def upload_attempt_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="s3-listing-study upload-attempt")
    parser.add_argument("--attempt-dir", required=True, type=Path)
    parser.add_argument(
        "--destination",
        required=True,
        help="orchestrator-supplied gs://bucket/prefix/ this attempt's files land directly under",
    )
    args = parser.parse_args(argv)

    try:
        uploaded = upload_attempt(args.attempt_dir, args.destination)
    except UploadError as exc:
        print(f"upload-attempt: {exc}", file=sys.stderr)
        return 2

    for blob_path in uploaded:
        print(f"upload-attempt: uploaded {blob_path}", file=sys.stderr)
    return 0
