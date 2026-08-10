"""Fetch and stage the exact ijson wheel locked for the shared-image runtime."""

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import stat
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import IO, Final

VERSION: Final = "3.5.1"
PYTHON_TAG: Final = "cp312-cp312"
BACKEND: Final = "yajl2_c"
FETCH_TIMEOUT_S: Final = 120.0

# Recorded verbatim from uv.lock's ijson 3.5.1 wheel metadata. Shared images
# are GNU/glibc only; no source build or package-manager resolution is allowed
# inside Docker.
WHEELS: Final[dict[str, tuple[str, str]]] = {
    "aarch64": (
        "https://files.pythonhosted.org/packages/41/fb/f9c1664d75467453e6bd4e5f9cd2211b730b09e049445ab64cbac68cc6a3/ijson-3.5.1-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl",
        "350caea815e53151994b597abc80cf669454276b5ac6aadcec69ef6d48f7e90b",
    ),
    "x86_64": (
        "https://files.pythonhosted.org/packages/43/80/d20b1c49c4aa7cc6644131e2e57192b45346ef4816566ed1cd9fd05bae38/ijson-3.5.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl",
        "e4fcebfe1685bb7ba06a8255a5d428ea6b4b895d7acf979cb637d8bbc9db2f47",
    ),
}


class IjsonRuntimeError(RuntimeError):
    """The locked ijson payload could not be verified or staged."""


def default_cache_root() -> Path:
    return Path.home() / ".cache" / "s3-listing-study" / "ijson"


