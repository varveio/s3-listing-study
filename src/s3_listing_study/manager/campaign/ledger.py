"""Durable local-controller state: exact submissions and what became of them.

Operational, not run evidence. ``campaign.json`` remains the authoritative
evidence index, while this SQLite database is authoritative for controller
ownership, retry/finalization decisions, and unsettled provider effects. Retain
and back it up for the campaign's lifetime; it is not generally rebuildable
after Batch resources age out.

The tables have distinct roles. ``campaigns`` owns the frozen controller
identity, ``controller_inputs`` binds its exact Batch requests, and
``controller_cases`` carries current case state. ``attempts`` retains each
submission's immutable case identity plus its mutable provider state, while
``events`` is append-only so updating current state does not erase what was seen
and when. An ``UPDATE`` alone would leave a campaign that misbehaved with no
account of how.

The unique constraint is the real guard against submitting one attempt twice:
an intent row is written *before* the API call, so a crash between the call and
its record leaves a row that says "we were about to" rather than no row at all.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import twinstamp as ts
from s3_listing_study.manager.campaign.models import (
    BatchJobSpec,
    CaseControllerProgress,
    canonical_job_json,
    retry_job,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    job_id       TEXT PRIMARY KEY,
    campaign     TEXT NOT NULL,
    run_ordinal  INTEGER NOT NULL,
    submission   INTEGER NOT NULL,
    state        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,

    -- What was invoked, in the terms the case stated it. A fingerprint says
    -- whether two attempts match; it cannot say what either one was, and this
    -- is the record of what we actually ran. Typed here rather than only inside
    -- case_json so "everything at 2 GB" is a WHERE clause.
    bucket       TEXT NOT NULL,
    region       TEXT NOT NULL,
    tool         TEXT NOT NULL,
    case_id      TEXT NOT NULL,
    mode         TEXT NOT NULL,
    machine_type TEXT NOT NULL,
    vcpus        INTEGER NOT NULL,
    memory_gb    INTEGER NOT NULL,
    -- NULL is the honest answer for no ceiling: the container saw the whole box.
    container_memory_gb INTEGER,
    timeout_s    INTEGER NOT NULL,
    -- The heap share a managed runtime was told, as the environment it was told
    -- through. Empty for the nine tools with no heap to size.
    env_json     TEXT NOT NULL,

    derived_image TEXT NOT NULL,
    case_fingerprint TEXT NOT NULL,
    fingerprint  TEXT NOT NULL,
    prefix       TEXT NOT NULL,

    -- The whole resolved attempt verbatim, so a column nobody thought to add
    -- is still recoverable from the row that was written at the time.
    case_json    TEXT NOT NULL,

    UNIQUE (campaign, fingerprint, run_ordinal, submission)
);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id    TEXT NOT NULL,
    at        TEXT NOT NULL,
    event     TEXT NOT NULL,
    detail    TEXT,
    FOREIGN KEY (job_id) REFERENCES attempts (job_id)
);

CREATE INDEX IF NOT EXISTS events_by_job ON events (job_id, id);

CREATE TABLE IF NOT EXISTS campaigns (
    campaign TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    location TEXT NOT NULL,
    results_bucket TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finalized_at TEXT
);

CREATE TABLE IF NOT EXISTS controller_inputs (
    base_job_id TEXT PRIMARY KEY,
    campaign TEXT NOT NULL,
    job_json TEXT NOT NULL,
    controller_timeout_s INTEGER NOT NULL,
    FOREIGN KEY (campaign) REFERENCES campaigns (campaign)
);

CREATE TABLE IF NOT EXISTS controller_cases (
    base_job_id TEXT PRIMARY KEY,
    campaign TEXT NOT NULL,
    phase TEXT NOT NULL,
    current_submission INTEGER NOT NULL,
    current_job_id TEXT NOT NULL UNIQUE,
    job_json TEXT NOT NULL,
    controller_timeout_s INTEGER NOT NULL,
    provider_state TEXT,
    failure_type TEXT,
    provider_resource_name TEXT,
    provider_settled INTEGER NOT NULL DEFAULT 0,
    accepted_failure INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (campaign) REFERENCES campaigns (campaign)
);

CREATE INDEX IF NOT EXISTS controller_cases_by_campaign
ON controller_cases (campaign, base_job_id);
"""


