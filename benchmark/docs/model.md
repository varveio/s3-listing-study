# The state model

What the benchmark records about its own runs: how a measurement is identified,
where its evidence lands, and the table that binds the two.

[`running.md`](running.md) is how to operate a run; this page is what a run *is*
to the system.

## Status of this model: not implemented

The controller in `../src/benchmark/campaign.py` still uses the earlier
single-run vocabulary — `campaign_id`, `base_job_id`, a case ID rendered from
plan keys, and a separate fingerprint. Sections below mark what exists today
where it differs. Nothing here describes a shipped schema until this line says
so.

## One identity

A case is identified by **the tool and a hash over everything that can change
the measurement**:

```
<tool>.<hash>
aws-cli.9f300cc4d2b1
```

The hash covers, in canonical form: mode, auth, the target bucket and region,
the prefix, the resource allocation (vCPUs, memory, container ceiling), the
timeout, any case environment, and the pinned toolbox image digest. Anything
that could make two runs non-comparable is in it by construction.

That last property is the point. The earlier model had a readable ID rendered
from the plan keys *and* a fingerprint hashed over the resolved case, which
forced a law: every key must move both, or one ID renders two fingerprints and
two non-comparable runs land in one directory. `timeout_s` is barred from plan
rows for exactly that reason. With a single hash the law is unnecessary — a
field either changes the identity or it is not an input.

It also removes the need for a revision counter. A rebuilt toolbox is a
different image digest, so a different hash, so a different case: no allocation,
no counter, nothing to keep in sync. And "have we already measured exactly
this?" becomes a primary-key lookup rather than a comparison.

**What it costs:** the identity is no longer readable in a bucket listing.
`swath.recursive-parquet-sorted.container_memory_gb-2` said what it was;
`swath.4c1e8a77b920` does not. The columns say it instead, reports render from
them, and each attempt's `result.json` carries its own full provenance — so a
lost database is rebuilt by reading objects, not by parsing their names.

**Changing what goes into the hash re-identifies everything.** Old rows keep
their identity and their evidence stays put; new runs of the same plan get new
prefixes. Treat the input list as a contract.

### Attempts

Two runs of one case have identical inputs and therefore the identical hash.
They are told apart by an ordinal — and only when there is more than one:

```
<tool>.<hash>          first attempt
<tool>.<hash>.2        it failed; this is the retry
<tool>.<hash>.3
```

So the suffix appears only when something went wrong, and the ordinary case
stays a bare `<tool>.<hash>`. The manager allocates the next ordinal as
`max(attempt) + 1`; the ledger already says which kind each was, because a row
whose predecessor settled in a failure state is a retry and one whose
predecessor succeeded is a repeat. Nothing needs to record that separately.

Failed attempts can be pruned later — their prefixes deleted, keeping only the
evidence that settled successfully. The ledger row stays regardless, so "this
took three tries" survives even when the bytes from the first two do not.

### suite and group

**`suite`** is the namespace: the first path segment in the results bucket, the
Batch job label a polling pass filters on (`labels.suite=<value>`, server-side),
and the job-name prefix keeping this study's jobs disjoint from anything else in
the project. Constant for the life of a file, so it lives in `meta`. *Today:*
all three are the hardcoded string `benchmark`.

**`group_id`** records what was launched together. It is a column only: nothing
in the object layout needs it, because the image digest is already inside every
case hash.

## Object layout

```
gs://<results-bucket>/<suite>/<target-bucket>/<tool>.<hash>[.<attempt>]/
```

Deterministic, so evidence is computed from a row rather than discovered by
listing. The worker writes `result.json` last; its presence is what makes an
attempt complete.

*Today:* `campaigns/<run>/results/<bucket>/<tool>/<case>/run-<rep>/submission-<n>/<uuid>/`
— seven levels ending in a random per-execution UUID.

Dropping the random leaf deletes machinery: `resolve_leaf`, `list_leaves` with
its "exactly one leaf, refuse zero or 2+" rule, and the `AMBIGUOUS` branch of the
retry evidence check, which becomes one existence check on
`<prefix>/result.json`.

It requires **create-only writes** in exchange. `gcs.py` documents plain
overwrites as deliberate, which a random leaf made survivable. With
deterministic prefixes a second execution of one attempt would silently merge —
overwriting `result.json` while leaving behind any file the first wrote and the
second did not. An `ifGenerationMatch=0` precondition makes that a loud failure.

## The tables

```sql
CREATE TABLE meta (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    suite          TEXT NOT NULL,        -- namespace for prefixes, labels, and job names
    schema_version INTEGER NOT NULL,
    created_at     TEXT NOT NULL
);
```

One row, typed rather than key/value: the key set is closed and load-bearing, so
a missing or misspelled `suite` should fail at open time as a schema error
rather than surface as a `None` far from the problem. `CHECK (id = 1)` makes
single-rowness a database invariant. *Today:* there is no version marker at all;
`open_db` adds missing columns with a bare `ALTER TABLE`.

