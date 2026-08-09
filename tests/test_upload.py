"""Tests for s3_listing_study.manager.upload.

File selection, ordering, and precondition handling.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from s3_listing_study.manager.upload import UploadError, parse_destination, upload_attempt


class _FakeBlob:
    def __init__(self, bucket: _FakeBucket, path: str) -> None:
        self._bucket = bucket
        self.path = path

    def upload_from_filename(self, local_path: str, *, if_generation_match: int) -> None:
        assert if_generation_match == 0
        if self.path in self._bucket.existing:
            from google.api_core.exceptions import PreconditionFailed

            # google-api-core ships no type information, so its exception
            # constructor reads as untyped from here.
            raise PreconditionFailed("already exists")  # type: ignore[no-untyped-call]
        self._bucket.uploaded[self.path] = Path(local_path).read_bytes()


class _FakeBucket:
    def __init__(self, existing: set[str] | None = None) -> None:
        self.uploaded: dict[str, bytes] = {}
        self.existing = existing or set()

    def blob(self, path: str) -> _FakeBlob:
        return _FakeBlob(self, path)


class _FakeClient:
    def __init__(self, bucket: _FakeBucket) -> None:
        self._bucket = bucket

    def bucket(self, name: str) -> _FakeBucket:
        return self._bucket


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
    client = _FakeClient(bucket)
    uploaded = upload_attempt(
        attempt_dir, "gs://my-bucket/campaign-1/case-a", metadata_only=False, client=client
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
    client = _FakeClient(bucket)
    uploaded = upload_attempt(attempt_dir, "gs://my-bucket/x", metadata_only=False, client=client)
    assert "x/native/part-0.parquet" in uploaded


def test_metadata_only_skips_raw_listing_bytes(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path, with_native=True)
    bucket = _FakeBucket()
    client = _FakeClient(bucket)
    uploaded = upload_attempt(attempt_dir, "gs://my-bucket/x", metadata_only=True, client=client)
    assert set(uploaded) == {"x/collected.json", "x/result.json"}


def test_existing_object_refuses_overwrite(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path)
    bucket = _FakeBucket(existing={"x/result.json"})
    client = _FakeClient(bucket)
    with pytest.raises(UploadError, match="never overwritten"):
        upload_attempt(attempt_dir, "gs://my-bucket/x", metadata_only=False, client=client)


def test_missing_result_json_refuses(tmp_path: Path) -> None:
    attempt_dir = tmp_path / "attempt-1"
    attempt_dir.mkdir()
    client = _FakeClient(_FakeBucket())
    with pytest.raises(UploadError, match="not a finalized attempt directory"):
        upload_attempt(attempt_dir, "gs://my-bucket/x", metadata_only=False, client=client)
