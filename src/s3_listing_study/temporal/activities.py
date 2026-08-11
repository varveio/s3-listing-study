from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

from google.api_core import exceptions as google_exceptions
from google.cloud import batch_v1
from google.protobuf.json_format import ParseDict
from temporalio import activity
from temporalio.exceptions import ApplicationError

from s3_listing_study.temporal.models import BatchJobOutcome, BatchJobSpec

TERMINAL_STATES = frozenset(("SUCCEEDED", "FAILED"))
PERMANENT_GOOGLE_ERRORS: tuple[type[BaseException], ...] = (
    google_exceptions.BadRequest,
    google_exceptions.Forbidden,
    google_exceptions.Unauthorized,
    google_exceptions.FailedPrecondition,
    google_exceptions.NotFound,
)


def _job_from_json(document: dict[str, Any]) -> batch_v1.Job:
    protobuf = batch_v1.Job.pb(batch_v1.Job())
    ParseDict(document, protobuf)
    return cast(batch_v1.Job, batch_v1.Job.wrap(protobuf))


def _collision(message: str) -> ApplicationError:
    return ApplicationError(message, type="BatchJobCollision", non_retryable=True)


def _validated_adoption(spec: BatchJobSpec, job: batch_v1.Job) -> None:
    if job.name != spec.resource_name:
        raise _collision("preexisting Batch job has the wrong resource name")
    expected = _job_from_json(spec.job)
    actual = batch_v1.Job(job)
    for group in actual.task_groups:
        group.name = ""  # Batch supplies this output-only child resource name.
    if not expected.allocation_policy.labels:
        actual.allocation_policy.labels = {}
    if not expected.allocation_policy.location.allowed_locations:
        actual.allocation_policy.location = cast(Any, None)
    immutable = ("labels", "task_groups", "allocation_policy", "logs_policy")
    if any(getattr(expected, field) != getattr(actual, field) for field in immutable):
        raise _collision(
            "preexisting Batch job does not match the expected attempt identity and execution"
        )


@activity.defn
async def run_batch_job(spec: BatchJobSpec) -> BatchJobOutcome:
    info = activity.info()
    client = batch_v1.BatchServiceAsyncClient()
    parent = f"projects/{spec.project}/locations/{spec.location}"
    adopted = False
    job: batch_v1.Job | None = None
    identity = spec.job.get("labels", {}).get("s3-study-attempt")
    if not isinstance(identity, str) or not identity:
        return BatchJobOutcome(spec.resource_name, "NOT_CREATED", "BatchJobCollision")
    try:
        activity.heartbeat({"job_name": spec.resource_name, "state": "STARTING"})
        collision = False
        try:
            await client.create_job(
                parent=parent,
                job=_job_from_json(spec.job),
                job_id=spec.job_id,
                retry=None,
                timeout=20,
            )
        except google_exceptions.AlreadyExists:
            adopted = True
            collision = info.attempt == 1
            activity.heartbeat({"job_name": spec.resource_name, "state": "ADOPTING"})
            job = await client.get_job(name=spec.resource_name, retry=None, timeout=20)
        except PERMANENT_GOOGLE_ERRORS:
            # A definitive create rejection proves this request created no provider
            # effect. Errors after create/adoption are intentionally retryable below.
            activity.heartbeat({"job_name": spec.resource_name, "state": "NOT_CREATED"})
            return BatchJobOutcome(spec.resource_name, "NOT_CREATED", "PermanentGoogleError")
        while True:
            if job is None:
                activity.heartbeat({"job_name": spec.resource_name, "state": "GETTING"})
                job = await client.get_job(name=spec.resource_name, retry=None, timeout=20)
            if adopted:
                try:
                    _validated_adoption(spec, job)
                except ApplicationError:
                    # A conflicting deterministic resource still has to settle before
                    # its possible effects can be declared final.
                    collision = True
            state = batch_v1.JobStatus.State(job.status.state).name
            activity.heartbeat({"job_name": spec.resource_name, "state": state})
            if state in TERMINAL_STATES:
                return BatchJobOutcome(
                    spec.resource_name,
                    state,
                    "BatchJobCollision" if collision else None,
                )
            await asyncio.sleep(10)
            job = None
    except ApplicationError:
        raise
    finally:
        close = cast(Callable[[], Awaitable[None]], client.transport.close)
        await close()
