"""Stateless campaign observation and summary-only GCS reconciliation."""

from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from google.api_core.exceptions import GoogleAPIError
from google.auth.exceptions import DefaultCredentialsError
from temporalio.client import Client as TemporalClient
from temporalio.client import WorkflowExecutionStatus
from temporalio.converter import DataConverter

from s3_listing_study.manager import cli as manager_cli
from s3_listing_study.manager.campaign import cli as campaign_cli
from s3_listing_study.manager.campaign import report as campaign_report
from s3_listing_study.temporal import TASK_QUEUE
from s3_listing_study.temporal.models import CaseControllerProgress

CAMPAIGN = "2026-08-11-report"
BUCKET = "study-results"
SCOPE = campaign_cli.TemporalScope("temporal.example.invalid:7233", "s3-study")
UUIDS = (
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
)


class FakeBlob:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.size = len(content)
        self.generation = 1
        self.downloads = 0
        self.uploads: list[tuple[bytes, dict[str, Any]]] = []

    def download_as_bytes(self, **_kwargs: Any) -> bytes:
        self.downloads += 1
        return self.content

    def upload_from_string(self, content: bytes, **kwargs: Any) -> None:
        self.uploads.append((content, kwargs))
        self.content = content
        self.size = len(content)


class FakeBucket:
    name = BUCKET

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = {name: FakeBlob(content) for name, content in objects.items()}
        self.listed: list[tuple[str, str]] = []
        self.requested: list[str] = []

    def get_blob(self, name: str) -> FakeBlob | None:
        self.requested.append(name)
        return self.objects.get(name)

    def blob(self, name: str) -> FakeBlob:
        return self.objects.setdefault(name, FakeBlob(b""))

    def list_blobs(self, *, prefix: str, delimiter: str) -> Any:
        self.listed.append((prefix, delimiter))
        prefixes = {
            prefix + name.removeprefix(prefix).split("/", 1)[0] + "/"
            for name in self.objects
            if name.startswith(prefix) and "/" in name.removeprefix(prefix)
        }
        return SimpleNamespace(pages=(SimpleNamespace(prefixes=prefixes),))


class FakeDescription:
    id = CAMPAIGN
    run_id = "parent-run"
    namespace = SCOPE.namespace
    workflow_type = "CampaignWorkflow"
    task_queue = TASK_QUEUE
    status = WorkflowExecutionStatus.RUNNING

    def __init__(self) -> None:
        self.digest = DIGEST

    async def memo_value(self, key: str, *, type_hint: type[str]) -> str:
        assert (key, type_hint) == ("campaign_digest", str)
        return self.digest


class FakeParentHandle:
    def __init__(self, client: FakeTemporalClient) -> None:
        self.client = client

    async def describe(self) -> FakeDescription:
        return self.client.description

    async def query(self, *_args: Any, **_kwargs: Any) -> list[CaseControllerProgress]:
        return self.client.progress


class FakeChildHandle:
    def __init__(self, pending: list[Any]) -> None:
        self.pending = pending

    async def describe(self) -> Any:
        return SimpleNamespace(raw_description=SimpleNamespace(pending_activities=self.pending))


class FakeTemporalClient:
    def __init__(self, progress: list[CaseControllerProgress], pending: list[Any]) -> None:
        self.progress = progress
        self.pending = pending
        self.description = FakeDescription()
        self.data_converter = DataConverter.default
        self.handles: list[tuple[str, str | None]] = []

    def get_workflow_handle(self, workflow_id: str, *, run_id: str | None = None) -> Any:
        self.handles.append((workflow_id, run_id))
        if workflow_id == CAMPAIGN:
            return FakeParentHandle(self)
        return FakeChildHandle(self.pending)


def manifest() -> tuple[bytes, list[dict[str, Any]], dict[str, Any]]:
    image = {
        "derived_image": "sha256:" + "f" * 64,
        "image_uri": "registry.example/derived@sha256:" + "f" * 64,
        "shared_base_digest": "sha256:" + "b" * 64,
        "shared_base_uri": "registry.example/base@sha256:" + "b" * 64,
        "shared_base_source_sha256": "c" * 64,
        "tool_build_sha256": "e" * 64,
        "tool_image_digest": "sha256:" + "1" * 64,
        "tool_image_uri": "registry.example/tool@sha256:" + "1" * 64,
        "selection_sha256": "2" * 64,
        "tool_artifact": {"kind": "url", "locator": "https://example.invalid", "sha256": "3" * 64},
        "tool_version": "2.31.0",
        "adapter_bundle_sha256": "a" * 64,
        "harness_revision": "4" * 40,
    }
    attempts: list[dict[str, Any]] = []
    for index in range(3):
        case_id = f"case-{index + 1}"
        attempts.append(
            {
                "job_id": f"job-{index + 1}",
                "submission": 1,
                "run_ordinal": 1,
                "bucket": "example-bucket",
                "region": "us-east-1",
                "tool": "aws-cli",
                "case_id": case_id,
                "mode": "s3api-v2-text",
                "auth": "anonymous",
                "case_fingerprint": chr(ord("a") + index) * 64,
                "derived_image": image["derived_image"],
                "fingerprint": chr(ord("d") + index) * 64,
                "attempt_fingerprint": chr(ord("d") + index) * 64,
                "resources": {
                    "machine_type": "n4-highcpu-2",
                    "vcpus": 2,
                    "memory_gb": 4,
                    "container_memory_gb": 2,
                },
                "env": [],
                "reps": 1,
                "timeout_s": 3600,
                "prefix": (f"campaigns/{CAMPAIGN}/results/example-bucket/aws-cli/{case_id}/run-1"),
            }
        )
    document = {
        "schema_version": 3,
        "campaign": CAMPAIGN,
        "results_bucket": BUCKET,
        "attempt_fingerprint_version": 3,
        "provisioning": "SPOT",
        "zone": None,
        "plans": [],
        "images": {"aws-cli": image},
        "attempts": attempts,
    }
    return campaign_cli._canonical_json(document), attempts, image


