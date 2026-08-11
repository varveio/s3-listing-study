"""Exact provider-neutral contract for the image-owned attempt worker."""

from __future__ import annotations

import pytest

from s3_listing_study.manager.campaign import CampaignError
from s3_listing_study.manager.campaign.request import evidence_prefix, worker_argv


def realistic_attempt(*, env: list[list[str]] | None = None) -> dict[str, object]:
    return {
        "job_id": "c-2026-08-11-snake-swath-recursive-pa-bbbbbbbb-r1-s1",
        "submission": 1,
        "run_ordinal": 1,
        "bucket": "noaa-rtma-pds",
        "region": "us-east-1",
        "tool": "swath",
        "case_id": "recursive-parquet-sorted.vcpus-2.memory_gb-4.container_memory_gb-2",
        "mode": "recursive-parquet-sorted",
        "auth": "anonymous",
        "case_fingerprint": "40034035509cfcf1c65d5b50a4fcd8b78e24b113b831c17ee2396e60dcdc91d8",
        "attempt_fingerprint": "b" * 64,
        "timeout_s": 28800,
        "prefix": (
            "campaigns/2026-08-11-snake/results/noaa-rtma-pds/swath/"
            "recursive-parquet-sorted.vcpus-2.memory_gb-4.container_memory_gb-2/run-1"
        ),
        "resources": {
            "machine_type": "n4-highcpu-2",
            "vcpus": 2,
            "memory_gb": 4,
            "container_memory_gb": 2,
        },
        "env": (
            env
            if env is not None
            else [["JAVA_TOOL_OPTIONS", "-XX:MaxRAMPercentage=75"]]
        ),
    }


def test_worker_argv_is_the_complete_exact_attempt_contract() -> None:
    attempt = realistic_attempt()
    image = {
        "tool_version": "0.2.2",
        "shared_base_digest": "sha256:" + "c" * 64,
        "shared_base_uri": "ghcr.io/varveio/study@sha256:" + "c" * 64,
        "derived_image": "sha256:" + "d" * 64,
        "harness_revision": "e" * 40,
    }
    destination = evidence_prefix(
        campaign="2026-08-11-snake",
        attempt_prefix=str(attempt["prefix"]),
        object_root="snakemake/evidence/",
    )

    assert worker_argv(
        campaign="2026-08-11-snake",
        attempt=attempt,
        image=image,
        results_bucket="study-results",
        output_path="/tmp/s3-listing-study-attempt",
        term_grace_s=5,
        destination_prefix=destination,
    ) == [
        "--request-schema",
        "2",
        "--output",
        "/tmp/s3-listing-study-attempt",
        "--timeout",
        "28800",
        "--term-grace",
        "5",
        "--tool",
        "swath",
        "--tool-version",
        "0.2.2",
        "--shared-base-digest",
        "sha256:" + "c" * 64,
        "--shared-base-uri",
        "ghcr.io/varveio/study@sha256:" + "c" * 64,
        "--derived-image",
        "sha256:" + "d" * 64,
        "--harness-revision",
        "e" * 40,
        "--operation",
        "list",
        "--auth",
        "anonymous",
        "--mode",
        "recursive-parquet-sorted",
        "--bucket",
        "noaa-rtma-pds",
        "--region",
        "us-east-1",
        "--prefix",
        "",
        "--scope",
        "full",
        "--campaign-id",
        "2026-08-11-snake",
        "--job-id",
        "c-2026-08-11-snake-swath-recursive-pa-bbbbbbbb-r1-s1",
        "--case-id",
        "recursive-parquet-sorted.vcpus-2.memory_gb-4.container_memory_gb-2",
        "--case-fingerprint",
        "40034035509cfcf1c65d5b50a4fcd8b78e24b113b831c17ee2396e60dcdc91d8",
        "--attempt-fingerprint",
        "b" * 64,
        "--run-ordinal",
        "1",
        "--submission-number",
        "1",
        "--machine-type",
        "n4-highcpu-2",
        "--vcpus",
        "2",
        "--memory-gb",
        "4",
        "--container-memory-gb",
        "2",
        "--destination",
        (
            "gs://study-results/snakemake/evidence/2026-08-11-snake/results/"
            "noaa-rtma-pds/swath/"
            "recursive-parquet-sorted.vcpus-2.memory_gb-4.container_memory_gb-2/run-1"
        ),
        "--case-env",
        "JAVA_TOOL_OPTIONS=-XX:MaxRAMPercentage=75",
    ]


@pytest.mark.parametrize("object_root", ("../escape/", "/absolute/", "bad root/"))
def test_evidence_prefix_refuses_unsafe_destination_roots(object_root: str) -> None:
    attempt = realistic_attempt()
    with pytest.raises(CampaignError, match="relative prefix"):
        evidence_prefix(
            campaign="2026-08-11-snake",
            attempt_prefix=str(attempt["prefix"]),
            object_root=object_root,
        )


def test_evidence_prefix_refuses_a_different_campaign() -> None:
    with pytest.raises(CampaignError, match="outside its logical campaign root"):
        evidence_prefix(
            campaign="2026-08-11-other",
            attempt_prefix=str(realistic_attempt()["prefix"]),
            object_root="snakemake/evidence/",
        )


@pytest.mark.parametrize(
    "env",
    (
        [["AWS_SECRET_ACCESS_KEY", "secret"]],
        [["NODE_OPTIONS", "ok"], ["NODE_OPTIONS", "again"]],
        [["JAVA_TOOL_OPTIONS", "bad\x00value"]],
    ),
)
def test_worker_argv_refuses_unsafe_case_environment(env: list[list[str]]) -> None:
    with pytest.raises(CampaignError, match="case environment"):
        worker_argv(
            campaign="2026-08-11-snake",
            attempt=realistic_attempt(env=env),
            image={
                "tool_version": "2.1.0",
                "shared_base_digest": "sha256:" + "c" * 64,
                "shared_base_uri": "registry.example/base@sha256:" + "c" * 64,
                "derived_image": "sha256:" + "d" * 64,
                "harness_revision": "e" * 40,
            },
            results_bucket="study-results",
            output_path="/tmp/result",
            term_grace_s=5,
        )
