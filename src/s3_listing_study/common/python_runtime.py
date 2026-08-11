"""Provision the pinned interpreter staged into the stable shared base.

The shared base supplies one controlled Python runtime for the attempt engine
used by every final per-tool image. The interpreter is a pinned study input,
not a property inherited from a tool artifact: one python-build-standalone
build, verified by digest on the host and bound into the shared-base build as
the ``python`` named context. No package manager runs while assembling a final
per-tool image, and the tool artifact remains untouched.

The archive is fetched once into a user cache and reused. It is verified against
the digest recorded below BEFORE it is extracted, because an unverified archive
that has already been unpacked has already run its content through ``tarfile``.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import platform
import shutil
import stat
import sysconfig
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import IO, Final

RELEASE: Final = "20260807"
"""The pinned python-build-standalone release tag."""

VERSION: Final = "3.12.13"
"""The pinned CPython version inside that release."""

_BASE_URL: Final = "https://github.com/astral-sh/python-build-standalone/releases/download"

ARCHIVES: Final[dict[tuple[str, str], str]] = {
    ("aarch64", "gnu"): "e2a33a26bae0f0975a9786c2e3beaee9cfeb35f856bdd273ff10ae35cf7e06ce",
    ("aarch64", "musl"): "3dc546520b90bc1852cc6494212c1ca3af307e533ce764b43733027311004d55",
    ("x86_64", "gnu"): "5bd6f36fd7ef02b909234c94dca9994ef0da06ace3bc3cece4fe27870e9cdbbe",
    ("x86_64", "musl"): "b4359517553d83126658c5df5a11e611c0c3129505fe57755755a103b8c3c7c4",
}
"""``(architecture, libc)`` to the SHA-256 of its ``install_only`` archive.