def temporal_input(manifest_bytes: bytes, attempts: list[dict[str, Any]]) -> bytes:
    document = {
        "schema_version": 1,
        "campaign": CAMPAIGN,
        "campaign_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "workflow_type": "CampaignWorkflow",
        "task_queue": TASK_QUEUE,
        "temporal_scope": SCOPE.document(),
        "cases": [
            {
                "project": "study",
                "location": "us-east1",
                "job_id": attempt["job_id"],
                "job": {},
                "controller_timeout_s": 9000,
            }
            for attempt in attempts
        ],
    }
    return campaign_cli._canonical_json(document)


def frozen_objects(manifest_bytes: bytes, attempts: list[dict[str, Any]]) -> dict[str, bytes]:
    return {
        f"campaigns/{CAMPAIGN}/campaign.json": manifest_bytes,
        f"campaigns/{CAMPAIGN}/inputs/temporal.json": temporal_input(manifest_bytes, attempts),
    }


_BASE_MANIFEST, _BASE_ATTEMPTS, _BASE_IMAGE = manifest()
DIGEST = hashlib.sha256(temporal_input(_BASE_MANIFEST, _BASE_ATTEMPTS)).hexdigest()


def result(attempt: dict[str, Any], image: dict[str, Any], leaf: str, status: str) -> bytes:
    prefix = attempt["prefix"]
    artifact_uri = f"gs://{BUCKET}/{prefix}/{leaf}"
    outcome = {
        "completed": {
            "status": "completed",
            "exit_code": 0,
            "signal": None,
            "timed_out": False,
            "cleanup": {
                "state": "not_needed",
                "term_sent": False,
                "kill_sent": False,
                "process_group_empty": True,
                "escaped_descendants": [],
            },
        },
        "failed": {
            "status": "failed",
            "exit_code": 7,
            "signal": None,
            "timed_out": False,
            "cleanup": {
                "state": "not_needed",
                "term_sent": False,
                "kill_sent": False,
                "process_group_empty": True,
                "escaped_descendants": [],
            },
        },
        "signaled": {
            "status": "signaled",
            "exit_code": None,
            "signal": 15,
            "timed_out": False,
            "cleanup": {
                "state": "not_needed",
                "term_sent": False,
                "kill_sent": False,
                "process_group_empty": True,
                "escaped_descendants": [],
            },
        },
        "timed_out": {
            "status": "timed_out",
            "exit_code": None,
            "signal": 9,
            "timed_out": True,
            "cleanup": {
                "state": "killed",
                "term_sent": True,
                "kill_sent": True,
                "process_group_empty": True,
                "escaped_descendants": [],
            },
        },
        "harness_error": {
            "status": "harness_error",
            "exit_code": 0,
            "signal": None,
            "timed_out": False,
            "cleanup": {
                "state": "failed",
                "term_sent": False,
                "kill_sent": False,
                "process_group_empty": True,
                "escaped_descendants": [4242],
            },
        },
    }[status]
    summary = {
        "schema_version": 2,
        "status": "counted" if status == "completed" else "skipped",
        "row_count": 789 if status == "completed" else None,
        "reason": None if status == "completed" else f"tool_outcome_{status}",
        "error": None,
        "adapter_bundle_sha256": image["adapter_bundle_sha256"],
        "duckdb_version": "1.5.5",
        "interpreter": {
            "architecture": "x86_64",
            "implementation": "CPython",
            "libc": "glibc-2.41",
            "package_manifest_sha256": "5" * 64,
            "running_version": "3.13.5",
            "source": "image-marker",
        },
    }
    document = {
        "schema_version": 3,
        "attempt_id": leaf,
        "tool": {"name": attempt["tool"], "version": image["tool_version"]},
        "images": {
            "derived": attempt["derived_image"],
            "tool": {"digest": image["tool_image_digest"], "uri": image["tool_image_uri"]},
            "shared_base": {
                "digest": image["shared_base_digest"],
                "uri": image["shared_base_uri"],
            },
        },
        "build_inputs": {
            "shared_base": {"source_sha256": image["shared_base_source_sha256"]},
            "tool": {
                "build_sha256": image["tool_build_sha256"],
                "artifact": image["tool_artifact"],
                "selection_sha256": image["selection_sha256"],
            },
        },
        "harness_revision": image["harness_revision"],
        "adapter_bundle_sha256": image["adapter_bundle_sha256"],
        "campaign": {
            "campaign_id": CAMPAIGN,
            "job_id": attempt["job_id"],
            "case_id": attempt["case_id"],
            "case_fingerprint": attempt["case_fingerprint"],
            "attempt_fingerprint": attempt["attempt_fingerprint"],
            "run_ordinal": attempt["run_ordinal"],
            "submission_number": attempt["submission"],
            "declared_resources": attempt["resources"],
        },
        "artifact_uri": artifact_uri,
        "result_uri": f"{artifact_uri}/result.json",
        "outcome": outcome,
        "timing": {"elapsed_ns": 123},
        "resources": {"rusage_children_max_child_peak_rss_kb": 456},
        "invocation": {"argv": ["list", "café"]},
        "logical_request": {
            "schema_version": 1,
            "operation": "list",
            "mode": attempt["mode"],
            "bucket": attempt["bucket"],
            "region": attempt["region"],
            "prefix": "",
            "authentication": attempt["auth"],
            "concurrency": None,
        },
        "target": {
            "mode": attempt["mode"],
            "bucket": attempt["bucket"],
            "region": attempt["region"],
            "prefix": "",
            "scope": "full",
        },
        "summary": summary,
        "secret_scan": {
            "status": "clean",
            "streams": {"stdout": "clean", "stderr": "clean"},
        },
    }
    return campaign_report._worker_json(document)


def resource_name(job_id: str) -> str:
    return f"projects/study/locations/us-east1/jobs/{job_id}"


