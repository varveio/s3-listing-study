from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.envconfig import ClientConfig
from temporalio.worker import Worker

from s3_listing_study.temporal import TASK_QUEUE
from s3_listing_study.temporal.activities import (
    ensure_batch_job,
    run_batch_job,
    wait_for_batch_job,
)
from s3_listing_study.temporal.workflows import CampaignWorkflow, CaseWorkflow


async def run_worker() -> None:
    client = await Client.connect(**ClientConfig.load_client_connect_config())
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=(CampaignWorkflow, CaseWorkflow),
        activities=(ensure_batch_job, wait_for_batch_job, run_batch_job),
    )
    await worker.run()


def main() -> None:
    asyncio.run(run_worker())
