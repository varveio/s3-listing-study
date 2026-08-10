"""The derived image receives one hash-verified locked DuckDB wheel."""

from __future__ import annotations

import concurrent.futures
import hashlib
import io
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from s3_listing_study.common import duckdb_runtime


def _wheel() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("duckdb/__init__.py", "__version__ = '1.5.5'\n")
        archive.writestr("_duckdb.cpython-313-test.so", b"native-placeholder")
        archive.writestr("duckdb-1.5.5.dist-info/METADATA", "Version: 1.5.5\n")
    return output.getvalue()


def test_locked_wheel_is_hash_verified_and_extracted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = _wheel()
    digest = hashlib.sha256(wheel).hexdigest()
    monkeypatch.setitem(
        duckdb_runtime.WHEELS,
        "x86_64",
        ("https://example.invalid/duckdb.whl", digest),
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(wheel))
    tree = duckdb_runtime.ensure_runtime("x86_64", cache_root=tmp_path)
    assert (tree / "duckdb/__init__.py").is_file()
    assert (tree / "_duckdb.cpython-313-test.so").is_file()
    assert (tree / "duckdb-1.5.5.dist-info/METADATA").is_file()
    assert (tree.parent / "locked.whl").read_bytes() == wheel


def test_valid_cache_hit_is_reauthenticated_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = _wheel()
    monkeypatch.setitem(
        duckdb_runtime.WHEELS,
        "x86_64",
        ("https://example.invalid/duckdb.whl", hashlib.sha256(wheel).hexdigest()),
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(wheel))
    first = duckdb_runtime.ensure_runtime("x86_64", cache_root=tmp_path)

    def no_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a valid authenticated cache hit must not fetch")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    assert duckdb_runtime.ensure_runtime("x86_64", cache_root=tmp_path) == first


def test_concurrent_callers_publish_one_authenticated_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = _wheel()
    monkeypatch.setitem(
        duckdb_runtime.WHEELS,
        "x86_64",
        ("https://example.invalid/duckdb.whl", hashlib.sha256(wheel).hexdigest()),
    )
    fetches = 0

    def fetch(*_args: object, **_kwargs: object) -> io.BytesIO:
        nonlocal fetches
        fetches += 1
        return io.BytesIO(wheel)

    monkeypatch.setattr(urllib.request, "urlopen", fetch)
    start = threading.Barrier(2)

    def ensure() -> Path:
        start.wait()
        return duckdb_runtime.ensure_runtime("x86_64", cache_root=tmp_path)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        trees = list(pool.map(lambda _index: ensure(), range(2)))
    assert trees[0] == trees[1]
    assert fetches == 1


@pytest.mark.parametrize(
    "relative",
    ["duckdb/__init__.py", "_duckdb.cpython-313-test.so"],
)
def test_tampered_cached_payload_is_rejected(
    relative: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = _wheel()
    monkeypatch.setitem(
        duckdb_runtime.WHEELS,
        "x86_64",
        ("https://example.invalid/duckdb.whl", hashlib.sha256(wheel).hexdigest()),
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(wheel))
    tree = duckdb_runtime.ensure_runtime("x86_64", cache_root=tmp_path)
    (tree / relative).write_bytes(b"attacker-controlled")

    with pytest.raises(duckdb_runtime.DuckDBRuntimeError, match="does not match locked wheel"):
        duckdb_runtime.ensure_runtime("x86_64", cache_root=tmp_path)


def test_filename_only_fake_cache_is_not_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = tmp_path / duckdb_runtime.VERSION / "x86_64" / "site-packages"
    (tree / "duckdb").mkdir(parents=True)
    (tree / "duckdb/__init__.py").write_text("__version__ = '1.5.5'\n")
    (tree / "_duckdb.fake.so").write_bytes(b"attacker-controlled")
    metadata = tree / "duckdb-1.5.5.dist-info/METADATA"
    metadata.parent.mkdir()
    metadata.write_text("Version: 1.5.5\n")

    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", unavailable)
    with pytest.raises(duckdb_runtime.DuckDBRuntimeError, match="unauthenticated prior cache"):
        duckdb_runtime.ensure_runtime("x86_64", cache_root=tmp_path)


def test_locked_wheel_digest_mismatch_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = _wheel()
    monkeypatch.setitem(
        duckdb_runtime.WHEELS,
        "x86_64",
        ("https://example.invalid/duckdb.whl", "0" * 64),
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(wheel))
    with pytest.raises(duckdb_runtime.DuckDBRuntimeError, match="digest mismatch"):
        duckdb_runtime.ensure_runtime("x86_64", cache_root=tmp_path)


def test_wheel_without_native_extension_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("duckdb/__init__.py", "__version__ = '1.5.5'\n")
        archive.writestr("duckdb-1.5.5.dist-info/METADATA", "Version: 1.5.5\n")
    wheel = output.getvalue()
    monkeypatch.setitem(
        duckdb_runtime.WHEELS,
        "x86_64",
        ("https://example.invalid/duckdb.whl", hashlib.sha256(wheel).hexdigest()),
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(wheel))
    with pytest.raises(duckdb_runtime.DuckDBRuntimeError, match="native _duckdb module"):
        duckdb_runtime.ensure_runtime("x86_64", cache_root=tmp_path)


def test_wheel_traversal_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("../outside", b"must not escape")
        archive.writestr("duckdb/__init__.py", "__version__ = '1.5.5'\n")
        archive.writestr("_duckdb.cpython-313-test.so", b"native-placeholder")
        archive.writestr("duckdb-1.5.5.dist-info/METADATA", "Version: 1.5.5\n")
    wheel = output.getvalue()
    monkeypatch.setitem(
        duckdb_runtime.WHEELS,
        "x86_64",
        ("https://example.invalid/duckdb.whl", hashlib.sha256(wheel).hexdigest()),
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(wheel))
    with pytest.raises(duckdb_runtime.DuckDBRuntimeError, match="unsafe path"):
        duckdb_runtime.ensure_runtime("x86_64", cache_root=tmp_path)
    assert not (tmp_path / "outside").exists()


def test_only_native_campaign_architectures_have_locked_wheels() -> None:
    assert set(duckdb_runtime.WHEELS) == {"x86_64", "aarch64"}
    assert duckdb_runtime.VERSION == "1.5.5"
    assert duckdb_runtime.PYTHON_TAG == "cp313-cp313"


def test_locked_wheel_urls_and_hashes_are_recorded_in_uv_lock() -> None:
    lock = Path("uv.lock").read_text()
    for url, digest in duckdb_runtime.WHEELS.values():
        assert url in lock
        assert f"sha256:{digest}" in lock