async def retry_pending_activity() -> Any:
    payloads = await DataConverter.default.encode(
        [{"job_name": "projects/study/locations/us-east1/jobs/job-1", "state": "RUNNING"}]
    )
    return SimpleNamespace(
        activity_type=SimpleNamespace(name="run_batch_job"),
        attempt=2,
        heartbeat_details=SimpleNamespace(payloads=payloads),
    )


def owner() -> campaign_cli.TemporalOwner:
    return campaign_cli.TemporalOwner(CAMPAIGN, DIGEST, SCOPE, CAMPAIGN, "parent-run")


def test_retry_snapshot_does_not_list_or_read_nonterminal_evidence() -> None:
    manifest_bytes, attempts, _image = manifest()
    bucket = FakeBucket(frozen_objects(manifest_bytes, attempts))
    progress = [
        CaseControllerProgress(attempt["job_id"], f"child-{index}", "running", None, None)
        for index, attempt in enumerate(attempts)
    ]
    client = FakeTemporalClient(progress, [asyncio.run(retry_pending_activity())])
    report = asyncio.run(
        campaign_report.observe_once(
            temporal_client=cast(TemporalClient, client),
            bucket=bucket,
            campaign=CAMPAIGN,
            owner=owner(),
            scope=SCOPE,
        )
    )
    assert not report["controller_complete"]
    assert not report["provider_settled"]
    assert not report["report_final"]
    assert not report["operational_success"]
    assert report["cases"][0]["controller"] == {
        "phase": "retrying",
        "child_workflow_id": "job-1",
        "child_run_id": "child-0",
        "activity_attempt": 2,
        "last_heartbeat": {
            "job_name": "projects/study/locations/us-east1/jobs/job-1",
            "state": "RUNNING",
        },
        "terminal": None,
    }
    assert bucket.listed == []
    assert all(case["evidence"]["state"] == "pending" for case in report["cases"])


def test_wait_observations_download_bound_manifest_once() -> None:
    manifest_bytes, attempts, _image = manifest()
    manifest_name = f"campaigns/{CAMPAIGN}/campaign.json"
    bucket = FakeBucket(frozen_objects(manifest_bytes, attempts))
    progress = [
        CaseControllerProgress(attempt["job_id"], None, "pending", None, None)
        for attempt in attempts
    ]
    client = FakeTemporalClient(progress, [])
    manifest_cache = campaign_report._load_manifest(bucket, CAMPAIGN)
    for _ in range(2):
        report = asyncio.run(
            campaign_report.observe_once(
                temporal_client=cast(TemporalClient, client),
                bucket=bucket,
                campaign=CAMPAIGN,
                owner=owner(),
                scope=SCOPE,
                manifest_cache=manifest_cache,
            )
        )
        assert not report["report_final"]
    assert bucket.objects[manifest_name].downloads == 1


def test_manifest_cache_refuses_different_bucket_or_campaign() -> None:
    manifest_bytes, attempts, _image = manifest()
    bucket = FakeBucket(frozen_objects(manifest_bytes, attempts))
    manifest_cache = campaign_report._load_manifest(bucket, CAMPAIGN)
    progress = [
        CaseControllerProgress(attempt["job_id"], None, "pending", None, None)
        for attempt in attempts
    ]
    other_bucket = FakeBucket({})
    other_bucket.name = "other-results"
    with pytest.raises(campaign_report.ReportError, match="cache does not match"):
        asyncio.run(
            campaign_report.observe_once(
                temporal_client=cast(TemporalClient, FakeTemporalClient(progress, [])),
                bucket=other_bucket,
                campaign=CAMPAIGN,
                owner=owner(),
                scope=SCOPE,
                manifest_cache=manifest_cache,
            )
        )
    with pytest.raises(campaign_report.ReportError, match="cache does not match"):
        asyncio.run(
            campaign_report.observe_once(
                temporal_client=cast(TemporalClient, FakeTemporalClient(progress, [])),
                bucket=bucket,
                campaign="2026-08-11-other",
                owner=owner(),
                scope=SCOPE,
                manifest_cache=manifest_cache,
            )
        )


