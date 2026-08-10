"""What we actually invoked: a local sqlite record of submissions and what became of them.

Operational, not evidence. ``campaign.json`` in the results bucket is the
authoritative index and this is rebuildable from Batch and GCS if the runner is
lost — so it is not committed, and nothing downstream may depend on it.

Two tables on purpose. ``attempts`` is mutable and carries current state, which
is what a resubmission needs to consult; ``events`` is append-only, so updating
a state does not erase the history of what was seen and when. An ``UPDATE``
alone would leave a campaign that misbehaved with no account of how.

The unique constraint is the real guard against submitting one attempt twice:
an intent row is written *before* the API call, so a crash between the call and
its record leaves a row that says "we were about to" rather than no row at all.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    job_id       TEXT PRIMARY KEY,
    campaign     TEXT NOT NULL,
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

    UNIQUE (campaign, fingerprint, submission)
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


def record_intent(
    connection: sqlite3.Connection,
    *,
    attempt: Mapping[str, Any],
    campaign: str,
    now: str,
) -> None:
    """Write the row that says we are about to submit this attempt."""
    resources = attempt["resources"]
    try:
        connection.execute(
            "INSERT INTO attempts (job_id, campaign, submission, state, created_at, updated_at,"
            " bucket, region, tool, case_id, mode, machine_type, vcpus, memory_gb,"
            " container_memory_gb, timeout_s, env_json, derived_image, case_fingerprint,"
            " fingerprint, prefix, case_json)"
            " VALUES (?, ?, ?, 'submitting', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                attempt["job_id"],
                campaign,
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
    updated = connection.execute(
        "UPDATE attempts SET state = ?, updated_at = ? WHERE job_id = ?", (state, now, job_id)
    )
    if updated.rowcount == 0:
        raise LedgerError(f"{job_id}: no such attempt in the ledger")
    _event(connection, job_id, now, state, detail)


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


def next_submission(connection: sqlite3.Connection, *, campaign: str, fingerprint: str) -> int:
    """The submission number a re-send of this attempt would take.

    Counted here rather than from Batch because a job id is never deleted and
    never reused: the ledger is the only thing that knows how many names one
    attempt has already spent.
    """
    row = connection.execute(
        "SELECT MAX(submission) AS highest FROM attempts WHERE campaign = ? AND fingerprint = ?",
        (campaign, fingerprint),
    ).fetchone()
    return 1 if row is None or row["highest"] is None else int(row["highest"]) + 1


def attempts(connection: sqlite3.Connection, *, campaign: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM attempts WHERE campaign = ? ORDER BY tool, case_id, submission", (campaign,)
    ).fetchall()
    return [dict(row) for row in rows]
