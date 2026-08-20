"""Case identity: determinism, the absent/null/empty distinction, and the
measurement/preparation split (`benchmark/docs/identity.md`)."""

from __future__ import annotations

from benchmark.identity import (
    attempt_id,
    case_hash,
    case_id,
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
    assert case_hash(_measurement_env(), config, TOOL_SLICE, PLATFORM) == "4ca988b33945"


def test_every_replay_fact_changes_identity() -> None:
    replay = {
        "backend": {
            "server_image_uri": f"registry/replay@sha256:{'1' * 64}",
            "fixture_sha256": "2" * 64,
            "reference_manifest_uri": "gs://reference/manifest",
            "reference_manifest_sha256": "3" * 64,
            "serving_mode": "sorted",
            "latency_model": {
                "deadlines_ms": {
                    "worker_page": 107,
                    "pivot_probe": 41,
                    "structure_probe": 49,
                },
                "scale": 1.0,
                "jitter": "none",
                "injector_version": "injector-v1",
                "semantics_version": "deadline-floor-v1",
            },
            "evidence_protocol_version": "measurement-v1",
        },
        "allocation": {
            "subject_vcpus": 7,
            "subject_memory_gb": 40,
            "host_reserved_vcpus": 1,
            "host_reserved_memory_gb": 8,
            "replay_vcpus": 8,
            "replay_memory_gb": 16,
            "replay_parquet_connections": 640,
            "replay_max_concurrent_requests": 512,
            "replay_prefetch": False,
            "replay_heap_percent": 75,
        },
    }
    baseline = case_hash(_measurement_env(), {}, TOOL_SLICE, PLATFORM, replay)
    mutations = (
        ("backend.server_image_uri", f"registry/replay@sha256:{'4' * 64}"),
        ("backend.fixture_sha256", "5" * 64),
        ("backend.reference_manifest_uri", "gs://reference/other"),
        ("backend.reference_manifest_sha256", "6" * 64),
        ("backend.serving_mode", "duckdb"),
        ("backend.latency_model.deadlines_ms.worker_page", 108),
        ("backend.latency_model.scale", 0.5),
        ("backend.latency_model.injector_version", "injector-v2"),
        ("backend.latency_model.semantics_version", "deadline-floor-v2"),
        ("backend.evidence_protocol_version", "measurement-v2"),
        *(
            (f"allocation.{key}", value + 1)
            for key, value in replay["allocation"].items()
            if isinstance(value, int) and not isinstance(value, bool)
        ),
        ("allocation.replay_prefetch", True),
    )
    import copy

    for path, value in mutations:
        changed = copy.deepcopy(replay)
        target = changed
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
        assert case_hash(_measurement_env(), {}, TOOL_SLICE, PLATFORM, changed) != baseline, path


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


def test_tool_is_not_a_hash_input() -> None:
    env = _measurement_env()
    config = {"mode": "recursive"}
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
