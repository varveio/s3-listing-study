"""The Google Batch client: create, reconcile, describe, list, cancel.

Every call this harness makes to the provider is here, and nothing here knows
what a campaign is. `ensure_job` is the one that carries judgement -- creating a
job and reconciling with one of that name are the same intent, and which of them
happened is what the caller records as state (`benchmark/docs/model.md` § *The
state column*).
"""

from __future__ import annotations

from typing import Any, cast

from google.api_core.exceptions import (
    AlreadyExists,
    BadRequest,
    FailedPrecondition,
    Forbidden,
    GoogleAPIError,
    NotFound,
    Unauthorized,
)
from google.cloud import batch_v1
from google.protobuf.json_format import MessageToDict, ParseDict

from benchmark.ledger import CampaignError


def _job_from_dict(document: dict[str, Any]) -> batch_v1.Job:
    protobuf = batch_v1.Job.pb(batch_v1.Job())
    ParseDict(document, protobuf)
    return cast(batch_v1.Job, batch_v1.Job.wrap(protobuf))


def _job_document(job: batch_v1.Job) -> dict[str, Any]:
    value = MessageToDict(batch_v1.Job.pb(job), preserving_proto_field_name=False)
    return {
        key: value[key]
        for key in ("labels", "taskGroups", "allocationPolicy", "logsPolicy")
        if key in value
    }


def _matches_intent(job: batch_v1.Job, resource_name: str, expected: dict[str, Any]) -> bool:
    if job.name != resource_name:
        return False
    actual = batch_v1.Job(job)
    for group in actual.task_groups:
        group.name = ""
    actual.allocation_policy.labels.pop("batch-job-id", None)
    # Batch resolves allowedLocations for itself: it echoes the enclosing region
    # back, and expands an unrestricted request into that region's zones. Neither
    # is a different job, so the check is that every location this launch asked
    # for survived, and the provider's own expansion is then left out of the
    # byte comparison on both sides.
    requested = expected.get("allocationPolicy", {}).get("location", {}).get("allowedLocations", [])
    actual_locations = list(actual.allocation_policy.location.allowed_locations)
    if not set(requested) <= set(actual_locations):
        return False
    batch_v1.AllocationPolicy.pb(actual.allocation_policy).ClearField("location")
    intended = _job_from_dict(expected)
    batch_v1.AllocationPolicy.pb(intended.allocation_policy).ClearField("location")
    return _job_document(intended) == _job_document(actual)


def _close_batch_client(client: batch_v1.BatchServiceClient) -> None:
    try:
        client.transport.close()  # type: ignore[no-untyped-call]
    except Exception as exc:
        raise CampaignError(f"could not close Batch client: {exc}") from exc


def ensure_job(
    project: str,
    location: str,
    job_name: str,
    request: dict[str, Any],
    *,
    client: batch_v1.BatchServiceClient | None = None,
) -> tuple[str, str | None]:
    """Create the job, or reconcile with one of that name, and say which state that is.

    `SUBMITTED` covers a job this run created and one of that name it found
    already matching the recorded request; `NOT_CREATED` covers a refusal and a
    job of that name that does not match. `model.md` § *The state column*.
    """
    owned = client is None
    selected = client or batch_v1.BatchServiceClient()
    parent = f"projects/{project}/locations/{location}"
    resource_name = f"{parent}/jobs/{job_name}"
    try:
        try:
            created = selected.create_job(
                parent=parent,
                job=_job_from_dict(request),
                job_id=job_name,
                retry=None,
                timeout=20,
            )
            if not _matches_intent(created, resource_name, request):
                raise CampaignError(
                    f"{job_name}: provider created a job that does not match intent"
                )
            return "SUBMITTED", None
        except AlreadyExists:
            existing = selected.get_job(name=resource_name, retry=None, timeout=20)
            if not _matches_intent(existing, resource_name, request):
                return "NOT_CREATED", f"{job_name}: existing job does not match recorded intent"
            return "SUBMITTED", f"{job_name}: adopted an existing job matching recorded intent"
        except (BadRequest, Forbidden, Unauthorized, FailedPrecondition, NotFound) as exc:
            return "NOT_CREATED", f"{type(exc).__name__}: {exc}"
        except GoogleAPIError as exc:
            try:
                existing = selected.get_job(name=resource_name, retry=None, timeout=20)
            except (NotFound, GoogleAPIError):
                raise CampaignError(f"{job_name}: create outcome is ambiguous: {exc}") from exc
            if not _matches_intent(existing, resource_name, request):
                raise CampaignError(f"{job_name}: ambiguous create found a colliding job") from exc
            return "SUBMITTED", f"{job_name}: ambiguous create found the intended job"
    finally:
        if owned:
            _close_batch_client(selected)


def describe_job(
    project: str, location: str, job_name: str, *, client: batch_v1.BatchServiceClient
) -> str:
    job = client.get_job(
        name=f"projects/{project}/locations/{location}/jobs/{job_name}", retry=None, timeout=20
    )
    return str(batch_v1.JobStatus.State(job.status.state).name)


def list_job_states(
    project: str, location: str, suite: str, *, client: batch_v1.BatchServiceClient
) -> dict[str, str]:
    """Job name -> provider state for this suite's jobs under the parent.

    One paginated call answers a whole polling pass, and because the label
    carries the suite the filter is exact rather than a narrowing over anything
    benchmark-shaped. Rows are still matched by job name afterwards.
    """
    # Quoted because the value is opaque text to the filter grammar: an unquoted
    # suite with hyphens is a 400 from the real API, which is exactly what the
    # first live polling pass got.
    request = {
        "parent": f"projects/{project}/locations/{location}",
        "filter": f'labels.suite="{suite}"',
    }
    return {
        job.name.rsplit("/", 1)[-1]: str(batch_v1.JobStatus.State(job.status.state).name)
        for job in client.list_jobs(request=request, retry=None, timeout=60)
    }


def cancel_job(
    project: str, location: str, job_name: str, *, client: batch_v1.BatchServiceClient
) -> None:
    try:
        client.delete_job(
            name=f"projects/{project}/locations/{location}/jobs/{job_name}",
            retry=None,
            timeout=20,
        )
    except NotFound:
        return
