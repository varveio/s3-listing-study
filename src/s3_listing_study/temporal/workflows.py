from __future__ import annotations

import asyncio
import copy
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (
    ApplicationError,
    ChildWorkflowError,
    FailureError,
    WorkflowAlreadyStartedError,
)
from temporalio.workflow import (
    ActivityCancellationType,
    ChildWorkflowCancellationType,
    ParentClosePolicy,
)

from s3_listing_study.temporal import models

SETTLED_PROVIDER_STATES = ("SUCCEEDED", "FAILED")
NO_EFFECT_PROVIDER_STATE = "NOT_CREATED"
SETTLEMENT_RETRY_PATCH = "provider-settlement-retry-v1"
ENSURE_WAIT_PATCH = "ensure-wait-activities-v1"
CAMPAIGN_RETRY_PATCH = "campaign-targeted-retry-v1"
START_WAVE_SIZE = 8
START_WAVE_DELAY = timedelta(seconds=1)


def _safe_failure_type(error: FailureError) -> str:
    """Return the deepest typed Temporal failure without exposing messages."""
    current = error
    while isinstance(current.cause, FailureError):
        current = current.cause
    if isinstance(current, ApplicationError) and current.type:
        return current.type
    return type(current).__name__


@workflow.defn
class CaseWorkflow:
    @workflow.run
    async def run(self, request: models.BatchJobSpec) -> models.BatchJobOutcome:
        if workflow.patched(ENSURE_WAIT_PATCH):
            ensured: models.BatchJobHandle = await workflow.execute_activity(
                "ensure_batch_job",
                request,
                cancellation_type=ActivityCancellationType.ABANDON,
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=0),
                result_type=models.BatchJobHandle,
            )
            valid_ready = ensured.state == "READY" and ensured.failure_type in (
                None,
                "BatchJobCollision",
            )
            valid_no_effect = ensured.state == "NOT_CREATED" and ensured.failure_type in (
                "PermanentGoogleError",
                "BatchJobCollision",
            )
            if ensured.resource_name != request.resource_name or not (
                valid_ready or valid_no_effect
            ):
                raise ApplicationError(
                    "ensure Activity returned an invalid handle",
                    type="InvalidBatchJobHandle",
                    non_retryable=True,
                )
            if valid_no_effect:
                return models.BatchJobOutcome(
                    ensured.resource_name, ensured.state, ensured.failure_type
                )
            result: models.BatchJobOutcome = await workflow.execute_activity(
                "wait_for_batch_job",
                ensured,
                cancellation_type=ActivityCancellationType.ABANDON,
                start_to_close_timeout=timedelta(seconds=request.controller_timeout_s),
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=0),
                result_type=models.BatchJobOutcome,
            )
            return result
        options: dict[str, Any] = {
            "cancellation_type": ActivityCancellationType.ABANDON,
            "start_to_close_timeout": timedelta(seconds=request.controller_timeout_s),
            "heartbeat_timeout": timedelta(seconds=30),
            "result_type": models.BatchJobOutcome,
        }
        if workflow.patched(SETTLEMENT_RETRY_PATCH):
            # There is deliberately no schedule-to-close bound. Once a deterministic
            # Batch job may exist, the controller must keep adopting and observing it
            # until the provider settles. Each individual Activity attempt and RPC is
            # still bounded.
            options["retry_policy"] = RetryPolicy(maximum_attempts=0)
        else:
            # Replay compatibility for retained pre-settlement-safety histories.
            options["schedule_to_close_timeout"] = timedelta(
                seconds=request.controller_timeout_s * 3
            )
            options["retry_policy"] = RetryPolicy(
                maximum_attempts=8,
                non_retryable_error_types=("PermanentGoogleError", "BatchJobCollision"),
            )
        legacy_result: models.BatchJobOutcome = await workflow.execute_activity(
            "run_batch_job", request, **options
        )
        return legacy_result


