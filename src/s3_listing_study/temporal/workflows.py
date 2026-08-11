from __future__ import annotations

import asyncio
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
        result: models.BatchJobOutcome = await workflow.execute_activity(
            "run_batch_job",
            request,
            cancellation_type=ActivityCancellationType.ABANDON,
            start_to_close_timeout=timedelta(seconds=request.controller_timeout_s),
            schedule_to_close_timeout=timedelta(seconds=request.controller_timeout_s * 3),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                maximum_attempts=8,
                non_retryable_error_types=("PermanentGoogleError", "BatchJobCollision"),
            ),
            result_type=models.BatchJobOutcome,
        )
        return result


@workflow.defn
class CampaignWorkflow:
    def __init__(self) -> None:
        self._claims: set[str] = set()
        self._progress: tuple[models.CaseControllerProgress, ...] = ()

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

    async def _finish_child(
        self,
        case: models.BatchJobSpec,
        handle: workflow.ChildWorkflowHandle[Any, models.BatchJobOutcome],
    ) -> None:
        try:
            outcome = await handle
        except ChildWorkflowError as exc:
            self._replace_progress(
                models.CaseControllerProgress(
                    case.job_id,
                    handle.first_execution_run_id,
                    "terminal",
                    None,
                    _safe_failure_type(exc),
                )
            )
        else:
            if outcome.resource_name != case.resource_name or outcome.state not in (
                "SUCCEEDED",
                "FAILED",
            ):
                self._replace_progress(
                    models.CaseControllerProgress(
                        case.job_id,
                        handle.first_execution_run_id,
                        "terminal",
                        None,
                        "InvalidBatchJobOutcome",
                    )
                )
            else:
                self._replace_progress(
                    models.CaseControllerProgress(
                        case.job_id,
                        handle.first_execution_run_id,
                        "terminal",
                        outcome.state,
                        None,
                        outcome.resource_name,
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
            models.CaseControllerProgress(case.job_id, None, "pending", None, None)
            for case in request.cases
        )
        await workflow.wait_condition(lambda: request.campaign_digest in self._claims)
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
                        case.job_id, None, "terminal", None, _safe_failure_type(exc)
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
            watchers.append(asyncio.create_task(self._finish_child(case, child)))
        await asyncio.gather(*watchers)
        return list(self._progress)
