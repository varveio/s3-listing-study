from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import timedelta
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
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.workflow import (
    ActivityCancellationType,
    ChildWorkflowCancellationType,
    ParentClosePolicy,
)

from s3_listing_study.manager.campaign import cli as campaign_cli
from s3_listing_study.temporal import TASK_QUEUE, activities, workflows
from s3_listing_study.temporal.models import (
    BatchJobOutcome,
    BatchJobSpec,
    CampaignWorkflowInput,
)

DIGEST = "d" * 64
SCOPE = campaign_cli.TemporalScope("temporal.example.invalid:7233", "s3-study")
CLIENT_CONFIG: dict[str, Any] = {
    **SCOPE.document(),
    "api_key": "temporal-test-api-key",
    "tls": True,
}


def spec(*, controller_timeout_s: int = 9000) -> BatchJobSpec:
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
        controller_timeout_s=controller_timeout_s,
    )


def provider_job(selected: BatchJobSpec, state: str, *, mismatch: bool = False) -> batch_v1.Job:
    body = dict(selected.job)
    if mismatch:
        body["allocationPolicy"] = {"instances": [{"policy": {"machineType": "n4-highcpu-4"}}]}
    job = activities._job_from_json(body)
    job.name = selected.resource_name
    job.allocation_policy.labels["batch-job-id"] = selected.job_id
    job.allocation_policy.location.allowed_locations.extend(
        ("regions/us-east1", "zones/us-east1-b")
    )
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


def test_first_activity_attempt_collision_is_nonretryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = spec()
    client = FakeBatchClient(selected, [], collision=True)
    with pytest.raises(ApplicationError) as raised:
        run_activity(monkeypatch, client, attempt=1)
    assert raised.value.type == "BatchJobCollision"
    assert raised.value.non_retryable
    assert client.gets == 0
    assert client.closed


