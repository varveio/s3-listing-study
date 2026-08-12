"""Transactional SQLite campaign controller over deterministic GCP Batch jobs."""

from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import twinstamp as ts
from s3_listing_study.manager.campaign import ledger, provider
from s3_listing_study.manager.campaign.models import CaseControllerProgress

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


def _status(result: ts.EnsureResult[CaseControllerProgress]) -> dict[str, Any]:
    progress, fact = result.progress, result.fact
    state = progress.phase if fact is None else (
        "unsettled" if isinstance(fact, ts.Ambiguous) else fact.settlement.state
    )
    status = {"job_id": progress.current_job_id or progress.job_id, "state": state}
    if isinstance(fact, ts.Ambiguous) and fact.error_type is not None:
        status["error_type"] = fact.error_type
    return status


def start_campaign(
    *, ledger_path: Path, campaign: str, project: str, location: str,
    results_bucket: str, manifest_sha256: str, attempts: Sequence[Mapping[str, Any]],
    jobs: Sequence[dict[str, Any]], controller_timeouts: Sequence[int],
) -> list[dict[str, Any]]:
    """Persist all intent, then asynchronously create jobs in paced waves."""
    ledger.register_controller(
        ledger_path, campaign=campaign, project=project, location=location,
        results_bucket=results_bucket, manifest_sha256=manifest_sha256, attempts=attempts,
        jobs=jobs, controller_timeouts=controller_timeouts, now=utc_now(),
    )
    journal = ledger.SQLiteIntentJournal(ledger_path, campaign)
    backend = ts.FunctionBackend(provider.ensure_batch_job, provider.observe_batch_job)
    ensure = ts.ensure_submission
    statuses: list[dict[str, Any]] = []
    for index, base_job_id in enumerate((str(item["job_id"]) for item in attempts), start=1):
        with _provider_call_lock(ledger_path, base_job_id):
            result = ensure(base_job_id, journal=journal, backend=backend, now=utc_now())
            statuses.append(_status(result))
        if index % START_WAVE_SIZE == 0 and index < len(attempts):
            time.sleep(START_WAVE_DELAY_S)
    return statuses


def reconcile_once(*, ledger_path: Path, campaign: str) -> list[CaseControllerProgress]:
    """Poll each active exact resource once and persist terminal settlement."""
    return ts.observe_submissions(
        journal=ledger.SQLiteIntentJournal(ledger_path, campaign),
        backend=ts.FunctionBackend(provider.ensure_batch_job, provider.observe_batch_job),
        now=utc_now(),
    )


def progress(*, ledger_path: Path, campaign: str) -> list[CaseControllerProgress]:
    return ledger.SQLiteIntentJournal(ledger_path, campaign).progress()


def retry_case(
    *, ledger_path: Path, campaign: str, base_job_id: str, submission: int
) -> CaseControllerProgress:
    with _provider_call_lock(ledger_path, base_job_id):
        try:
            claim = ledger.claim_retry(
                ledger_path, campaign=campaign, base_job_id=base_job_id, submission=submission,
                now=utc_now(),
            )
        except (ValueError, ledger.LedgerError) as exc:
            raise ControllerError(str(exc)) from None
        if claim is not None:
            ts.ensure_claim(
                claim, journal=ledger.SQLiteIntentJournal(ledger_path, campaign),
                backend=ts.FunctionBackend(provider.ensure_batch_job, provider.observe_batch_job),
                now=utc_now(),
            )
    items = progress(ledger_path=ledger_path, campaign=campaign)
    return next(item for item in items if item.job_id == base_job_id)


def finalize(*, ledger_path: Path, campaign: str) -> list[CaseControllerProgress]:
    with ledger.open_ledger(ledger_path) as connection:
        ledger.finalize_campaign(connection, campaign=campaign, now=utc_now())
    return progress(ledger_path=ledger_path, campaign=campaign)
