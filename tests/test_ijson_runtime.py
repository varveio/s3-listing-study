"""The shared image receives one hash-verified locked ijson C runtime."""

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

from s3_listing_study.common import ijson_runtime


def _wheel() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("ijson/__init__.py", "__version__ = '3.5.1'\n")
        archive.writestr("ijson/backends/yajl2_c.py", "backend = 'native'\n")
        archive.writestr("ijson/backends/_yajl2.cpython-312-test.so", b"native-placeholder")
        archive.writestr("ijson-3.5.1.dist-info/METADATA", "Version: 3.5.1\n")
    return output.getvalue()


def _select_test_wheel(monkeypatch: pytest.MonkeyPatch, wheel: bytes) -> None:
    monkeypatch.setitem(
        ijson_runtime.WHEELS,
        "x86_64",
        ("https://example.invalid/ijson.whl", hashlib.sha256(wheel).hexdigest()),
    )


def test_locked_wheel_is_hash_verified_and_extracted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = _wheel()
    _select_test_wheel(monkeypatch, wheel)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(wheel))

    tree = ijson_runtime.ensure_runtime("x86_64", cache_root=tmp_path)

    assert (tree / "ijson/__init__.py").is_file()
    assert (tree / "ijson/backends/yajl2_c.py").is_file()
    assert (tree / "ijson/backends/_yajl2.cpython-312-test.so").is_file()
    assert (tree / "ijson-3.5.1.dist-info/METADATA").is_file()
    assert (tree.parent / "locked.whl").read_bytes() == wheel


def test_valid_cache_hit_is_reauthenticated_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = _wheel()
    _select_test_wheel(monkeypatch, wheel)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(wheel))
    first = ijson_runtime.ensure_runtime("x86_64", cache_root=tmp_path)

    def no_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a valid authenticated cache hit must not fetch")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    assert ijson_runtime.ensure_runtime("x86_64", cache_root=tmp_path) == first


def test_concurrent_callers_publish_one_authenticated_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = _wheel()
    _select_test_wheel(monkeypatch, wheel)
    fetches = 0

    def fetch(*_args: object, **_kwargs: object) -> io.BytesIO:
        nonlocal fetches
        fetches += 1
        return io.BytesIO(wheel)

    monkeypatch.setattr(urllib.request, "urlopen", fetch)
    start = threading.Barrier(2)

    def ensure() -> Path:
        start.wait()
        return ijson_runtime.ensure_runtime("x86_64", cache_root=tmp_path)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        trees = list(pool.map(lambda _index: ensure(), range(2)))
    assert trees[0] == trees[1]
    assert fetches == 1


@pytest.mark.parametrize(
    "relative",
    ["ijson/__init__.py", "ijson/backends/_yajl2.cpython-312-test.so"],
)
def test_tampered_cached_payload_is_rejected(
    relative: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = _wheel()
    _select_test_wheel(monkeypatch, wheel)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(wheel))
    tree = ijson_runtime.ensure_runtime("x86_64", cache_root=tmp_path)
    (tree / relative).write_bytes(b"attacker-controlled")

    with pytest.raises(ijson_runtime.IjsonRuntimeError, match="does not match locked wheel"):
        ijson_runtime.ensure_runtime("x86_64", cache_root=tmp_path)


def test_filename_only_fake_cache_is_not_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = tmp_path / ijson_runtime.VERSION / ijson_runtime.PYTHON_TAG / "x86_64" / "site-packages"
    (tree / "ijson/backends").mkdir(parents=True)
    (tree / "ijson/__init__.py").write_text("__version__ = '3.5.1'\n")
    (tree / "ijson/backends/yajl2_c.py").write_text("backend = 'native'\n")
    (tree / "ijson/backends/_yajl2.fake.so").write_bytes(b"attacker-controlled")
    metadata = tree / "ijson-3.5.1.dist-info/METADATA"
    metadata.parent.mkdir()
    metadata.write_text("Version: 3.5.1\n")

    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", unavailable)
    with pytest.raises(ijson_runtime.IjsonRuntimeError, match="unauthenticated prior cache"):
        ijson_runtime.ensure_runtime("x86_64", cache_root=tmp_path)


def test_locked_wheel_digest_mismatch_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = _wheel()
    monkeypatch.setitem(
        ijson_runtime.WHEELS,
        "x86_64",
        ("https://example.invalid/ijson.whl", "0" * 64),
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(wheel))
    with pytest.raises(ijson_runtime.IjsonRuntimeError, match="digest mismatch"):
        ijson_runtime.ensure_runtime("x86_64", cache_root=tmp_path)


def test_wheel_without_native_extension_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("ijson/__init__.py", "__version__ = '3.5.1'\n")
        archive.writestr("ijson/backends/yajl2_c.py", "backend = 'python'\n")
        archive.writestr("ijson-3.5.1.dist-info/METADATA", "Version: 3.5.1\n")
    wheel = output.getvalue()
    _select_test_wheel(monkeypatch, wheel)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(wheel))
    with pytest.raises(ijson_runtime.IjsonRuntimeError, match="native _yajl2 module"):
        ijson_runtime.ensure_runtime("x86_64", cache_root=tmp_path)


def test_wheel_traversal_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("../outside", b"must not escape")
        archive.writestr("ijson/__init__.py", "__version__ = '3.5.1'\n")
        archive.writestr("ijson/backends/yajl2_c.py", "backend = 'native'\n")
        archive.writestr("ijson/backends/_yajl2.cpython-312-test.so", b"native-placeholder")
        archive.writestr("ijson-3.5.1.dist-info/METADATA", "Version: 3.5.1\n")
    wheel = output.getvalue()
    _select_test_wheel(monkeypatch, wheel)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(wheel))
    with pytest.raises(ijson_runtime.IjsonRuntimeError, match="unsafe path"):
        ijson_runtime.ensure_runtime("x86_64", cache_root=tmp_path)
    assert not (tmp_path / "outside").exists()


def test_only_native_campaign_architectures_have_locked_wheels() -> None:
    assert set(ijson_runtime.WHEELS) == {"x86_64", "aarch64"}
    assert ijson_runtime.VERSION == "3.5.1"
    assert ijson_runtime.PYTHON_TAG == "cp312-cp312"
    assert ijson_runtime.BACKEND == "yajl2_c"


def test_locked_wheel_urls_and_hashes_are_recorded_in_uv_lock() -> None:
    lock = Path("uv.lock").read_text()
    for url, digest in ijson_runtime.WHEELS.values():
        assert url in lock
        assert f"sha256:{digest}" in lock
