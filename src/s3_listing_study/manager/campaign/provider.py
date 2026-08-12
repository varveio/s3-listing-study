"""GCP Batch create/adopt/observe operations for the local controller."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from google.api_core import exceptions as google_exceptions
from google.cloud import batch_v1
from google.protobuf.json_format import MessageToDict, ParseDict  # type: ignore[import-untyped]

from s3_listing_study.manager.campaign.models import BatchJobOutcome, BatchJobSpec

TERMINAL_STATES = frozenset(("SUCCEEDED", "FAILED"))
PERMANENT_GOOGLE_ERRORS: tuple[type[BaseException], ...] = (
    google_exceptions.BadRequest,
    google_exceptions.Forbidden,
    google_exceptions.Unauthorized,
    google_exceptions.FailedPrecondition,
    google_exceptions.NotFound,
)


class ProviderError(RuntimeError):
    """The provider could not be observed without guessing about an effect."""


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
    """Retain only requested immutable input, removing known provider materialization."""
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
    """Whether an existing provider resource is exactly the frozen request."""
    if job.name != spec.resource_name:
        return False
    expected = _job_document(_job_from_json(spec.job))
    immutable = {
        key: expected[key]
        for key in ("labels", "taskGroups", "allocationPolicy", "logsPolicy")
        if key in expected
    }
    return immutable == _normalized_immutable(spec, job)


def ensure_batch_job(
    spec: BatchJobSpec,
    *,
    collision_on_adoption: bool = True,
    client: batch_v1.BatchServiceClient | None = None,
) -> BatchJobOutcome:
    """Create or exactly adopt a deterministic job; prove explicit no-effect when possible."""
    owned = client is None
    selected = client or batch_v1.BatchServiceClient()
    identity = spec.job.get("labels", {}).get("s3-study-attempt")
    if not isinstance(identity, str) or not identity:
        return BatchJobOutcome(None, "NOT_CREATED", "BatchJobCollision")
    try:
        try:
            created = selected.create_job(
                parent=f"projects/{spec.project}/locations/{spec.location}",
                job=_job_from_json(spec.job),
                job_id=spec.job_id,
                retry=None,
                timeout=20,
            )
        except google_exceptions.AlreadyExists:
            existing = selected.get_job(name=spec.resource_name, retry=None, timeout=20)
            exact = validated_adoption(spec, existing)
            return BatchJobOutcome(
                spec.resource_name,
                batch_v1.JobStatus.State(existing.status.state).name,
                "BatchJobCollision" if collision_on_adoption or not exact else None,
                True,
            )
        except PERMANENT_GOOGLE_ERRORS:
            return BatchJobOutcome(None, "NOT_CREATED", "PermanentGoogleError")
        if created.name != spec.resource_name:
            raise ProviderError("Batch created an unexpected resource name")
        return BatchJobOutcome(
            spec.resource_name,
            batch_v1.JobStatus.State(created.status.state).name or "QUEUED",
        )
    finally:
        if owned:
            cast(Callable[[], None], selected.transport.close)()


def observe_batch_job(
    spec: BatchJobSpec, *, client: batch_v1.BatchServiceClient | None = None
) -> BatchJobOutcome:
    """Read the exact provider resource and preserve collision status to settlement."""
    owned = client is None
    selected = client or batch_v1.BatchServiceClient()
    try:
        job = selected.get_job(name=spec.resource_name, retry=None, timeout=20)
        state = batch_v1.JobStatus.State(job.status.state).name
        return BatchJobOutcome(
            spec.resource_name,
            state,
            None if validated_adoption(spec, job) else "BatchJobCollision",
            True,
        )
    except google_exceptions.NotFound as exc:
        raise ProviderError(f"Batch resource is not visible: {spec.resource_name}") from exc
    finally:
        if owned:
            cast(Callable[[], None], selected.transport.close)()
