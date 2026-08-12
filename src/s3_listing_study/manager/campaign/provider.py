from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from google.api_core import exceptions as google_exceptions
from google.cloud import batch_v1
from google.protobuf.json_format import MessageToDict, ParseDict  # type: ignore[import-untyped]

import twinstamp as ts
from s3_listing_study.manager.campaign.models import BatchJobSpec

PERMANENT_GOOGLE_ERRORS: tuple[type[BaseException], ...] = (
    google_exceptions.BadRequest, google_exceptions.Forbidden, google_exceptions.Unauthorized,
    google_exceptions.FailedPrecondition, google_exceptions.NotFound,
)


class ProviderError(RuntimeError):
    """The Batch response could not be safely interpreted."""


def _job_from_json(document: dict[str, Any]) -> batch_v1.Job:
    protobuf = batch_v1.Job.pb(batch_v1.Job())
    ParseDict(document, protobuf)
    return cast(batch_v1.Job, batch_v1.Job.wrap(protobuf))


def _job_document(job: batch_v1.Job) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        MessageToDict(
            batch_v1.Job.pb(job),
            preserving_proto_field_name=False,
            use_integers_for_enums=False,
        ),
    )


def _normalized_immutable(spec: BatchJobSpec, job: batch_v1.Job) -> dict[str, Any]:
    actual = batch_v1.Job(job)
    protobuf = batch_v1.Job.pb(actual)
    for field in ("name", "uid", "create_time", "update_time", "status"):
        protobuf.ClearField(field)
    for group in actual.task_groups:
        group.name = ""
    actual.allocation_policy.labels.pop("batch-job-id", None)
    parent_region = f"regions/{spec.location}"
    requested = spec.job.get("allocationPolicy", {}).get("location", {}).get("allowedLocations", [])
    actual_locations = actual.allocation_policy.location.allowed_locations
    if parent_region not in requested and parent_region in actual_locations:
        actual_locations.remove(parent_region)
    if not requested and not actual_locations:
        actual.allocation_policy.location = cast(Any, None)
    document = _job_document(actual)
    return {
        key: document[key]
        for key in ("labels", "taskGroups", "allocationPolicy", "logsPolicy")
        if key in document
    }


def validated_adoption(spec: BatchJobSpec, job: batch_v1.Job) -> bool:
    if job.name != spec.resource_name:
        return False
    expected = _job_document(_job_from_json(spec.job))
    immutable = {
        key: expected[key]
        for key in ("labels", "taskGroups", "allocationPolicy", "logsPolicy")
        if key in expected
    }
    return immutable == _normalized_immutable(spec, job)


def _state(job: batch_v1.Job) -> str:
    return batch_v1.JobStatus.State(job.status.state).name or "QUEUED"


def ensure_batch_job(
    spec: ts.SubmissionSpec[BatchJobSpec], *, client: batch_v1.BatchServiceClient | None = None
) -> ts.EnsureFact:
    batch = spec.payload
    owned = client is None
    selected = client or batch_v1.BatchServiceClient()
    identity = batch.job.get("labels", {}).get("s3-study-attempt")
    if not isinstance(identity, str) or not identity:
        return ts.Collision("BatchJobCollision", state="NOT_CREATED", settled=True)
    try:
        try:
            created = selected.create_job(
                parent=f"projects/{batch.project}/locations/{batch.location}",
                job=_job_from_json(batch.job), job_id=batch.job_id, retry=None, timeout=20,
            )
        except google_exceptions.AlreadyExists:
            existing = selected.get_job(name=batch.resource_name, retry=None, timeout=20)
            state = _state(existing)
            settled = state in ("SUCCEEDED", "FAILED", "NOT_CREATED")
            if validated_adoption(batch, existing):
                return ts.AdoptedExact(batch.resource_name, state, settled)
            return ts.Collision("BatchJobCollision", batch.resource_name, state, settled)
        except PERMANENT_GOOGLE_ERRORS:
            return ts.RejectedNoEffect("PermanentGoogleError")
        if created.name != batch.resource_name:
            return ts.Ambiguous("Batch created an unexpected resource name", "ProviderError")
        state = _state(created)
        settled = state in ("SUCCEEDED", "FAILED", "NOT_CREATED")
        return ts.Created(batch.resource_name, state, settled)
    except google_exceptions.GoogleAPIError as exc:
        return ts.Ambiguous(str(exc), type(exc).__name__)
    finally:
        if owned:
            cast(Callable[[], None], selected.transport.close)()


def observe_batch_job(
    spec: ts.SubmissionSpec[BatchJobSpec], *, client: batch_v1.BatchServiceClient | None = None
) -> ts.ObservationFact:
    batch = spec.payload
    owned = client is None
    selected = client or batch_v1.BatchServiceClient()
    try:
        job = selected.get_job(name=batch.resource_name, retry=None, timeout=20)
        state = _state(job)
        settled = state in ("SUCCEEDED", "FAILED", "NOT_CREATED")
        if validated_adoption(batch, job):
            return ts.ObservedExact(batch.resource_name, state, settled)
        return ts.ObservedCollision("BatchJobCollision", batch.resource_name, state, settled)
    except google_exceptions.NotFound:
        return ts.NotVisible(f"Batch resource is not visible: {batch.resource_name}")
    except google_exceptions.GoogleAPIError as exc:
        return ts.ObservationAmbiguous(str(exc), type(exc).__name__)
    finally:
        if owned:
            cast(Callable[[], None], selected.transport.close)()