def test_final_report_records_single_missing_and_duplicate_without_raw_reads() -> None:
    manifest_bytes, attempts, image = manifest()
    objects = frozen_objects(manifest_bytes, attempts)
    first_result = f"{attempts[0]['prefix']}/{UUIDS[0]}/result.json"
    objects[first_result] = result(attempts[0], image, UUIDS[0], "completed")
    objects[f"{attempts[2]['prefix']}/{UUIDS[1]}/result.json"] = result(
        attempts[2], image, UUIDS[1], "failed"
    )
    objects[f"{attempts[2]['prefix']}/{UUIDS[2]}/stdout.raw.gz"] = b"raw-not-read"
    bucket = FakeBucket(objects)
    progress = [
        CaseControllerProgress(
            "job-1",
            "child-1",
            "terminal",
            "SUCCEEDED",
            None,
            resource_name("job-1"),
            True,
        ),
        CaseControllerProgress(
            "job-2", None, "terminal", "NOT_CREATED", "PermanentGoogleError", None, True
        ),
        CaseControllerProgress(
            "job-3",
            "child-3",
            "terminal",
            "FAILED",
            None,
            resource_name("job-3"),
            True,
        ),
    ]
    client = FakeTemporalClient(progress, [])
    client.description.status = WorkflowExecutionStatus.COMPLETED
    evidence_cache: dict[str, campaign_report.EvidenceSnapshot] = {}
    report = asyncio.run(
        campaign_report.observe_once(
            temporal_client=cast(TemporalClient, client),
            bucket=bucket,
            campaign=CAMPAIGN,
            owner=owner(),
            scope=SCOPE,
            evidence_cache=evidence_cache,
        )
    )
    assert report["schema_version"] == 3
    assert set(report) == {
        "schema_version",
        "campaign",
        "campaign_manifest_sha256",
        "campaign_digest",
        "controller_input_sha256",
        "engine",
        "controller_complete",
        "provider_settled",
        "report_final",
        "operational_success",
        "aggregate",
        "cases",
    }
    assert (
        report["controller_input_sha256"]
        == hashlib.sha256(temporal_input(manifest_bytes, attempts)).hexdigest()
    )
    assert report["engine"] == {
        "name": "temporal",
        "execution_id": CAMPAIGN,
        "run_id": "parent-run",
        "status": "completed",
    }
    assert set(report["engine"]) == {"name", "execution_id", "run_id", "status"}
    assert report["controller_complete"]
    assert report["provider_settled"]
    assert report["report_final"]
    assert not report["operational_success"]
    assert report["campaign_manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert report["campaign_digest"] == DIGEST
    assert [case["evidence"]["state"] for case in report["cases"]] == [
        "recorded",
        "missing",
        "duplicate",
    ]
    assert report["cases"][0]["subject"]["status"] == "completed"
    assert report["cases"][0]["metrics"] == {
        "elapsed_ns": 123,
        "rusage_children_max_child_peak_rss_kb": 456,
        "row_count": 789,
    }
    assert report["cases"][1]["subject"] is None
    assert report["cases"][2]["controller"]["terminal"]["provider_state"] == "FAILED"
    assert report["cases"][2]["controller"]["terminal"]["provider_resource_name"] == (
        resource_name("job-3")
    )
    assert report["cases"][2]["subject"] is None
    assert len(report["cases"][2]["evidence"]["leaves"]) == 2
    assert report["cases"][2]["evidence"]["canonical_result_uri"] is None
    assert report["aggregate"]["evidence"] == {
        "pending": 0,
        "missing": 1,
        "recorded": 1,
        "duplicate": 1,
        "invalid": 0,
        "unsealed": 0,
    }
    assert report["aggregate"]["provider"] == {
        "SUCCEEDED": 1,
        "FAILED": 1,
        "NOT_CREATED": 1,
        "unavailable": 0,
    }
    assert not any(name.endswith("stdout.raw.gz") for name in bucket.requested)
    assert bucket.objects[first_result].downloads == 1
    assert client.handles == [(CAMPAIGN, "parent-run")]
    asyncio.run(
        campaign_report.observe_once(
            temporal_client=cast(TemporalClient, client),
            bucket=bucket,
            campaign=CAMPAIGN,
            owner=owner(),
            scope=SCOPE,
            evidence_cache=evidence_cache,
        )
    )
    assert len(bucket.listed) == 3
    assert bucket.objects[first_result].downloads == 1
    assert client.handles == [(CAMPAIGN, "parent-run"), (CAMPAIGN, "parent-run")]


def test_successful_second_submission_is_final_and_operationally_successful() -> None:
    base_manifest, attempts, image = manifest()
    document = json.loads(base_manifest)
    document["attempts"][0]["job_id"] = "job-1-s1"
    manifest_bytes = campaign_cli._canonical_json(document)
    attempts = document["attempts"]
    temporal_bytes = temporal_input(manifest_bytes, attempts)
    digest = hashlib.sha256(temporal_bytes).hexdigest()
    selected_owner = campaign_cli.TemporalOwner(CAMPAIGN, digest, SCOPE, CAMPAIGN, "parent-run")
    retry_attempt = dict(attempts[0])
    retry_attempt["job_id"] = "job-1-s2"
    retry_attempt["submission"] = 2
    historical_uuid = "44444444-4444-4444-8444-444444444444"
    duplicate_uuid = "55555555-5555-4555-8555-555555555555"
    objects = {
        f"campaigns/{CAMPAIGN}/campaign.json": manifest_bytes,
        f"campaigns/{CAMPAIGN}/inputs/temporal.json": temporal_bytes,
        f"{attempts[0]['prefix']}/{UUIDS[0]}/result.json": result(
            retry_attempt, image, UUIDS[0], "completed"
        ),
        f"{attempts[0]['prefix']}/{historical_uuid}/result.json": result(
            attempts[0], image, historical_uuid, "failed"
        ),
        f"{attempts[1]['prefix']}/{UUIDS[1]}/result.json": result(
            attempts[1], image, UUIDS[1], "completed"
        ),
        f"{attempts[2]['prefix']}/{UUIDS[2]}/result.json": result(
            attempts[2], image, UUIDS[2], "completed"
        ),
    }
    progress = [
        CaseControllerProgress(
            "job-1-s1",
            "child-retry",
            "terminal",
            "SUCCEEDED",
            None,
            resource_name("job-1-s2"),
            True,
            2,
            "job-1-s2",
        ),
        *[
            CaseControllerProgress(
                item["job_id"],
                f"child-{index}",
                "terminal",
                "SUCCEEDED",
                None,
                resource_name(item["job_id"]),
                True,
                1,
                item["job_id"],
            )
            for index, item in enumerate(attempts[1:], start=2)
        ],
    ]
    client = FakeTemporalClient(progress, [])
    client.description.status = WorkflowExecutionStatus.COMPLETED
    client.description.digest = digest
    bucket = FakeBucket(objects)
    report = asyncio.run(
        campaign_report.observe_once(
            temporal_client=cast(TemporalClient, client),
            bucket=bucket,
            campaign=CAMPAIGN,
            owner=selected_owner,
            scope=SCOPE,
        )
    )
    assert report["controller_complete"]
    assert report["provider_settled"]
    assert report["report_final"]
    assert report["operational_success"]
    assert report["cases"][0]["job_id"] == "job-1-s1"
    assert report["cases"][0]["current_job_id"] == "job-1-s2"
    assert report["cases"][0]["current_submission"] == 2
    assert report["cases"][0]["evidence"]["state"] == "recorded"
    assert [leaf["state"] for leaf in report["cases"][0]["evidence"]["leaves"]] == [
        "recorded",
        "historical",
    ]
    historical = report["cases"][0]["evidence"]["leaves"][1]
    assert historical["submission_number"] == 1
    assert historical["job_id"] == "job-1-s1"

    duplicate_name = f"{attempts[0]['prefix']}/{duplicate_uuid}/result.json"
    bucket.objects[duplicate_name] = FakeBlob(
        result(retry_attempt, image, duplicate_uuid, "completed")
    )
    case = campaign_report._parse_manifest(
        manifest_bytes, campaign=CAMPAIGN, results_bucket=BUCKET
    )[0]
    duplicate = campaign_report._evidence(
        bucket,
        case,
        terminal=True,
        current_job_id="job-1-s2",
        current_submission=2,
    )
    assert duplicate.state == "duplicate"
    assert [leaf["state"] for leaf in duplicate.leaves].count("recorded") == 2
    assert [leaf["state"] for leaf in duplicate.leaves].count("historical") == 1


def test_terminal_controller_failure_never_finalizes_unsettled_provider() -> None:
    manifest_bytes, attempts, _image = manifest()
    bucket = FakeBucket(frozen_objects(manifest_bytes, attempts))
    progress = [
        CaseControllerProgress(attempt["job_id"], None, "terminal", None, "failed")
        for attempt in attempts
    ]
    client = FakeTemporalClient(progress, [])
    report = asyncio.run(
        campaign_report.observe_once(
            temporal_client=cast(TemporalClient, client),
            bucket=bucket,
            campaign=CAMPAIGN,
            owner=owner(),
            scope=SCOPE,
        )
    )
    assert report["engine"]["status"] == "running"
    assert not report["controller_complete"]
    assert not report["provider_settled"]
    assert not report["report_final"]
    assert bucket.listed == []
    client.description.status = WorkflowExecutionStatus.COMPLETED
    unsettled = asyncio.run(
        campaign_report.observe_once(
            temporal_client=cast(TemporalClient, client),
            bucket=bucket,
            campaign=CAMPAIGN,
            owner=owner(),
            scope=SCOPE,
        )
    )
    assert unsettled["controller_complete"]
    assert not unsettled["provider_settled"]
    assert not unsettled["report_final"]
    assert all(case["evidence"]["state"] == "pending" for case in unsettled["cases"])
    assert bucket.listed == []


@pytest.mark.parametrize(
    "hostile",
    [
        CaseControllerProgress(
            "job-1",
            "child-1",
            "terminal",
            "SUCCEEDED",
            "PermanentGoogleError",
            resource_name("job-1"),
            True,
        ),
        CaseControllerProgress("job-1", "child-1", "terminal", "NOT_CREATED", None, None, True),
        CaseControllerProgress(
            "job-1", "child-1", "terminal", "NOT_CREATED", "ActivityError", None, True
        ),
    ],
)
def test_observer_rejects_invalid_settled_provider_failure_combinations(
    hostile: CaseControllerProgress,
) -> None:
    manifest_bytes, attempts, _image = manifest()
    cases = campaign_report._parse_manifest(
        manifest_bytes, campaign=CAMPAIGN, results_bucket=BUCKET
    )
    temporal_cases = tuple(
        campaign_report.TemporalCase(
            "study", "us-east1", item["job_id"], resource_name(item["job_id"])
        )
        for item in attempts
    )
    progress = [
        hostile,
        *[
            CaseControllerProgress(
                item["job_id"],
                f"child-{index}",
                "terminal",
                "SUCCEEDED",
                None,
                resource_name(item["job_id"]),
                True,
            )
            for index, item in enumerate(attempts[1:], start=2)
        ],
    ]
    with pytest.raises(campaign_report.ReportError, match=r"invalid .*outcome|no-effect proof"):
        campaign_report._validate_progress(progress, cases, temporal_cases)


def test_one_mismatched_or_unsealed_leaf_is_not_recorded() -> None:
    manifest_bytes, attempts, image = manifest()
    mismatched = json.loads(result(attempts[0], image, UUIDS[0], "completed"))
    mismatched["campaign"]["job_id"] = "foreign"
    objects = {
        **frozen_objects(manifest_bytes, attempts),
        f"{attempts[0]['prefix']}/{UUIDS[0]}/result.json": campaign_report._worker_json(mismatched),
        f"{attempts[1]['prefix']}/{UUIDS[1]}/stdout.raw.gz": b"unsealed",
    }
    bucket = FakeBucket(objects)
    progress = [
        CaseControllerProgress(
            attempt["job_id"],
            f"child-{index}",
            "terminal",
            "SUCCEEDED",
            None,
            resource_name(attempt["job_id"]),
            True,
        )
        for index, attempt in enumerate(attempts)
    ]
    report = asyncio.run(
        campaign_report.observe_once(
            temporal_client=cast(TemporalClient, FakeTemporalClient(progress, [])),
            bucket=bucket,
            campaign=CAMPAIGN,
            owner=owner(),
            scope=SCOPE,
        )
    )
    assert [case["evidence"]["state"] for case in report["cases"]] == [
        "invalid",
        "unsealed",
        "missing",
    ]
    assert report["cases"][0]["evidence"]["leaves"][0]["reason"] == ("invalid_result_identity")
    assert report["cases"][1]["evidence"]["leaves"][0]["reason"] == ("missing_result_commit")


def test_invalid_evidence_reason_codes_are_stable_and_safe() -> None:
    manifest_bytes, attempts, image = manifest()
    case = campaign_report._parse_manifest(
        manifest_bytes, campaign=CAMPAIGN, results_bucket=BUCKET
    )[0]
    invalid_uuid, _ = campaign_report._validated_result(FakeBucket({}), case, "not-a-uuid")
    assert invalid_uuid["reason"] == "invalid_attempt_id"

    result_name = f"{attempts[0]['prefix']}/{UUIDS[0]}/result.json"
    invalid_json, _ = campaign_report._validated_result(
        FakeBucket({result_name: b"not-json-and-never-echoed"}), case, UUIDS[0]
    )
    assert invalid_json["reason"] == "invalid_result_json"

    invalid_metrics_record = json.loads(result(attempts[0], image, UUIDS[0], "completed"))
    invalid_metrics_record["timing"]["elapsed_ns"] = "not-an-integer"
    invalid_metrics, _ = campaign_report._validated_result(
        FakeBucket({result_name: campaign_report._worker_json(invalid_metrics_record)}),
        case,
        UUIDS[0],
    )
    assert invalid_metrics["reason"] == "invalid_result_metrics"
    assert "not-json" not in json.dumps((invalid_uuid, invalid_json, invalid_metrics))


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda document: document["logical_request"].update(mode="wrong-mode"),
            "invalid_result_request",
        ),
        (
            lambda document: document.update(harness_revision="0" * 40),
            "invalid_result_provenance",
        ),
        (
            lambda document: document["images"]["tool"].update(digest="sha256:" + "9" * 64),
            "invalid_result_provenance",
        ),
        (
            lambda document: document["outcome"].update(exit_code=7),
            "invalid_result_outcome",
        ),
        (
            lambda document: (
                document["outcome"].update(status="failed", exit_code=7),
                document["summary"].update(status="counted", row_count=1),
            ),
            "invalid_result_summary",
        ),
        (
            lambda document: document["outcome"]["cleanup"].update(state="terminated"),
            "invalid_result_cleanup",
        ),
        (
            lambda document: document["summary"].update(reason="tool_outcome_failed"),
            "invalid_result_summary",
        ),
        (
            lambda document: document["summary"].update(duckdb_version=None),
            "invalid_result_summary",
        ),
    ],
)
def test_hostile_request_provenance_and_outcome_mismatches_are_invalid(
    mutate: Any, reason: str
) -> None:
    manifest_bytes, attempts, image = manifest()
    case = campaign_report._parse_manifest(
        manifest_bytes, campaign=CAMPAIGN, results_bucket=BUCKET
    )[0]
    document = json.loads(result(attempts[0], image, UUIDS[0], "completed"))
    mutate(document)
    name = f"{attempts[0]['prefix']}/{UUIDS[0]}/result.json"
    leaf, normalized = campaign_report._validated_result(
        FakeBucket({name: campaign_report._worker_json(document)}), case, UUIDS[0]
    )
    assert leaf["reason"] == reason
    assert normalized is None


