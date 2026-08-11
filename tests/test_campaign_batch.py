"""Pure GCP Batch rendering for frozen campaign attempts."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from s3_listing_study.manager.bench import plan as bench
from s3_listing_study.manager.bench.plan import Case, Resources
from s3_listing_study.manager.campaign import Attempt, CampaignError, attempts_for
from s3_listing_study.manager.campaign.batch import BatchConfig, render_job

DIGEST = "sha256:" + "d" * 64
SUBJECT = "sha256:" + "a" * 64
SECRET = "projects/study/secrets/aws/versions/7"


def attempt(
    *,
    auth: str = "anonymous",
    ceiling: int | None = 2,
    env: tuple[tuple[str, str], ...] = (),
) -> Attempt:
    resources = Resources(
        vcpus=2,
        memory_gb=4,
        machine_type="n4-highcpu-2",
        container_memory_gb=ceiling,
    )
    case = Case(
        tool="swath",
        case_id="recursive-tsv.container_memory_gb-2",
        mode="recursive-tsv",
        auth=auth,
        resources=resources,
        reps=1,
        timeout_s=3600,
        axes=(("mode", "recursive-tsv"), ("container_memory_gb", ceiling)),
        env=env,
        fingerprint="c" * 64,
    )
    return Attempt(
        campaign="2026-08-10-first",
        bucket="example-bucket",
        region="us-east-1",
        case=case,
        image={
            "derived_image": DIGEST,
            "image_uri": f"us-east1-docker.pkg.dev/study/images/swath@{DIGEST}",
            "shared_base_digest": SUBJECT,
            "shared_base_uri": f"registry.example/base@{SUBJECT}",
            "shared_base_source_sha256": "a" * 64,
            "tool_build_sha256": "b" * 64,
            "tool_artifact": {"kind": "release-archive", "locator": "example", "sha256": "c" * 64},
            "tool_version": "0.2.2",
            "adapter_bundle_sha256": "a" * 64,
            "harness_revision": "0.1.0",
        },
        fingerprint="f" * 64,
        job_id="c-2026-08-10-first-swath-recursive-ffffffff-r1-s1",
        submission=1,
        run_ordinal=1,
        prefix=(
            "campaigns/2026-08-10-first/example-bucket/swath/"
            "recursive-tsv.container_memory_gb-2/run-1"
        ),
    )


def config(
    *,
    network: str | None = None,
    subnetwork: str | None = None,
    evidence_object_root: str | None = None,
) -> BatchConfig:
    return BatchConfig(
        results_bucket="study-results",
        anonymous_worker_service_account="worker@study.iam.gserviceaccount.com",
        authenticated_worker_service_account="auth-worker@study.iam.gserviceaccount.com",
        aws_credential_secret=SECRET,
        network=network,
        subnetwork=subnetwork,
        provisioning="SPOT",
        zone="us-east1-b",
        evidence_object_root=evidence_object_root,
    )


def test_renderer_emits_exact_worker_request_and_provenance() -> None:
    selected = attempt()
    job = render_job(selected, config())
    container = job["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]

    assert "entrypoint" not in container
    assert container["imageUri"].endswith(f"@{DIGEST}")
    assert container["commands"] == [
        "--request-schema",
        "2",
        "--output",
        "/tmp/s3-listing-study-attempt",
        "--timeout",
        "3600",
        "--term-grace",
        "5",
        "--tool",
        "swath",
        "--tool-version",
        "0.2.2",
        "--shared-base-digest",
        SUBJECT,
        "--shared-base-uri",
        "registry.example/base@" + SUBJECT,
        "--derived-image",
        DIGEST,
        "--harness-revision",
        "0.1.0",
        "--operation",
        "list",
        "--auth",
        "anonymous",
        "--mode",
        "recursive-tsv",
        "--bucket",
        "example-bucket",
        "--region",
        "us-east-1",
        "--prefix",
        "",
        "--scope",
        "full",
        "--campaign-id",
        "2026-08-10-first",
        "--job-id",
        selected.job_id,
        "--case-id",
        selected.case.case_id,
        "--case-fingerprint",
        "c" * 64,
        "--attempt-fingerprint",
        "f" * 64,
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
        f"gs://study-results/{selected.prefix}",
    ]
    assert "uuid" not in container["commands"][-1]


def test_renderer_can_project_an_isolated_evidence_object_root() -> None:
    selected = attempt()
    selected = replace(
        selected,
        prefix=(
            "campaigns/2026-08-10-first/results/example-bucket/swath/"
            "recursive-tsv.container_memory_gb-2/run-1"
        ),
    )
    job = render_job(selected, config(evidence_object_root="snakemake/evidence/"))
    commands = job["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]["commands"]
    assert commands[-1] == (
        "gs://study-results/snakemake/evidence/2026-08-10-first/results/"
        "example-bucket/swath/recursive-tsv.container_memory_gb-2/run-1"
    )


def test_renderer_reserves_the_whole_machine_and_disables_retries() -> None:
    job = render_job(attempt(), config())
    group = job["taskGroups"][0]
    task = group["taskSpec"]
    assert (group["taskCount"], group["parallelism"]) == ("1", "1")
    assert task["maxRetryCount"] == 0
    assert task["maxRunDuration"] == "5405s"
    assert task["computeResource"] == {"cpuMilli": "2000", "memoryMib": "4096"}
    assert task["runnables"][0]["container"]["options"] == ("--memory=2g --memory-swap=2g")
    assert job["allocationPolicy"]["instances"] == [
        {
            "policy": {
                "machineType": "n4-highcpu-2",
                "provisioningModel": "SPOT",
                "bootDisk": {"type": "hyperdisk-balanced", "image": "batch-cos"},
            }
        }
    ]
    assert job["allocationPolicy"]["location"] == {"allowedLocations": ["zones/us-east1-b"]}
    assert job["logsPolicy"] == {"destination": "CLOUD_LOGGING"}


def test_anonymous_and_authenticated_jobs_have_separate_identity_and_secret() -> None:
    anonymous = render_job(attempt(), config())
    authenticated = render_job(attempt(auth="authenticated"), config())
    anonymous_task = anonymous["taskGroups"][0]["taskSpec"]
    authenticated_task = authenticated["taskGroups"][0]["taskSpec"]

    assert anonymous["allocationPolicy"]["serviceAccount"]["email"] == (
        "worker@study.iam.gserviceaccount.com"
    )
    assert "environment" not in anonymous_task
    assert authenticated["allocationPolicy"]["serviceAccount"]["email"] == (
        "auth-worker@study.iam.gserviceaccount.com"
    )
    assert authenticated_task["environment"]["secretVariables"] == {
        "S3_STUDY_AWS_CREDENTIAL": SECRET
    }


def test_container_options_are_absent_without_a_case_ceiling() -> None:
    job = render_job(attempt(ceiling=None), config())
    container = job["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]
    assert "options" not in container


def test_case_environment_fails_closed_until_the_worker_can_forward_it() -> None:
    selected = attempt(env=(("JAVA_TOOL_OPTIONS", "-XX:MaxRAMPercentage=75"),))
    job = render_job(selected, config())
    commands = job["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]["commands"]
    assert commands[-2:] == ["--case-env", "JAVA_TOOL_OPTIONS=-XX:MaxRAMPercentage=75"]


def test_committed_managed_runtime_cases_render_their_explicit_environment() -> None:
    plan = bench.Plan.load(bench.default_path("noaa-ghcn-pds"))
    cases = tuple(case for case in plan.cases if case.tool in ("swath", "s3p"))
    selected = replace(plan, cases=cases)
    root = Path(__file__).resolve().parents[1]
    images: dict[str, dict[str, object]] = {}
    for tool in selected.tools():
        registration = json.loads(
            (root / "tools" / tool / "build" / "image.json").read_text(encoding="utf-8")
        )
        registration.update(
            {
                "derived_image": DIGEST,
                "image_uri": f"us-east1-docker.pkg.dev/study/images/{tool}@{DIGEST}",
                "shared_base_digest": SUBJECT,
                "shared_base_uri": f"registry.example/base@{SUBJECT}",
                "harness_revision": "revision",
            }
        )
        images[tool] = registration
    generated = attempts_for(selected, campaign="2026-08-10-first", images=images)

    for selected_attempt in generated:
        job = render_job(selected_attempt, config())
        commands = job["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]["commands"]
        expected = [
            item for pair in selected_attempt.case.env for item in ("--case-env", "=".join(pair))
        ]
        assert commands[-len(expected) :] == expected


def test_image_digest_mismatch_is_refused() -> None:
    selected = attempt()
    bad = replace(selected, image={**selected.image, "image_uri": "example/image:latest"})
    with pytest.raises(CampaignError, match="must be pinned"):
        render_job(bad, config())


def test_authentication_identities_must_be_distinct() -> None:
    duplicate = BatchConfig(
        results_bucket="study-results",
        anonymous_worker_service_account="worker@study.iam.gserviceaccount.com",
        authenticated_worker_service_account="worker@study.iam.gserviceaccount.com",
        aws_credential_secret=SECRET,
    )
    with pytest.raises(CampaignError, match="must differ"):
        render_job(attempt(), duplicate)


def test_post_attempt_allowance_must_be_positive() -> None:
    invalid = replace(config(), post_attempt_allowance_s=0)
    with pytest.raises(CampaignError, match="allowance must be positive"):
        render_job(attempt(), invalid)


def test_renderer_refuses_environment_outside_managed_runtime_allowlist() -> None:
    selected = attempt(env=(("LD_PRELOAD", "/tmp/injected.so"),))
    with pytest.raises(CampaignError, match="key must be one of"):
        render_job(selected, config())


def test_network_and_subnetwork_are_all_or_none() -> None:
    with pytest.raises(CampaignError, match="supplied together"):
        render_job(attempt(), config(network="projects/study/global/networks/study"))

    job = render_job(
        attempt(),
        config(
            network="projects/study/global/networks/study",
            subnetwork="projects/study/regions/us-east1/subnetworks/study",
        ),
    )
    assert job["allocationPolicy"]["network"]["networkInterfaces"] == [
        {
            "network": "projects/study/global/networks/study",
            "subnetwork": "projects/study/regions/us-east1/subnetworks/study",
        }
    ]