```sql
CREATE TABLE attempts (
    -- identity
    case_id             TEXT NOT NULL,      -- <tool>.<hash>
    attempt             INTEGER NOT NULL,   -- ordinal, 1-based
    attempt_id          TEXT GENERATED ALWAYS AS (
                            CASE attempt WHEN 1 THEN case_id
                            ELSE case_id || '.' || attempt END) VIRTUAL,
    group_id            TEXT NOT NULL,      -- the launch this went out with

    -- the hashed inputs, kept legible
    tool                TEXT NOT NULL,
    mode                TEXT NOT NULL,
    auth                TEXT NOT NULL,      -- anonymous | authenticated
    target_bucket       TEXT NOT NULL,
    target_region       TEXT NOT NULL,
    target_prefix       TEXT NOT NULL,
    vcpus               INTEGER NOT NULL,
    memory_gb           INTEGER NOT NULL,
    container_memory_gb INTEGER,            -- null means no ceiling
    timeout_s           INTEGER NOT NULL,
    case_env            TEXT NOT NULL,      -- canonical JSON; {} when empty
    image_uri           TEXT NOT NULL,      -- pinned @sha256 toolbox

    -- context, not hashed
    image_set_sha256    TEXT NOT NULL,      -- the eleven-tool provenance document
    harness_revision    TEXT NOT NULL,
    project             TEXT NOT NULL,
    location            TEXT NOT NULL,
    job_name            TEXT NOT NULL UNIQUE,  -- provider job ID: sanitized, <= 63 chars
    evidence_prefix     TEXT NOT NULL,

    -- the request and its outcome
    request_json        TEXT NOT NULL,      -- frozen provider request; a retry is diffed against it
    state               TEXT NOT NULL,
    state_detail        TEXT,               -- the provider's message, when it failed
    recorded_at         TEXT NOT NULL,      -- intent journaled, before the provider was called
    updated_at          TEXT NOT NULL,
    settled_at          TEXT,

    PRIMARY KEY (case_id, attempt)
);

CREATE INDEX attempts_by_group ON attempts (group_id);
CREATE INDEX attempts_by_state ON attempts (state);
```

**A row is one attempt, not one case.** Nothing is overwritten and no row is
deleted, so the table is the study's full run history even after failed evidence
is pruned from the bucket.

The hashed inputs are stored as columns too — the hash makes them comparable,
the columns make them readable, and reports render from the columns.
`image_set_sha256` and `harness_revision` sit outside the hash because the image
digest already covers what ran; they stay for provenance.

### What is stored, and what is not

Store what the row cannot regenerate, plus anything whose derivation rule may
change — history outlives the code that wrote it.

- `attempt_id` is **not** stored: a generated column over the two columns it
  composes. Greppable and indexable, with no way to drift from its parts.
- `job_name` is stored: its sanitize-and-hash derivation may change, and
  recomputing it later would orphan jobs already out there.
- `evidence_prefix` is stored for the same reason applied to the layout: rows
  written under an earlier layout must stay resolvable after it changes.

### The provider's ID cannot be the key

It is unique, so it is tempting. But **a row exists before its job does**:
intent is journaled first, and `NOT_CREATED` records a job that never came into
being — so keying on the remote name leaves exactly the failures unkeyed. It is
also truncated and hashed, provider-scoped, and outlived by the evidence.

`case_id` cannot serve as the Batch job name either: job IDs are capped at 63
characters of lowercase alphanumerics and hyphens, no dots. You grep
`attempt_id`; you paste `job_name` into `gcloud batch jobs describe`.

## The state column

Two vocabularies share it: the controller writes its own relationship with the
provider, and polling writes Batch's lifecycle states through as it sees them.

| State | Written by | Terminal | Retryable | Meaning |
| --- | --- | --- | --- | --- |
| `SUBMITTING` | intent journaling | no | no | Intent is durable; the provider has not been called. A row left here means the process died in that window. |
| `SUBMITTED` | submit | no | no | Created, and the provider's copy matches the recorded request. |
| `ADOPTED` | submit | no | no | A job of that name already existed and matched the recorded request exactly. |
| `AMBIGUOUS` | submit | no | no | The create outcome could not be established. Re-running submit reconciles the row rather than duplicating it. |
| *(provider states)* | poll | no | no | `QUEUED`, `SCHEDULED`, `RUNNING`, and the rest of Batch's lifecycle. |
| `SUCCEEDED` | poll | yes | no | The job ran cleanly. Not a verdict about the listing — that is `verify`'s question. |
| `FAILED` | poll | yes | **yes** | Settled failure. |
| `NOT_CREATED` | submit | yes | **yes** | The provider permanently refused creation. Never probed as ambiguous. |
| `COLLISION` | submit | yes | **yes** | A job of that name exists and does *not* match recorded intent. |
| `CANCELLED` | cancel | yes | no | One-way. |
| `ACCEPTED_FAILED`, `ACCEPTED_NOT_CREATED`, `ACCEPTED_COLLISION` | accept-failure | yes | no | You declared that failure final. An absent measurement, never a passing one. |

Polling never invents a state: a describe that fails leaves the row untouched and
reports "not all terminal".

## Scope under accumulation

One file accumulates every group. Each command says what it acts on.

| Command | Scope |
| --- | --- |
| `poll` | Everything non-terminal. One listing filtered by `labels.suite` covers every group in flight, so parallel launches need no extra machinery. |
| `status` | Optional `--group` / `--case` filters; unfiltered prints the whole history, which is the point of accumulating. |
| `retry` | One group. Rows from other groups are skipped, not refused. *Today:* raises on the first foreign row. |
| `cancel` | Requires `--group`; without one it refuses rather than cancelling the file. *Today:* cancels every non-terminal row, group-blind. |
| `verify` | One group. The "rows agree on image and provider parent" check narrows to that group. *Today:* applies it to the whole file, which fails the moment a second launch lands. |

## What is deliberately absent

No results, metrics, or verdicts. Those live in the evidence objects and are
recomputed by `verify` and `report` on demand — a cached verdict is a second
answer to a settled question, and the two can disagree.

## Reading it

```sh
sqlite3 'file:benchmark.db?mode=ro' \
  'SELECT attempt_id, state, tool, mode, target_bucket FROM attempts ORDER BY case_id, attempt'
```

Back the file up. Losing it loses the *binding*, not the evidence: the objects
survive, but nothing then ties them to the case, toolbox, and request that
produced them, and `report` refuses results it cannot bind.