def test_worker_non_ascii_encoding_and_completed_summary_error_are_valid() -> None:
    manifest_bytes, attempts, image = manifest()
    case = campaign_report._parse_manifest(
        manifest_bytes, campaign=CAMPAIGN, results_bucket=BUCKET
    )[0]
    document = json.loads(result(attempts[0], image, UUIDS[0], "completed"))
    assert document["invocation"]["argv"][-1] == "café"
    document["summary"].update(
        status="error",
        row_count=None,
        reason=None,
        error={"code": "row_count_failed", "type": "RuntimeError"},
    )
    name = f"{attempts[0]['prefix']}/{UUIDS[0]}/result.json"
    leaf, normalized = campaign_report._validated_result(
        FakeBucket({name: campaign_report._worker_json(document)}), case, UUIDS[0]
    )
    assert leaf["state"] == "recorded"
    assert normalized is not None
    assert normalized["metrics"]["row_count"] is None


def test_temporal_input_binds_owner_manifest_scope_and_order() -> None:
    manifest_bytes, attempts, _image = manifest()
    bucket = FakeBucket(frozen_objects(manifest_bytes, attempts))
    manifest_cache = campaign_report._load_manifest(bucket, CAMPAIGN)
    temporal_bytes = temporal_input(manifest_bytes, attempts)
    exact_owner = campaign_cli.TemporalOwner(
        CAMPAIGN, hashlib.sha256(temporal_bytes).hexdigest(), SCOPE, CAMPAIGN, "run"
    )
    loaded = campaign_report._load_temporal_input(
        bucket, CAMPAIGN, manifest_cache, exact_owner, SCOPE
    )
    assert loaded.campaign_manifest_sha256 == manifest_cache.sha256
    assert [case.job_id for case in loaded.cases] == [item["job_id"] for item in attempts]
    assert loaded.cases[0].resource_name == resource_name("job-1")

    tampered = json.loads(temporal_bytes)
    tampered["cases"] = list(reversed(tampered["cases"]))
    tampered_bytes = campaign_cli._canonical_json(tampered)
    bucket.objects[f"campaigns/{CAMPAIGN}/inputs/temporal.json"] = FakeBlob(tampered_bytes)
    tampered_owner = campaign_cli.TemporalOwner(
        CAMPAIGN, hashlib.sha256(tampered_bytes).hexdigest(), SCOPE, CAMPAIGN, "run"
    )
    with pytest.raises(campaign_report.ReportError, match="job order"):
        campaign_report._load_temporal_input(
            bucket, CAMPAIGN, manifest_cache, tampered_owner, SCOPE
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda document: document.update(attempt_fingerprint_version=2),
        lambda document: document["images"]["aws-cli"].pop("selection_sha256"),
        lambda document: document["images"]["aws-cli"].pop("tool_image_digest"),
    ],
)
def test_manifest_requires_full_schema3_image_binding(mutate: Any) -> None:
    manifest_bytes, _attempts, _image = manifest()
    document = json.loads(manifest_bytes)
    mutate(document)
    with pytest.raises(campaign_report.ReportError):
        campaign_report._parse_manifest(
            campaign_cli._canonical_json(document),
            campaign=CAMPAIGN,
            results_bucket=BUCKET,
        )


