"""Provision the one interpreter every derived image runs the attempt engine on.

The attempt engine runs *inside* the subject image, so it needs a Python there.
Only one of the eleven subjects ships one: ``aws-cli`` is itself written in
Python. Swath's image is a Temurin JRE, s5cmd's and rclone's are Go binaries on
minimal bases, and none of them has ``/usr/bin/python3``. Depending on the
subject's own interpreter therefore does not merely fail for ten tools — it
would also make the interpreter one more uncontrolled difference across the
comparison this repository exists to make fair.

So the interpreter is an input the study pins, not something a subject supplies:
one python-build-standalone build, the same version for every subject, verified
by digest on the host and bound into the build as the ``python`` named context.
Nothing runs a package manager inside a subject image, no build step needs
network beyond this fetch, and the subject's own package set is untouched.

The archive is fetched once into a user cache and reused. It is verified against
the digest recorded below BEFORE it is extracted, because an unverified archive
that has already been unpacked has already run its content through ``tarfile``.
"""

from __future__ import annotations

import hashlib
import platform
import shutil
import sysconfig
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Final

RELEASE: Final = "20260807"
"""The pinned python-build-standalone release tag."""

VERSION: Final = "3.13.15"
"""The pinned CPython version inside that release."""

_BASE_URL: Final = "https://github.com/astral-sh/python-build-standalone/releases/download"

ARCHIVES: Final[dict[tuple[str, str], str]] = {
    ("aarch64", "gnu"): "68159637492dcf3501c0967b69e07665c3dda070fb42231737055f72963f66fc",
    ("aarch64", "musl"): "38a9cf47dba720794f5f60b974bd3d3e9f9e42801b0b0536a131c6357aecb3c6",
    ("x86_64", "gnu"): "7253808c3413d9ebd03e76b3853c895b9287f12e0750a30fce1cbf430e516113",
    ("x86_64", "musl"): "2af970fab79c436cb3292f7914bc2812e83fcb4152031e0a27c51660bae6d9d7",
}
"""``(architecture, libc)`` to the SHA-256 of its ``install_only`` archive.

Recorded from the release's own ``SHA256SUMS``. A subject whose base is Alpine
needs the ``musl`` build; everything else takes ``gnu``. The pair is declared per
capsule rather than sniffed, because guessing wrong produces an interpreter that
loads on the build host and dies inside the subject.
"""

LIBC_VALUES: Final = ("gnu", "musl")

FETCH_TIMEOUT_S: Final = 120.0
"""Per-socket-operation deadline on the archive fetch, so a build cannot hang."""

INSTALL_PREFIX: Final = "/opt/s3-listing-study/python"
"""Where the provisioned tree lands inside a derived image."""

INTERPRETER: Final = f"{INSTALL_PREFIX}/bin/python3"
"""The absolute interpreter path the shared derived-image recipe invokes."""


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
    with tarfile.open(archive, "r:gz") as bundle:
        # ``data`` refuses absolute paths, traversal, links out of the tree, and
        # device nodes. The archive is already digest-verified; this is the
        # second gate, not the first.
        bundle.extractall(destination, filter="data")


def ensure_runtime(
    architecture: str,
    libc: str,
    *,
    cache_root: Path | None = None,
) -> Path:
    """Return the extracted interpreter tree for one platform, fetching if absent.

    The returned directory is what the build binds as the ``python`` context: it
    holds ``bin/``, ``lib/`` and the rest, and is copied to
    :data:`INSTALL_PREFIX` inside the derived image.
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
    tree = root / RELEASE / f"{architecture}-{libc}" / "python"
    if (tree / "bin" / "python3").exists():
        return tree

    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".python-runtime-", dir=root) as scratch_name:
        scratch = Path(scratch_name)
        archive = scratch / name
        url = f"{_BASE_URL}/{RELEASE}/{name}"
        try:
            with (
                urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as response,
                archive.open("wb") as sink,
            ):
                shutil.copyfileobj(response, sink)
        except (urllib.error.URLError, OSError) as exc:
            raise PythonRuntimeError(f"cannot fetch pinned interpreter {url}: {exc}") from exc
        _verify(archive, expected)
        _extract(archive, scratch)
        extracted = scratch / "python"
        if not (extracted / "bin" / "python3").exists():
            raise PythonRuntimeError(
                f"pinned interpreter archive has no python/bin/python3: {name}"
            )
        tree.parent.mkdir(parents=True, exist_ok=True)
        try:
            extracted.replace(tree)
        except OSError as exc:
            # A concurrent build can win the race and leave a populated
            # directory here, which rename refuses with ENOTEMPTY. Its tree came
            # from the same digest-verified archive, so it is the same tree.
            if (tree / "bin" / "python3").exists():
                return tree
            raise PythonRuntimeError(f"cannot install interpreter tree at {tree}: {exc}") from exc
    return tree
