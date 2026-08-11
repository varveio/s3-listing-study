from __future__ import annotations

import argparse
import asyncio
import hashlib
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from google.api_core import exceptions as google_exceptions
from google.cloud import batch_v1
from temporalio import activity as temporal_activity
from temporalio import workflow as temporal_workflow
from temporalio.client import Client as TemporalClient
from temporalio.client import WorkflowExecutionStatus
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.envconfig import ClientConfig as TemporalClientConfig
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.workflow import (
    ActivityCancellationType,
    ChildWorkflowCancellationType,
    ParentClosePolicy,
)

from s3_listing_study.manager.bench.plan import Plan
from s3_listing_study.manager.campaign import cli as campaign_cli
from s3_listing_study.manager.campaign.batch import BatchConfig
from s3_listing_study.temporal import TASK_QUEUE, activities, starter, workflows
from s3_listing_study.temporal.models import (
    BatchJobOutcome,
    BatchJobSpec,
    CampaignWorkflowInput,
)


def spec() -> BatchJobSpec:
    identity = "a" * 52
    return BatchJobSpec(
        project="study",
        location="us-east1",
        job_id="c-campaign-tool-case-12345678-r1-s1",
        job={
            "labels": {"s3-study-attempt": identity},
            "taskGroups": [
                {
                    "taskCount": "1",
                    "parallelism": "1",
                    "taskSpec": {
                        "runnables": [
                            {"container": {"imageUri": "registry/image@sha256:" + "a" * 64}}
                        ],
                        "maxRetryCount": 0,
                    },
                }
            ],
            "allocationPolicy": {"instances": [{"policy": {"machineType": "n4-highcpu-2"}}]},
            "logsPolicy": {"destination": "CLOUD_LOGGING"},
        },
    )


def provider_job(selected: BatchJobSpec, state: str, *, mismatch: bool = False) -> batch_v1.Job:
    body = dict(selected.job)
    if mismatch:
        body["allocationPolicy"] = {"instances": [{"policy": {"machineType": "n4-highcpu-4"}}]}
    job = activities._job_from_json(body)
    job.name = selected.resource_name
    job.status.state = getattr(batch_v1.JobStatus.State, state)
    return job


class FakeBatchClient:
    def __init__(
        self, selected: BatchJobSpec, states: list[batch_v1.Job], *, collision: bool
    ) -> None:
        self.selected = selected
        self.states = iter(states)
        self.collision = collision
        self.creates = 0
        self.gets = 0
        self.closed = False

    @property
    def transport(self) -> Any:
        client = self

        class Transport:
            async def close(self) -> None:
                client.closed = True

        return Transport()

    async def create_job(self, **kwargs: Any) -> batch_v1.Job:
        self.creates += 1
        assert kwargs["job"].task_groups[0].task_spec.max_retry_count == 0
        assert kwargs["retry"] is None
        assert kwargs["timeout"] == 20
        if self.collision:
            error = cast(type[Exception], google_exceptions.AlreadyExists)
            raise error("exists")
        return provider_job(self.selected, "QUEUED")

    async def get_job(self, **kwargs: Any) -> batch_v1.Job:
        self.gets += 1
        assert kwargs == {"name": self.selected.resource_name, "retry": None, "timeout": 20}
        return next(self.states)


def run_activity(
    monkeypatch: pytest.MonkeyPatch,
    client: FakeBatchClient,
    *,
    attempt: int,
) -> tuple[BatchJobOutcome, list[dict[str, str]]]:
    heartbeats: list[dict[str, str]] = []

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(batch_v1, "BatchServiceAsyncClient", lambda: client)
    monkeypatch.setattr(temporal_activity, "info", lambda: SimpleNamespace(attempt=attempt))
    monkeypatch.setattr(temporal_activity, "heartbeat", lambda detail: heartbeats.append(detail))
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    return asyncio.run(activities.run_batch_job(client.selected)), heartbeats