def test_activity_describes_and_execution_leaves_are_bounded() -> None:
    class ConcurrencyClient:
        def __init__(self) -> None:
            self.active = 0
            self.maximum = 0

        def get_workflow_handle(self, *_args: Any, **_kwargs: Any) -> Any:
            client = self

            class Handle:
                async def describe(self) -> Any:
                    client.active += 1
                    client.maximum = max(client.maximum, client.active)
                    await asyncio.sleep(0.001)
                    client.active -= 1
                    return SimpleNamespace(raw_description=SimpleNamespace(pending_activities=[]))

            return Handle()

    progress = [
        CaseControllerProgress(f"job-{index}", f"run-{index}", "running", None, None)
        for index in range(40)
    ]
    client = ConcurrencyClient()
    asyncio.run(campaign_report._activity_views(cast(TemporalClient, client), progress))
    assert client.maximum == campaign_report.MAX_CONCURRENT_CHILD_DESCRIBES

    manifest_bytes, _attempts, _image = manifest()
    case = campaign_report._parse_manifest(
        manifest_bytes, campaign=CAMPAIGN, results_bucket=BUCKET
    )[0]
    objects = {
        f"{case.prefix}/{index:04d}/stdout.raw.gz": b"not-read"
        for index in range(campaign_report.MAX_EXECUTION_LEAVES_PER_RUN + 1)
    }
    bucket = FakeBucket(objects)
    with pytest.raises(campaign_report.ReportError, match="execution leaves"):
        campaign_report._evidence(bucket, case, terminal=True)
    assert bucket.requested == []


