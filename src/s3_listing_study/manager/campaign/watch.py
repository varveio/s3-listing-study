"""Watch GCP Batch scheduler state and reconcile it into the campaign ledger.

This is deliberately only a scheduler-state watcher. It does not inspect result
artifacts, judge attempt correctness, retry work, or mutate Batch jobs.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from s3_listing_study.common.argparse_utils import UniqueStoreAction
from s3_listing_study.manager.campaign import ledger

PROVISIONING_STATES = frozenset({"QUEUED", "SCHEDULED"})
BATCH_TO_LEDGER = {"RUNNING": "running", "SUCCEEDED": "succeeded", "FAILED": "failed"}
TERMINAL_STATES = frozenset({"succeeded", "failed", "abandoned"})
# Provider text is operational context, not an unbounded logging channel.
PROVIDER_TEXT_LIMIT = 2048
PROJECT_IDENTITY_LIMIT = 256
PROJECT_RESPONSE_LIMIT = 2048


class WatchError(RuntimeError):
    """Batch state could not be reconciled without guessing."""


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="s3-listing-study watch-campaign", allow_abbrev=False)
    parser.add_argument("--campaign", action=UniqueStoreAction, required=True)
    parser.add_argument("--project", action=UniqueStoreAction, required=True)
    parser.add_argument("--location", action=UniqueStoreAction, required=True)
    parser.add_argument("--ledger", action=UniqueStoreAction, required=True)
    parser.add_argument(
        "--poll-interval-s",
        type=_positive_seconds,
        default=30.0,
        help="seconds between Batch polls (default: 30)",
    )
    parser.add_argument("--once", action="store_true", help="reconcile one poll and return")
    return parser


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(argv, capture_output=True, check=False)
    except OSError as exc:
        raise WatchError(f"cannot run {argv[0]}: {exc}") from None


def _resolve_project(project: str) -> frozenset[str]:
    result = _run(
        (
            "gcloud",
            "projects",
            "describe",
            project,
            "--format=json(projectId,projectNumber)",
        )
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise WatchError(f"gcloud project lookup failed: {detail or f'exit {result.returncode}'}")
    if len(result.stdout) > PROJECT_RESPONSE_LIMIT:
        raise WatchError("gcloud project lookup returned oversized JSON")
    try:
        document = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WatchError(f"gcloud project lookup returned malformed JSON: {exc}") from None
    if not isinstance(document, dict) or set(document) != {"projectId", "projectNumber"}:
        raise WatchError("gcloud project lookup returned malformed project identity")
    project_id = document["projectId"]
    project_number = document["projectNumber"]
    if isinstance(project_number, int) and not isinstance(project_number, bool):
        project_number = str(project_number)
    identities = (project_id, project_number)
    if not all(
        isinstance(identity, str)
        and 0 < len(identity) <= PROJECT_IDENTITY_LIMIT
        and "/" not in identity
        for identity in identities
    ):
        raise WatchError("gcloud project lookup returned malformed project identity")
    canonical = frozenset(identities)
    if project not in canonical:
        raise WatchError(f"project lookup for {project!r} returned non-matching identity")
    return canonical


def _describe_job(
    *,
    job_id: str,
    project: str,
    project_identities: frozenset[str],
    location: str,
) -> Mapping[str, Any]:
    result = _run(
        (
            "gcloud",
            "batch",
            "jobs",
            "describe",
            job_id,
            "--project",
            project,
            "--location",
            location,
            "--format=json",
        )
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        lowered = detail.lower()
        if any(marker in lowered for marker in ("not_found", "not found", "code=404", " 404")):
            raise WatchError(f"Batch did not return ledger job: {job_id}")
        raise WatchError(
            f"{job_id}: gcloud Batch describe failed: {detail or f'exit {result.returncode}'}"
        )
    try:
        document = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WatchError(
            f"{job_id}: gcloud Batch describe returned malformed JSON: {exc}"
        ) from None
    if not isinstance(document, dict):
        raise WatchError(f"{job_id}: gcloud Batch describe returned malformed JSON object")
    _validate_resource_name(
        job_id=job_id,
        project_identities=project_identities,
        location=location,
        job=document,
    )
    return document


def _validate_resource_name(
    *,
    job_id: str,
    project_identities: frozenset[str],
    location: str,
    job: Mapping[str, Any],
) -> None:
    name = job.get("name")
    parts = name.split("/") if isinstance(name, str) else []
    if (
        len(parts) != 6
        or parts[0] != "projects"
        or parts[1] not in project_identities
        or parts[2] != "locations"
        or parts[3] != location
        or parts[4] != "jobs"
        or parts[5] != job_id
    ):
        raise WatchError(
            f"{job_id}: Batch returned unexpected resource name {name!r} for location {location!r}"
        )


def _read_jobs(
    job_ids: Sequence[str],
    *,
    project: str,
    project_identities: frozenset[str],
    location: str,
) -> dict[str, Mapping[str, Any]]:
    return {
        job_id: _describe_job(
            job_id=job_id,
            project=project,
            project_identities=project_identities,
            location=location,
        )
        for job_id in job_ids
    }


def _observed_state(job_id: str, job: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    status = job.get("status")
    if not isinstance(status, dict):
        raise WatchError(f"{job_id}: Batch job has no status object")
    state = status.get("state")
    if not isinstance(state, str) or not state:
        raise WatchError(f"{job_id}: Batch job has no status state")
    if state not in PROVISIONING_STATES and state not in BATCH_TO_LEDGER:
        raise WatchError(f"{job_id}: unknown Batch state {state!r}")
    return state, status


def _transition(current: str, observed: str, *, job_id: str) -> str | None:
    target = BATCH_TO_LEDGER.get(observed)
    if current in TERMINAL_STATES:
        if target == current:
            return None
        if observed in PROVISIONING_STATES or target == "running":
            return None
        raise WatchError(
            f"{job_id}: contradictory terminal Batch state {observed!r} "
            f"for terminal ledger state {current!r}"
        )
    if current not in ledger.STATES:
        raise WatchError(f"{job_id}: unknown ledger state {current!r}")
    if observed in PROVISIONING_STATES:
        return None
    if target == current:
        return None
    if current == "running" and target == "running":
        return None
    if target is None:  # Defensive: _observed_state rejects this first.
        raise WatchError(f"{job_id}: unknown Batch state {observed!r}")
    return target


def _bounded_provider_text(value: Any) -> str | None:
    return value[:PROVIDER_TEXT_LIMIT] if isinstance(value, str) else None


def _event_detail(*, batch_state: str, status: Mapping[str, Any]) -> dict[str, Any]:
    detail: dict[str, Any] = {"source": "gcp-batch", "batch_state": batch_state}
    run_duration = _bounded_provider_text(status.get("runDuration"))
    if run_duration is not None:
        detail["runDuration"] = run_duration
    status_events = status.get("statusEvents")
    if isinstance(status_events, list) and status_events and isinstance(status_events[-1], dict):
        latest = {
            key: bounded
            for key in ("type", "eventTime", "description")
            if (bounded := _bounded_provider_text(status_events[-1].get(key))) is not None
        }
        if latest:
            detail["status_event"] = latest
    return detail


def _current_state(connection: sqlite3.Connection, *, job_id: str) -> str:
    row = connection.execute("SELECT state FROM attempts WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        raise ledger.LedgerError(f"{job_id}: no such attempt in the ledger")
    return str(row["state"])


def _apply_observation(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    current: str,
    batch_state: str,
    status: Mapping[str, Any],
    now: str,
) -> None:
    # Watcher transitions are finite and monotonic. More losses than there are
    # states indicates another writer is churning the row rather than advancing it.
    for _ in range(len(ledger.STATES) + 1):
        target = _transition(current, batch_state, job_id=job_id)
        if target is None:
            return
        if ledger.record_state_if_current(
            connection,
            job_id=job_id,
            expected_state=current,
            state=target,
            now=now,
            detail=_event_detail(batch_state=batch_state, status=status),
        ):
            return
        current = _current_state(connection, job_id=job_id)
    raise WatchError(f"{job_id}: ledger state changed too often during reconciliation")


def _summary(*, campaign: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row["state"]) for row in rows)
    terminal = sum(counts[state] for state in TERMINAL_STATES)
    failed = counts["failed"] + counts["abandoned"]
    return {
        "campaign": campaign,
        "complete": terminal == len(rows),
        "successful": terminal == len(rows) and failed == 0,
        "states": dict(sorted(counts.items())),
        "terminal": terminal,
        "total": len(rows),
    }


def reconcile_once(
    *,
    campaign: str,
    project: str,
    location: str,
    ledger_path: Path,
) -> dict[str, Any]:
    """Perform one fully validated Batch-to-ledger reconciliation poll."""
    with ledger.open_ledger(ledger_path) as connection:
        rows = ledger.attempts(connection, campaign=campaign)
        if not rows:
            raise WatchError(f"campaign {campaign!r} has no attempts in the ledger")
        expected = {str(row["job_id"]): row for row in rows}

        project_identities = _resolve_project(project)
        observed = _read_jobs(
            tuple(expected),
            project=project,
            project_identities=project_identities,
            location=location,
        )

        transitions: list[tuple[str, str, str, Mapping[str, Any]]] = []
        for job_id, row in expected.items():
            batch_state, status = _observed_state(job_id, observed[job_id])
            current = str(row["state"])
            target = _transition(current, batch_state, job_id=job_id)
            if target is not None:
                transitions.append((job_id, current, batch_state, status))

        now = _utc_now()
        for job_id, current, batch_state, status in transitions:
            _apply_observation(
                connection,
                job_id=job_id,
                current=current,
                batch_state=batch_state,
                status=status,
                now=now,
            )
        return _summary(campaign=campaign, rows=ledger.attempts(connection, campaign=campaign))


def watch_campaign_main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        while True:
            summary = reconcile_once(
                campaign=args.campaign,
                project=args.project,
                location=args.location,
                ledger_path=Path(args.ledger),
            )
            print(json.dumps(summary, sort_keys=True, separators=(",", ":")), flush=True)
            unsuccessful = any(summary["states"].get(state, 0) for state in ("failed", "abandoned"))
            if args.once or summary["complete"]:
                return 1 if unsuccessful else 0
            time.sleep(args.poll_interval_s)
    except KeyboardInterrupt:
        print("watch-campaign: interrupted", file=sys.stderr)
        return 130
    except (WatchError, ledger.LedgerError, OSError) as exc:
        print(f"watch-campaign: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    return watch_campaign_main(argv)