def test_later_activity_attempt_adopts_exact_provider_normalized_job(
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


def test_later_activity_attempt_refuses_mismatched_adoption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = spec()
    client = FakeBatchClient(
        selected, [provider_job(selected, "QUEUED", mismatch=True)], collision=True
    )
    with pytest.raises(ApplicationError) as raised:
        run_activity(monkeypatch, client, attempt=2)
    assert raised.value.type == "BatchJobCollision"
    assert raised.value.non_retryable


def test_campaign_waits_for_exact_claim_then_fans_out_without_spike_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = spec()
    cases = tuple(replace(selected, job_id=f"campaign-case-{number}") for number in range(8))
    calls: list[dict[str, Any]] = []

    async def child(*args: Any, **kwargs: Any) -> BatchJobOutcome:
        calls.append(kwargs)
        request = args[1]
        return BatchJobOutcome(request.resource_name, "SUCCEEDED")

    async def wait_condition(predicate: Any) -> None:
        while not predicate():
            await asyncio.sleep(0)

    monkeypatch.setattr(temporal_workflow, "execute_child_workflow", child)
    monkeypatch.setattr(temporal_workflow, "wait_condition", wait_condition)

    async def run() -> list[BatchJobOutcome]:
        campaign = workflows.CampaignWorkflow()
        task = asyncio.create_task(campaign.run(CampaignWorkflowInput(cases, DIGEST)))
        await asyncio.sleep(0)
        assert calls == []
        campaign.claim("wrong")
        await asyncio.sleep(0)
        assert calls == []
        campaign.claim(DIGEST)
        campaign.claim(DIGEST)
        assert campaign._claims == {"wrong", DIGEST}
        return await task

    result = asyncio.run(run())
    assert len(result) == len(cases)
    assert [call["id"] for call in calls] == [case.job_id for case in cases]
    assert all(call["parent_close_policy"] is ParentClosePolicy.ABANDON for call in calls)
    assert all(call["cancellation_type"] is ChildWorkflowCancellationType.ABANDON for call in calls)
    assert all("retry_policy" not in call for call in calls)


def test_campaign_workflow_refuses_empty_input() -> None:
    with pytest.raises(ApplicationError) as raised:
        asyncio.run(workflows.CampaignWorkflow().run(CampaignWorkflowInput((), DIGEST)))
    assert raised.value.type == "InvalidCampaignInput"
    assert raised.value.non_retryable


def test_case_declares_activity_retries_timeouts_and_abandonment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def execute(*_args: Any, **kwargs: Any) -> BatchJobOutcome:
        captured.update(kwargs)
        return BatchJobOutcome(spec().resource_name, "FAILED")

    monkeypatch.setattr(temporal_workflow, "execute_activity", execute)
    selected = spec(controller_timeout_s=34_205)
    result = asyncio.run(workflows.CaseWorkflow().run(selected))
    assert result.state == "FAILED"
    # The committed plan boundary allows 28,800 seconds of subject time. Batch
    # then permits TERM grace and post-attempt finalization before the explicit
    # one-hour queue/control allowance ends.
    batch_max_run_duration_s = 28_800 + 5 + 1800
    assert selected.controller_timeout_s == batch_max_run_duration_s + 3600
    assert captured["start_to_close_timeout"] == timedelta(seconds=selected.controller_timeout_s)
    assert captured["schedule_to_close_timeout"] == timedelta(
        seconds=selected.controller_timeout_s * 3
    )
    assert captured["start_to_close_timeout"] > timedelta(seconds=batch_max_run_duration_s)
    assert captured["heartbeat_timeout"] == timedelta(seconds=30)
    assert captured["retry_policy"].maximum_attempts == 8
    assert captured["retry_policy"].non_retryable_error_types == (
        "PermanentGoogleError",
        "BatchJobCollision",
    )
    assert captured["cancellation_type"] is ActivityCancellationType.ABANDON


class Description:
    def __init__(self) -> None:
        self.id = "2026-08-11-trial"
        self.run_id = "run-a"
        self.namespace = SCOPE.namespace
        self.status = WorkflowExecutionStatus.RUNNING
        self.workflow_type = "CampaignWorkflow"
        self.task_queue = TASK_QUEUE
        self.digest: str | None = DIGEST

    async def memo_value(self, key: str, *, type_hint: type[str]) -> str:
        assert (key, type_hint) == ("campaign_digest", str)
        if self.digest is None:
            raise KeyError(key)
        return self.digest


class Handle:
    id = "2026-08-11-trial"

    def __init__(self, description: Description | BaseException) -> None:
        self.description = description
        self.signals: list[tuple[Any, Any]] = []

    async def describe(self) -> Description:
        if isinstance(self.description, BaseException):
            raise self.description
        return self.description

    async def signal(self, signal: Any, arg: Any) -> None:
        self.signals.append((signal, arg))


class FakeTemporalClient:
    def __init__(self, handle: Handle | BaseException) -> None:
        self.handle = handle
        self.start_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.get_calls: list[tuple[str, str | None]] = []

    async def start_workflow(self, *args: Any, **kwargs: Any) -> Handle:
        self.start_calls.append((args, kwargs))
        if isinstance(self.handle, BaseException):
            raise self.handle
        return self.handle

    def get_workflow_handle(self, workflow_id: str, *, run_id: str | None = None) -> Handle:
        self.get_calls.append((workflow_id, run_id))
        if isinstance(self.handle, BaseException):
            raise self.handle
        return self.handle


def owner(*, digest: str = DIGEST, scope: campaign_cli.TemporalScope = SCOPE) -> Any:
    return campaign_cli.TemporalOwner(
        campaign=Handle.id,
        campaign_digest=digest,
        scope=scope,
        workflow_id=Handle.id,
        run_id="run-a",
    )


def run_start(
    monkeypatch: pytest.MonkeyPatch,
    client: FakeTemporalClient,
    *,
    existing_owner: campaign_cli.TemporalOwner | None = None,
) -> tuple[str, list[tuple[str, bytes]]]:
    frozen: list[tuple[str, bytes]] = []
    monkeypatch.setattr(TemporalClient, "connect", lambda **_kwargs: asyncio.sleep(0, client))
    monkeypatch.setattr(
        campaign_cli,
        "_freeze_owner",
        lambda uri, selected_owner: frozen.append(
            (uri, campaign_cli._canonical_json(selected_owner.document()))
        ),
    )
    result = asyncio.run(
        campaign_cli._start_workflow(
            campaign=Handle.id,
            request=CampaignWorkflowInput((spec(),), DIGEST),
            campaign_digest=DIGEST,
            client_config=CLIENT_CONFIG,
            temporal_scope=SCOPE,
            owner_uri="gs://results/campaigns/trial/inputs/temporal-owner.json",
            owner=existing_owner,
        )
    )
    return result, frozen


def test_owner_absent_ambiguous_open_start_is_adopted_owned_then_signaled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    description = Description()
    handle = Handle(description)
    client = FakeTemporalClient(handle)
    workflow_id, frozen = run_start(monkeypatch, client)
    assert workflow_id == Handle.id
    assert len(client.start_calls) == 1
    _args, options = client.start_calls[0]
    assert options["id"] == Handle.id
    assert options["task_queue"] == TASK_QUEUE
    assert options["id_reuse_policy"] is WorkflowIDReusePolicy.REJECT_DUPLICATE
    assert options["id_conflict_policy"] is WorkflowIDConflictPolicy.USE_EXISTING
    assert options["memo"] == {"campaign_digest": DIGEST}
    assert "retry_policy" not in options
    assert len(frozen) == 1
    owner_document = json.loads(frozen[0][1])
    assert owner_document == owner().document()
    assert CLIENT_CONFIG["api_key"] not in frozen[0][1].decode()
    assert handle.signals == [(workflows.CampaignWorkflow.claim, DIGEST)]


def test_existing_owner_uses_recorded_run_without_start_and_resignals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = Handle(Description())
    client = FakeTemporalClient(handle)
    workflow_id, frozen = run_start(monkeypatch, client, existing_owner=owner())
    assert workflow_id == Handle.id
    assert client.start_calls == []
    assert client.get_calls == [(Handle.id, "run-a")]
    assert frozen == []
    assert handle.signals == [(workflows.CampaignWorkflow.claim, DIGEST)]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("workflow_type", "ForeignWorkflow", "different Temporal Workflow"),
        ("task_queue", "foreign", "different Temporal Workflow"),
        ("digest", "wrong", "different Temporal Workflow"),
        ("digest", None, "different Temporal Workflow"),
        ("namespace", "foreign", "different Temporal Workflow"),
        ("run_id", "run-b", "different Temporal Workflow"),
        ("status", WorkflowExecutionStatus.COMPLETED, "already closed"),
    ],
)
def test_start_refuses_mismatched_or_closed_ownership(
    monkeypatch: pytest.MonkeyPatch, field: str, value: Any, message: str
) -> None:
    description = Description()
    setattr(description, field, value)
    with pytest.raises(campaign_cli.SubmissionError, match=message):
        run_start(monkeypatch, FakeTemporalClient(Handle(description)), existing_owner=owner())


