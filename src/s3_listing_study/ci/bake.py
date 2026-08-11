"""Generate one ``docker buildx bake`` definition for every thin worker layer.

Eleven execution images used to be eleven matrix jobs. Each spent roughly
thirty-two seconds on setup — checkout, uv, an artifact download, a registry
container, a builder — to perform four seconds of work, so the fan-out bought
about forty seconds of parallelism for about three hundred and fifty seconds of
overhead.

Bake inverts that. One job, one BuildKit instance, every target concurrent, the
shared parent layers pulled once and reused across targets, and every resulting
digest returned together in one ``--metadata-file``. The repository's recipe was
already shaped for it: the derived image takes its adapter and its registration
through *named build contexts*, which is exactly bake's ``contexts`` field.

The definition is generated rather than committed. Every tag and every build
argument here is derived from ``build_selection``; a checked-in HCL file would be
a second place for the tag grammar to live and a second place for it to drift.

Provenance and SBOM attestations are switched off explicitly. Left to BuildKit's
default, a registry output is wrapped in an OCI *index* carrying an attestation
manifest, so the digest published and promoted stops being a plain image digest.
In a study whose entire correctness argument is ``@sha256:`` identity that is a
trap, so the flag is stated rather than inherited. Standard provenance is
attached out of band, after the push, where it cannot alter the digest.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from s3_listing_study.ci import CIError
from s3_listing_study.ci.plan import Plan, ToolPlan
from s3_listing_study.ci.tags import DIGEST_RE, digest_reference

DERIVED_DOCKERFILE = "harness/derived-image/Dockerfile"


def _target(plan: Plan, item: ToolPlan, *, push: bool) -> dict[str, Any]:
    if item.tool_digest is None or DIGEST_RE.fullmatch(item.tool_digest) is None:
        raise CIError(f"{item.tool}: cannot bake a thin layer without a published tool parent")
    parent = digest_reference(plan.repository, item.tool_digest)
    return {
        "context": ".",
        "dockerfile": DERIVED_DOCKERFILE,
        "contexts": {
            "adapter": f"tools/{item.tool}/adapter",
            "selection": f"tools/{item.tool}/build",
        },
        "args": {
            "TOOL_IMAGE": parent,
            "TOOL_IMAGE_DIGEST": item.tool_digest,
            "TOOL_IMAGE_URI": parent,
            "SELECTION_SHA256": item.selection_sha256,
            "WORKER_SOURCE_SHA256": item.worker_source_sha256,
        },
        "tags": [item.execution_tag],
        # `cacheonly` on a pull request is deliberate: the validation is that the
        # build succeeded, because `validate_selection.py` runs as a RUN step and
        # fails the build. Loading the image afterwards would cost seconds and
        # prove nothing further.
        "output": ["type=registry" if push else "type=cacheonly"],
        # `attest` entries, not the `provenance`/`sbom` target fields. Bake ignores
        # unknown keys without complaint, and those two are among the keys it
        # ignores in a JSON definition — a definition carrying them still pushed an
        # attestation index in testing. The workflow additionally exports
        # BUILDX_NO_DEFAULT_ATTESTATIONS, and `assert_plain_manifests` checks what
        # was actually published, so a future regression here fails loudly.
        "attest": ["type=provenance,disabled=true", "type=sbom,disabled=true"],
    }


def bake_definition(
    plan: Plan, *, push: bool, tools: Sequence[str] | None = None
) -> dict[str, Any]:
    """The bake file for every tool whose parent is published and whose child is not."""
    selected = plan.bucket("bake")
    if tools is not None:
        wanted = set(tools)
        selected = tuple(item for item in selected if item.tool in wanted)
    if not selected:
        raise CIError("no thin layers to bake")
    targets = {item.tool: _target(plan, item, push=push) for item in selected}
    return {
        "target": targets,
        "group": {"default": {"targets": sorted(targets)}},
    }


def write_bake_definition(
    plan: Plan, path: Path, *, push: bool, tools: Sequence[str] | None = None
) -> dict[str, Any]:
    definition = bake_definition(plan, push=push, tools=tools)
    path.write_text(json.dumps(definition, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return definition


def assert_metadata_digests(
    plan: Plan,
    metadata: Mapping[str, Any],
    *,
    expected_tools: Sequence[str],
    push: bool,
) -> dict[str, str]:
    """Check bake's own report against the plan, and return tool -> published digest.

    The two output modes report different things, and conflating them is why this
    takes ``push``. A ``type=registry`` build reports ``containerimage.digest``
    and ``image.name``; a ``type=cacheonly`` build — what a pull request runs —
    reports neither, only ``buildx.build.ref``. Demanding a digest from a
    cacheonly bake fails every pull-request run, and accepting a missing digest
    on a push would let a target that never published pass as complete.
    """
    expected = list(expected_tools)
    resolved: dict[str, str] = {}
    for tool in expected:
        entry = metadata.get(tool)
        if not isinstance(entry, dict):
            raise CIError(f"bake metadata has no target for {tool}")
        digest = entry.get("containerimage.digest")
        if not push:
            if digest is not None:
                raise CIError(
                    f"{tool}: a validating bake must not produce a published image digest"
                )
            if not entry.get("buildx.build.ref"):
                raise CIError(f"{tool}: bake reported no build for this target")
            continue
        if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
            raise CIError(f"{tool}: bake reported an invalid image digest: {digest!r}")
        expected_tag = next((item.execution_tag for item in plan.tools if item.tool == tool), None)
        reported = entry.get("image.name")
        if not isinstance(reported, str) or not reported:
            raise CIError(f"{tool}: bake reported no image name for a pushed target")
        names = {name.strip() for name in reported.split(",") if name.strip()}
        if expected_tag is None or names != {expected_tag}:
            raise CIError(
                f"{tool}: bake pushed {sorted(names)} rather than exactly the planned "
                f"{expected_tag}"
            )
        resolved[tool] = digest
    unexpected = sorted(
        key for key in metadata if key not in set(expected) and not key.startswith("buildx.")
    )
    if unexpected:
        raise CIError(f"bake metadata contains unplanned targets: {', '.join(unexpected)}")
    return resolved
