"""The campaign ledger: its schema, the row it stores, and every statement.

Normative reference: `benchmark/docs/model.md` — the tables and the state
vocabulary are that page's, implemented here.

The file is `campaign.db` and the record inside it is the ledger. A row is one
attempt, nothing is overwritten, and no row is ever deleted. This module owns
the SQL: the DDL below and the statements beneath it are the whole of what the
harness asks SQLite for, so a reader can see the storage contract in one place
without reading the submission lifecycle that drives it.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from benchmark import identity

STATE_FILENAME = "campaign.db"

# Bumped whenever a file written by an older reader would be misread by this
# one. There is no migration: an unrecognised version is refused, so a command
# either fully understands the file it opened or does not open it.
SCHEMA_VERSION = 2

RETRYABLE_STATES = {"FAILED", "NOT_CREATED"}
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "NOT_CREATED", "CANCELLED", "ACCEPTED"}
# What `prune` may delete the evidence of: an attempt that settled without a
# measurement behind it.
UNSUCCESSFUL_STATES = TERMINAL_STATES - {"SUCCEEDED"}

SUITE_RE = re.compile(r"\A[a-z][a-z0-9-]{0,30}[a-z0-9]\Z")
GROUP_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,38}[a-z0-9]\Z")

SCHEMA = """
CREATE TABLE meta (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    suite          TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE attempts (
    case_id             TEXT NOT NULL,
    attempt             INTEGER NOT NULL,
    attempt_id          TEXT GENERATED ALWAYS AS (case_id || '.s' || attempt) VIRTUAL,
    case_inputs         TEXT NOT NULL,
    group_id            TEXT NOT NULL,
    tool                TEXT NOT NULL,

    auth_role           TEXT,
    executor            TEXT NOT NULL,
    location            TEXT NOT NULL,
    machine_type        TEXT NOT NULL,
    vcpus               INTEGER NOT NULL,
    memory_gb           INTEGER NOT NULL,
    container_memory_gb INTEGER,
    heap_percent        INTEGER NOT NULL,
    timeout_s           INTEGER NOT NULL,
    target_bucket       TEXT NOT NULL,
    target_region       TEXT NOT NULL,
    target_prefix       TEXT NOT NULL,

    config              TEXT NOT NULL,
    mode                TEXT    GENERATED ALWAYS AS (json_extract(config, '$.mode')) VIRTUAL,
    concurrency         INTEGER GENERATED ALWAYS AS (json_extract(config, '$.concurrency')) VIRTUAL,

    input_artifact_sha256 TEXT,
    produced_by           TEXT,
    artifact_sha256       TEXT,

    tool_slice_sha256   TEXT NOT NULL,
    platform_sha256     TEXT NOT NULL,
    image_uri           TEXT NOT NULL,
    image_set_sha256    TEXT NOT NULL,

    executor_env        TEXT NOT NULL,
    service_account     TEXT NOT NULL,
    secret_resource     TEXT,
    job_name            TEXT NOT NULL UNIQUE,
    result_prefix       TEXT NOT NULL,

    request_json        TEXT NOT NULL,
    purpose             TEXT NOT NULL
        CHECK (purpose IN ('measurement', 'preparation', 'canary', 'diagnostic')),
    statistic           TEXT NOT NULL CHECK (statistic IN ('timing', 'rate')),
    origin              TEXT NOT NULL CHECK (origin IN ('planned', 'retry')),
    state               TEXT NOT NULL,
    state_detail        TEXT,
    recorded_at         TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    settled_at          TEXT,

    PRIMARY KEY (case_id, attempt)
);

CREATE TABLE pending (
    group_id      TEXT NOT NULL,
    slot          INTEGER NOT NULL,
    tool          TEXT NOT NULL,
    purpose       TEXT NOT NULL
        CHECK (purpose IN ('measurement', 'preparation', 'canary', 'diagnostic')),
    known_inputs  TEXT NOT NULL,
    producer      TEXT,
    awaiting      TEXT,
    disqualified  TEXT,
    state         TEXT NOT NULL CHECK (state IN ('BLOCKED', 'RESOLVED', 'ABANDONED')),
    became        TEXT,
    recorded_at   TEXT NOT NULL,
    settled_at    TEXT,

    CHECK ((producer IS NULL) <> (awaiting IS NULL)),
    PRIMARY KEY (group_id, slot)
);
"""


class CampaignError(RuntimeError):
    """Campaign input, ledger state, or provider state cannot be used safely."""


@dataclass(frozen=True)
class Attempt:
    """One attempt's identity and resolved environment: what a request renders from.

    Every field is a column of `attempts`, so a retry and a poll work from the
    recorded row rather than from a plan that may have been edited since.
    """

    case_id: str
    attempt: int
    case_inputs: str
    group_id: str
    tool: str
    auth_role: str | None
    executor: str
    location: str
    machine_type: str
    vcpus: int
    memory_gb: int
    container_memory_gb: int | None
    heap_percent: int
    timeout_s: int
    target_bucket: str
    target_region: str
    target_prefix: str
    config: str
    input_artifact_sha256: str | None
    produced_by: str | None
    tool_slice_sha256: str
    platform_sha256: str
    image_uri: str
    image_set_sha256: str
    executor_env: str
    service_account: str
    secret_resource: str | None
    job_name: str
    result_prefix: str
    purpose: str
    statistic: str
    origin: str

    @property
    def attempt_id(self) -> str:
        return identity.attempt_id(self.case_id, self.attempt)

    @property
    def visible_memory_gb(self) -> int:
        return self.container_memory_gb or self.memory_gb

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Attempt:
        return cls(**{field: row[field] for field in cls.__dataclass_fields__})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def validate_suite(value: str) -> str:
    """The one value used as path segment, job label, and job-name prefix."""
    if SUITE_RE.fullmatch(value) is None:
        raise CampaignError("suite must be lowercase alphanumerics and hyphens, 2-32 characters")
    return value


# What each superseded schema is missing, as the columns this code reads. A
# read-only command projects them in so it can still be pointed at a settled
# campaign; nothing here makes such a file writable.
READONLY_COMPATIBLE: dict[object, str] = {
    1: (
        "SELECT group_id, slot, tool, purpose, known_inputs, "
        "NULL AS producer, awaiting, NULL AS disqualified, "
        "state, became, recorded_at, settled_at FROM main.pending"
    )
}


def open_ledger(
    path: str, *, suite: str | None = None, readonly: bool = False
) -> sqlite3.Connection:
    """Open `campaign.db`, creating it for `suite` when it does not exist yet.

    A file whose `schema_version` this code does not recognise is refused for
    **writing**: a command that adapted to whatever columns it found would write
    rows that are quietly incomplete.

    Reading is the other question. `status`, `report`, `verify` and `prune` only
    ever ask a settled campaign what happened, and refusing them locks the
    evidence of every campaign run before the bump away behind a version number.
    So a superseded schema this code still knows how to *read* opens read-only,
    with the columns it predates projected in as `NULL` -- which is what they
    mean: a slot booked before producer specs existed named one attempt id and
    disqualified nothing. There is no migration either way.
    """
    con = sqlite3.connect(
        f"file:{path}?mode=ro" if readonly else path, uri=readonly, isolation_level=None
    )
    con.row_factory = sqlite3.Row
    existing = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if existing is None:
        if readonly or suite is None:
            con.close()
            raise CampaignError(f"{path} is not a campaign ledger")
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        con.execute(
            "INSERT INTO meta (id, suite, schema_version, created_at) VALUES (1, ?, ?, ?)",
            (validate_suite(suite), SCHEMA_VERSION, _now()),
        )
        return con
    row = con.execute("SELECT suite, schema_version FROM meta WHERE id = 1").fetchone()
    version = None if row is None else row["schema_version"]
    superseded = READONLY_COMPATIBLE.get(version) if readonly else None
    if version != SCHEMA_VERSION and superseded is None:
        con.close()
        raise CampaignError(
            f"{path} states schema_version {version!r}; this code writes {SCHEMA_VERSION} "
            "and does not migrate"
        )
    if superseded is not None:
        # A temp view shadows the table it stands in for, so every statement in
        # this module reads one shape whatever the file on disk holds.
        con.execute(f"CREATE TEMP VIEW pending AS {superseded}")
    if suite is not None and row["suite"] != suite:
        con.close()
        raise CampaignError(f"{path} is the {row['suite']!r} suite, not {suite!r}")
    return con


def ledger_suite(con: sqlite3.Connection) -> str:
    return str(con.execute("SELECT suite FROM meta WHERE id = 1").fetchone()["suite"])


def mint_group_id(con: sqlite3.Connection, override: str | None = None) -> str:
    """`gYYYYMMDD-HHMMSS`, or the operator's own name, unique within the file.

    Assigned without a round trip and typeable at a prompt, because `retry`,
    `cancel` and `prune` all take it as their scope. Two launches in one second
    are suffixed rather than merged: a group is what was launched together.
    """
    if override is not None:
        if GROUP_RE.fullmatch(override) is None:
            raise CampaignError("group id must be lowercase alphanumerics and hyphens")
        if _group_exists(con, override):
            raise CampaignError(f"group {override} already exists in this ledger")
        return override
    base = datetime.now(UTC).strftime("g%Y%m%d-%H%M%S")
    candidate, ordinal = base, 1
    while _group_exists(con, candidate):
        ordinal += 1
        candidate = f"{base}-{ordinal}"
    return candidate


def _group_exists(con: sqlite3.Connection, group_id: str) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM attempts WHERE group_id=? UNION ALL "
            "SELECT 1 FROM pending WHERE group_id=? LIMIT 1",
            (group_id, group_id),
        ).fetchone()
        is not None
    )


def attempt_rows(
    con: sqlite3.Connection, *, group_id: str | None = None, case_id: str | None = None
) -> list[sqlite3.Row]:
    """Every attempt, newest last, optionally scoped to one group or one case."""
    where, values = [], []
    if group_id is not None:
        where.append("group_id = ?")
        values.append(group_id)
    if case_id is not None:
        where.append("case_id = ?")
        values.append(case_id)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    return con.execute(
        f"SELECT * FROM attempts{clause} ORDER BY recorded_at, case_id, attempt", values
    ).fetchall()


def pending_rows(con: sqlite3.Connection, *, group_id: str | None = None) -> list[sqlite3.Row]:
    clause = " WHERE group_id = ?" if group_id is not None else ""
    values = [group_id] if group_id is not None else []
    return con.execute(f"SELECT * FROM pending{clause} ORDER BY group_id, slot", values).fetchall()


def blocked_slots(con: sqlite3.Connection, group_id: str) -> list[sqlite3.Row]:
    """Every slot one group still owes, in booking order."""
    return con.execute(
        "SELECT * FROM pending WHERE group_id=? AND state='BLOCKED' ORDER BY slot", (group_id,)
    ).fetchall()


# Every key of a slot's producer spec is a column of `attempts`, so the document
# a slot stored is exactly what the satisfaction query compares: a field cannot
# be written into the spec and then quietly left out of the match.
PRODUCER_SPEC_COLUMNS = (
    "tool",
    "mode",
    "config",
    "target_bucket",
    "target_prefix",
    "target_region",
    "tool_slice_sha256",
    "platform_sha256",
)


def producer_summary(producer: str) -> str:
    """A slot's producer spec, as one line an operator reads.

    The spec is matched byte-exactly and printed by shape: what an operator needs
    from `status` is which run would pay this slot, not the digests that make two
    of them the same run.
    """
    spec = json.loads(producer)
    return f"any {spec['tool']} {spec['mode']} of this group"


def slot_candidates(
    con: sqlite3.Connection, slot: sqlite3.Row, *, state: str | None = None
) -> list[sqlite3.Row]:
    """Every attempt that could pay this slot, earliest-settled first.

    **Scoped to the slot's own group**, and that is load-bearing: an unscoped
    spec matches any launch in the file, so a second campaign would silently bind
    the first one's hours-old bytes — the decision `--reuse-preparations` exists
    to make an operator take. Every legitimate candidate is journaled in the
    slot's own group at launch time, so scoping costs nothing.

    Ordered by `(settled_at, attempt_id)`, because earliest-settled is
    *monotone-stable in the ordering*: `settled_at` only ever appends later
    values, so nothing that settles afterwards moves ahead of a candidate that
    already has. That is what lets sibling slots of a sweep resolved on
    different poll passes bind one producer, and so one corpus snapshot.

    It is stability of the order, not a guarantee of the winner. The winner is
    the earliest candidate the *caller* can accept, and acceptance is recomputed
    every pass — a `result.json` that will not download disqualifies its
    candidate for that pass alone, deliberately, in case what was unreadable was
    the bucket. So a sweep whose slots resolve across two passes can straddle two
    producers when a transient read fails between them. Nothing here pins a
    winner; `model.md` § *What a slot waits for is a shape, not a name* states
    the limit.

    The `attempt_id` tiebreak is what makes it a total order; millisecond ties
    are real.
    """
    where = ["group_id = ?"]
    values: list[object] = [slot["group_id"]]
    if slot["producer"] is not None:
        spec = json.loads(slot["producer"])
        if not isinstance(spec, dict) or set(spec) != set(PRODUCER_SPEC_COLUMNS):
            raise CampaignError(
                f"slot {slot['group_id']}/{slot['slot']}: producer spec is not one this code "
                "understands"
            )
        for column in PRODUCER_SPEC_COLUMNS:
            where.append(f"{column} = ?")
            values.append(spec[column])
    elif "/" in str(slot["awaiting"]):
        # A slot, not an attempt: nothing can match until it resolves into one.
        return []
    else:
        where.append("attempt_id = ?")
        values.append(slot["awaiting"])
    if state is not None:
        where.append("state = ?")
        values.append(state)
    return con.execute(
        f"SELECT * FROM attempts WHERE {' AND '.join(where)} ORDER BY settled_at, attempt_id",
        values,
    ).fetchall()


def slot_owed_reason(con: sqlite3.Connection, slot: sqlite3.Row) -> str | None:
    """Why nothing can ever pay this slot, or `None` while something still might.

    A slot that can never be paid is the failure a slot exists to prevent — a
    measurement quietly absent — so this is what makes it loud in `status` and
    `report`, and what `accept-failure` abandons on. The candidate set is closed
    only because the spec is group-scoped: "no matching attempt in this group is
    live, payable by retry, or usable".
    """
    if slot["state"] != "BLOCKED":
        return None
    awaiting = slot["awaiting"]
    if awaiting is not None and "/" in str(awaiting):
        group, _, ordinal = str(awaiting).partition("/")
        upstream = con.execute(
            "SELECT state FROM pending WHERE group_id=? AND slot=?", (group, ordinal)
        ).fetchone()
        if upstream is None or upstream["state"] == "ABANDONED":
            return f"the slot it waits on ({awaiting}) will not produce an attempt"
        return None
    candidates = slot_candidates(con, slot)
    if any(
        row["state"] not in TERMINAL_STATES or row["state"] in RETRYABLE_STATES
        for row in candidates
    ):
        return None
    disqualified = json.loads(slot["disqualified"]) if slot["disqualified"] else {}
    if any(
        row["state"] == "SUCCEEDED" and row["attempt_id"] not in disqualified for row in candidates
    ):
        return None
    if not candidates:
        return "no attempt in this group produces what it consumes"
    settled = ", ".join(
        f"{row['attempt_id']} {disqualified.get(row['attempt_id']) or row['state']}"
        for row in candidates
    )
    return f"every candidate is settled and none published a usable artifact: {settled}"


# The insert's column list and its named parameters are both rendered from this
# tuple, so the row's field order is the dataclass's and cannot drift from it.
INSERT_COLUMNS = tuple(Attempt.__dataclass_fields__)


def journal_intent(
    con: sqlite3.Connection,
    *,
    case_id: str,
    case_inputs: str,
    build: Callable[[int], tuple[Attempt, str]],
    repeat: bool = False,
    claim: Callable[[sqlite3.Connection, Attempt], None] | None = None,
) -> tuple[Attempt, str]:
    """Allocate the next ordinal and write `SUBMITTING`, before any provider call.

    `build(ordinal) -> (attempt, request_json)` renders the request the row
    freezes, because the ordinal is part of the job name and the result prefix
    and is not known until this transaction holds the write lock. The ordinal is
    allocated inside it because groups may be submitted concurrently: the
    primary key makes a lost race an integrity error, the transaction is what
    stops the race.

    `claim` runs inside that same transaction, once the row exists: a slot's
    claim on the attempt it is becoming commits with that attempt or not at all,
    so a claim can always be finished from the row it names. A claim another
    pass already holds raises, and then nothing is journaled either.
    """
    con.execute("BEGIN IMMEDIATE")
    try:
        rows = con.execute(
            "SELECT attempt, case_inputs, state FROM attempts WHERE case_id=?", (case_id,)
        ).fetchall()
        for row in rows:
            if row["case_inputs"] != case_inputs:
                # 48 bits of hash, so this is a collision rather than a
                # coincidence: two cases filing evidence under one identity.
                raise CampaignError(
                    f"{case_id}: recorded case inputs differ from this case's — "
                    "two different cases hash to one case_id"
                )
        if not repeat and any(row["state"] == "SUCCEEDED" for row in rows):
            raise CampaignError(
                f"{case_id} already has a successful attempt; re-measuring is 'reps' "
                "in the plan or an explicit --repeat"
            )
        ordinal = max((int(row["attempt"]) for row in rows), default=0) + 1
        attempt, request = build(ordinal)
        now = _now()
        con.execute(
            f"""
            INSERT INTO attempts ({", ".join(INSERT_COLUMNS)}, request_json, state,
                                  recorded_at, updated_at)
            VALUES ({", ".join(f":{name}" for name in INSERT_COLUMNS)},
                    :request_json, 'SUBMITTING', :now, :now)
            """,
            {
                **{name: getattr(attempt, name) for name in INSERT_COLUMNS},
                "request_json": request,
                "now": now,
            },
        )
        if claim is not None:
            claim(con, attempt)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return attempt, request


def set_state(
    con: sqlite3.Connection, attempt_id: str, state: str, detail: str | None = None
) -> None:
    """Write one attempt's state, stamping `settled_at` when it settles."""
    now = _now()
    con.execute(
        "UPDATE attempts SET state=?, state_detail=?, updated_at=?, "
        "settled_at=CASE WHEN ? THEN ? ELSE settled_at END WHERE attempt_id=?",
        (state, detail, now, state in TERMINAL_STATES, now, attempt_id),
    )
