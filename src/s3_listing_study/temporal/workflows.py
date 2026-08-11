from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.workflow import (
    ActivityCancellationType,
    ChildWorkflowCancellationType,
    ParentClosePolicy,
)

from s3_listing_study.temporal import MAX_CASES, models


@workflow.defn
class CaseWorkflow:
    @workflow.run
    async def run(self, request: models.BatchJobSpec) -> models.BatchJobOutcome:
        result: models.BatchJobOutcome = await workflow.execute_activity(
            "run_batch_job",
            request,
            cancellation_type=ActivityCancellationType.ABANDON,
            start_to_close_timeout=timedelta(hours=8),
            schedule_to_close_timeout=timedelta(hours=24),
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
    @workflow.run
    async def run(self, request: models.CampaignWorkflowInput) -> list[models.BatchJobOutcome]:
        if not request.cases or len(request.cases) > MAX_CASES:
            raise ApplicationError(
                f"Temporal spike requires between 1 and {MAX_CASES} cases",
                type="InvalidCampaignInput",
                non_retryable=True,
            )
        children = [
            workflow.execute_child_workflow(
                CaseWorkflow.run,
                case,
                id=case.job_id,
                cancellation_type=ChildWorkflowCancellationType.ABANDON,
                parent_close_policy=ParentClosePolicy.ABANDON,
            )
            for case in request.cases
        ]
        return list(await asyncio.gather(*children))
