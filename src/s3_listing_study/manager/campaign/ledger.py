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

# `submitting` is written before the API call and `submitted` after it, so a
# crash between the two is legible as itself rather than as an attempt that was
# never tried.
STATES = ("submitting", "submitted", "running", "succeeded", "failed", "abandoned")


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
    connection: sqlite3.Connection,
    *,
    attempt: Mapping[str, Any],
    campaign: str,
    now: str,
) -> None:
    """Write the row that says we are about to submit this attempt."""
    with _transaction(connection):
        _insert_intent(connection, attempt=attempt, campaign=campaign, now=now)


def _insert_intent(
    connection: sqlite3.Connection,
    *,
    attempt: Mapping[str, Any],
    campaign: str,
    now: str,
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


def record_state(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    state: str,
    now: str,
    detail: Mapping[str, Any] | None = None,
) -> None:
    """Move an attempt's current state, keeping the transition in ``events``."""
    if state not in STATES:
        raise LedgerError(f"unknown state {state!r} ({'|'.join(STATES)})")
    with _transaction(connection):
        updated = connection.execute(
            "UPDATE attempts SET state = ?, updated_at = ? WHERE job_id = ?", (state, now, job_id)
        )
        if updated.rowcount == 0:
            raise LedgerError(f"{job_id}: no such attempt in the ledger")
        _event(connection, job_id, now, state, detail)


def record_state_if_current(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    expected_state: str,
    state: str,
    now: str,
    detail: Mapping[str, Any] | None = None,
) -> bool:
    """Move and record a state only if it still matches the caller's snapshot.

    A false return is an ordinary lost compare-and-swap: another reconciler
    advanced the row first. The caller must re-read rather than overwrite it.
    """
    if expected_state not in STATES:
        raise LedgerError(f"unknown expected state {expected_state!r} ({'|'.join(STATES)})")
    if state not in STATES:
        raise LedgerError(f"unknown state {state!r} ({'|'.join(STATES)})")
    with _transaction(connection):
        updated = connection.execute(
            "UPDATE attempts SET state = ?, updated_at = ? WHERE job_id = ? AND state = ?",
            (state, now, job_id, expected_state),
        )
        if updated.rowcount == 1:
            _event(connection, job_id, now, state, detail)
            return True
        exists = connection.execute("SELECT 1 FROM attempts WHERE job_id = ?", (job_id,)).fetchone()
        if exists is None:
            raise LedgerError(f"{job_id}: no such attempt in the ledger")
        return False


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


def next_submission(
    connection: sqlite3.Connection,
    *,
    campaign: str,
    fingerprint: str,
    run_ordinal: int = 1,
) -> int:
    """The submission number a re-send of this attempt would take.

    Counted here rather than from Batch because a job id is never deleted and
    never reused: the ledger is the only thing that knows how many names one
    attempt has already spent.
    """
    row = connection.execute(
        "SELECT MAX(submission) AS highest FROM attempts "
        "WHERE campaign = ? AND fingerprint = ? AND run_ordinal = ?",
        (campaign, fingerprint, run_ordinal),
    ).fetchone()
    return 1 if row is None or row["highest"] is None else int(row["highest"]) + 1


def attempts(connection: sqlite3.Connection, *, campaign: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM attempts WHERE campaign = ? ORDER BY tool, case_id, run_ordinal, submission",
        (campaign,),
    ).fetchall()
    return [dict(row) for row in rows]


def register_campaign(
    connection: sqlite3.Connection,
    *,
    campaign: str,
    project: str,
    location: str,
    results_bucket: str,
    manifest_sha256: str,
    cases: Sequence[Mapping[str, Any]],
    now: str,
) -> None:
    """Create or exactly reconnect to the frozen local campaign owner."""
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
        known = {
            str(row["base_job_id"]): row
            for row in connection.execute(
                "SELECT * FROM controller_cases WHERE campaign = ?", (campaign,)
            )
        }
        if known and set(known) != {str(case["base_job_id"]) for case in cases}:
            raise LedgerError(f"{campaign}: ledger cases disagree with frozen campaign")
        frozen = {
            str(row["base_job_id"]): row
            for row in connection.execute(
                "SELECT * FROM controller_inputs WHERE campaign = ?", (campaign,)
            )
        }
        if frozen and set(frozen) != {str(case["base_job_id"]) for case in cases}:
            raise LedgerError(f"{campaign}: frozen controller inputs disagree with campaign")
        for case in cases:
            base_job_id = str(case["base_job_id"])
            encoded = json.dumps(case["job"], sort_keys=True, separators=(",", ":"))
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


def controller_cases(connection: sqlite3.Connection, campaign: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM controller_cases WHERE campaign = ? ORDER BY rowid", (campaign,)
    ).fetchall()
    if not rows:
        raise LedgerError(f"campaign {campaign!r} has no controller cases")
    return [dict(row) for row in rows]


def controller_inputs(connection: sqlite3.Connection, campaign: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM controller_inputs WHERE campaign = ? ORDER BY rowid", (campaign,)
    ).fetchall()
    if not rows:
        raise LedgerError(f"campaign {campaign!r} has no frozen controller inputs")
    return [dict(row) for row in rows]


def reserve_start(connection: sqlite3.Connection, *, base_job_id: str, now: str) -> bool:
    """Atomically reserve a pending case before the provider call."""
    with _transaction(connection):
        updated = connection.execute(
            "UPDATE controller_cases SET phase = 'running', updated_at = ?"
            " WHERE base_job_id = ? AND phase = 'pending'",
            (now, base_job_id),
        )
        return updated.rowcount == 1


def reserve_retry(
    connection: sqlite3.Connection,
    *,
    base_job_id: str,
    submission: int,
    current_job_id: str,
    job: Mapping[str, Any],
    attempt: Mapping[str, Any],
    now: str,
) -> dict[str, Any]:
    """Make retry active before I/O, serializing it against finalization."""
    with _transaction(connection):
        row = connection.execute(
            "SELECT * FROM controller_cases WHERE base_job_id = ?", (base_job_id,)
        ).fetchone()
        if row is None:
            raise LedgerError(f"{base_job_id}: no such campaign case")
        if row["current_submission"] == submission:
            return dict(row)
        if row["phase"] != "awaiting_retry" or submission != row["current_submission"] + 1:
            raise LedgerError("submission must be exactly current + 1 for an awaiting case")
        connection.execute(
            "UPDATE controller_cases SET phase = 'running', current_submission = ?,"
            " current_job_id = ?, job_json = ?, provider_state = NULL, failure_type = NULL,"
            " provider_resource_name = NULL, provider_settled = 0, accepted_failure = 0,"
            " updated_at = ? WHERE base_job_id = ?",
            (
                submission,
                current_job_id,
                json.dumps(job, sort_keys=True, separators=(",", ":")),
                now,
                base_job_id,
            ),
        )
        _insert_intent(connection, attempt=attempt, campaign=str(row["campaign"]), now=now)
        _event(connection, base_job_id, now, "retry_reserved", {"submission": submission})
        return dict(
            connection.execute(
                "SELECT * FROM controller_cases WHERE base_job_id = ?", (base_job_id,)
            ).fetchone()
        )


def record_provider_outcome(
    connection: sqlite3.Connection,
    *,
    base_job_id: str,
    expected_current_job_id: str,
    state: str,
    failure_type: str | None,
    resource_name: str | None,
    settled: bool,
    now: str,
) -> bool:
    """Record provider observation and derive the operator-facing phase."""
    if settled:
        successful = state == "SUCCEEDED" and failure_type is None
        phase = "terminal" if successful else "awaiting_retry"
    else:
        phase = "running"
    with _transaction(connection):
        row = connection.execute(
            "SELECT current_job_id, phase, provider_settled FROM controller_cases"
            " WHERE base_job_id = ?",
            (base_job_id,),
        ).fetchone()
        if row is None:
            raise LedgerError(f"{base_job_id}: no such campaign case")
        if (
            row["current_job_id"] != expected_current_job_id
            or row["phase"] != "running"
            or row["provider_settled"]
        ):
            return False
        connection.execute(
            "UPDATE controller_cases SET phase = ?, provider_state = ?, failure_type = ?,"
            " provider_resource_name = ?, provider_settled = ?, updated_at = ?"
            " WHERE base_job_id = ?",
            (phase, state, failure_type, resource_name, int(settled), now, base_job_id),
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
        _event(
            connection,
            str(row["current_job_id"]),
            now,
            attempt_state,
            {"provider_state": state, "failure_type": failure_type},
        )
        _event(
            connection,
            base_job_id,
            now,
            phase,
            {"provider_state": state, "failure_type": failure_type, "settled": settled},
        )
        return True


def finalize_campaign(connection: sqlite3.Connection, *, campaign: str, now: str) -> None:
    """Accept settled failures, refusing every active or unsettled provider effect."""
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
