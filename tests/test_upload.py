"""Tests for s3_listing_study.worker.upload.

File selection, ordering, precondition handling, and token resolution.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

import pytest

from s3_listing_study.manager.upload import UploadError, parse_destination, upload_attempt
from s3_listing_study.worker.upload import TOKEN_ENV_VAR, access_token


class _FakeBucket:
    """Records what a uploader was asked to create, and refuses replacements."""

    def __init__(self, existing: set[str] | None = None) -> None:
        self.uploaded: dict[str, bytes] = {}
        self.existing = existing or set()

    def send(self, bucket: str, object_name: str, local_path: Path) -> None:
        assert bucket == "my-bucket"
        if object_name in self.existing:
            raise UploadError(
                f"{object_name} already exists at the destination — an attempt is never overwritten"
            )
        self.uploaded[object_name] = local_path.read_bytes()


def _write_attempt(tmp_path: Path, *, with_native: bool = False) -> Path:
    attempt_dir = tmp_path / "attempt-1"
    attempt_dir.mkdir()
    (attempt_dir / "result.json").write_bytes(b'{"outcome":{"status":"completed"}}')
    (attempt_dir / "stdout.raw.gz").write_bytes(b"stdout-bytes")
    (attempt_dir / "stderr.raw.gz").write_bytes(b"stderr-bytes")
    (attempt_dir / "collected.json").write_bytes(b'{"row_count":1}')
    if with_native:
        native = attempt_dir / "native"
        native.mkdir()
        (native / "part-0.parquet").write_bytes(b"parquet-bytes")
    return attempt_dir


def test_parse_destination_splits_bucket_and_prefix() -> None:
    assert parse_destination("gs://bucket/some/prefix/") == ("bucket", "some/prefix")
    assert parse_destination("gs://bucket") == ("bucket", "")


def test_parse_destination_refuses_non_gs_url() -> None:
    with pytest.raises(UploadError):
        parse_destination("s3://bucket/prefix")


def test_result_json_uploads_last(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path)
    bucket = _FakeBucket()
    uploaded = upload_attempt(
        attempt_dir, "gs://my-bucket/campaign-1/case-a", metadata_only=False, uploader=bucket.send
    )
    assert uploaded[-1] == "campaign-1/case-a/result.json"
    assert set(uploaded) == {
        "campaign-1/case-a/collected.json",
        "campaign-1/case-a/stdout.raw.gz",
        "campaign-1/case-a/stderr.raw.gz",
        "campaign-1/case-a/result.json",
    }


def test_native_output_directory_uploads_too(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path, with_native=True)
    bucket = _FakeBucket()
    uploaded = upload_attempt(
        attempt_dir, "gs://my-bucket/x", metadata_only=False, uploader=bucket.send
    )
    assert "x/native/part-0.parquet" in uploaded


def test_metadata_only_skips_raw_listing_bytes(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path, with_native=True)
    bucket = _FakeBucket()
    uploaded = upload_attempt(
        attempt_dir, "gs://my-bucket/x", metadata_only=True, uploader=bucket.send
    )
    assert set(uploaded) == {"x/collected.json", "x/result.json"}


def test_existing_object_refuses_overwrite(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path)
    bucket = _FakeBucket(existing={"x/result.json"})
    with pytest.raises(UploadError, match="never overwritten"):
        upload_attempt(attempt_dir, "gs://my-bucket/x", metadata_only=False, uploader=bucket.send)


def test_missing_result_json_refuses(tmp_path: Path) -> None:
    attempt_dir = tmp_path / "attempt-1"
    attempt_dir.mkdir()
    bucket = _FakeBucket()
    with pytest.raises(UploadError, match="not a finalized attempt directory"):
        upload_attempt(attempt_dir, "gs://my-bucket/x", metadata_only=False, uploader=bucket.send)


def test_injected_token_is_preferred_over_the_metadata_server() -> None:
    """The bridge a subject runs on cannot reach metadata, so injection must win."""
    assert access_token({TOKEN_ENV_VAR: "  ya29.injected  "}) == "ya29.injected"


def test_absent_token_and_unreachable_metadata_is_an_upload_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure must name the env var, not surface a bare urllib error.

    The metadata endpoint is stubbed rather than left to the network: this
    suite runs on a GCE instance where that name really does resolve and would
    hand back a live token, which is neither the behaviour under test nor
    something a test should pull into its output.
    """

    def unreachable(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(urllib.request, "urlopen", unreachable)
    with pytest.raises(UploadError, match=TOKEN_ENV_VAR):
        access_token({"PATH": "/usr/bin"})
