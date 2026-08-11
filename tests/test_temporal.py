from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
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
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    CancelledError,
    ChildWorkflowError,
    RetryState,
    WorkflowAlreadyStartedError,
)
from temporalio.service import RPCError, RPCStatusCode
from temporalio.workflow import (
    ActivityCancellationType,
    ChildWorkflowCancellationType,
    ParentClosePolicy,
)

from s3_listing_study.manager.campaign import cli as campaign_cli
from s3_listing_study.temporal import TASK_QUEUE, activities, workflows
from s3_listing_study.temporal.models import (
    BatchJobHandle,
    BatchJobOutcome,
    BatchJobSpec,
    CampaignWorkflowInput,
    CaseControllerProgress,
    RetryCaseRequest,
)

DIGEST = "d" * 64
SCOPE = campaign_cli.TemporalScope("temporal.example.invalid:7233", "s3-study")
CLIENT_CONFIG: dict[str, Any] = {
    **SCOPE.document(),
    "api_key": "temporal-test-api-key",
    "tls": True,
}


@pytest.fixture(autouse=True)
def legacy_workflow_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch: False)


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
    job.allocation_policy.location.allowed_locations.append("regions/us-east1")
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


def test_first_activity_attempt_collision_waits_for_provider_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = spec()
    client = FakeBatchClient(selected, [provider_job(selected, "FAILED")], collision=True)
    outcome, _heartbeats = run_activity(monkeypatch, client, attempt=1)
    assert outcome == BatchJobOutcome(selected.resource_name, "FAILED", "BatchJobCollision")
    assert client.gets == 1
    assert client.closed


def test_definitive_create_rejection_returns_explicit_no_effect_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = spec()

    class RejectedCreateClient(FakeBatchClient):
        async def create_job(self, **kwargs: Any) -> batch_v1.Job:
            self.creates += 1
            error = cast(type[Exception], google_exceptions.Forbidden)
            raise error("create rejected")

    client = RejectedCreateClient(selected, [], collision=False)
    outcome, heartbeats = run_activity(monkeypatch, client, attempt=1)
    assert outcome == BatchJobOutcome(selected.resource_name, "NOT_CREATED", "PermanentGoogleError")
    assert heartbeats[-1] == {"job_name": selected.resource_name, "state": "NOT_CREATED"}
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


def test_adoption_normalizes_only_provider_parent_region_with_requested_zone() -> None:
    selected = spec()
    selected.job["allocationPolicy"]["location"] = {"allowedLocations": ["zones/us-east1-b"]}
    actual = provider_job(selected, "QUEUED")
    assert list(actual.allocation_policy.location.allowed_locations) == [
        "zones/us-east1-b",
        "regions/us-east1",
    ]
    activities._validated_adoption(selected, actual)


@pytest.mark.parametrize("unexpected", ["zones/us-east1-c", "regions/us-west1"])
def test_adoption_refuses_other_provider_location_differences(unexpected: str) -> None:
    selected = spec()
    selected.job["allocationPolicy"]["location"] = {"allowedLocations": ["zones/us-east1-b"]}
    actual = provider_job(selected, "QUEUED")
    actual.allocation_policy.location.allowed_locations.append(unexpected)
    with pytest.raises(ApplicationError) as raised:
        activities._validated_adoption(selected, actual)
    assert raised.value.type == "BatchJobCollision"


def test_later_activity_attempt_settles_mismatched_adoption_before_reporting_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = spec()
    client = FakeBatchClient(
        selected,
        [
            provider_job(selected, "QUEUED", mismatch=True),
            provider_job(selected, "FAILED", mismatch=True),
        ],
        collision=True,
    )
    outcome, _heartbeats = run_activity(monkeypatch, client, attempt=2)
    assert outcome == BatchJobOutcome(selected.resource_name, "FAILED", "BatchJobCollision")
    assert client.gets == 2