@pytest.mark.parametrize("interval", [float("nan"), float("inf"), 0.0, -1.0])
def test_poll_interval_must_be_finite_and_positive(interval: float) -> None:
    args = campaign_report.build_parser().parse_args(
        [
            "--campaign",
            CAMPAIGN,
            "--results-bucket",
            BUCKET,
            "--poll-interval-s",
            str(interval),
        ]
    )
    with pytest.raises(campaign_report.ReportError, match="finite and positive"):
        asyncio.run(campaign_report._run_report(args))


def test_report_rejects_duplicate_poll_interval_option(capsys: Any) -> None:
    with pytest.raises(SystemExit):
        campaign_report.report_campaign_main(
            [
                "--campaign",
                CAMPAIGN,
                "--results-bucket",
                BUCKET,
                "--poll-interval-s",
                "1",
                "--poll-interval-s",
                "2",
            ]
        )
    assert "--poll-interval-s may only be specified once" in capsys.readouterr().err


def test_report_maps_malformed_poll_interval_to_command_error(
    monkeypatch: Any, capsys: Any
) -> None:
    monkeypatch.setattr(
        vars(campaign_report)["storage"],
        "Client",
        lambda: pytest.fail("malformed poll interval contacted storage"),
    )
    assert (
        campaign_report.report_campaign_main(
            [
                "--campaign",
                CAMPAIGN,
                "--results-bucket",
                BUCKET,
                "--poll-interval-s",
                "not-a-number",
            ]
        )
        == 1
    )
    error = capsys.readouterr().err
    assert "--poll-interval-s must be a number" in error
    assert "Traceback" not in error


@pytest.mark.parametrize("status", ["timed_out", "harness_error"])
def test_timeout_and_harness_error_are_recorded_subject_outcomes(status: str) -> None:
    manifest_bytes, attempts, image = manifest()
    case = campaign_report._parse_manifest(
        manifest_bytes, campaign=CAMPAIGN, results_bucket=BUCKET
    )[0]
    name = f"{attempts[0]['prefix']}/{UUIDS[0]}/result.json"
    bucket = FakeBucket({name: result(attempts[0], image, UUIDS[0], status)})
    leaf, normalized = campaign_report._validated_result(bucket, case, UUIDS[0])
    assert leaf["state"] == "recorded"
    assert normalized is not None
    assert normalized["subject"]["status"] == status


@pytest.mark.parametrize(
    "changes",
    [
        {"term_sent": True},
        {"kill_sent": True},
        {"process_group_empty": False},
    ],
)
def test_non_timeout_harness_error_refuses_impossible_cleanup(changes: dict[str, bool]) -> None:
    manifest_bytes, attempts, image = manifest()
    case = campaign_report._parse_manifest(
        manifest_bytes, campaign=CAMPAIGN, results_bucket=BUCKET
    )[0]
    document = json.loads(result(attempts[0], image, UUIDS[0], "harness_error"))
    document["outcome"]["cleanup"].update(changes)
    name = f"{attempts[0]['prefix']}/{UUIDS[0]}/result.json"
    leaf, normalized = campaign_report._validated_result(
        FakeBucket({name: campaign_report._worker_json(document)}), case, UUIDS[0]
    )
    assert leaf["reason"] == "invalid_result_cleanup"
    assert normalized is None


def test_timed_out_harness_error_accepts_failed_cleanup_variant() -> None:
    manifest_bytes, attempts, image = manifest()
    case = campaign_report._parse_manifest(
        manifest_bytes, campaign=CAMPAIGN, results_bucket=BUCKET
    )[0]
    document = json.loads(result(attempts[0], image, UUIDS[0], "harness_error"))
    document["outcome"].update(timed_out=True)
    document["outcome"]["cleanup"].update(
        term_sent=True,
        kill_sent=True,
        process_group_empty=False,
    )
    name = f"{attempts[0]['prefix']}/{UUIDS[0]}/result.json"
    leaf, normalized = campaign_report._validated_result(
        FakeBucket({name: campaign_report._worker_json(document)}), case, UUIDS[0]
    )
    assert leaf["state"] == "recorded"
    assert normalized is not None
    assert normalized["subject"]["status"] == "harness_error"


def test_non_v4_attempt_uuid_is_invalid() -> None:
    manifest_bytes, attempts, image = manifest()
    case = campaign_report._parse_manifest(
        manifest_bytes, campaign=CAMPAIGN, results_bucket=BUCKET
    )[0]
    uuid_v1 = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    name = f"{attempts[0]['prefix']}/{uuid_v1}/result.json"
    leaf, normalized = campaign_report._validated_result(
        FakeBucket({name: result(attempts[0], image, uuid_v1, "completed")}),
        case,
        uuid_v1,
    )
    assert leaf["reason"] == "invalid_attempt_id"
    assert normalized is None


def test_observer_refuses_owner_run_mismatch_before_query_or_harvest() -> None:
    manifest_bytes, attempts, _image = manifest()
    bucket = FakeBucket(frozen_objects(manifest_bytes, attempts))
    progress = [
        CaseControllerProgress(attempt["job_id"], None, "terminal", None, "failed")
        for attempt in attempts
    ]
    client = FakeTemporalClient(progress, [])
    client.description.run_id = "foreign-run"
    with pytest.raises(campaign_report.ReportError, match="does not exactly match"):
        asyncio.run(
            campaign_report.observe_once(
                temporal_client=cast(TemporalClient, client),
                bucket=bucket,
                campaign=CAMPAIGN,
                owner=owner(),
                scope=SCOPE,
            )
        )
    assert bucket.listed == []


