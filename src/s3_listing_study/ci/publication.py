"""Assemble the publication manifest and the channel-promotion record.

Two documents come out of a publication run, and the difference between them is
the difference between evidence and bookkeeping.

The **publication manifest** is sealed into an OCI image, ``set-v2-<manifest12>``,
whose only content is the manifest bytes. That image is the durable ledger: it
outlives Actions artifact retention, it is content-addressed, and it names every
digest the run published. Its format stays at 2 so the existing published
ledgers and this one describe one lineage.

The **promotion report** records what every movable channel pointed at *before*
anything moved, so a partially-completed promotion can be reasoned about and
reversed by hand. It is written before the first channel changes and updated as
each one succeeds.

The ordering these support is the study's integrity contract: individual
execution channels may move one at a time, but the authoritative set channel
moves last and therefore never advertises an incomplete set.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from s3_listing_study.ci import CIError
from s3_listing_study.ci.plan import Plan
from s3_listing_study.ci.tags import DIGEST_RE, digest_reference

MANIFEST_FORMAT_VERSION = 2
MANIFEST_KIND = "github-container-image-publication"
PROMOTION_KIND = "github-container-image-promotion-report"
REUSE_SOURCES = frozenset({"built", "adopted"})
GIT_OID_RE = re.compile(r"[0-9a-f]+")


def _require_digest(value: str | None, label: str) -> str:
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        raise CIError(f"{label} is not a sha256 digest: {value!r}")
    return value


def publication_manifest(
    plan: Plan,
    *,
    checkout_revision: str,
    source_ref: str,
    pull_request: dict[str, Any] | None,
) -> dict[str, Any]:
    """The complete, validated record of one publication.

    Every tool must have resolved both a parent and an execution digest by the
    time this runs. A manifest is only ever written for a complete set — an
    incomplete one has no valid representation here, which is what keeps a
    partial run from producing a ledger at all.
    """
    # Only SHA-1 (40) and SHA-256 (64) are valid Git object IDs; anything between
    # is a truncation nobody should be publishing under.
    if len(checkout_revision) not in {40, 64} or GIT_OID_RE.fullmatch(checkout_revision) is None:
        raise CIError(f"checkout revision is not a Git object ID: {checkout_revision!r}")
    shared_digest = _require_digest(plan.shared_digest, "shared runtime digest")
    images: dict[str, Any] = {}
    for item in plan.tools:
        tool_digest = _require_digest(item.tool_digest, f"{item.tool} tool digest")
        execution_digest = _require_digest(item.execution_digest, f"{item.tool} execution digest")
        images[item.tool] = {
            "channel_tag_suffix": plan.channel_suffix,
            "checkout_revision": checkout_revision,
            "execution": {
                "channel_tag": item.execution_channel_tag,
                "digest": execution_digest,
                "reuse_source": item.reuse_source,
                "uri": digest_reference(plan.repository, execution_digest),
                "version_tag": item.execution_tag,
            },
            "format_version": MANIFEST_FORMAT_VERSION,
            "kind": "tool-execution-publication",
            "pull_request": pull_request,
            "selection_sha256": item.selection_sha256,
            "shared": {
                "digest": shared_digest,
                "source_sha256": plan.shared_source_sha256,
                "uri": digest_reference(plan.repository, shared_digest),
                "version_tag": plan.shared_tag,
            },
            "tool": {
                "build_sha256": item.tool_build_sha256,
                "digest": tool_digest,
                "uri": digest_reference(plan.repository, tool_digest),
                "version_tag": item.tool_tag,
            },
            "tool_name": item.tool,
            "tool_version": item.tool_version,
            "worker_revision": checkout_revision,
            "worker_source_sha256": item.worker_source_sha256,
            "worker_version": item.worker_version,
        }
    return {
        "channel_tag_suffix": plan.channel_suffix,
        "checkout_revision": checkout_revision,
        "format_version": MANIFEST_FORMAT_VERSION,
        "images": images,
        "kind": MANIFEST_KIND,
        "pull_request": pull_request,
        "repository": plan.repository,
        "shared": {
            "digest": shared_digest,
            "source_sha256": plan.shared_source_sha256,
            "uri": digest_reference(plan.repository, shared_digest),
            "version_tag": plan.shared_tag,
        },
        "source_ref": source_ref,
    }


def promotion_report(
    plan: Plan,
    *,
    checkout_revision: str,
    set_digest: str,
    set_version_tag: str,
    previous_channels: Mapping[str, str | None],
    previous_set: str | None,
) -> dict[str, Any]:
    """What every channel pointed at before this run touched anything."""
    channels: dict[str, Any] = {}
    for item in plan.tools:
        channels[item.tool] = {
            "channel_tag": item.execution_channel_tag,
            "intended_digest": _require_digest(item.execution_digest, f"{item.tool} execution"),
            "previous_digest": previous_channels.get(item.execution_channel_tag) or "absent",
            "status": "planned",
        }
    return {
        "checkout_revision": checkout_revision,
        "execution_channels": channels,
        "format_version": 1,
        "kind": PROMOTION_KIND,
        "publication_set": {
            "channel_tag": plan.set_channel_tag,
            "intended_digest": _require_digest(set_digest, "publication set"),
            "previous_digest": previous_set or "absent",
            "reference": digest_reference(plan.repository, set_digest),
            "status": "planned",
            "version_tag": set_version_tag,
        },
    }


def promotion_summary_rows(report: dict[str, Any]) -> list[tuple[str, str, str]]:
    """``(channel, previous, intended)`` rows for the run's step summary table."""
    rows = [
        (entry["channel_tag"], entry["previous_digest"], entry["intended_digest"])
        for entry in report["execution_channels"].values()
    ]
    publication_set = report["publication_set"]
    rows.append(
        (
            publication_set["channel_tag"],
            publication_set["previous_digest"],
            publication_set["intended_digest"],
        )
    )
    return rows
