"""The only module here that talks to a registry.

Four copies of an ``inspect_remote`` shell function used to decide, in four
slightly different surroundings, the single most consequential question in the
pipeline: *is it safe to publish this tag fresh?* That predicate now has one
implementation and a test suite.

Two properties it must have, both of which the shell had and neither of which is
obvious:

**Absence and failure are different.** A tag that is genuinely absent means
"build and publish it". An authentication failure, a rate limit, or a network
error means "stop" — degrading either into a cache miss would republish over a
tag that already exists, or silently rebuild an image that was already canonical.
Only an explicit not-found response is absence; everything else raises.

**Reads never pull layers.** ``imagetools inspect`` resolves manifests and image
configs over the registry API, so probing twenty-three references costs a few
hundred milliseconds and no disk. Verification reads labels from the config for
the same reason: a job that never materialises a subject tool's filesystem
cannot accidentally execute one.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Final

from s3_listing_study.ci import CIError
from s3_listing_study.ci.tags import DIGEST_RE

CLIENT_FAILURE_PATTERN: Final = re.compile(
    r"unauthorized|denied|forbidden|failed to authorize|credential|"
    r"executable file not found|command not found|no such host|"
    r"connection refused|connection reset|i/o timeout|timeout|"
    r"rate limit|toomanyrequests|no match for platform",
    re.IGNORECASE,
)
"""Failures that are about *us*, not about the reference being absent.

Checked first and unconditionally fatal. A broken credential helper reports
``executable file not found in $PATH``, and a repository the token cannot see
reports ``403 Forbidden`` from the token endpoint — both contain wording that a
naive absence test reads as "this tag does not exist", which would republish
over a canonical tag from an unverified build.
"""

ABSENT_TOKEN_PATTERN: Final = re.compile(
    r"manifest unknown|manifest_unknown|name unknown|name_unknown|"
    r"no such manifest|\bnot found\b|\b404\b",
    re.IGNORECASE,
)
"""Registry wording for "this reference does not exist".

GHCR answers a missing tag with ``ERROR: <reference>: not found``, so the bare
phrase cannot be dropped. It is only accepted together with the reference
itself appearing in the message — see :func:`_classify`.
"""

PROBE_TIMEOUT_S: Final = 120.0
MAX_PROBE_WORKERS: Final = 8


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
            check=False,
        )
    except OSError as exc:
        raise CIError(f"cannot invoke {command[0]}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CIError(f"timed out after {PROBE_TIMEOUT_S}s: {' '.join(command)}") from exc


INDEX_MEDIA_TYPES: Final = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
"""Media types that describe a list of manifests rather than an image.