@workflow.defn
class CampaignWorkflow:
    def __init__(self) -> None:
        self._claims: set[str] = set()
        self._progress: tuple[models.CaseControllerProgress, ...] = ()
        self._base_cases: dict[str, models.BatchJobSpec] = {}
        self._retryable = False
        self._watchers: set[asyncio.Task[None]] = set()
        self._watcher_failure: BaseException | None = None

    @workflow.signal
    def claim(self, campaign_digest: str) -> None:
        """Idempotently record a frozen-input ownership claim."""
        self._claims.add(campaign_digest)

    @workflow.query
    def progress(self) -> list[models.CaseControllerProgress]:
        """Return deterministic per-case controller state in campaign order."""
        return list(self._progress)

    def _replace_progress(self, updated: models.CaseControllerProgress) -> None:
        self._progress = tuple(
            updated if item.job_id == updated.job_id else item for item in self._progress
        )

    def _progress_for(self, job_id: str) -> models.CaseControllerProgress:
        for item in self._progress:
            if item.job_id == job_id:
                return item
        raise ApplicationError(
            "unknown manifest job ID", type="InvalidRetryRequest", non_retryable=True
        )

    @staticmethod
    def _retry_spec(case: models.BatchJobSpec, submission: int) -> models.BatchJobSpec:
        stem, separator, original = case.job_id.rpartition("-s")
        if not separator or original != "1" or submission < 2:
            raise ApplicationError(
                "base Batch job ID is not a submission-1 identity",
                type="InvalidRetryRequest",
                non_retryable=True,
            )
        job_id = f"{stem}-s{submission}"
        job = copy.deepcopy(case.job)
        try:
            commands = job["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]["commands"]
        except (KeyError, IndexError, TypeError):
            commands = None
        if not isinstance(commands, list) or any(not isinstance(value, str) for value in commands):
            raise ApplicationError(
                "Batch worker command is not retryable",
                type="InvalidRetryRequest",
                non_retryable=True,
            )
        for flag, old, new in (
            ("--job-id", case.job_id, job_id),
            ("--submission-number", "1", str(submission)),
        ):
            positions = [index for index, value in enumerate(commands) if value == flag]
            if len(positions) != 1 or positions[0] + 1 >= len(commands):
                raise ApplicationError(
                    "Batch worker command omits retry identity",
                    type="InvalidRetryRequest",
                    non_retryable=True,
                )
            value_index = positions[0] + 1
            if commands[value_index] != old:
                raise ApplicationError(
                    "Batch worker command retry identity does not match",
                    type="InvalidRetryRequest",
                    non_retryable=True,
                )
            commands[value_index] = new
        return models.BatchJobSpec(
            case.project, case.location, job_id, job, case.controller_timeout_s
        )

    async def _start_case(
        self, base_job_id: str, case: models.BatchJobSpec, submission: int
    ) -> models.CaseControllerProgress:
        try:
            child = await workflow.start_child_workflow(
                CaseWorkflow.run,
                case,
                id=case.job_id,
                cancellation_type=ChildWorkflowCancellationType.ABANDON,
                parent_close_policy=ParentClosePolicy.ABANDON,
            )
        except WorkflowAlreadyStartedError as exc:
            progress = models.CaseControllerProgress(
                base_job_id,
                None,
                "terminal",
                None,
                _safe_failure_type(exc),
                None,
                False,
                submission,
                case.job_id,
            )
            self._replace_progress(progress)
            return progress
        progress = models.CaseControllerProgress(
            base_job_id,
            child.first_execution_run_id,
            "running",
            None,
            None,
            None,
            False,
            submission,
            case.job_id,
        )
        self._replace_progress(progress)
        watcher = asyncio.create_task(self._watch_child(base_job_id, case, submission, child))
        self._watchers.add(watcher)
        watcher.add_done_callback(self._watchers.discard)
        return progress

    async def _watch_child(
        self,
        base_job_id: str,
        case: models.BatchJobSpec,
        submission: int,
        handle: workflow.ChildWorkflowHandle[Any, models.BatchJobOutcome],
    ) -> None:
        try:
            await self._finish_child(base_job_id, case, submission, handle)
        except BaseException as exc:
            # Consume the task exception, wake the run method, and preserve the
            # original programming/cancellation fault for parent failure.
            if self._watcher_failure is None:
                self._watcher_failure = exc

    @workflow.update
    async def retry_case(self, request: models.RetryCaseRequest) -> models.CaseControllerProgress:
        if not self._retryable:
            raise ApplicationError(
                "campaign predates targeted retries",
                type="RetryUnsupported",
                non_retryable=True,
            )
        current = self._progress_for(request.job_id)
        if request.submission == current.current_submission:
            return current
        if (
            request.submission != current.current_submission + 1
            or current.phase != "awaiting_retry"
        ):
            raise ApplicationError(
                "submission must be exactly current + 1 for an awaiting case",
                type="InvalidRetryRequest",
                non_retryable=True,
            )
        base = self._base_cases[request.job_id]
        retry = self._retry_spec(base, request.submission)
        return await self._start_case(request.job_id, retry, request.submission)

    @workflow.update
    def finalize_campaign(self) -> list[models.CaseControllerProgress]:
        if not self._retryable:
            raise ApplicationError(
                "campaign predates explicit finalization",
                type="FinalizeUnsupported",
                non_retryable=True,
            )
        if any(item.phase in ("pending", "running") for item in self._progress):
            raise ApplicationError(
                "campaign still has active cases",
                type="CampaignStillRunning",
                non_retryable=True,
            )
        for item in self._progress:
            if item.phase == "awaiting_retry":
                self._replace_progress(
                    models.CaseControllerProgress(
                        item.job_id,
                        item.child_run_id,
                        "terminal",
                        item.provider_state,
                        item.failure_type,
                        item.provider_resource_name,
                        item.provider_settled,
                        item.current_submission,
                        item.current_job_id,
                    )
                )
        return list(self._progress)

    async def _finish_child(
        self,
        base_job_id: str,
        case: models.BatchJobSpec,
        submission: int,
        handle: workflow.ChildWorkflowHandle[Any, models.BatchJobOutcome],
    ) -> None:
        try:
            outcome = await handle
        except ChildWorkflowError as exc:
            self._replace_progress(
                models.CaseControllerProgress(
                    base_job_id,
                    handle.first_execution_run_id,
                    "terminal",
                    None,
                    _safe_failure_type(exc),
                    None,
                    False,
                    submission,
                    case.job_id,
                )
            )
        else:
            terminal_job = outcome.state in SETTLED_PROVIDER_STATES
            valid_terminal_job = terminal_job and outcome.failure_type in (
                None,
                "BatchJobCollision",
            )
            valid_no_effect = (
                outcome.state == NO_EFFECT_PROVIDER_STATE
                and outcome.failure_type in ("PermanentGoogleError", "BatchJobCollision")
            )
            if outcome.resource_name != case.resource_name or not (
                valid_terminal_job or valid_no_effect
            ):
                self._replace_progress(
                    models.CaseControllerProgress(
                        base_job_id,
                        handle.first_execution_run_id,
                        "terminal",
                        None,
                        "InvalidBatchJobOutcome",
                        None,
                        False,
                        submission,
                        case.job_id,
                    )
                )
            else:
                requires_operator_decision = self._retryable and (
                    outcome.state != "SUCCEEDED" or outcome.failure_type is not None
                )
                self._replace_progress(
                    models.CaseControllerProgress(
                        base_job_id,
                        handle.first_execution_run_id,
                        ("awaiting_retry" if requires_operator_decision else "terminal"),
                        outcome.state,
                        outcome.failure_type,
                        outcome.resource_name if terminal_job else None,
                        True,
                        submission,
                        case.job_id,
                    )
                )

    @workflow.run
    async def run(
        self, request: models.CampaignWorkflowInput
    ) -> list[models.CaseControllerProgress]:
        if not request.cases:
            raise ApplicationError(
                "campaign contains no scheduled runs",
                type="InvalidCampaignInput",
                non_retryable=True,
            )
        self._progress = tuple(
            models.CaseControllerProgress(
                case.job_id,
                None,
                "pending",
                None,
                None,
                None,
                False,
                1,
                case.job_id,
            )
            for case in request.cases
        )
        await workflow.wait_condition(lambda: request.campaign_digest in self._claims)
        if workflow.patched(CAMPAIGN_RETRY_PATCH):
            self._retryable = True
            self._base_cases = {case.job_id: case for case in request.cases}
            for index, case in enumerate(request.cases, start=1):
                await self._start_case(case.job_id, case, 1)
                if index % START_WAVE_SIZE == 0 and index < len(request.cases):
                    await workflow.sleep(START_WAVE_DELAY)
            await workflow.wait_condition(
                lambda: (
                    self._watcher_failure is not None
                    or all(item.phase == "terminal" for item in self._progress)
                )
            )
            if self._watcher_failure is not None:
                raise self._watcher_failure
            return list(self._progress)
        watchers: list[asyncio.Task[None]] = []
        for case in request.cases:
            try:
                child = await workflow.start_child_workflow(
                    CaseWorkflow.run,
                    case,
                    id=case.job_id,
                    cancellation_type=ChildWorkflowCancellationType.ABANDON,
                    parent_close_policy=ParentClosePolicy.ABANDON,
                )
            except WorkflowAlreadyStartedError as exc:
                self._replace_progress(
                    models.CaseControllerProgress(
                        case.job_id,
                        None,
                        "terminal",
                        None,
                        _safe_failure_type(exc),
                        None,
                        False,
                    )
                )
                continue
            self._replace_progress(
                models.CaseControllerProgress(
                    case.job_id,
                    child.first_execution_run_id,
                    "running",
                    None,
                    None,
                )
            )
            watchers.append(asyncio.create_task(self._finish_child(case.job_id, case, 1, child)))
        await asyncio.gather(*watchers)
        return list(self._progress)
