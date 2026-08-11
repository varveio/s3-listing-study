"""Provider-neutral construction of the immutable attempt-worker request."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from s3_listing_study.manager.campaign import CampaignError

CASE_ENV_KEYS = frozenset(("JAVA_TOOL_OPTIONS", "NODE_OPTIONS"))


def _token(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise CampaignError(f"{label} must be a non-empty token")
    return value


def evidence_prefix(*, campaign: str, attempt_prefix: str, object_root: str | None) -> str:
    """Map a logical attempt prefix below an optional isolated object root."""
    if object_root is None:
        return attempt_prefix
    segments = object_root.removesuffix("/").split("/")
    logical_root = f"campaigns/{campaign}/"
    if (
        not object_root
        or not object_root.endswith("/")
        or object_root.startswith("/")
        or any(not segment or segment in (".", "..") for segment in segments)
        or any(character.isspace() or character == "\x00" for character in object_root)
    ):
        raise CampaignError("evidence object root must be a relative prefix ending in '/'")
    logical_segments = attempt_prefix.split("/")
    if (
        not attempt_prefix.startswith(logical_root)
        or any(not segment or segment in (".", "..") for segment in logical_segments)
        or any(character.isspace() or character == "\x00" for character in attempt_prefix)
    ):
        raise CampaignError("attempt prefix is outside its logical campaign root")
    return f"{object_root}{attempt_prefix.removeprefix('campaigns/')}"


def worker_argv(
    *,
    campaign: str,
    attempt: Mapping[str, Any],
    image: Mapping[str, Any],
    results_bucket: str,
    output_path: str,
    term_grace_s: int,
    destination_prefix: str | None = None,
) -> list[str]:
    """Build the argv appended to the fixed execution-image entrypoint.

    The input is the compiler's serialized campaign row so the workflow engine
    transports one frozen, provider-neutral worker request without translating
    worker flags into scheduler-specific state.
    """
    resources = attempt["resources"]
    container_memory = resources["container_memory_gb"]
    commands = [
        "--request-schema",
        "2",
        "--output",
        output_path,
        "--timeout",
        str(attempt["timeout_s"]),
        "--term-grace",
        str(term_grace_s),
        "--tool",
        str(attempt["tool"]),
        "--tool-version",
        _token(image.get("tool_version"), label=f"{attempt['tool']}: image tool_version"),
        "--shared-base-digest",
        str(image["shared_base_digest"]),
        "--shared-base-uri",
        str(image["shared_base_uri"]),
        "--derived-image",
        str(image["derived_image"]),
        "--harness-revision",
        _token(
            image.get("harness_revision"),
            label=f"{attempt['tool']}: image harness_revision",
        ),
        "--operation",
        "list",
        "--auth",
        str(attempt["auth"]),
        "--mode",
        str(attempt["mode"]),
        "--bucket",
        str(attempt["bucket"]),
        "--region",
        str(attempt["region"]),
        "--prefix",
        "",
        "--scope",
        "full",
        "--campaign-id",
        campaign,
        "--job-id",
        str(attempt["job_id"]),
        "--case-id",
        str(attempt["case_id"]),
        "--case-fingerprint",
        str(attempt["case_fingerprint"]),
        "--attempt-fingerprint",
        str(attempt["attempt_fingerprint"]),
        "--run-ordinal",
        str(attempt["run_ordinal"]),
        "--submission-number",
        str(attempt["submission"]),
        "--machine-type",
        str(resources["machine_type"]),
        "--vcpus",
        str(resources["vcpus"]),
        "--memory-gb",
        str(resources["memory_gb"]),
        "--container-memory-gb",
        "none" if container_memory is None else str(container_memory),
        "--destination",
        f"gs://{results_bucket}/{destination_prefix or attempt['prefix']}",
    ]
    seen: set[str] = set()
    environment: Sequence[Sequence[str]] = attempt.get("env", ())
    for pair in environment:
        if len(pair) != 2:
            raise CampaignError("case environment entries must be name/value pairs")
        name, value = pair
        if name not in CASE_ENV_KEYS:
            allowed = "|".join(sorted(CASE_ENV_KEYS))
            raise CampaignError(f"case environment key must be one of {allowed}: {name!r}")
        if name in seen:
            raise CampaignError(f"case environment repeats {name}")
        if not isinstance(value, str) or not value or "\x00" in value:
            raise CampaignError(f"case environment {name} value must be non-empty and NUL-free")
        seen.add(name)
        commands.extend(("--case-env", f"{name}={value}"))
    return commands
