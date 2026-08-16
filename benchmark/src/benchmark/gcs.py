"""Thin google-cloud-storage helpers shared by every script that touches gs://.

SDK calls are typed (list_blobs pagination, blob.exists(), download_as_bytes())
instead of parsing `gsutil ls`/`gsutil stat` text output, and there is no
external binary to shell out to. Every upload here is a plain overwrite --
no create-only (ifGenerationMatch=0) precondition, deliberately still out of
scope (see README.md, "Evidence publication is not sealed").
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import google.cloud.storage as storage

_client: storage.Client | None = None


def client() -> storage.Client:
    global _client
    if _client is None:
        _client = storage.Client()
    return _client


def parse_gs_uri(uri: str) -> tuple[str, str]:
    """``gs://bucket/some/prefix/`` -> ``("bucket", "some/prefix/")``."""
    if not uri.startswith("gs://"):
        raise ValueError(f"not a gs:// URI: {uri!r}")
    bucket, _, blob_prefix = uri.removeprefix("gs://").partition("/")
    return bucket, blob_prefix


def list_child_prefixes(uri: str) -> list[str]:
    """Immediate child "directories" under a gs:// prefix, as gs:// URIs."""
    bucket_name, prefix = parse_gs_uri(uri)
    if not prefix.endswith("/"):
        prefix += "/"
    iterator = client().bucket(bucket_name).list_blobs(prefix=prefix, delimiter="/")
    prefixes: list[str] = []
    for page in iterator.pages:
        prefixes.extend(page.prefixes)
    return [f"gs://{bucket_name}/{p}" for p in prefixes]


def blob_exists(uri: str) -> bool:
    bucket_name, name = parse_gs_uri(uri)
    return cast(bool, client().bucket(bucket_name).blob(name).exists())


def download_bytes(uri: str) -> bytes:
    bucket_name, name = parse_gs_uri(uri)
    return cast(bytes, client().bucket(bucket_name).blob(name).download_as_bytes())


def upload_file(local_path: Path, uri: str) -> None:
    bucket_name, name = parse_gs_uri(uri)
    client().bucket(bucket_name).blob(name).upload_from_filename(str(local_path))


def upload_tree(local_root: Path, uri: str) -> None:
    """Recursively upload files below ``local_root``, preserving relative paths."""
    for path in sorted(local_root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(local_root).as_posix()
            upload_file(path, uri.rstrip("/") + "/" + relative)


def download_tree(uri: str, local_root: Path) -> None:
    """Download every object below a prefix, preserving relative paths."""
    bucket_name, prefix = parse_gs_uri(uri)
    if not prefix.endswith("/"):
        prefix += "/"
    for blob in client().bucket(bucket_name).list_blobs(prefix=prefix):
        relative = blob.name.removeprefix(prefix)
        if not relative:
            continue
        target = local_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(target))


def upload_bytes(data: bytes, uri: str, *, content_type: str = "application/octet-stream") -> None:
    bucket_name, name = parse_gs_uri(uri)
    client().bucket(bucket_name).blob(name).upload_from_string(data, content_type=content_type)
