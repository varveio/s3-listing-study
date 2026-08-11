"""Tests for s3_listing_study.worker.upload.

File selection, ordering, precondition handling, and token resolution.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import cast

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
    stdout = b"stdout-bytes"
    stderr = b"stderr-bytes"
    (attempt_dir / "stdout.raw.gz").write_bytes(stdout)
    (attempt_dir / "stderr.raw.gz").write_bytes(stderr)
    # A stale manager-produced file is deliberately not worker-owned and must
    # never be selected for upload.
    (attempt_dir / "collected.json").write_bytes(b'{"row_count":1}')
    native_output: list[dict[str, object]] = []
    if with_native:
        native = attempt_dir / "native"
        native.mkdir()
        data = b"parquet-bytes"
        (native / "part-0.parquet").write_bytes(data)
        native_output.append(
            {
                "path": "native/part-0.parquet",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    result = {
        "schema_version": 2,
        "outcome": {"status": "completed"},
        "streams": {
            "stdout": {
                "path": "stdout.raw.gz",
                "stored_bytes": len(stdout),
                "stored_sha256": hashlib.sha256(stdout).hexdigest(),
            },
            "stderr": {
                "path": "stderr.raw.gz",
                "stored_bytes": len(stderr),
                "stored_sha256": hashlib.sha256(stderr).hexdigest(),
            },
        },
        "native_output": native_output,
    }
    (attempt_dir / "result.json").write_text(json.dumps(result))
    return attempt_dir


def _result(attempt_dir: Path) -> dict[str, object]:
    return json.loads((attempt_dir / "result.json").read_text())  # type: ignore[no-any-return]


def _replace_result(attempt_dir: Path, result: dict[str, object]) -> None:
    (attempt_dir / "result.json").write_text(json.dumps(result))


def test_parse_destination_splits_bucket_and_prefix() -> None:
    assert parse_destination("gs://bucket/some/prefix/") == ("bucket", "some/prefix")
    assert parse_destination("gs://bucket") == ("bucket", "")


def test_parse_destination_refuses_non_gs_url() -> None:
    with pytest.raises(UploadError):
        parse_destination("s3://bucket/prefix")


def test_result_json_uploads_last(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path)
    bucket = _FakeBucket()
    uploaded = upload_attempt(attempt_dir, "gs://my-bucket/campaign-1/case-a", uploader=bucket.send)
    assert uploaded[-1] == "campaign-1/case-a/result.json"
    assert set(uploaded) == {
        "campaign-1/case-a/stdout.raw.gz",
        "campaign-1/case-a/stderr.raw.gz",
        "campaign-1/case-a/result.json",
    }


def test_native_output_directory_uploads_too(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path, with_native=True)
    bucket = _FakeBucket()
    uploaded = upload_attempt(attempt_dir, "gs://my-bucket/x", uploader=bucket.send)
    assert "x/native/part-0.parquet" in uploaded
    assert uploaded[-1] == "x/result.json"


def test_result_only_attempt_refuses_before_upload(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path)
    (attempt_dir / "stdout.raw.gz").unlink()
    (attempt_dir / "stderr.raw.gz").unlink()
    bucket = _FakeBucket()
    with pytest.raises(UploadError, match="missing stdout artifact"):
        upload_attempt(attempt_dir, "gs://my-bucket/x", uploader=bucket.send)
    assert bucket.uploaded == {}


def test_missing_declared_native_refuses_before_upload(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path, with_native=True)
    (attempt_dir / "native/part-0.parquet").unlink()
    bucket = _FakeBucket()
    with pytest.raises(UploadError, match="missing native_output"):
        upload_attempt(attempt_dir, "gs://my-bucket/x", uploader=bucket.send)
    assert bucket.uploaded == {}


def test_declared_traversal_refuses_before_upload(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path, with_native=True)
    result = _result(attempt_dir)
    native = cast(list[dict[str, object]], result["native_output"])
    native[0]["path"] = "native/../../outside"
    _replace_result(attempt_dir, result)
    bucket = _FakeBucket()
    with pytest.raises(UploadError, match="not canonical"):
        upload_attempt(attempt_dir, "gs://my-bucket/x", uploader=bucket.send)
    assert bucket.uploaded == {}


def test_undeclared_native_file_refuses_before_upload(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path, with_native=True)
    (attempt_dir / "native/extra").write_bytes(b"undeclared")
    bucket = _FakeBucket()
    with pytest.raises(UploadError, match=r"undeclared=.*native/extra"):
        upload_attempt(attempt_dir, "gs://my-bucket/x", uploader=bucket.send)
    assert bucket.uploaded == {}


def test_undeclared_native_symlink_refuses_before_upload(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path)
    (attempt_dir / "native").symlink_to(tmp_path / "missing")
    bucket = _FakeBucket()
    with pytest.raises(UploadError, match="native artifact root"):
        upload_attempt(attempt_dir, "gs://my-bucket/x", uploader=bucket.send)
    assert bucket.uploaded == {}


def test_tampered_declared_artifact_refuses_before_upload(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path)
    (attempt_dir / "stdout.raw.gz").write_bytes(b"changed")
    bucket = _FakeBucket()
    with pytest.raises(UploadError, match="does not match its recorded evidence"):
        upload_attempt(attempt_dir, "gs://my-bucket/x", uploader=bucket.send)
    assert bucket.uploaded == {}


def test_existing_object_refuses_overwrite(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path)
    bucket = _FakeBucket(existing={"x/result.json"})
    with pytest.raises(UploadError, match="never overwritten"):
        upload_attempt(attempt_dir, "gs://my-bucket/x", uploader=bucket.send)


def test_missing_result_json_refuses(tmp_path: Path) -> None:
    attempt_dir = tmp_path / "attempt-1"
    attempt_dir.mkdir()
    bucket = _FakeBucket()
    with pytest.raises(UploadError, match="not a finalized attempt directory"):
        upload_attempt(attempt_dir, "gs://my-bucket/x", uploader=bucket.send)


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
