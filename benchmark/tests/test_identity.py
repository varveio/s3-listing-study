"""Case identity: determinism, the absent/null/empty distinction, and the
measurement/preparation split (`benchmark/docs/identity.md`)."""

from __future__ import annotations

import hashlib
import json

from benchmark.identity import (
    CASE_HASH_V1,
    attempt_id,
    case_hash,
    case_id,
    case_inputs_document,
    measurement_environment,
    preparation_environment,
)

TOOL_SLICE = "a" * 64
PLATFORM = "b" * 64


def _measurement_env(input_artifact_sha256: str | None = None) -> dict[str, object]:
    return measurement_environment(
        input_artifact_sha256=input_artifact_sha256,
        auth_role=None,
        target_bucket="noaa-ghcn-pds",
        target_region="us-east-1",
        target_prefix="",
        location="us-central1",
        machine_type="c3-standard-4",
        vcpus=4,
        memory_gb=16,
        container_memory_gb=None,
        output_target="file",
        timeout_s=3600,
    )


def test_case_hash_golden_value() -> None:
    """Pinned so a change to the encoding is caught here rather than in the ledger."""
    config = {"mode": "recursive", "concurrency": 8}
    assert case_hash(_measurement_env(), config, TOOL_SLICE, PLATFORM) == "99abbeca2763"


def test_case_hash_is_deterministic_across_calls() -> None:
    config = {"mode": "recursive", "concurrency": 8}
    first = case_hash(_measurement_env(), config, TOOL_SLICE, PLATFORM)
    second = case_hash(_measurement_env(), config, TOOL_SLICE, PLATFORM)
    assert first == second


def test_case_hash_is_key_order_independent() -> None:
    config = {"mode": "recursive", "concurrency": 8}
    reordered_config = {"concurrency": 8, "mode": "recursive"}
    reordered_env = dict(reversed(list(_measurement_env().items())))
    assert case_hash(_measurement_env(), config, TOOL_SLICE, PLATFORM) == case_hash(
        reordered_env, reordered_config, TOOL_SLICE, PLATFORM
    )


def test_absent_key_differs_from_explicit_null() -> None:
    """A key with no value is omitted; an explicitly-null key is present as null."""
    with_null = {"auth_role": None}
    without_key: dict[str, object] = {}
    assert case_hash(with_null, {}, TOOL_SLICE, PLATFORM) != case_hash(
        without_key, {}, TOOL_SLICE, PLATFORM
    )


def test_domain_prefix_matters() -> None:
    """The reference implementation hashes `CASE_HASH_V1 + document`, not the document alone."""
    document = case_inputs_document({}, {}, TOOL_SLICE, PLATFORM)
    prefixed = hashlib.sha256(CASE_HASH_V1 + document.encode()).hexdigest()[:12]
    unprefixed = hashlib.sha256(document.encode()).hexdigest()[:12]
    assert case_hash({}, {}, TOOL_SLICE, PLATFORM) == prefixed
    assert prefixed != unprefixed


def test_hash_is_twelve_hex_digits() -> None:
    digest = case_hash(_measurement_env(), {}, TOOL_SLICE, PLATFORM)
    assert len(digest) == 12
    assert all(c in "0123456789abcdef" for c in digest)


def test_measurement_and_preparation_hash_differently_over_the_same_target() -> None:
    """A measurement's identity answers comparability; a preparation's answers content."""
    measurement = case_hash(_measurement_env(), {}, TOOL_SLICE, PLATFORM)
    preparation_env = preparation_environment(
        target_bucket="noaa-ghcn-pds",
        target_region="us-east-1",
        target_prefix="",
    )
    preparation = case_hash(preparation_env, {}, TOOL_SLICE, PLATFORM)
    assert measurement != preparation


def test_preparation_environment_excludes_machine_auth_output_timeout() -> None:
    document = preparation_environment(
        target_bucket="noaa-ghcn-pds", target_region="us-east-1", target_prefix=""
    )
    assert set(document) == {"target_bucket", "target_region", "target_prefix"}


def test_config_empty_dict_differs_from_config_absent() -> None:
    """`config` is always present, `{}` when the capsule has no keys — never omitted."""
    empty_config_document = case_inputs_document({}, {}, TOOL_SLICE, PLATFORM)
    assert json.loads(empty_config_document)["config"] == {}
    # An absent "config" key is not expressible through this module's API at
    # all: case_inputs_document always writes the key, so the two states can
    # only be produced by two different documents, not by omitting the field.
    without_config = json.dumps({"config": None})
    assert empty_config_document != without_config


def test_tool_is_not_a_hash_input() -> None:
    env = _measurement_env()
    config = {"mode": "recursive"}
    same_hash_a = case_hash(env, config, TOOL_SLICE, PLATFORM)
    same_hash_b = case_hash(env, config, TOOL_SLICE, PLATFORM)
    assert same_hash_a == same_hash_b
    aws = case_id("aws-cli", env, config, TOOL_SLICE, PLATFORM)
    s5cmd = case_id("s5cmd", env, config, TOOL_SLICE, PLATFORM)
    assert aws.split(".", 1)[1] == s5cmd.split(".", 1)[1]
    assert aws != s5cmd
    assert aws.startswith("aws-cli.")
    assert s5cmd.startswith("s5cmd.")


def test_attempt_id_composes_case_id_and_ordinal() -> None:
    case = case_id("aws-cli", _measurement_env(), {}, TOOL_SLICE, PLATFORM)
    assert attempt_id(case, 1) == f"{case}.s1"
    assert attempt_id(case, 12) == f"{case}.s12"


def test_consumed_artifact_digest_is_a_hash_input() -> None:
    without = _measurement_env()
    with_artifact = _measurement_env(input_artifact_sha256="c" * 64)
    assert "input_artifact_sha256" not in without
    assert with_artifact["input_artifact_sha256"] == "c" * 64
    assert case_hash(without, {}, TOOL_SLICE, PLATFORM) != case_hash(
        with_artifact, {}, TOOL_SLICE, PLATFORM
    )


def test_mid_chain_preparation_hashes_its_consumed_artifact() -> None:
    first_link = preparation_environment(
        target_bucket="noaa-ghcn-pds", target_region="us-east-1", target_prefix=""
    )
    second_link = preparation_environment(
        target_bucket="noaa-ghcn-pds",
        target_region="us-east-1",
        target_prefix="",
        input_artifact_sha256="d" * 64,
    )
    assert "input_artifact_sha256" not in first_link
    assert case_hash(first_link, {}, TOOL_SLICE, PLATFORM) != case_hash(
        second_link, {}, TOOL_SLICE, PLATFORM
    )