def test_publish_uses_create_only_report_commit() -> None:
    bucket = FakeBucket({})
    report = {"schema_version": 3, "campaign": CAMPAIGN, "report_final": True}
    campaign_report._publish(bucket, CAMPAIGN, report)
    blob = bucket.objects[f"campaigns/{CAMPAIGN}/report.json"]
    assert blob.uploads == [
        (
            campaign_cli._canonical_json(report),
            {"content_type": "application/json", "if_generation_match": 0},
        )
    ]


def test_publish_refuses_nonfinal_report_before_creating_object() -> None:
    bucket = FakeBucket({})
    with pytest.raises(campaign_report.ReportError, match="without provider settlement"):
        campaign_report._publish(
            bucket,
            CAMPAIGN,
            {"schema_version": 3, "campaign": CAMPAIGN, "report_final": False},
        )
    assert f"campaigns/{CAMPAIGN}/report.json" not in bucket.objects


@pytest.mark.parametrize(
    ("workflow_status", "controller_complete", "provider_settled", "cases", "message"),
    [
        ("failed", False, False, [], "closed with status failed"),
        (
            "completed",
            True,
            False,
            [
                {
                    "job_id": "job-1",
                    "provider_settled": False,
                    "controller": {
                        "phase": "terminal",
                        "activity_attempt": None,
                        "last_heartbeat": None,
                    },
                    "evidence": {"state": "pending"},
                }
            ],
            "completed without provider settlement for job-1",
        ),
    ],
)
def test_wait_refuses_closed_failure_or_unsettled_provider(
    monkeypatch: Any,
    capsys: Any,
    workflow_status: str,
    controller_complete: bool,
    provider_settled: bool,
    cases: list[dict[str, Any]],
    message: str,
) -> None:
    fake_bucket = SimpleNamespace(name=BUCKET)
    fake_client = object()
    report = {
        "controller_complete": controller_complete,
        "provider_settled": provider_settled,
        "report_final": False,
        "operational_success": False,
        "engine": {"status": workflow_status},
        "cases": cases,
        "aggregate": {
            "controller": {"pending": 0, "running": 0, "retrying": 0, "terminal": 3},
            "evidence": {
                "pending": 0,
                "missing": 3,
                "recorded": 0,
                "duplicate": 0,
                "invalid": 0,
                "unsealed": 0,
            },
        },
    }

    async def connect(**_kwargs: Any) -> object:
        return fake_client

    async def observe(**_kwargs: Any) -> dict[str, Any]:
        return report

    monkeypatch.setattr(
        vars(campaign_report)["ClientConfig"],
        "load_client_connect_config",
        staticmethod(lambda: {"target_host": SCOPE.target_host, "namespace": SCOPE.namespace}),
    )
    monkeypatch.setattr(
        vars(campaign_report)["storage"],
        "Client",
        lambda: SimpleNamespace(bucket=lambda _name: fake_bucket),
    )
    monkeypatch.setattr(vars(campaign_report)["Client"], "connect", connect)
    monkeypatch.setattr(
        campaign_report,
        "_required_blob",
        lambda *_args, **_kwargs: campaign_cli._canonical_json(owner().document()),
    )
    monkeypatch.setattr(
        campaign_report,
        "_load_manifest",
        lambda *_args: campaign_report.ManifestSnapshot(BUCKET, CAMPAIGN, 1, b"{}\n", (), "0" * 64),
    )
    monkeypatch.setattr(
        campaign_report,
        "_load_temporal_input",
        lambda *_args: campaign_report.TemporalSnapshot(
            BUCKET,
            CAMPAIGN,
            1,
            b"{}\n",
            DIGEST,
            "0" * 64,
            SCOPE,
            "CampaignWorkflow",
            TASK_QUEUE,
            (),
        ),
    )
    monkeypatch.setattr(campaign_report, "observe_once", observe)
    args = campaign_report.build_parser().parse_args(
        ["--campaign", CAMPAIGN, "--results-bucket", BUCKET, "--wait"]
    )
    with pytest.raises(campaign_report.ReportError, match=message):
        asyncio.run(campaign_report._run_report(args))
    assert f"workflow={workflow_status}" in capsys.readouterr().err


def test_manager_dispatches_standalone_report_command(monkeypatch: Any) -> None:
    seen: list[str] = []

    def report(argv: Any) -> int:
        seen.extend(argv)
        return 9

    monkeypatch.setattr(campaign_report, "report_campaign_main", report)
    assert manager_cli.main(["report-campaign", "--campaign", CAMPAIGN]) == 9
    assert seen == ["--campaign", CAMPAIGN]


def test_report_command_rejects_invalid_campaign_without_traceback(capsys: Any) -> None:
    assert (
        campaign_report.report_campaign_main(["--campaign", "invalid", "--results-bucket", BUCKET])
        == 1
    )
    error = capsys.readouterr().err
    assert "is not <yyyy-mm-dd>-<word>" in error
    assert "Traceback" not in error


@pytest.mark.parametrize(
    "failure",
    [
        GoogleAPIError("cloud request failed"),
        cast(type[Exception], DefaultCredentialsError)("ADC missing"),
    ],
)
def test_report_command_handles_google_and_adc_failures_without_traceback(
    monkeypatch: Any, capsys: Any, failure: Exception
) -> None:
    async def fail(_args: Any) -> dict[str, Any]:
        raise failure

    monkeypatch.setattr(campaign_report, "_run_report", fail)
    assert (
        campaign_report.report_campaign_main(["--campaign", CAMPAIGN, "--results-bucket", BUCKET])
        == 1
    )
    assert "Traceback" not in capsys.readouterr().err
