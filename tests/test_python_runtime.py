"""The shared image receives one hash-verified, versioned Python runtime."""

from __future__ import annotations

import concurrent.futures
import hashlib
import io
import tarfile
import threading
import urllib.request
from pathlib import Path

import pytest

from s3_listing_study.common import python_runtime


def _archive() -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        executable = b"placeholder"
        member = tarfile.TarInfo("python/bin/python3")
        member.mode = 0o755
        member.size = len(executable)
        archive.addfile(member, io.BytesIO(executable))
        link = tarfile.TarInfo("python/bin/python")
        link.type = tarfile.SYMTYPE
        link.linkname = "python3"
        archive.addfile(link)
    return payload.getvalue()


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, archive: bytes) -> Path:
    monkeypatch.setitem(
        python_runtime.ARCHIVES,
        ("x86_64", "gnu"),
        hashlib.sha256(archive).hexdigest(),
    )
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(archive),
    )
    return python_runtime.ensure_runtime("x86_64", "gnu", cache_root=tmp_path)


def test_cache_is_namespaced_by_release_and_python_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _archive()
    stale = tmp_path / python_runtime.RELEASE / "x86_64-gnu" / "python/bin/python3"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"wrong-version")

    tree = _install(tmp_path, monkeypatch, archive)

    assert tree == (
        tmp_path / python_runtime.RELEASE / python_runtime.VERSION / "x86_64-gnu" / "python"
    )
    assert (tree / "bin/python3").read_bytes() == b"placeholder"
    assert (tree.parent / "locked.tar.gz").read_bytes() == archive


def test_valid_cache_hit_is_reauthenticated_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _install(tmp_path, monkeypatch, _archive())

    def no_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("an authenticated cache hit must not fetch")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    assert python_runtime.ensure_runtime("x86_64", "gnu", cache_root=tmp_path) == tree


def test_old_tree_only_cache_is_replaced_after_authenticated_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = tmp_path / python_runtime.RELEASE / python_runtime.VERSION / "x86_64-gnu" / "python"
    executable = tree / "bin/python3"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"unauthenticated")

    installed = _install(tmp_path, monkeypatch, _archive())

    assert installed == tree
    assert executable.read_bytes() == b"placeholder"
    assert (tree.parent / "locked.tar.gz").is_file()


def test_retained_but_corrupt_locked_archive_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _install(tmp_path, monkeypatch, _archive())
    (tree.parent / "locked.tar.gz").write_bytes(b"corrupt")

    def no_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a retained corrupt archive must not be replaced")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    with pytest.raises(python_runtime.PythonRuntimeError, match="digest mismatch"):
        python_runtime.ensure_runtime("x86_64", "gnu", cache_root=tmp_path)


def test_tampered_cached_content_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _install(tmp_path, monkeypatch, _archive())
    (tree / "bin/python3").write_bytes(b"tampered")

    with pytest.raises(python_runtime.PythonRuntimeError, match="does not match locked archive"):
        python_runtime.ensure_runtime("x86_64", "gnu", cache_root=tmp_path)


def test_tampered_cached_link_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tree = _install(tmp_path, monkeypatch, _archive())
    link = tree / "bin/python"
    link.unlink()
    link.symlink_to("elsewhere")

    with pytest.raises(python_runtime.PythonRuntimeError, match="link does not match"):
        python_runtime.ensure_runtime("x86_64", "gnu", cache_root=tmp_path)


def test_concurrent_callers_publish_one_authenticated_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _archive()
    monkeypatch.setitem(
        python_runtime.ARCHIVES,
        ("x86_64", "gnu"),
        hashlib.sha256(archive).hexdigest(),
    )
    fetches = 0

    def fetch(*_args: object, **_kwargs: object) -> io.BytesIO:
        nonlocal fetches
        fetches += 1
        return io.BytesIO(archive)

    monkeypatch.setattr(urllib.request, "urlopen", fetch)
    start = threading.Barrier(2)

    def ensure() -> Path:
        start.wait()
        return python_runtime.ensure_runtime("x86_64", "gnu", cache_root=tmp_path)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        trees = list(pool.map(lambda _index: ensure(), range(2)))
    assert trees[0] == trees[1]
    assert fetches == 1
