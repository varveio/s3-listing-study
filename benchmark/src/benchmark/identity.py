"""Case identity: the hash that says two runs are the same measurement.

Normative reference: `benchmark/docs/identity.md`. This module only hashes and
formats documents callers assemble; it does no I/O and imports nothing from
`campaign`, `plan`, or `measure` — the identity is a pure function of what a
caller already knows.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from benchmark.contract import canonical_json

CASE_HASH_V2 = b"s3-listing-study-case-v2\0"

_HASH_HEX_DIGITS = 12


def case_inputs_document(
    environment: Mapping[str, Any],
    config: Mapping[str, Any],
    tool_slice: str,
    platform: str,
    replay: Mapping[str, Any] | None = None,
) -> str:
    """The exact canonical JSON `case_hash` is taken over.

    Stored byte-exactly as `case_inputs` (`model.md`): a hash collision is
    caught by comparing this document, not by trusting the digest.
    """
    inputs: dict[str, Any] = {
        "environment": environment,
        "config": config,
        "tool_slice_sha256": tool_slice,
        "platform_sha256": platform,
    }
    if replay is not None:
        inputs["replay"] = replay
    return canonical_json(inputs)


def case_hash(
    environment: Mapping[str, Any],
    config: Mapping[str, Any],
    tool_slice: str,
    platform: str,
    replay: Mapping[str, Any] | None = None,
) -> str:
    """Domain-separated sha256 of `case_inputs_document`, truncated to 12 hex digits.

    `tool` and `suite` are deliberately absent from every parameter here: the
    tool prefixes `case_id` and the suite prefixes the path, neither is a hash
    input.
    """
    document = case_inputs_document(environment, config, tool_slice, platform, replay)
    digest = hashlib.sha256(CASE_HASH_V2 + document.encode())
    return digest.hexdigest()[:_HASH_HEX_DIGITS]


def case_id(
    tool: str,
    environment: Mapping[str, Any],
    config: Mapping[str, Any],
    tool_slice: str,
    platform: str,
    replay: Mapping[str, Any] | None = None,
) -> str:
    """`<tool>.<hash>` — `tool` prefixes the identifier without entering the hash."""
    return f"{tool}.{case_hash(environment, config, tool_slice, platform, replay)}"


def attempt_id(case: str, attempt: int) -> str:
    """`<case_id>.s<attempt>` — the generated column `model.md` defines over these two."""
    return f"{case}.s{attempt}"


def measurement_environment(
    *,
    auth_role: str | None,
    target_bucket: str,
    target_region: str,
    target_prefix: str,
    location: str,
    machine_type: str,
    vcpus: int,
    memory_gb: int,
    container_memory_gb: int | None,
    output_target: str,
    timeout_s: int,
    input_artifact_sha256: str | None = None,
) -> dict[str, Any]:
    """The environment document a measurement hashes over (`identity.md` § *Two identities*).

    Every key here is required and therefore always present in the returned
    document, with one exception. `auth_role` and `container_memory_gb` may be
    `None`, which is a value (unsigned; no ceiling) — the caller cannot omit the
    key by mistake, only choose what it is explicitly null for.
    `input_artifact_sha256` is the exception: a case that consumes nothing has
    no such key at all, so `None` here means omitted, not null — the content
    digest is a hash input (`model.md`), the path never is.
    """
    document: dict[str, Any] = {
        "auth_role": auth_role,
        "target_bucket": target_bucket,
        "target_region": target_region,
        "target_prefix": target_prefix,
        "location": location,
        "machine_type": machine_type,
        "vcpus": vcpus,
        "memory_gb": memory_gb,
        "container_memory_gb": container_memory_gb,
        "output_target": output_target,
        "timeout_s": timeout_s,
    }
    if input_artifact_sha256 is not None:
        document["input_artifact_sha256"] = input_artifact_sha256
    return document


def preparation_environment(
    *,
    target_bucket: str,
    target_region: str,
    target_prefix: str,
    input_artifact_sha256: str | None = None,
) -> dict[str, Any]:
    """The environment document a preparation hashes over (`identity.md` § *Two identities*).

    Machine, auth role, output target, and timeout are recorded on the row but
    excluded from identity — a preparation's hash answers "do we already have
    this artifact?", not "are these comparable?". They are absent here by
    construction: this function has no parameter for them, so a caller cannot
    hash them by accident.

    `input_artifact_sha256` is present because a mid-chain preparation consumes
    the previous link's artifact, and a transform over different upstream bytes
    is a different artifact — so the digest is part of "do we already have this?".
    `None` means the key is omitted: this preparation consumes nothing.
    """
    document: dict[str, Any] = {
        "target_bucket": target_bucket,
        "target_region": target_region,
        "target_prefix": target_prefix,
    }
    if input_artifact_sha256 is not None:
        document["input_artifact_sha256"] = input_artifact_sha256
    return document