def test_first_attempt_collision_is_nonretryable(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = spec()
    client = FakeBatchClient(selected, [], collision=True)
    with pytest.raises(ApplicationError) as raised:
        run_activity(monkeypatch, client, attempt=1)
    assert raised.value.type == "BatchJobCollision"
    assert raised.value.non_retryable
    assert client.gets == 0
    assert client.closed


def test_retry_adopts_exact_job_and_rereads_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = spec()
    queued = provider_job(selected, "QUEUED")
    succeeded = provider_job(selected, "SUCCEEDED")
    queued.task_groups[0].name = selected.resource_name + "/taskGroups/group0"
    succeeded.task_groups[0].name = selected.resource_name + "/taskGroups/group0"
    client = FakeBatchClient(selected, [queued, succeeded], collision=True)
    outcome, heartbeats = run_activity(monkeypatch, client, attempt=2)
    assert outcome == BatchJobOutcome(selected.resource_name, "SUCCEEDED")
    assert heartbeats == [
        {"job_name": selected.resource_name, "state": "STARTING"},
        {"job_name": selected.resource_name, "state": "ADOPTING"},
        {"job_name": selected.resource_name, "state": "QUEUED"},
        {"job_name": selected.resource_name, "state": "GETTING"},
        {"job_name": selected.resource_name, "state": "SUCCEEDED"},
    ]
    assert client.gets == 2
    assert client.closed


def test_retry_refuses_mismatched_adoption(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = spec()
    client = FakeBatchClient(
        selected, [provider_job(selected, "QUEUED", mismatch=True)], collision=True
    )
    with pytest.raises(ApplicationError) as raised:
        run_activity(monkeypatch, client, attempt=2)
    assert raised.value.type == "BatchJobCollision"
    assert raised.value.non_retryable


def test_campaign_fans_out_typed_child_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = spec()
    cases = tuple(replace(selected, job_id=f"campaign-case-{number}") for number in range(2))
    calls: list[dict[str, Any]] = []

    async def child(*args: Any, **kwargs: Any) -> BatchJobOutcome:
        calls.append(kwargs)
        request = args[1]
        return BatchJobOutcome(request.resource_name, "SUCCEEDED")

    monkeypatch.setattr(temporal_workflow, "execute_child_workflow", child)
    result = asyncio.run(workflows.CampaignWorkflow().run(CampaignWorkflowInput(cases)))
    assert all(isinstance(item, BatchJobOutcome) for item in result)
    assert [call["id"] for call in calls] == [case.job_id for case in cases]
    assert all(call["parent_close_policy"] is ParentClosePolicy.ABANDON for call in calls)
    assert all(call["cancellation_type"] is ChildWorkflowCancellationType.ABANDON for call in calls)


def test_campaign_refuses_invalid_case_count_without_workflow_task_loop() -> None:
    with pytest.raises(ApplicationError) as raised:
        asyncio.run(workflows.CampaignWorkflow().run(CampaignWorkflowInput(())))
    assert raised.value.type == "InvalidCampaignInput"
    assert raised.value.non_retryable


def test_case_declares_activity_retries_and_all_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def execute(*args: Any, **kwargs: Any) -> BatchJobOutcome:
        captured.update(kwargs)
        return BatchJobOutcome(spec().resource_name, "FAILED")

    monkeypatch.setattr(temporal_workflow, "execute_activity", execute)
    result = asyncio.run(workflows.CaseWorkflow().run(spec()))
    assert result.state == "FAILED"
    assert captured["start_to_close_timeout"] == timedelta(hours=8)
    assert captured["schedule_to_close_timeout"] == timedelta(hours=24)
    assert captured["heartbeat_timeout"] == timedelta(seconds=30)
    assert captured["retry_policy"].maximum_attempts == 8
    assert captured["cancellation_type"] is ActivityCancellationType.ABANDON


def test_starter_freezes_prepared_bytes_and_uses_stable_id_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    request = CampaignWorkflowInput((spec(),))
    monkeypatch.setattr(starter, "prepare", lambda _args: (request, b"{}\n", (("bucket", b"old"),)))
    frozen: list[tuple[str, bytes]] = []

    def freeze(uri: str, data: bytes) -> bool:
        frozen.append((uri, data))
        return True

    monkeypatch.setattr(campaign_cli, "_freeze", freeze)
    plan = tmp_path / "plan.yaml"
    plan.write_bytes(b"changed after prepare")
    captured: dict[str, Any] = {}

    class FakeClient:
        async def start_workflow(self, *args: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return SimpleNamespace(id=kwargs["id"])

    monkeypatch.setattr(TemporalClientConfig, "load_client_connect_config", lambda: {})
    monkeypatch.setattr(TemporalClient, "connect", lambda **_kwargs: asyncio.sleep(0, FakeClient()))
    args = argparse.Namespace(
        campaign="2026-08-11-trial",
        results_bucket="results",
        path=[str(plan)],
        prepare_only=False,
    )
    assert asyncio.run(starter.start(args)) == args.campaign
    assert captured["id"] == args.campaign
    assert captured["task_queue"] == TASK_QUEUE
    assert captured["id_reuse_policy"] is WorkflowIDReusePolicy.REJECT_DUPLICATE
    assert captured["id_conflict_policy"] is WorkflowIDConflictPolicy.FAIL
    assert captured["memo"] == {"campaign_digest": hashlib.sha256(b"{}\n").hexdigest()}
    assert frozen[0][1] == b"old"


def test_existing_reservation_returns_only_matching_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = CampaignWorkflowInput((spec(),))
    monkeypatch.setattr(starter, "prepare", lambda _args: (request, b"{}\n", ()))
    monkeypatch.setattr(campaign_cli, "_freeze", lambda *_args: False)

    class Description:
        status = WorkflowExecutionStatus.RUNNING
        workflow_type = "CampaignWorkflow"
        task_queue = TASK_QUEUE
        digest = hashlib.sha256(b"{}\n").hexdigest()

        async def memo_value(self, key: str, *, type_hint: type[str]) -> str:
            assert (key, type_hint) == ("campaign_digest", str)
            return self.digest

    description = Description()

    class Handle:
        id = "2026-08-11-trial"

        async def describe(self) -> Any:
            return description

    class FakeClient:
        def get_workflow_handle(self, workflow_id: str) -> Handle:
            assert workflow_id == Handle.id
            return Handle()

        async def start_workflow(self, *_args: Any, **_kwargs: Any) -> Any:
            pytest.fail("existing reservation started a new Workflow")

    monkeypatch.setattr(TemporalClientConfig, "load_client_connect_config", lambda: {})
    monkeypatch.setattr(TemporalClient, "connect", lambda **_kwargs: asyncio.sleep(0, FakeClient()))
    args = argparse.Namespace(campaign=Handle.id, results_bucket="results")
    assert asyncio.run(starter.start(args)) == Handle.id

    description.task_queue = "foreign"
    with pytest.raises(RuntimeError, match="different Temporal Workflow"):
        asyncio.run(starter.start(args))

    description.task_queue = TASK_QUEUE
    description.digest = "wrong"
    with pytest.raises(RuntimeError, match="different Temporal Workflow"):
        asyncio.run(starter.start(args))

    description.digest = hashlib.sha256(b"{}\n").hexdigest()
    description.status = WorkflowExecutionStatus.COMPLETED
    with pytest.raises(RuntimeError, match="different Temporal Workflow"):
        asyncio.run(starter.start(args))


def test_existing_reservation_refuses_missing_or_expired_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = CampaignWorkflowInput((spec(),))
    monkeypatch.setattr(starter, "prepare", lambda _args: (request, b"{}\n", ()))
    monkeypatch.setattr(campaign_cli, "_freeze", lambda *_args: False)

    class Handle:
        id = "2026-08-11-trial"

        async def describe(self) -> Any:
            raise RPCError("missing", RPCStatusCode.NOT_FOUND, b"")

    client = SimpleNamespace(get_workflow_handle=lambda _workflow_id: Handle())
    monkeypatch.setattr(TemporalClientConfig, "load_client_connect_config", lambda: {})
    monkeypatch.setattr(TemporalClient, "connect", lambda **_kwargs: asyncio.sleep(0, client))
    args = argparse.Namespace(campaign=Handle.id, results_bucket="results")
    with pytest.raises(RuntimeError, match="unstarted or expired"):
        asyncio.run(starter.start(args))


def test_prepare_passes_explicit_network_pair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[BatchConfig] = []

    def render(_attempt: Any, config: BatchConfig) -> dict[str, Any]:
        captured.append(config)
        return spec().job

    plan_path = tmp_path / "plan.yaml"
    plan_path.write_bytes(b"plan")
    plan = SimpleNamespace(
        path=plan_path,
        digest=hashlib.sha256(b"plan").hexdigest(),
        bucket="bucket",
        tools=lambda: ("aws-cli",),
    )
    attempt = SimpleNamespace(
        case=SimpleNamespace(auth="anonymous"), fingerprint="a" * 64, job_id="job"
    )
    monkeypatch.setattr(Plan, "load", lambda _path: plan)
    monkeypatch.setattr(campaign_cli, "_read_image_set", lambda _path: {"aws-cli": {}})
    monkeypatch.setattr(campaign_cli, "validate_registered_images", lambda _images: None)
    monkeypatch.setattr(starter, "attempts_for", lambda *_args, **_kwargs: (attempt,))
    monkeypatch.setattr(starter, "render_job", render)
    monkeypatch.setattr(starter, "manifest", lambda **_kwargs: {})
    args = starter.build_parser().parse_args(
        [
            "--path",
            str(plan_path),
            "--campaign",
            "2026-08-11-trial",
            "--image-set",
            "images",
            "--project",
            "study",
            "--location",
            "us-east1",
            "--results-bucket",
            "results",
            "--anonymous-worker-sa",
            "worker@example",
            "--network",
            "network",
            "--subnetwork",
            "subnetwork",
        ]
    )
    starter.prepare(args)
    assert (captured[0].network, captured[0].subnetwork) == ("network", "subnetwork")

    monkeypatch.setattr(starter, "attempts_for", lambda *_args, **_kwargs: (attempt, attempt))
    with pytest.raises(RuntimeError, match="duplicate Batch job IDs"):
        starter.prepare(args)

    args.path.append(str(plan_path))
    with pytest.raises(RuntimeError, match="duplicate plan buckets"):
        starter.prepare(args)


def test_temporal_payload_uses_digest_pinned_image_and_full_identity_label() -> None:
    selected = spec()
    container = selected.job["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]
    assert "@sha256:" in container["imageUri"]
    assert len(selected.job["labels"]["s3-study-attempt"]) == 52