def _verify(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise IjsonRuntimeError(
            f"locked ijson wheel digest mismatch: expected {expected}, got {actual}"
        )


def _validated_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    for member in archive.infolist():
        name = PurePosixPath(member.filename)
        canonical = name.as_posix()
        mode = member.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if (
            not canonical
            or name.is_absolute()
            or ".." in name.parts
            or member.filename.rstrip("/") != canonical
        ):
            raise IjsonRuntimeError(f"locked ijson wheel contains unsafe path: {name}")
        if file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise IjsonRuntimeError(f"locked ijson wheel contains non-file entry: {name}")
        if not member.is_dir():
            if canonical in members:
                raise IjsonRuntimeError(f"locked ijson wheel contains duplicate path: {canonical}")
            members[canonical] = member
    return members


def _extract(wheel: Path, destination: Path) -> None:
    """Extract a verified wheel after rejecting traversal and non-files."""
    try:
        with zipfile.ZipFile(wheel) as archive:
            _validated_members(archive)
            for member in archive.infolist():
                relative = PurePosixPath(member.filename)
                target = destination.joinpath(*relative.parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with archive.open(member) as source, target.open("xb") as sink:
                        shutil.copyfileobj(source, sink)
                except FileExistsError as exc:
                    raise IjsonRuntimeError(
                        f"locked ijson wheel contains duplicate path: {relative}"
                    ) from exc
                permissions = (member.external_attr >> 16) & 0o777
                if permissions:
                    target.chmod(permissions)
    except zipfile.BadZipFile as exc:
        raise IjsonRuntimeError("locked ijson wheel is not a valid wheel archive") from exc


def _complete(tree: Path) -> bool:
    return (
        (tree / "ijson" / "__init__.py").is_file()
        and (tree / "ijson" / "backends" / "yajl2_c.py").is_file()
        and (tree / f"ijson-{VERSION}.dist-info" / "METADATA").is_file()
        and any((tree / "ijson" / "backends").glob("_yajl2*.so"))
    )


def _tree_files(tree: Path) -> dict[str, Path]:
    if not tree.is_dir() or tree.is_symlink():
        raise IjsonRuntimeError("cached ijson site-packages is not a directory")
    files: dict[str, Path] = {}
    pending = [tree]
    while pending:
        directory = pending.pop()
        for child in directory.iterdir():
            relative = child.relative_to(tree).as_posix()
            if child.is_symlink():
                raise IjsonRuntimeError(f"cached ijson payload contains symlink: {relative}")
            if child.is_dir():
                pending.append(child)
            elif child.is_file():
                files[relative] = child
            else:
                raise IjsonRuntimeError(f"cached ijson payload contains non-file entry: {relative}")
    return files


def _digest(source: IO[bytes]) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _verify_extraction(wheel: Path, tree: Path) -> None:
    """Compare every extracted byte with the authenticated wheel payload."""
    try:
        with zipfile.ZipFile(wheel) as archive:
            members = _validated_members(archive)
            files = _tree_files(tree)
            if set(files) != set(members):
                missing = sorted(set(members) - set(files))
                unexpected = sorted(set(files) - set(members))
                raise IjsonRuntimeError(
                    "cached ijson payload does not match locked wheel "
                    f"(missing={missing}, unexpected={unexpected})"
                )
            for name, member in members.items():
                with archive.open(member) as expected, files[name].open("rb") as actual:
                    if _digest(expected) != _digest(actual):
                        raise IjsonRuntimeError(
                            f"cached ijson payload does not match locked wheel: {name}"
                        )
    except zipfile.BadZipFile as exc:
        raise IjsonRuntimeError("cached ijson wheel is not a valid wheel archive") from exc


def _authenticate(install: Path, expected: str) -> Path:
    wheel = install / "locked.whl"
    tree = install / "site-packages"
    if not wheel.is_file() or wheel.is_symlink():
        raise IjsonRuntimeError("cached ijson payload has no authenticated locked wheel")
    _verify(wheel, expected)
    _verify_extraction(wheel, tree)
    if not _complete(tree):
        raise IjsonRuntimeError(
            "locked ijson wheel is missing package metadata or its native _yajl2 module"
        )
    return tree


def ensure_runtime(architecture: str, *, cache_root: Path | None = None) -> Path:
    """Return extracted site-packages for the locked native CPython 3.12 wheel."""
    try:
        url, expected = WHEELS[architecture]
    except KeyError:
        raise IjsonRuntimeError(
            f"no locked ijson {VERSION} wheel for native architecture {architecture!r}"
        ) from None
    root = default_cache_root() if cache_root is None else cache_root
    version_root = root / VERSION / PYTHON_TAG
    version_root.mkdir(parents=True, exist_ok=True)
    install = version_root / architecture
    lock_path = version_root / f".{architecture}.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if install.exists():
            try:
                return _authenticate(install, expected)
            except IjsonRuntimeError as exc:
                if (install / "locked.whl").exists():
                    raise
                unauthenticated = exc
        else:
            unauthenticated = None
        with tempfile.TemporaryDirectory(
            prefix=".ijson-runtime-", dir=version_root
        ) as scratch_name:
            scratch = Path(scratch_name)
            candidate = scratch / "install"
            candidate.mkdir()
            wheel = candidate / "locked.whl"
            try:
                with (
                    urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as response,
                    wheel.open("xb") as sink,
                ):
                    shutil.copyfileobj(response, sink)
            except (urllib.error.URLError, OSError) as exc:
                detail = (
                    f"; unauthenticated prior cache: {unauthenticated}" if unauthenticated else ""
                )
                raise IjsonRuntimeError(
                    f"cannot fetch locked ijson wheel {url}: {exc}{detail}"
                ) from exc
            _verify(wheel, expected)
            extracted = candidate / "site-packages"
            extracted.mkdir()
            _extract(wheel, extracted)
            _authenticate(candidate, expected)

            stale = scratch / "stale"
            if os.path.lexists(install):
                install.replace(stale)
            try:
                candidate.replace(install)
            except OSError as exc:
                if stale.exists():
                    stale.replace(install)
                raise IjsonRuntimeError(
                    f"cannot install locked ijson payload at {install}: {exc}"
                ) from exc
        return _authenticate(install, expected)
