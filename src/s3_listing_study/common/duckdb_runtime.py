"""Fetch and stage the exact DuckDB wheel locked for the derived-image runtime."""

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

VERSION: Final = "1.5.5"
PYTHON_TAG: Final = "cp312-cp312"
FETCH_TIMEOUT_S: Final = 120.0

# Recorded verbatim from uv.lock's duckdb 1.5.5 wheel metadata.  Derived
# images are GNU/glibc only; no source build or package-manager resolution is
# allowed inside Docker.
WHEELS: Final[dict[str, tuple[str, str]]] = {
    "aarch64": (
        "https://files.pythonhosted.org/packages/ea/a9/5f1f09da421d8e930e0b063d11c1b3f90363f40ede74438cd188afdd13a2/duckdb-1.5.5-cp312-cp312-manylinux_2_26_aarch64.manylinux_2_28_aarch64.whl",
        "f316eae2323d9a851883fdf2dee91c1f9efe251ab33e14a2272f82a913422ed6",
    ),
    "x86_64": (
        "https://files.pythonhosted.org/packages/4f/98/6549769f158126fa64fd6c1ac2eb59a18282146c939867a3eb31b7c1db07/duckdb-1.5.5-cp312-cp312-manylinux_2_26_x86_64.manylinux_2_28_x86_64.whl",
        "7a6d2d11859d82a936ebdcb30ce3d8a1cbb3e990bff05c12abb9b54c44fa7bd1",
    ),
}


class DuckDBRuntimeError(RuntimeError):
    """The locked DuckDB payload could not be verified or staged."""


def default_cache_root() -> Path:
    return Path.home() / ".cache" / "s3-listing-study" / "duckdb"


def _verify(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise DuckDBRuntimeError(
            f"locked DuckDB wheel digest mismatch: expected {expected}, got {actual}"
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
            raise DuckDBRuntimeError(f"locked DuckDB wheel contains unsafe path: {name}")
        if file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise DuckDBRuntimeError(f"locked DuckDB wheel contains non-file entry: {name}")
        if not member.is_dir():
            if canonical in members:
                raise DuckDBRuntimeError(
                    f"locked DuckDB wheel contains duplicate path: {canonical}"
                )
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
                    raise DuckDBRuntimeError(
                        f"locked DuckDB wheel contains duplicate path: {relative}"
                    ) from exc
                permissions = (member.external_attr >> 16) & 0o777
                if permissions:
                    target.chmod(permissions)
    except zipfile.BadZipFile as exc:
        raise DuckDBRuntimeError("locked DuckDB wheel is not a valid wheel archive") from exc


def _complete(tree: Path) -> bool:
    return (
        (tree / "duckdb" / "__init__.py").is_file()
        and (tree / f"duckdb-{VERSION}.dist-info" / "METADATA").is_file()
        and any(tree.glob("_duckdb*.so"))
    )


def _tree_files(tree: Path) -> dict[str, Path]:
    if not tree.is_dir() or tree.is_symlink():
        raise DuckDBRuntimeError("cached DuckDB site-packages is not a directory")
    files: dict[str, Path] = {}
    pending = [tree]
    while pending:
        directory = pending.pop()
        for child in directory.iterdir():
            relative = child.relative_to(tree).as_posix()
            if child.is_symlink():
                raise DuckDBRuntimeError(f"cached DuckDB payload contains symlink: {relative}")
            if child.is_dir():
                pending.append(child)
            elif child.is_file():
                files[relative] = child
            else:
                raise DuckDBRuntimeError(
                    f"cached DuckDB payload contains non-file entry: {relative}"
                )
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
                raise DuckDBRuntimeError(
                    "cached DuckDB payload does not match locked wheel "
                    f"(missing={missing}, unexpected={unexpected})"
                )
            for name, member in members.items():
                with archive.open(member) as expected, files[name].open("rb") as actual:
                    if _digest(expected) != _digest(actual):
                        raise DuckDBRuntimeError(
                            f"cached DuckDB payload does not match locked wheel: {name}"
                        )
    except zipfile.BadZipFile as exc:
        raise DuckDBRuntimeError("cached DuckDB wheel is not a valid wheel archive") from exc


def _authenticate(install: Path, expected: str) -> Path:
    wheel = install / "locked.whl"
    tree = install / "site-packages"
    if not wheel.is_file() or wheel.is_symlink():
        raise DuckDBRuntimeError("cached DuckDB payload has no authenticated locked wheel")
    _verify(wheel, expected)
    _verify_extraction(wheel, tree)
    if not _complete(tree):
        raise DuckDBRuntimeError(
            "locked DuckDB wheel is missing package metadata or its native _duckdb module"
        )
    return tree


def ensure_runtime(architecture: str, *, cache_root: Path | None = None) -> Path:
    """Return extracted site-packages for the locked native CPython 3.12 wheel."""
    try:
        url, expected = WHEELS[architecture]
    except KeyError:
        raise DuckDBRuntimeError(
            f"no locked DuckDB {VERSION} wheel for native architecture {architecture!r}"
        ) from None
    root = default_cache_root() if cache_root is None else cache_root
    # A DuckDB release has distinct native wheels for each CPython ABI. Keep
    # those payloads in distinct cache namespaces so changing the one shared
    # interpreter cannot collide with an authenticated wheel for the old ABI.
    version_root = root / VERSION / PYTHON_TAG
    version_root.mkdir(parents=True, exist_ok=True)
    install = version_root / architecture
    lock_path = version_root / f".{architecture}.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if install.exists():
            try:
                return _authenticate(install, expected)
            except DuckDBRuntimeError as exc:
                # The former cache shape did not retain its wheel. It is not an
                # authenticated hit, but can be replaced from the locked URL.
                if (install / "locked.whl").exists():
                    raise
                unauthenticated = exc
        else:
            unauthenticated = None
        with tempfile.TemporaryDirectory(
            prefix=".duckdb-runtime-", dir=version_root
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
                raise DuckDBRuntimeError(
                    f"cannot fetch locked DuckDB wheel {url}: {exc}{detail}"
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
                raise DuckDBRuntimeError(
                    f"cannot install locked DuckDB payload at {install}: {exc}"
                ) from exc
        return _authenticate(install, expected)
