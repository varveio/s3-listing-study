"""The one canonical spelling of every published tag.

The tag grammar *is* the identity scheme: each tag is the concatenation of the
twelve-character prefixes of the content hashes its image depends on, so what a
tag names and what invalidates it are the same statement.

    shared-python3.11-<shared12>
    tool-<tool>-v<toolver>-base-<shared12>-build-<build12>
    execution-<tool>-v<toolver>-base-<shared12>-build-<build12>-worker-v<wver>-src-<worker12>

Channel tags (``execution-<tool>-<suffix>``, ``set-<suffix>``) are movable
pointers, and ``set-v2-<manifest12>`` is the immutable publication ledger.

This module previously existed twice: once here in spirit, as
``build_selection.derived_image_tag``, and once as inline Python in the workflow
that re-derived the same strings with weaker regexes. A tag that two
implementations disagree about is an image nobody can find, so there is now one
implementation and the workflow calls it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final

from s3_listing_study.ci import CIError

DOCKER_TAG_RE: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
"""A legal Docker tag component. Anchored with ``fullmatch``, max 128 characters."""

CHANNEL_SUFFIX_RE: Final = re.compile(r"[a-z0-9_][a-z0-9._-]{0,127}")
SHA256_RE: Final = re.compile(r"[0-9a-f]{64}")
DIGEST_RE: Final = re.compile(r"sha256:[0-9a-f]{64}")
REPOSITORY_RE: Final = re.compile(
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+"
)

MAIN_CHANNEL: Final = "main"
BRANCH_SLUG_LIMIT: Final = 50
"""Characters of the branch name kept in a channel tag before the disambiguating hash."""

SHARED_PYTHON_SERIES: Final = "python3.11"
"""The interpreter series the shared runtime publishes under.

It is part of the tag rather than derived from the image because a reader
scanning the package listing should see which interpreter a runtime carries
without pulling it. The Dockerfile asserts the running version matches.
"""


def short(digest: str, field: str) -> str:
    """The twelve-character tag prefix of a validated 64-hex content hash."""
    if SHA256_RE.fullmatch(digest) is None:
        raise CIError(f"{field} must be 64 lowercase hexadecimal digits")
    return digest[:12]


def validate_tag(reference: str) -> str:
    """Return a full ``repository:tag`` reference whose tag component is legal."""
    repository, separator, tag = reference.rpartition(":")
    if not separator or not repository:
        raise CIError(f"image reference has no tag component: {reference}")
    if DOCKER_TAG_RE.fullmatch(tag) is None:
        raise CIError(f"invalid Docker tag: {tag}")
    return reference


def validate_repository(repository: str) -> str:
    if REPOSITORY_RE.fullmatch(repository) is None:
        raise CIError(f"invalid image repository: {repository}")
    return repository


def channel_suffix(ref_name: str, *, is_main_publication: bool) -> str:
    """The channel component naming what a movable tag tracks.

    ``main`` for a push or dispatch on the default branch; otherwise a sanitized
    branch slug plus a hash of the *full* ref name, so two long branches sharing
    a fifty-character prefix cannot collide on one channel.

    A pull request resolves through its head ref and never reaches the ``main``
    branch here, so a pull request from a branch literally named ``main`` lands on
    ``branch-main-<hash>`` and cannot hijack the main channel.
    """
    if not ref_name:
        raise CIError("publication requires a ref name")
    if is_main_publication:
        return MAIN_CHANNEL
    slug = ref_name.lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", slug)
    slug = re.sub(r"^[.-]+", "", slug)
    slug = re.sub(r"[.-]+$", "", slug)
    slug = re.sub(r"-+", "-", slug)
    # Truncation is deliberately applied after sanitising, matching the shell this
    # replaces. A branch long enough to be cut at a separator yields a suffix with
    # a doubled dash, which is legal and stable; the trailing hash keeps it unique.
    slug = slug[:BRANCH_SLUG_LIMIT]
    if not slug:
        raise CIError(f"branch does not produce a safe Docker tag: {ref_name!r}")
    digest = hashlib.sha256(ref_name.encode("utf-8")).hexdigest()[:12]
    suffix = f"branch-{slug}-{digest}"
    if CHANNEL_SUFFIX_RE.fullmatch(suffix) is None:
        raise CIError(f"invalid sanitized branch tag suffix: {suffix}")
    return suffix


@dataclass(frozen=True, slots=True)
class ToolTags:
    """Every published reference for one tool, at one exact set of input hashes."""

    tool: str
    tool_version: str
    tool_tag_version: str
    worker_version: str
    worker_tag_version: str
    shared_source_sha256: str
    tool_build_sha256: str
    worker_source_sha256: str
    tool_tag: str
    execution_tag: str
    execution_channel_tag: str


def shared_tag(repository: str, shared_source_sha256: str) -> str:
    validate_repository(repository)
    return validate_tag(
        f"{repository}:shared-{SHARED_PYTHON_SERIES}-"
        f"{short(shared_source_sha256, 'shared_base_source_sha256')}"
    )


def tool_tag(
    repository: str, tool: str, tool_tag_version: str, shared_source: str, tool_build: str
) -> str:
    validate_repository(repository)
    return validate_tag(
        f"{repository}:tool-{tool}-v{tool_tag_version}"
        f"-base-{short(shared_source, 'shared_base_source_sha256')}"
        f"-build-{short(tool_build, 'tool_build_sha256')}"
    )


def execution_tag(
    repository: str,
    tool: str,
    tool_tag_version: str,
    shared_source: str,
    tool_build: str,
    worker_tag_version: str,
    worker_source: str,
) -> str:
    validate_repository(repository)
    return validate_tag(
        f"{repository}:execution-{tool}-v{tool_tag_version}"
        f"-base-{short(shared_source, 'shared_base_source_sha256')}"
        f"-build-{short(tool_build, 'tool_build_sha256')}"
        f"-worker-v{worker_tag_version}"
        f"-src-{short(worker_source, 'worker_source_sha256')}"
    )


def execution_channel_tag(repository: str, tool: str, suffix: str) -> str:
    validate_repository(repository)
    return validate_tag(f"{repository}:execution-{tool}-{suffix}")


def set_ledger_tag(repository: str, manifest_sha256: str) -> str:
    validate_repository(repository)
    return validate_tag(
        f"{repository}:set-v2-{short(manifest_sha256, 'publication manifest sha256')}"
    )


def set_channel_tag(repository: str, suffix: str) -> str:
    validate_repository(repository)
    return validate_tag(f"{repository}:set-{suffix}")


def digest_reference(repository: str, digest: str) -> str:
    """The immutable ``repository@sha256:…`` form that is authoritative everywhere."""
    validate_repository(repository)
    if DIGEST_RE.fullmatch(digest) is None:
        raise CIError(f"not a sha256 digest: {digest}")
    return f"{repository}@{digest}"