class LedgerError(RuntimeError):
    """The ledger refused a write that would have lost or duplicated a record."""


@contextmanager
def open_ledger(path: Path) -> Iterator[sqlite3.Connection]:
    """A connection with the schema applied and foreign keys enforced."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        # A campaign that crashes mid-submission should not lose the rows that
        # said what had already been sent.
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
        yield connection
    finally:
        connection.close()


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """Commit one ledger mutation and its history event as a unit."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def record_intent(
    connection: sqlite3.Connection, *, attempt: Mapping[str, Any], campaign: str, now: str
) -> None:
    """Write the row that says we are about to submit this attempt."""
    with _transaction(connection):
        _insert_intent(connection, attempt=attempt, campaign=campaign, now=now)


def _insert_intent(
    connection: sqlite3.Connection, *, attempt: Mapping[str, Any], campaign: str, now: str
) -> None:
    resources = attempt["resources"]
    try:
        connection.execute(
            "INSERT INTO attempts (job_id, campaign, run_ordinal, submission, state,"
            " created_at, updated_at,"
            " bucket, region, tool, case_id, mode, machine_type, vcpus, memory_gb,"
            " container_memory_gb, timeout_s, env_json, derived_image, case_fingerprint,"
            " fingerprint, prefix, case_json)"
            " VALUES (?, ?, ?, ?, 'submitting', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, ?, ?)",
            (
                attempt["job_id"],
                campaign,
                attempt["run_ordinal"],
                attempt["submission"],
                now,
                now,
                attempt["bucket"],
                attempt["region"],
                attempt["tool"],
                attempt["case_id"],
                attempt["mode"],
                resources["machine_type"],
                resources["vcpus"],
                resources["memory_gb"],
                resources["container_memory_gb"],
                attempt["timeout_s"],
                json.dumps(attempt["env"], sort_keys=True),
                attempt["derived_image"],
                attempt["case_fingerprint"],
                attempt["fingerprint"],
                attempt["prefix"],
                json.dumps(attempt, sort_keys=True),
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise LedgerError(
            f"{attempt['job_id']}: this attempt is already in the ledger for {campaign} "
            f"({exc}) — raise the submission number to send it again"
        ) from None
    _event(connection, attempt["job_id"], now, "submitting", None)


def _event(
    connection: sqlite3.Connection,
    job_id: str,
    now: str,
    event: str,
    detail: Mapping[str, Any] | None,
) -> None:
    connection.execute(
        "INSERT INTO events (job_id, at, event, detail) VALUES (?, ?, ?, ?)",
        (job_id, now, event, None if detail is None else json.dumps(detail, sort_keys=True)),
    )


def _base_job_rows(
    connection: sqlite3.Connection, campaign: str, table: str
) -> dict[str, sqlite3.Row]:
    return {
        str(row["base_job_id"]): row
        for row in connection.execute(f"SELECT * FROM {table} WHERE campaign = ?", (campaign,))
    }


def register_campaign(
    connection: sqlite3.Connection, campaign: str, project: str, location: str,
    results_bucket: str, manifest_sha256: str, cases: Sequence[Mapping[str, Any]], now: str,
) -> None:
    with _transaction(connection):
        existing = connection.execute(
            "SELECT * FROM campaigns WHERE campaign = ?", (campaign,)
        ).fetchone()
        identity = (project, location, results_bucket, manifest_sha256)
        if existing is None:
            connection.execute(
                "INSERT INTO campaigns (campaign, project, location, results_bucket,"
                " manifest_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (campaign, *identity, now),
            )
        elif (
            tuple(
                existing[key]
                for key in ("project", "location", "results_bucket", "manifest_sha256")
            )
            != identity
        ):
            raise LedgerError(f"{campaign}: ledger owner disagrees with frozen campaign")
        known = _base_job_rows(connection, campaign, "controller_cases")
        case_ids = {str(case["base_job_id"]) for case in cases}
        if known and set(known) != case_ids:
            raise LedgerError(f"{campaign}: ledger cases disagree with frozen campaign")
        frozen = _base_job_rows(connection, campaign, "controller_inputs")
        if frozen and set(frozen) != case_ids:
            raise LedgerError(f"{campaign}: frozen controller inputs disagree with campaign")
        for case in cases:
            base_job_id = str(case["base_job_id"])
            encoded = canonical_job_json(dict(case["job"]))
            frozen_input = frozen.get(base_job_id)
            if frozen_input is None:
                connection.execute(
                    "INSERT INTO controller_inputs (base_job_id, campaign, job_json,"
                    " controller_timeout_s) VALUES (?, ?, ?, ?)",
                    (base_job_id, campaign, encoded, case["controller_timeout_s"]),
                )
            elif (
                frozen_input["job_json"] != encoded
                or frozen_input["controller_timeout_s"] != case["controller_timeout_s"]
            ):
                raise LedgerError(f"{base_job_id}: frozen controller request changed")
            current = known.get(base_job_id)
            if current is None:
                connection.execute(
                    "INSERT INTO controller_cases (base_job_id, campaign, phase,"
                    " current_submission, current_job_id, job_json, controller_timeout_s,"
                    " updated_at) VALUES (?, ?, 'pending', 1, ?, ?, ?, ?)",
                    (
                        base_job_id,
                        campaign,
                        base_job_id,
                        encoded,
                        case["controller_timeout_s"],
                        now,
                    ),
                )
            elif (
                current["current_submission"] != 1
                or current["current_job_id"] != base_job_id
                or current["job_json"] != encoded
                or current["controller_timeout_s"] != case["controller_timeout_s"]
            ) and current["phase"] == "pending":
                raise LedgerError(f"{base_job_id}: ledger case disagrees with frozen request")


def campaign_record(connection: sqlite3.Connection, campaign: str) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM campaigns WHERE campaign = ?", (campaign,)).fetchone()
    if row is None:
        raise LedgerError(f"campaign {campaign!r} is not in the ledger")
    return dict(row)


def _controller_rows(
    connection: sqlite3.Connection, campaign: str, table: str, missing: str
) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"SELECT * FROM {table} WHERE campaign = ? ORDER BY rowid", (campaign,)
    ).fetchall()
    if not rows:
        raise LedgerError(f"campaign {campaign!r} has no {missing}")
    return [dict(row) for row in rows]


def controller_cases(connection: sqlite3.Connection, campaign: str) -> list[dict[str, Any]]:
    return _controller_rows(connection, campaign, "controller_cases", "controller cases")


def controller_inputs(connection: sqlite3.Connection, campaign: str) -> list[dict[str, Any]]:
    return _controller_rows(connection, campaign, "controller_inputs", "frozen controller inputs")


def case_record(
    connection: sqlite3.Connection, *, campaign: str, base_job_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM controller_cases WHERE campaign = ? AND base_job_id = ?",
        (campaign, base_job_id),
    ).fetchone()
    return None if row is None else dict(row)


def batch_spec(row: Mapping[str, Any], owner: Mapping[str, Any]) -> BatchJobSpec:
    job_json = str(row["job_json"])
    return BatchJobSpec(
        str(owner["project"]),
        str(owner["location"]),
        str(row["base_job_id"]),
        str(row["current_job_id"]),
        json.loads(job_json),
        int(row["controller_timeout_s"]),
        int(row["current_submission"]),
        job_json,
    )


def _progress(row: Mapping[str, Any]) -> CaseControllerProgress:
    return CaseControllerProgress(
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


def register_controller(
    path: Path, *, campaign: str, project: str, location: str, results_bucket: str,
    manifest_sha256: str, attempts: Sequence[Mapping[str, Any]], jobs: Sequence[dict[str, Any]],
    controller_timeouts: Sequence[int], now: str,
) -> None:
    cases = [
        {"base_job_id": attempt["job_id"], "job": job, "controller_timeout_s": timeout}
        for attempt, job, timeout in zip(attempts, jobs, controller_timeouts, strict=True)
    ]
    with open_ledger(path) as connection:
        register_campaign(
            connection, campaign, project, location, results_bucket, manifest_sha256, cases, now
        )
        for attempt in attempts:
            existing = connection.execute(
                "SELECT case_json FROM attempts WHERE job_id = ?", (attempt["job_id"],)
            ).fetchone()
            if existing is None:
                record_intent(connection, attempt=attempt, campaign=campaign, now=now)
            elif json.loads(existing["case_json"]) != dict(attempt):
                raise LedgerError(f"{attempt['job_id']}: ledger attempt does not match")


def claim_retry(
    path: Path, *, campaign: str, base_job_id: str, submission: int, now: str
) -> ts.SubmissionClaim[BatchJobSpec] | None:
    with open_ledger(path) as connection:
        owner = campaign_record(connection, campaign)
        with _transaction(connection):
            fresh = case_record(connection, campaign=campaign, base_job_id=base_job_id)
            if fresh is None:
                raise ValueError("--job-id is not an original frozen manifest job ID")
            if fresh["current_submission"] == submission:
                if (
                    fresh["phase"] == "running"
                    and not fresh["provider_settled"]
                    and fresh["provider_resource_name"] is None
                ):
                    return ts.SubmissionClaim(batch_spec(fresh, owner).submission_spec(), "redrive")
                return None
            if fresh["phase"] != "awaiting_retry" or submission != fresh["current_submission"] + 1:
                raise LedgerError("submission must be exactly current + 1 for an awaiting case")
            retried = retry_job(batch_spec(fresh, owner), submission)
            original = connection.execute(
                "SELECT case_json FROM attempts WHERE job_id = ?", (base_job_id,)
            ).fetchone()
            if original is None:
                raise ValueError("base attempt is absent from the ledger")
            retry_attempt = json.loads(original["case_json"])
            retry_attempt["job_id"] = retried.job_id
            retry_attempt["submission"] = submission
            encoded = retried.job_json or canonical_job_json(retried.job)
            connection.execute(
                "UPDATE controller_cases SET phase = 'running', current_submission = ?,"
                " current_job_id = ?, job_json = ?, provider_state = NULL, failure_type = NULL,"
                " provider_resource_name = NULL, provider_settled = 0, accepted_failure = 0,"
                " updated_at = ? WHERE base_job_id = ?",
                (submission, retried.job_id, encoded, now, base_job_id),
            )
            _insert_intent(connection, attempt=retry_attempt, campaign=campaign, now=now)
            _event(connection, base_job_id, now, "retry_reserved", {"submission": submission})
            return ts.SubmissionClaim(retried.submission_spec(), "first")


def _project(
    row: Mapping[str, Any], token: str, fact: ts.EnsureFact | ts.ObservationFact
) -> tuple[str | None, str | None, str | None, bool] | None:
    if isinstance(fact, ts.Ambiguous | ts.NotVisible | ts.ObservationAmbiguous):
        return None
    settlement = fact.settlement
    failure = settlement.failure_type
    if isinstance(fact, ts.AdoptedExact) and token == "first":
        failure = "BatchJobCollision"
    if row["failure_type"] == "BatchJobCollision" and failure is None:
        failure = "BatchJobCollision"
    return settlement.state, failure, fact.effect.resource_name, settlement.settled


def _record_fact(
    connection: sqlite3.Connection, *, claim: ts.SubmissionClaim[BatchJobSpec],
    fact: ts.EnsureFact | ts.ObservationFact, now: str,
) -> bool:
    with _transaction(connection):
        row = connection.execute(
            "SELECT * FROM controller_cases WHERE current_job_id = ?", (claim.spec.key,)
        ).fetchone()
        if row is None:
            return False
        projection = _project(row, claim.token, fact)
        if projection is None:
            return True
        state, failure_type, resource_name, settled = projection
        if row["phase"] != "running" or row["provider_settled"]:
            return False
        phase = "terminal" if settled and state == "SUCCEEDED" and failure_type is None else (
            "awaiting_retry" if settled else "running"
        )
        connection.execute(
            "UPDATE controller_cases SET phase = ?, provider_state = ?, failure_type = ?,"
            " provider_resource_name = ?, provider_settled = ?, updated_at = ?"
            " WHERE base_job_id = ?",
            (phase, state, failure_type, resource_name, int(settled), now, row["base_job_id"]),
        )
        attempt_state = (
            "succeeded"
            if state == "SUCCEEDED"
            else "failed"
            if state in ("FAILED", "NOT_CREATED")
            else "running"
            if state == "RUNNING"
            else "submitted"
        )
        connection.execute(
            "UPDATE attempts SET state = ?, updated_at = ? WHERE job_id = ?",
            (attempt_state, now, row["current_job_id"]),
        )
        detail = {"provider_state": state, "failure_type": failure_type}
        _event(connection, str(row["current_job_id"]), now, attempt_state, detail)
        _event(connection, str(row["base_job_id"]), now, phase, {**detail, "settled": settled})
        return True


def _record_and_read(
    connection: sqlite3.Connection, claim: ts.SubmissionClaim[BatchJobSpec],
    fact: ts.EnsureFact | ts.ObservationFact, now: str, *, stale_ok: bool = False,
) -> CaseControllerProgress | None:
    stale = not _record_fact(connection, claim=claim, fact=fact, now=now)
    row = connection.execute(
        "SELECT * FROM controller_cases WHERE current_job_id = ?", (claim.spec.key,)
    ).fetchone()
    return None if row is None or (stale and not stale_ok) else _progress(row)


class SQLiteIntentJournal:
    def __init__(self, path: Path, campaign: str) -> None:
        self.path = path
        self.campaign = campaign

    def _row_and_owner(
        self, connection: sqlite3.Connection, key: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        owner = campaign_record(connection, self.campaign)
        row = case_record(connection, campaign=self.campaign, base_job_id=key)
        if row is None:
            raise LedgerError(f"{key}: no such campaign case")
        return row, owner

    def claim_submission(
        self, key: str, *, now: str
    ) -> ts.SubmissionClaim[BatchJobSpec] | None:
        with open_ledger(self.path) as connection, _transaction(connection):
            row, owner = self._row_and_owner(connection, key)
            if row["phase"] == "pending":
                connection.execute(
                    "UPDATE controller_cases SET phase = 'running', updated_at = ?"
                    " WHERE base_job_id = ? AND phase = 'pending'",
                    (now, key),
                )
                row["phase"] = "running"
                return ts.SubmissionClaim(batch_spec(row, owner).submission_spec(), "first")
            if (
                row["phase"] == "running"
                and not row["provider_settled"]
                and row["provider_resource_name"] is None
            ):
                return ts.SubmissionClaim(batch_spec(row, owner).submission_spec(), "redrive")
            return None

    def existing_submission(self, key: str) -> CaseControllerProgress:
        with open_ledger(self.path) as connection:
            row, _owner = self._row_and_owner(connection, key)
            return _progress(row)

    def record_ensure(
        self, claim: ts.SubmissionClaim[BatchJobSpec], fact: ts.EnsureFact, *, now: str
    ) -> CaseControllerProgress:
        with open_ledger(self.path) as connection:
            progress = _record_and_read(connection, claim, fact, now, stale_ok=True)
            if progress is None:
                raise LedgerError(f"{claim.spec.key}: no such campaign case")
            return progress

    def observation_claims(self, *, now: str) -> list[ts.SubmissionClaim[BatchJobSpec]]:
        del now
        with open_ledger(self.path) as connection:
            owner = campaign_record(connection, self.campaign)
            return [
                ts.SubmissionClaim(batch_spec(row, owner).submission_spec(), "observe")
                for row in controller_cases(connection, self.campaign)
                if row["phase"] == "running"
            ]

    def record_observation(
        self, claim: ts.SubmissionClaim[BatchJobSpec], fact: ts.ObservationFact, *, now: str
    ) -> CaseControllerProgress | None:
        with open_ledger(self.path) as connection:
            return _record_and_read(connection, claim, fact, now)

    def progress(self) -> list[CaseControllerProgress]:
        with open_ledger(self.path) as connection:
            return [_progress(row) for row in controller_cases(connection, self.campaign)]


def finalize_campaign(connection: sqlite3.Connection, *, campaign: str, now: str) -> None:
    with _transaction(connection):
        rows = connection.execute(
            "SELECT * FROM controller_cases WHERE campaign = ?", (campaign,)
        ).fetchall()
        if not rows:
            raise LedgerError(f"campaign {campaign!r} has no controller cases")
        if any(row["phase"] in ("pending", "running") for row in rows):
            raise LedgerError("campaign still has active cases")
        if any(not row["provider_settled"] for row in rows):
            raise LedgerError("campaign has an unsettled provider effect")
        for row in rows:
            if row["phase"] == "awaiting_retry":
                connection.execute(
                    "UPDATE controller_cases SET phase = 'terminal', accepted_failure = 1,"
                    " updated_at = ? WHERE base_job_id = ?",
                    (now, row["base_job_id"]),
                )
                _event(connection, row["base_job_id"], now, "accepted_failure", None)
        connection.execute(
            "UPDATE campaigns SET finalized_at = ? WHERE campaign = ?", (now, campaign)
        )