def test_existing_owner_with_missing_history_refuses_without_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expired = RPCError("missing", RPCStatusCode.NOT_FOUND, b"")
    client = FakeTemporalClient(Handle(expired))
    with pytest.raises(campaign_cli.SubmissionError, match="missing or expired"):
        run_start(monkeypatch, client, existing_owner=owner())
    assert client.start_calls == []
    assert client.get_calls == [(Handle.id, "run-a")]


def test_owner_scope_digest_or_run_mismatch_refuses_without_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mismatches = (
        owner(digest="e" * 64),
        owner(scope=campaign_cli.TemporalScope("other.example.invalid:7233", SCOPE.namespace)),
        replace(owner(), run_id="run-b"),
    )
    for mismatched in mismatches[:2]:
        client = FakeTemporalClient(Handle(Description()))
        with pytest.raises(campaign_cli.SubmissionError, match="does not exactly match"):
            run_start(monkeypatch, client, existing_owner=mismatched)
        assert client.start_calls == []
        assert client.get_calls == []
    run_mismatch = mismatches[2]
    client = FakeTemporalClient(Handle(Description()))
    with pytest.raises(campaign_cli.SubmissionError, match="different Temporal Workflow"):
        run_start(monkeypatch, client, existing_owner=run_mismatch)
    assert client.start_calls == []
    assert client.get_calls == [(Handle.id, "run-b")]


def test_absent_owner_refuses_retained_closed_id(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = WorkflowAlreadyStartedError(Handle.id, "CampaignWorkflow")
    with pytest.raises(campaign_cli.SubmissionError, match="closed"):
        run_start(monkeypatch, FakeTemporalClient(closed))


def test_temporal_payload_is_compact_and_carries_provider_terminal_state_only() -> None:
    selected = spec()
    request = CampaignWorkflowInput((selected,), DIGEST)
    assert tuple(request.__dict__) == ("cases", "campaign_digest")
    assert tuple(selected.__dict__) == (
        "project",
        "location",
        "job_id",
        "job",
        "controller_timeout_s",
    )
    container = selected.job["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]
    assert "@sha256:" in container["imageUri"]
    assert BatchJobOutcome(selected.resource_name, "FAILED").state == "FAILED"
