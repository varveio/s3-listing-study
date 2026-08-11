"""Transactional SQLite campaign controller over deterministic GCP Batch jobs."""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google.api_core.exceptions import GoogleAPIError

from s3_listing_study.manager.campaign import ledger, provider
from s3_listing_study.manager.campaign.models import (
    BatchJobOutcome,
    BatchJobSpec,
    CaseControllerProgress,
    retry_job,
)

START_WAVE_SIZE = 8
START_WAVE_DELAY_S = 1.0


class ControllerError(RuntimeError):
    """The local controller could not make a safe state transition."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@contextmanager
def _provider_call_lock(ledger_path: Path, job_id: str) -> Iterator[None]:
    """Serialize one deterministic provider effect across processes and crashes."""
    lock_dir = ledger_path.parent / f".{ledger_path.name}.provider-locks"
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(lock_dir / job_id, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _settled(outcome: BatchJobOutcome) -> bool:
    return outcome.state in ("SUCCEEDED", "FAILED", "NOT_CREATED")


def _record_outcome(connection: Any, spec: BatchJobSpec, outcome: BatchJobOutcome) -> None:
    ledger.record_provider_outcome(
        connection,
        base_job_id=spec.base_job_id,
        expected_current_job_id=spec.job_id,
        state=outcome.state,
        failure_type=outcome.failure_type,
        resource_name=outcome.resource_name,
        settled=_settled(outcome),
        now=utc_now(),
    )


def _spec(row: Mapping[str, Any], owner: Mapping[str, Any]) -> BatchJobSpec:
    return BatchJobSpec(
        str(owner["project"]),
        str(owner["location"]),
        str(row["base_job_id"]),
        str(row["current_job_id"]),
        json.loads(str(row["job_json"])),
        int(row["controller_timeout_s"]),
        int(row["current_submission"]),
    )


def start_campaign(
    *,
    ledger_path: Path,
    campaign: str,
    project: str,
    location: str,
    results_bucket: str,
    manifest_sha256: str,
    attempts: Sequence[Mapping[str, Any]],
    jobs: Sequence[dict[str, Any]],
    controller_timeouts: Sequence[int],
) -> list[dict[str, Any]]:
    """Persist all intent, then asynchronously create jobs in paced waves."""
    cases = [
        {
            "base_job_id": attempt["job_id"],
            "job": job,
            "controller_timeout_s": timeout,
        }
        for attempt, job, timeout in zip(attempts, jobs, controller_timeouts, strict=True)
    ]
    now = utc_now()
    with ledger.open_ledger(ledger_path) as connection:
        ledger.register_campaign(
            connection,
            campaign=campaign,
            project=project,
            location=location,
            results_bucket=results_bucket,
            manifest_sha256=manifest_sha256,
            cases=cases,
            now=now,
        )
        for attempt in attempts:
            existing = connection.execute(
                "SELECT case_json FROM attempts WHERE job_id = ?", (attempt["job_id"],)
            ).fetchone()
            if existing is None:
                ledger.record_intent(connection, attempt=attempt, campaign=campaign, now=now)
            elif json.loads(existing["case_json"]) != dict(attempt):
                raise ControllerError(f"{attempt['job_id']}: ledger attempt does not match")

    statuses: list[dict[str, Any]] = []
    for index, base_job_id in enumerate((str(item["job_id"]) for item in attempts), start=1):
        with _provider_call_lock(ledger_path, base_job_id):
            with ledger.open_ledger(ledger_path) as connection:
                owner = ledger.campaign_record(connection, campaign)
                rows = {
                    row["base_job_id"]: row for row in ledger.controller_cases(connection, campaign)
                }
                row = rows[base_job_id]
                first_provider_call = False
                if row["phase"] == "pending":
                    first_provider_call = ledger.reserve_start(
                        connection, base_job_id=base_job_id, now=utc_now()
                    )
                    row["phase"] = "running"
                spec = _spec(row, owner)
            if row["provider_settled"] or row["provider_resource_name"]:
                statuses.append({"job_id": spec.job_id, "state": row["phase"]})
                continue
            try:
                outcome = (
                    provider.ensure_batch_job(spec)
                    if first_provider_call
                    else provider.ensure_batch_job(spec, collision_on_adoption=False)
                )
            except (GoogleAPIError, provider.ProviderError) as exc:
                statuses.append(
                    {
                        "job_id": spec.job_id,
                        "state": "unsettled",
                        "error_type": type(exc).__name__,
                    }
                )
            else:
                with ledger.open_ledger(ledger_path) as connection:
                    _record_outcome(connection, spec, outcome)
                statuses.append({"job_id": spec.job_id, "state": outcome.state})
        if index % START_WAVE_SIZE == 0 and index < len(attempts):
            time.sleep(START_WAVE_DELAY_S)
    return statuses


def reconcile_once(*, ledger_path: Path, campaign: str) -> list[CaseControllerProgress]:
    """Poll each active exact resource once and persist terminal settlement."""
    with ledger.open_ledger(ledger_path) as connection:
        owner = ledger.campaign_record(connection, campaign)
        rows = ledger.controller_cases(connection, campaign)
    for row in rows:
        if row["phase"] != "running":
            continue
        spec = _spec(row, owner)
        try:
            outcome = provider.observe_batch_job(spec)
        except provider.ProviderError:
            # A not-yet-visible deterministic resource is still a possible effect.
            # Keep the case active and unsettled; never translate ambiguity to absence.
            continue
        if row["failure_type"] == "BatchJobCollision" and outcome.failure_type is None:
            outcome = BatchJobOutcome(
                outcome.resource_name,
                outcome.state,
                "BatchJobCollision",
                outcome.adopted,
            )
        with ledger.open_ledger(ledger_path) as connection:
            _record_outcome(connection, spec, outcome)
    return progress(ledger_path=ledger_path, campaign=campaign)


def progress(*, ledger_path: Path, campaign: str) -> list[CaseControllerProgress]:
    with ledger.open_ledger(ledger_path) as connection:
        rows = ledger.controller_cases(connection, campaign)
    return [
        CaseControllerProgress(
            job_id=str(row["base_job_id"]),
            phase=str(row["phase"]),
            provider_state=row["provider_state"],
            failure_type=row["failure_type"],
            provider_resource_name=row["provider_resource_name"],
            provider_settled=bool(row["provider_settled"]),
            current_submission=int(row["current_submission"]),
            current_job_id=str(row["current_job_id"]),
            accepted_failure=bool(row["accepted_failure"]),
        )
        for row in rows
    ]


def retry_case(
    *, ledger_path: Path, campaign: str, base_job_id: str, submission: int
) -> CaseControllerProgress:
    """Reserve exactly the next submission, then create its provider resource."""
    with _provider_call_lock(ledger_path, base_job_id):
        with ledger.open_ledger(ledger_path) as connection:
            owner = ledger.campaign_record(connection, campaign)
            rows = {
                row["base_job_id"]: row for row in ledger.controller_cases(connection, campaign)
            }
            row = rows.get(base_job_id)
            if row is None:
                raise ControllerError("--job-id is not an original frozen manifest job ID")
            current = _spec(row, owner)
            persisted_redrive = False
            if submission == current.submission:
                if not (
                    row["phase"] == "running"
                    and not row["provider_settled"]
                    and row["provider_resource_name"] is None
                ):
                    return next(
                        item
                        for item in progress(ledger_path=ledger_path, campaign=campaign)
                        if item.job_id == base_job_id
                    )
                # The previous command may have crashed after the durable reservation
                # and before create. Re-drive the same deterministic provider request.
                retried = current
                persisted_redrive = True
            else:
                try:
                    retried = retry_job(current, submission)
                except ValueError as exc:
                    raise ControllerError(str(exc)) from None
                original = connection.execute(
                    "SELECT case_json FROM attempts WHERE job_id = ?", (base_job_id,)
                ).fetchone()
                if original is None:
                    raise ControllerError("base attempt is absent from the ledger")
                attempt = json.loads(original["case_json"])
                attempt["job_id"] = retried.job_id
                attempt["submission"] = submission
                ledger.reserve_retry(
                    connection,
                    base_job_id=base_job_id,
                    submission=submission,
                    current_job_id=retried.job_id,
                    job=retried.job,
                    attempt=attempt,
                    now=utc_now(),
                )
        outcome = (
            provider.ensure_batch_job(retried, collision_on_adoption=False)
            if persisted_redrive
            else provider.ensure_batch_job(retried)
        )
        with ledger.open_ledger(ledger_path) as connection:
            _record_outcome(connection, retried, outcome)
    return next(
        item
        for item in progress(ledger_path=ledger_path, campaign=campaign)
        if item.job_id == base_job_id
    )


def finalize(*, ledger_path: Path, campaign: str) -> list[CaseControllerProgress]:
    with ledger.open_ledger(ledger_path) as connection:
        ledger.finalize_campaign(connection, campaign=campaign, now=utc_now())
    return progress(ledger_path=ledger_path, campaign=campaign)