The study promotes and records plain image digests. If a build ever attaches
provenance or SBOM attestations, BuildKit wraps the image in an index and the
digest published stops naming the image — so the shape is asserted, not assumed.
"""


@dataclass(frozen=True, slots=True)
class Descriptor:
    """What a registry reports for one reference."""

    digest: str
    media_type: str

    @property
    def is_index(self) -> bool:
        return self.media_type in INDEX_MEDIA_TYPES


def _classify_absence(reference: str, stderr: str) -> bool:
    """Decide whether ``stderr`` means the reference is absent. Fail closed.

    Three conditions must all hold, because this predicate is what authorises
    publishing a tag fresh:

    1. nothing in the message looks like a client, auth, or transport failure;
    2. the message carries a registry not-found token; and
    3. the message names the reference we asked about — a registry error does,
       and a local tooling failure does not.
    """
    if CLIENT_FAILURE_PATTERN.search(stderr):
        return False
    if not ABSENT_TOKEN_PATTERN.search(stderr):
        return False
    return reference in stderr


def probe_descriptor(reference: str) -> Descriptor | None:
    """Resolve ``reference`` to its manifest descriptor, or ``None`` if it is absent.

    ``None`` means the registry answered that the reference does not exist. Any
    other failure raises rather than being reported as absence.
    """
    result = _run(
        ["docker", "buildx", "imagetools", "inspect", "--format", "{{json .Manifest}}", reference]
    )
    if result.returncode != 0:
        if _classify_absence(reference, result.stderr):
            return None
        raise CIError(f"cannot resolve {reference}: {result.stderr.strip() or 'unknown error'}")
    try:
        descriptor = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CIError(f"registry returned an unreadable descriptor for {reference}: {exc}") from exc
    if not isinstance(descriptor, dict):
        raise CIError(f"registry returned an unexpected descriptor for {reference}")
    digest = descriptor.get("digest")
    if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
        raise CIError(f"registry reported an invalid digest for {reference}: {digest!r}")
    media_type = descriptor.get("mediaType")
    if not isinstance(media_type, str) or not media_type:
        raise CIError(f"registry reported no media type for {reference}")
    return Descriptor(digest=digest, media_type=media_type)


def probe(reference: str) -> str | None:
    """The manifest digest a registry reports for ``reference``, or ``None`` if absent."""
    descriptor = probe_descriptor(reference)
    return None if descriptor is None else descriptor.digest


def assert_plain_manifests(references: Iterable[str]) -> None:
    """Refuse to proceed if any published reference is an index rather than an image."""
    for reference in sorted(set(references)):
        descriptor = probe_descriptor(reference)
        if descriptor is None:
            raise CIError(f"expected {reference} to be published, but it is absent")
        if descriptor.is_index:
            raise CIError(
                f"{reference} published as {descriptor.media_type}; attestations must be "
                "disabled so the published digest names the image itself"
            )


def probe_many(references: Iterable[str]) -> dict[str, str | None]:
    """Resolve many references concurrently, preserving fail-closed semantics.

    One raised error fails the whole probe: a partial view of what exists is
    exactly the input that would produce a partial publication.
    """
    unique = sorted(set(references))
    if not unique:
        return {}
    with ThreadPoolExecutor(max_workers=min(MAX_PROBE_WORKERS, len(unique))) as pool:
        digests = list(pool.map(probe, unique))
    return dict(zip(unique, digests, strict=True))


def image_config(reference: str) -> Mapping[str, Any]:
    """The image config for ``reference``, read from the registry without pulling layers."""
    result = _run(
        ["docker", "buildx", "imagetools", "inspect", "--format", "{{json .Image}}", reference]
    )
    if result.returncode != 0:
        raise CIError(f"cannot inspect {reference}: {result.stderr.strip() or 'unknown error'}")
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CIError(f"unreadable image config for {reference}: {exc}") from exc
    if not isinstance(document, dict):
        raise CIError(f"unexpected image config for {reference}")
    # A multi-platform reference nests one config per platform. The study builds
    # single-platform images, so anything else is a change nobody declared.
    if "config" not in document and len(document) == 1:
        document = next(iter(document.values()))
    if not isinstance(document, dict):
        raise CIError(f"unexpected image config for {reference}")
    return document


def image_labels(reference: str) -> Mapping[str, str]:
    config = image_config(reference)
    inner = config.get("config")
    if not isinstance(inner, dict):
        raise CIError(f"image config for {reference} has no config section")
    labels = inner.get("Labels") or {}
    if not isinstance(labels, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in labels.items()
    ):
        raise CIError(f"image config for {reference} has unreadable labels")
    return labels


def image_user(reference: str) -> str:
    config = image_config(reference)
    inner = config.get("config")
    if not isinstance(inner, dict):
        raise CIError(f"image config for {reference} has no config section")
    user = inner.get("User")
    if not isinstance(user, str):
        raise CIError(f"image config for {reference} declares no user")
    return user


def image_entrypoint(reference: str) -> tuple[str, ...]:
    config = image_config(reference)
    inner = config.get("config")
    if not isinstance(inner, dict):
        raise CIError(f"image config for {reference} has no config section")
    entrypoint = inner.get("Entrypoint")
    if not isinstance(entrypoint, list) or any(not isinstance(item, str) for item in entrypoint):
        raise CIError(f"image config for {reference} has an unreadable entrypoint")
    return tuple(entrypoint)