Recorded from the release's own ``SHA256SUMS``. The stable shared base currently
selects ``gnu``; ``musl`` remains supported for a future shared-base variant.
Callers select the pair explicitly rather than sniffing it, because choosing the
wrong archive produces an interpreter that cannot run in its target base.
"""

LIBC_VALUES: Final = ("gnu", "musl")

FETCH_TIMEOUT_S: Final = 120.0
"""Per-socket-operation deadline on the archive fetch, so a build cannot hang."""

INSTALL_PREFIX: Final = "/opt/s3-listing-study/python"
"""Where the provisioned tree lands inside the stable shared base."""

INTERPRETER: Final = f"{INSTALL_PREFIX}/bin/python3"
"""The absolute interpreter path the shared-base recipe provides."""


class PythonRuntimeError(RuntimeError):
    """The pinned interpreter could not be provisioned for this build."""


def archive_name(architecture: str, libc: str) -> str:
    """Return the ``install_only`` archive filename for one platform pair."""
    return f"cpython-{VERSION}+{RELEASE}-{architecture}-unknown-linux-{libc}-install_only.tar.gz"


def running_libc() -> str | None:
    """The libc the interpreter executing this call was built against.

    ``HOST_GNU_TYPE`` is the configure triple of that build, which spells the
    libc as its last component — ``…-linux-musl`` or ``…-linux-gnu``. ``None``
    means the triple did not name one, which callers that gate on it must treat
    as a refusal rather than a match.
    """
    triple = sysconfig.get_config_var("HOST_GNU_TYPE")
    if not isinstance(triple, str):
        return None
    for libc in LIBC_VALUES:
        if libc in triple:
            return libc
    return None


def interpreter_identity() -> dict[str, str | None]:
    """The pinned interpreter, as facts a run record can be audited against.

    The pin is a study input like any other, so a record carries it rather than
    leaving it implicit in a code constant. ``running_version`` is read from the
    live interpreter, so a record made on something other than the pin says so.
    """
    architecture = platform.machine()
    libc = running_libc()
    return {
        "release": RELEASE,
        "version": VERSION,
        "running_version": platform.python_version(),
        "architecture": architecture,
        "libc": libc,
        "archive_sha256": ARCHIVES.get((architecture, libc)) if libc else None,
    }


def default_cache_root() -> Path:
    """The per-user cache the fetched interpreter trees are reused from."""
    return Path.home() / ".cache" / "s3-listing-study" / "python"


def _verify(archive: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with archive.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise PythonRuntimeError(
            f"pinned interpreter digest mismatch for {archive.name}: "
            f"expected {expected}, got {actual}"
        )


def _extract(archive: Path, destination: Path) -> None:
    """Unpack the verified archive, which contains a single ``python/`` tree."""
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            # ``data`` refuses absolute paths, traversal, links out of the tree,
            # and device nodes. The archive is already digest-verified; this is
            # the second gate, not the first.
            bundle.extractall(destination, filter="data")
    except tarfile.TarError as exc:
        raise PythonRuntimeError("pinned interpreter archive is not a valid tar archive") from exc


def _digest(source: IO[bytes]) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _member_name(member: tarfile.TarInfo) -> str:
    path = PurePosixPath(member.name)
    canonical = path.as_posix()
    if (
        not canonical
        or path.is_absolute()
        or ".." in path.parts
        or member.name.rstrip("/") != canonical
        or not path.parts
        or path.parts[0] != "python"
    ):
        raise PythonRuntimeError(f"pinned interpreter archive contains unsafe path: {member.name}")
    relative = PurePosixPath(*path.parts[1:]).as_posix()
    return "" if relative == "." else relative


def _archive_entries(
    bundle: tarfile.TarFile,
) -> tuple[dict[str, tarfile.TarInfo], dict[str, tarfile.TarInfo], set[str]]:
    """Return authenticated file, symlink, and directory entries."""
    files: dict[str, tarfile.TarInfo] = {}
    links: dict[str, tarfile.TarInfo] = {}
    directories: set[str] = set()
    seen: set[str] = set()
    for member in bundle.getmembers():
        relative = _member_name(member)
        if relative in seen:
            raise PythonRuntimeError(
                f"pinned interpreter archive contains duplicate path: {member.name}"
            )
        seen.add(relative)
        if relative:
            parent = PurePosixPath(relative).parent
            while parent.as_posix() != ".":
                directories.add(parent.as_posix())
                parent = parent.parent
        if member.isdir():
            if relative:
                directories.add(relative)
        elif member.isreg() or member.islnk():
            if not relative:
                raise PythonRuntimeError("pinned interpreter archive has a non-directory root")
            files[relative] = member
        elif member.issym():
            if not relative:
                raise PythonRuntimeError("pinned interpreter archive has a symlink root")
            links[relative] = member
        else:
            raise PythonRuntimeError(
                f"pinned interpreter archive contains non-file entry: {member.name}"
            )
    return files, links, directories


def _tree_entries(tree: Path) -> tuple[dict[str, Path], dict[str, Path], set[str]]:
    if not tree.is_dir() or tree.is_symlink():
        raise PythonRuntimeError("cached interpreter tree is not a directory")
    files: dict[str, Path] = {}
    links: dict[str, Path] = {}
    directories: set[str] = set()
    pending = [tree]
    while pending:
        directory = pending.pop()
        for child in directory.iterdir():
            relative = child.relative_to(tree).as_posix()
            mode = child.lstat().st_mode
            if stat.S_ISLNK(mode):
                links[relative] = child
            elif stat.S_ISDIR(mode):
                directories.add(relative)
                pending.append(child)
            elif stat.S_ISREG(mode):
                files[relative] = child
            else:
                raise PythonRuntimeError(
                    f"cached interpreter tree contains non-file entry: {relative}"
                )
    return files, links, directories


def _verify_extraction(archive: Path, tree: Path) -> None:
    """Compare every cached file and symlink with the authenticated archive."""
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            expected_files, expected_links, expected_directories = _archive_entries(bundle)
            files, links, directories = _tree_entries(tree)
            if (
                set(files) != set(expected_files)
                or set(links) != set(expected_links)
                or directories != expected_directories
            ):
                raise PythonRuntimeError(
                    "cached interpreter tree does not match locked archive entry set"
                )
            for name, member in expected_files.items():
                expected = bundle.extractfile(member)
                if expected is None:
                    raise PythonRuntimeError(
                        f"pinned interpreter archive cannot read file entry: {name}"
                    )
                with expected, files[name].open("rb") as actual:
                    if _digest(expected) != _digest(actual):
                        raise PythonRuntimeError(
                            f"cached interpreter tree does not match locked archive: {name}"
                        )
            for name, member in expected_links.items():
                if os.readlink(links[name]) != member.linkname:
                    raise PythonRuntimeError(
                        f"cached interpreter link does not match locked archive: {name}"
                    )
    except tarfile.TarError as exc:
        raise PythonRuntimeError("pinned interpreter archive is not a valid tar archive") from exc


def _authenticate(install: Path, expected: str) -> Path:
    archive = install / "locked.tar.gz"
    tree = install / "python"
    if not archive.is_file() or archive.is_symlink():
        raise PythonRuntimeError("cached interpreter has no authenticated locked archive")
    _verify(archive, expected)
    _verify_extraction(archive, tree)
    if not (tree / "bin" / "python3").is_file():
        raise PythonRuntimeError("locked interpreter archive has no python/bin/python3")
    return tree


def ensure_runtime(
    architecture: str,
    libc: str,
    *,
    cache_root: Path | None = None,
) -> Path:
    """Return the extracted interpreter tree for one platform, fetching if absent.

    The returned directory is what the shared-base build binds as the ``python``
    context: it holds ``bin/``, ``lib/`` and the rest, and is copied to
    :data:`INSTALL_PREFIX` inside the stable shared base.
    """
    if libc not in LIBC_VALUES:
        raise PythonRuntimeError(f"unsupported libc: {libc!r}; expected one of {LIBC_VALUES}")
    try:
        expected = ARCHIVES[(architecture, libc)]
    except KeyError:
        raise PythonRuntimeError(
            f"no pinned interpreter for {architecture}-{libc}; "
            f"add its digest to {__name__}.ARCHIVES"
        ) from None

    root = default_cache_root() if cache_root is None else cache_root
    name = archive_name(architecture, libc)
    # One python-build-standalone release can publish several CPython versions.
    # Namespace by both values so a version change cannot silently reuse the
    # authenticated tree for a different interpreter from the same release.
    version_root = root / RELEASE / VERSION
    version_root.mkdir(parents=True, exist_ok=True)
    install = version_root / f"{architecture}-{libc}"
    lock_path = version_root / f".{architecture}-{libc}.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if os.path.lexists(install):
            try:
                return _authenticate(install, expected)
            except PythonRuntimeError as exc:
                locked = install / "locked.tar.gz"
                # Any cache with a retained archive is expected to be the new,
                # authenticated shape. Corruption must fail closed rather than
                # being hidden by a network refetch. The former tree-only shape
                # is replaceable, but only after a successful authenticated fetch.
                if install.is_dir() and not install.is_symlink() and os.path.lexists(locked):
                    raise
                unauthenticated = exc
        else:
            unauthenticated = None

        with tempfile.TemporaryDirectory(
            prefix=".python-runtime-", dir=version_root
        ) as scratch_name:
            scratch = Path(scratch_name)
            candidate = scratch / "install"
            candidate.mkdir()
            archive = candidate / "locked.tar.gz"
            url = f"{_BASE_URL}/{RELEASE}/{name}"
            try:
                with (
                    urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as response,
                    archive.open("xb") as sink,
                ):
                    shutil.copyfileobj(response, sink)
            except (urllib.error.URLError, OSError) as exc:
                detail = (
                    f"; unauthenticated prior cache: {unauthenticated}" if unauthenticated else ""
                )
                raise PythonRuntimeError(
                    f"cannot fetch pinned interpreter {url}: {exc}{detail}"
                ) from exc
            _verify(archive, expected)
            _extract(archive, candidate)
            _authenticate(candidate, expected)

            stale = scratch / "stale"
            if os.path.lexists(install):
                install.replace(stale)
            try:
                candidate.replace(install)
            except OSError as exc:
                if os.path.lexists(stale):
                    stale.replace(install)
                raise PythonRuntimeError(
                    f"cannot install interpreter cache at {install}: {exc}"
                ) from exc
        return _authenticate(install, expected)