def test_campaign_waits_for_exact_claim_then_fans_out_all_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = spec()
    cases = tuple(replace(selected, job_id=f"campaign-case-{number}") for number in range(8))
    calls: list[dict[str, Any]] = []

    async def child(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        request = args[1]

        class Handle:
            first_execution_run_id = f"run-{request.job_id}"

            def __await__(self) -> Any:
                async def done() -> BatchJobOutcome:
                    return BatchJobOutcome(request.resource_name, "SUCCEEDED")

                return done().__await__()

        return Handle()

    async def wait_condition(predicate: Any) -> None:
        while not predicate():
            await asyncio.sleep(0)

    monkeypatch.setattr(temporal_workflow, "start_child_workflow", child)
    monkeypatch.setattr(temporal_workflow, "wait_condition", wait_condition)

    async def run() -> list[Any]:
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
    assert all(item.phase == "terminal" and item.provider_state == "SUCCEEDED" for item in result)
    assert all(item.provider_settled for item in result)
    assert [call["id"] for call in calls] == [case.job_id for case in cases]
    assert all(call["parent_close_policy"] is ParentClosePolicy.ABANDON for call in calls)
    assert all(call["cancellation_type"] is ChildWorkflowCancellationType.ABANDON for call in calls)
    assert all("retry_policy" not in call for call in calls)


def test_campaign_workflow_refuses_empty_input() -> None:
    with pytest.raises(ApplicationError) as raised:
        asyncio.run(workflows.CampaignWorkflow().run(CampaignWorkflowInput((), DIGEST)))
    assert raised.value.type == "InvalidCampaignInput"
    assert raised.value.non_retryable


def test_campaign_records_one_child_failure_without_abandoning_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = (spec(), replace(spec(), job_id="campaign-case-two"))

    async def wait_condition(predicate: Any) -> None:
        assert predicate()

    async def start(*args: Any, **_kwargs: Any) -> Any:
        request = args[1]

        class Handle:
            first_execution_run_id = f"run-{request.job_id}"

            def __await__(self) -> Any:
                async def done() -> BatchJobOutcome:
                    if request.job_id == cases[0].job_id:
                        application_error = ApplicationError(
                            "safe message not exposed", type="PermanentGoogleError"
                        )
                        activity_error = ActivityError(
                            "activity failed",
                            scheduled_event_id=1,
                            started_event_id=2,
                            identity="worker",
                            activity_type="run_batch_job",
                            activity_id="activity-1",
                            retry_state=RetryState.MAXIMUM_ATTEMPTS_REACHED,
                        )
                        activity_error.__cause__ = application_error
                        child_error = ChildWorkflowError(
                            "child failed",
                            namespace="default",
                            workflow_id=request.job_id,
                            run_id=self.first_execution_run_id,
                            workflow_type="CaseWorkflow",
                            initiated_event_id=3,
                            started_event_id=4,
                            retry_state=RetryState.RETRY_POLICY_NOT_SET,
                        )
                        child_error.__cause__ = activity_error
                        raise child_error
                    return BatchJobOutcome(request.resource_name, "FAILED")

                return done().__await__()

        return Handle()

    monkeypatch.setattr(temporal_workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(temporal_workflow, "start_child_workflow", start)
    campaign = workflows.CampaignWorkflow()
    campaign.claim(DIGEST)
    result = asyncio.run(campaign.run(CampaignWorkflowInput(cases, DIGEST)))
    assert result[0].failure_type == "PermanentGoogleError"
    assert result[0].provider_state is None
    assert not result[0].provider_settled
    assert result[1].failure_type is None
    assert result[1].provider_state == "FAILED"
    assert result[1].provider_settled


@pytest.mark.parametrize("failure", [RuntimeError("bug"), CancelledError()])
def test_campaign_does_not_convert_local_or_cancellation_fault_to_success(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    async def wait_condition(predicate: Any) -> None:
        assert predicate()

    async def start(*_args: Any, **_kwargs: Any) -> Any:
        class Handle:
            first_execution_run_id = "run-local-fault"

            def __await__(self) -> Any:
                async def done() -> BatchJobOutcome:
                    raise failure

                return done().__await__()

        return Handle()

    monkeypatch.setattr(temporal_workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(temporal_workflow, "start_child_workflow", start)
    campaign = workflows.CampaignWorkflow()
    campaign.claim(DIGEST)
    with pytest.raises(type(failure)):
        asyncio.run(campaign.run(CampaignWorkflowInput((spec(),), DIGEST)))


def test_campaign_records_child_id_collision_and_runs_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = (spec(), replace(spec(), job_id="campaign-case-two"))

    async def wait_condition(predicate: Any) -> None:
        assert predicate()

    async def start(*args: Any, **_kwargs: Any) -> Any:
        request = args[1]
        if request.job_id == cases[0].job_id:
            raise WorkflowAlreadyStartedError(request.job_id, "CaseWorkflow")

        class Handle:
            first_execution_run_id = "run-sibling"

            def __await__(self) -> Any:
                async def done() -> BatchJobOutcome:
                    return BatchJobOutcome(request.resource_name, "SUCCEEDED")

                return done().__await__()

        return Handle()

    monkeypatch.setattr(temporal_workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(temporal_workflow, "start_child_workflow", start)
    campaign = workflows.CampaignWorkflow()
    campaign.claim(DIGEST)
    result = asyncio.run(campaign.run(CampaignWorkflowInput(cases, DIGEST)))
    assert result[0].failure_type == "WorkflowAlreadyStartedError"
    assert not result[0].provider_settled
    assert result[1].provider_state == "SUCCEEDED"
    assert result[1].provider_settled


@pytest.mark.parametrize(
    "outcome",
    [
        BatchJobOutcome("projects/other/locations/us-east1/jobs/wrong", "SUCCEEDED"),
        BatchJobOutcome(spec().resource_name, "QUEUED"),
        BatchJobOutcome(spec().resource_name, "SUCCEEDED", "PermanentGoogleError"),
        BatchJobOutcome(spec().resource_name, "NOT_CREATED"),
        BatchJobOutcome(spec().resource_name, "NOT_CREATED", "ActivityError"),
    ],
)
def test_campaign_refuses_invalid_batch_job_outcome(
    monkeypatch: pytest.MonkeyPatch, outcome: BatchJobOutcome
) -> None:
    async def wait_condition(predicate: Any) -> None:
        assert predicate()

    async def start(*_args: Any, **_kwargs: Any) -> Any:
        class Handle:
            first_execution_run_id = "run-invalid-outcome"

            def __await__(self) -> Any:
                async def done() -> BatchJobOutcome:
                    return outcome

                return done().__await__()

        return Handle()

    monkeypatch.setattr(temporal_workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(temporal_workflow, "start_child_workflow", start)
    campaign = workflows.CampaignWorkflow()
    campaign.claim(DIGEST)
    result = asyncio.run(campaign.run(CampaignWorkflowInput((spec(),), DIGEST)))
    assert result[0].failure_type == "InvalidBatchJobOutcome"
    assert result[0].provider_state is None
    assert result[0].provider_resource_name is None
    assert not result[0].provider_settled


def test_campaign_propagates_unexpected_child_start_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def wait_condition(predicate: Any) -> None:
        assert predicate()

    async def start(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("local child-start bug")

    monkeypatch.setattr(temporal_workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(temporal_workflow, "start_child_workflow", start)
    campaign = workflows.CampaignWorkflow()
    campaign.claim(DIGEST)
    with pytest.raises(RuntimeError, match="local child-start bug"):
        asyncio.run(campaign.run(CampaignWorkflowInput((spec(),), DIGEST)))


def test_case_declares_activity_retries_timeouts_and_abandonment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def execute(*_args: Any, **kwargs: Any) -> BatchJobOutcome:
        captured.update(kwargs)
        return BatchJobOutcome(spec().resource_name, "FAILED")

    monkeypatch.setattr(temporal_workflow, "execute_activity", execute)
    monkeypatch.setattr(
        temporal_workflow,
        "patched",
        lambda patch: patch == workflows.SETTLEMENT_RETRY_PATCH,
    )
    selected = spec(controller_timeout_s=34_205)
    result = asyncio.run(workflows.CaseWorkflow().run(selected))
    assert result.state == "FAILED"
    # The committed plan boundary allows 28,800 seconds of subject time. Batch
    # then permits TERM grace and post-attempt finalization before the explicit
    # one-hour queue/control allowance ends.
    batch_max_run_duration_s = 28_800 + 5 + 1800
    assert selected.controller_timeout_s == batch_max_run_duration_s + 3600
    assert captured["start_to_close_timeout"] == timedelta(seconds=selected.controller_timeout_s)
    assert "schedule_to_close_timeout" not in captured
    assert captured["start_to_close_timeout"] > timedelta(seconds=batch_max_run_duration_s)
    assert captured["heartbeat_timeout"] == timedelta(seconds=30)
    assert captured["retry_policy"].maximum_attempts == 0
    assert not captured["retry_policy"].non_retryable_error_types
    assert captured["cancellation_type"] is ActivityCancellationType.ABANDON


def test_case_replay_path_retains_legacy_activity_command_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def execute(*_args: Any, **kwargs: Any) -> BatchJobOutcome:
        captured.update(kwargs)
        return BatchJobOutcome(spec().resource_name, "FAILED")

    monkeypatch.setattr(temporal_workflow, "execute_activity", execute)
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch: False)
    selected = spec(controller_timeout_s=9000)
    asyncio.run(workflows.CaseWorkflow().run(selected))
    assert captured["schedule_to_close_timeout"] == timedelta(seconds=27_000)
    assert captured["retry_policy"].maximum_attempts == 8
    assert captured["retry_policy"].non_retryable_error_types == (
        "PermanentGoogleError",
        "BatchJobCollision",
    )


def test_new_case_history_ensures_then_waits_for_exact_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any, dict[str, Any]]] = []
    selected = spec(controller_timeout_s=9000)

    async def execute(name: str, arg: Any, **kwargs: Any) -> Any:
        calls.append((name, arg, kwargs))
        if name == "ensure_batch_job":
            return BatchJobHandle(selected.resource_name, "READY")
        return BatchJobOutcome(selected.resource_name, "SUCCEEDED")

    monkeypatch.setattr(temporal_workflow, "execute_activity", execute)
    monkeypatch.setattr(
        temporal_workflow, "patched", lambda patch: patch == workflows.ENSURE_WAIT_PATCH
    )
    result = asyncio.run(workflows.CaseWorkflow().run(selected))
    assert result.state == "SUCCEEDED"
    assert [item[0] for item in calls] == ["ensure_batch_job", "wait_for_batch_job"]
    assert calls[0][2]["start_to_close_timeout"] == timedelta(seconds=60)
    assert "heartbeat_timeout" not in calls[0][2]
    assert calls[1][1] == BatchJobHandle(selected.resource_name, "READY")
    assert calls[1][2]["heartbeat_timeout"] == timedelta(seconds=30)
    assert calls[1][2]["retry_policy"].maximum_attempts == 0


def test_new_case_history_refuses_invalid_ensure_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = spec()

    async def execute(_name: str, _arg: Any, **_kwargs: Any) -> BatchJobHandle:
        return BatchJobHandle(selected.resource_name, "READY", "PermanentGoogleError")

    monkeypatch.setattr(temporal_workflow, "execute_activity", execute)
    monkeypatch.setattr(
        temporal_workflow, "patched", lambda patch: patch == workflows.ENSURE_WAIT_PATCH
    )
    with pytest.raises(ApplicationError) as raised:
        asyncio.run(workflows.CaseWorkflow().run(selected))
    assert raised.value.type == "InvalidBatchJobHandle"


def retryable_spec() -> BatchJobSpec:
    selected = spec()
    job = json.loads(json.dumps(selected.job))
    job["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]["commands"] = [
        "python",
        "--job-id",
        selected.job_id,
        "--submission-number",
        "1",
        "--destination",
        "gs://bucket/stable/run-1",
    ]
    return replace(selected, job=job)


def test_failed_case_waits_for_targeted_next_submission_then_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = retryable_spec()
    started: list[BatchJobSpec] = []

    async def wait_condition(predicate: Any) -> None:
        while not predicate():
            await asyncio.sleep(0)

    async def start(*args: Any, **_kwargs: Any) -> Any:
        request = args[1]
        started.append(request)

        class Handle:
            first_execution_run_id = f"run-{request.job_id}"

            def __await__(self) -> Any:
                async def done() -> BatchJobOutcome:
                    state = "FAILED" if request.job_id.endswith("-s1") else "SUCCEEDED"
                    return BatchJobOutcome(request.resource_name, state)

                return done().__await__()

        return Handle()

    monkeypatch.setattr(temporal_workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(temporal_workflow, "start_child_workflow", start)
    monkeypatch.setattr(
        temporal_workflow, "patched", lambda patch: patch == workflows.CAMPAIGN_RETRY_PATCH
    )

    async def exercise() -> list[Any]:
        campaign = workflows.CampaignWorkflow()
        campaign.claim(DIGEST)
        run = asyncio.create_task(campaign.run(CampaignWorkflowInput((selected,), DIGEST)))
        while not campaign._progress or campaign._progress[0].phase != "awaiting_retry":
            await asyncio.sleep(0)
        assert not run.done()
        retry_case = cast(
            Callable[[RetryCaseRequest], Awaitable[CaseControllerProgress]],
            campaign.retry_case,
        )
        accepted = await retry_case(RetryCaseRequest(selected.job_id, 2))
        assert accepted.current_submission == 2
        duplicate = await retry_case(RetryCaseRequest(selected.job_id, 2))
        assert duplicate == accepted
        return await run

    result = asyncio.run(exercise())
    assert result[0].phase == "terminal"
    assert result[0].provider_state == "SUCCEEDED"
    assert result[0].current_job_id == selected.job_id.removesuffix("-s1") + "-s2"
    assert len(started) == 2
    retry_commands = started[1].job["taskGroups"][0]["taskSpec"]["runnables"][0]["container"][
        "commands"
    ]
    assert retry_commands[retry_commands.index("--submission-number") + 1] == "2"
    assert retry_commands[retry_commands.index("--destination") + 1] == ("gs://bucket/stable/run-1")


@pytest.mark.parametrize(
    ("provider_state", "failure_type"),
    [
        ("FAILED", None),
        ("NOT_CREATED", "PermanentGoogleError"),
        ("SUCCEEDED", "BatchJobCollision"),
    ],
)
def test_finalize_accepts_settled_operational_failure_and_closes_parent(
    monkeypatch: pytest.MonkeyPatch, provider_state: str, failure_type: str | None
) -> None:
    selected = retryable_spec()

    async def wait_condition(predicate: Any) -> None:
        while not predicate():
            await asyncio.sleep(0)

    async def start(*args: Any, **_kwargs: Any) -> Any:
        request = args[1]

        class Handle:
            first_execution_run_id = "run-failed"

            def __await__(self) -> Any:
                async def done() -> BatchJobOutcome:
                    return BatchJobOutcome(request.resource_name, provider_state, failure_type)

                return done().__await__()

        return Handle()

    monkeypatch.setattr(temporal_workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(temporal_workflow, "start_child_workflow", start)
    monkeypatch.setattr(
        temporal_workflow, "patched", lambda patch: patch == workflows.CAMPAIGN_RETRY_PATCH
    )

    async def exercise() -> list[Any]:
        campaign = workflows.CampaignWorkflow()
        campaign.claim(DIGEST)
        run = asyncio.create_task(campaign.run(CampaignWorkflowInput((selected,), DIGEST)))
        while not campaign._progress or campaign._progress[0].phase != "awaiting_retry":
            await asyncio.sleep(0)
        finalize_campaign = cast(
            Callable[[], list[CaseControllerProgress]], campaign.finalize_campaign
        )
        finalized = finalize_campaign()
        assert finalized[0].phase == "terminal"
        return await run

    result = asyncio.run(exercise())
    assert result[0].provider_state == provider_state
    assert result[0].failure_type == failure_type


@pytest.mark.parametrize("failure", [RuntimeError("watcher bug"), CancelledError()])
def test_retryable_campaign_propagates_unexpected_watcher_fault(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    selected = retryable_spec()

    async def wait_condition(predicate: Any) -> None:
        while not predicate():
            await asyncio.sleep(0)

    async def start(*_args: Any, **_kwargs: Any) -> Any:
        class Handle:
            first_execution_run_id = "run-fault"

            def __await__(self) -> Any:
                async def done() -> BatchJobOutcome:
                    raise failure

                return done().__await__()

        return Handle()

    monkeypatch.setattr(temporal_workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(temporal_workflow, "start_child_workflow", start)
    monkeypatch.setattr(
        temporal_workflow, "patched", lambda patch: patch == workflows.CAMPAIGN_RETRY_PATCH
    )
    campaign = workflows.CampaignWorkflow()
    campaign.claim(DIGEST)
    with pytest.raises(type(failure)):
        asyncio.run(campaign.run(CampaignWorkflowInput((selected,), DIGEST)))


def test_new_campaign_starts_cases_in_bounded_waves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = retryable_spec()
    cases = tuple(replace(base, job_id=f"campaign-wave-{index}-s1") for index in range(9))
    started: list[str] = []
    sleeps: list[timedelta] = []

    async def wait_condition(predicate: Any) -> None:
        while not predicate():
            await asyncio.sleep(0)

    async def sleep(delay: timedelta) -> None:
        sleeps.append(delay)

    async def start(*args: Any, **_kwargs: Any) -> Any:
        request = args[1]
        started.append(request.job_id)

        class Handle:
            first_execution_run_id = f"run-{request.job_id}"

            def __await__(self) -> Any:
                async def done() -> BatchJobOutcome:
                    return BatchJobOutcome(request.resource_name, "SUCCEEDED")

                return done().__await__()

        return Handle()

    monkeypatch.setattr(temporal_workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(temporal_workflow, "start_child_workflow", start)
    monkeypatch.setattr(temporal_workflow, "sleep", sleep)
    monkeypatch.setattr(
        temporal_workflow, "patched", lambda patch: patch == workflows.CAMPAIGN_RETRY_PATCH
    )
    campaign = workflows.CampaignWorkflow()
    campaign.claim(DIGEST)
    result = asyncio.run(campaign.run(CampaignWorkflowInput(cases, DIGEST)))
    assert [item.job_id for item in result] == [item.job_id for item in cases]
    assert started == [item.job_id for item in cases]
    assert sleeps == [workflows.START_WAVE_DELAY]


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
