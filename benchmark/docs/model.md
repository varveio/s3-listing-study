# The state model

What the benchmark records about its own runs: the identities it names things
with, the object layout that follows from them, and the one table that binds a
plan case to a provider job and the evidence it produced.

[`running.md`](running.md) is how to operate a run; this page is what a run *is*
to the system.

## Status of this model: partly implemented

The identities and layout below are the model the benchmark is built toward.
The controller in `../src/benchmark/campaign.py` still uses the earlier
single-run vocabulary, so every section marks what exists today where it
differs. Nothing here is a description of a shipped schema until this line says
so.

The model changed because the earlier one assumed **one run per state file**:
`verify` refuses a ledger whose rows disagree on the run identifier, `retry`
raises on a row from another run, and `cancel` stops every non-terminal row in
the file. The intended workflow is the opposite — one accumulating database,
cases added over time, several launches in flight at once.

## The four identities

| Concept | Name | Identifies | Lives in |
| --- | --- | --- | --- |
| The namespace | `suite` | This benchmark, as distinct from any other workload sharing the project or bucket | Object prefix, Batch job label, job-name prefix, `meta` |
| What was measured | `case_id` | Tool, mode, knobs, revision — the comparable unit | Object path, column |
| One try of it | `case_id` + `submission` | One physical execution; composed as `<case_id>.s<n>` | Object prefix leaf, primary key |
| What went out together | `group_id` | A launch: the cases submitted in one go on one frozen toolbox | Column only |

**`suite`** earns one value doing three jobs: the object namespace, the Batch
label a polling pass filters on (`labels.suite=<value>`, server-side), and the
job-name prefix that keeps this study's jobs disjoint from anything else in the
project. It is constant for the life of a file, so it lives in `meta` rather
than repeating identically on every row. *Today:* all three are the hardcoded
string `benchmark`, and polling filters `labels.benchmark-intent:*`.

**`case_id`** is tool-qualified, so two tools running a mode of the same name
cannot share a directory. *Today:* the tool is a separate path level and case IDs
deliberately repeat across tools — `s5cmd` and `s3kor` both produce case `list`,
distinguished only by fingerprint.

**`rev`** is part of `case_id`, and its rule is exact: it is an allocation over
the pair `(case_fingerprint, image_set_sha256)`. The fingerprint covers the
resolved case; it does not cover the toolbox. So "is this a different
measurement?" is precisely "did either of those two change?", and if so the
manager allocates the next revision. Both inputs are already recorded per row.
Without it, re-running a case against a rebuilt toolbox lands in the directory
its predecessor already occupies. *Today:* there is no revision; the run
identifier in the object prefix separates them implicitly, because a run freezes
one image set.

**`group_id`** stays out of the object layout. What it used to encode — "these
all ran on one frozen toolbox" — is now stated explicitly by the revision. It
remains the record of what a launch did and what it cost.

## Object layout

```
gs://<results-bucket>/<suite>/<target-bucket>/<case-id>/s<submission>/
```

Deterministic: every prefix is computable from the ledger row, so nothing has to
be discovered by listing. The worker writes `result.json` last, and its presence
is what makes a submission's evidence complete.

*Today:* `campaigns/<run>/results/<bucket>/<tool>/<case>/run-<rep>/submission-<n>/<uuid>/`
— seven levels, ending in a random per-execution UUID.

Dropping the random leaf deletes a class of machinery: `resolve_leaf` (verify
computes the path instead of discovering it), `list_leaves` with its "exactly one
leaf, refuse zero or 2+" rule, and the `AMBIGUOUS` branch of the retry evidence
check, which becomes one existence check on `<prefix>/result.json`.

It requires one thing in exchange: **create-only writes**. `gcs.py` currently
documents plain overwrites as deliberate, which a random leaf made survivable.
With deterministic prefixes, a second execution of one submission would silently
merge — overwriting `result.json` while leaving behind any file the first
attempt wrote and the second did not. An `ifGenerationMatch=0` precondition
turns that into a loud failure, which is the only thing that makes "never
overwritten" a guarantee rather than an expectation.

## The tables

Two: one row of file-level facts, and one row per submission.

```sql
CREATE TABLE meta (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    suite          TEXT NOT NULL,        -- the namespace every prefix, label, and job name carries
    schema_version INTEGER NOT NULL,     -- what shape this file is, for migrations to coordinate on
    created_at     TEXT NOT NULL
);
```

`suite` and the schema version are constant for the life of the file, so they
are stated once rather than repeated on every row. Typed columns rather than a
key/value table: the key set is small, closed, and load-bearing — `suite`
decides where every object is written, so a missing or misspelled value should
fail at open time as a schema error, not surface as a `None` at some call site
far from the problem. `CHECK (id = 1)` makes single-rowness an invariant of the
database rather than a convention, so a second suite cannot be inserted into a
file meant to hold one.

Adding a field here costs an `ALTER TABLE` — which is a migration, and
coordinating those is exactly what `schema_version` is for. *Today:* there is no
version marker at all; `open_db` adds missing columns with a bare `ALTER TABLE`
and nothing records which shape a file already has.

(SQLite's `PRAGMA user_version` is the other idiomatic home for a schema
version. It holds an integer in the file header and needs no table, but `suite`
still needs somewhere to live, and a pragma is invisible to anyone opening the
file with `sqlite3`.)

```sql
CREATE TABLE submissions (
    -- identity
    case_id           TEXT NOT NULL,        -- <tool>.<mode>[.<knob>-<value>...].r<rev>
    submission        INTEGER NOT NULL,     -- try ordinal, 1-based
    submission_id     TEXT GENERATED ALWAYS AS (case_id || '.s' || submission) VIRTUAL,
    rev               INTEGER NOT NULL,     -- measurement revision, also inside case_id
    group_id          TEXT NOT NULL,        -- the launch this went out with

    -- what was measured
    tool              TEXT NOT NULL,
    mode              TEXT NOT NULL,
    auth              TEXT NOT NULL,        -- anonymous | authenticated
    target_bucket     TEXT NOT NULL,        -- the S3 bucket listed
    target_region     TEXT NOT NULL,        -- its AWS region
    case_fingerprint  TEXT NOT NULL,        -- sha256 over the whole resolved case

    -- what ran it
    image_uri         TEXT NOT NULL,        -- pinned @sha256 toolbox
    image_set_sha256  TEXT NOT NULL,        -- digest of the eleven-tool provenance document
    harness_revision  TEXT NOT NULL,        -- commit the toolbox was built from

    -- where it ran
    project           TEXT NOT NULL,        -- GCP project
    location          TEXT NOT NULL,        -- GCP region Batch ran it in
    job_name          TEXT NOT NULL UNIQUE, -- provider job ID: sanitized, <= 63 characters

    -- where the evidence is
    evidence_prefix   TEXT NOT NULL,        -- gs://.../<suite>/<target>/<case>/s<n>/

    -- the request and its outcome
    request_json      TEXT NOT NULL,        -- frozen provider request; a retry is diffed against it
    state             TEXT NOT NULL,
    state_detail      TEXT,                 -- the provider's message, when it failed
    recorded_at       TEXT NOT NULL,        -- intent journaled, before the provider was called
    updated_at        TEXT NOT NULL,
    settled_at        TEXT,                 -- first entry into a terminal state

    PRIMARY KEY (case_id, submission)
);

CREATE INDEX submissions_by_group ON submissions (group_id);
CREATE INDEX submissions_by_state ON submissions (state);
```

**A row is one submission, not one case.** A retried case gains a second row with
`submission = 2`, its own `submission_id`, and its own object prefix. Nothing is
overwritten and no row is deleted, so the table is the study's full run history.

### `submission_id` and `job_name` are not the same string

`submission_id` is this system's identity: self-describing, unrestricted, equal
to the object prefix, permanent. `job_name` is the provider's — the handle for
`get_job` and `delete_job`, constrained to 63 characters of lowercase
alphanumerics and hyphens, no dots. A real `submission_id` like
`swath.recursive-parquet-sorted.container_memory_gb-2.r3.s2` cannot be a Batch
job name, so `job_name` is a sanitized derivation with a hash restoring the
uniqueness truncation destroys. Store it rather than recomputing it: a later
change to the derivation would otherwise orphan the jobs already out there.

You grep `submission_id`; you paste `job_name` into `gcloud batch jobs describe`.

### What is stored, and what is not

The rule: **store what this row cannot regenerate on its own, plus anything
whose derivation rule may change** — history outlives the code that wrote it.

- `case_id` is stored because it is *not* a function of the resolved values.
  `derive_case_id` renders the union of the keys the rows **stated**, so the ID
  depends on how the plan was authored; two plans resolving to identical values
  render different IDs. Nothing in the row can recompute it.
- `submission_id` is **not stored**. It is a generated column over `case_id` and
  `submission` — greppable and indexable, with one source of truth and no way to
  drift from its parts.
- `job_name` is stored because its derivation is a sanitize-and-hash function
  that may change; recomputing it later would orphan jobs already out there.
- `evidence_prefix` is stored for the same reason applied to the object layout:
  rows written under an earlier layout must stay resolvable after it changes.

### The provider's ID cannot be the key

It is unique, so it is tempting. It cannot be the primary identity for one
structural reason: **a row exists before its job does.** Intent is journaled
first, and `NOT_CREATED` means no job ever came into being — so a table keyed on
the remote name has no key for exactly the rows that record a failure to create
one. It is also opaque (truncated and hashed), provider-scoped, and outlived by
the evidence: a Batch job can be deleted while its measurement stands forever.

So the remote ID is what it says it is — a handle for talking to the provider —
and the local key is allocated before anyone is called.

### The readable ID and the fingerprint are both kept

`submission_id` serializes tool, mode, the row keys that differ from the layer
defaults, the revision, and the try. It deliberately omits `timeout_s` and
`reps`, and the target bucket sits above it as a path level — so it is a
*readable* identity, not a complete one. `case_fingerprint` is the complete one:
a sha256 over the whole resolved case, unreadable and exact.

Neither replaces the other. The ID is what a path, a grep, and an operator use;
the fingerprint is what identity comparisons use. The gap between them is
load-bearing, and the plan schema already enforces the rule that governs it:
**every key a row may state must move both the ID and the fingerprint.** A key
visible to one but not the other renders one ID for two fingerprints — two
non-comparable runs filed into one directory. That is why `timeout_s` may not
appear on a row at all: it is in the fingerprint and not the ID.

`case_id` leads the primary key, so every question actually asked — every try of
this case, has this case run, what is the next submission number — is an
equality on the key's first column rather than string surgery on a composed
identifier.

### Renames from the shipped table

| Shipped | Model | Why |
| --- | --- | --- |
| `base_job_id` + `submission` (composite key) | `case_id` + `submission` (composite key) | The key is unchanged in shape; `base_job_id` named the job when what it identified was the case. The composed `<case_id>.s<n>` is a generated column, not a third stored name. |
| `job_id` | `job_name` | It is the provider's resource name, not our identity. |
| `campaign_id` | `group_id` | It identifies a launch, not a campaign. |
| `bucket`, `region` | `target_bucket`, `target_region` | Ambiguous against the results bucket and the GCP location. |
| `fingerprint` | `case_fingerprint` | Says what it fingerprints. |
| `destination` | `evidence_prefix` | Says what is there. |
| `job_json` | `request_json` | It is the frozen request. |
| `submitted_at` | `recorded_at` | It is when intent was journaled — before submission. |
| *(absent)* | `rev`, `auth`, `harness_revision`, `state_detail`, `settled_at`, and the `meta` table | See below. |

`auth` matters because the stratum is part of what was measured, and the shipped
ledger cannot answer "was this request signed?" without parsing the frozen job
document. `state_detail` would have put a real failure —
`CODE_GCE_RESOURCE_NOT_FOUND: networks/default` — in the database rather than
only in the provider's event log. `settled_at` makes duration and cost per group
a query rather than an investigation.

## The state column

Two vocabularies share it: the controller writes its own relationship with the
provider, and polling writes Batch's lifecycle states through as it sees them.

| State | Written by | Terminal | Retryable | Meaning |
| --- | --- | --- | --- | --- |
| `SUBMITTING` | intent journaling | no | no | Intent is durable; the provider has not been called. A row left here means the process died in that window. |
| `SUBMITTED` | submit | no | no | Created, and the provider's copy matches the recorded request. |
| `ADOPTED` | submit | no | no | A job of that name already existed and matched the recorded request exactly. |
| `AMBIGUOUS` | submit | no | no | The create outcome could not be established. Re-running submit resolves it — the row is reconciled, never duplicated. |
| *(provider states)* | poll | no | no | `QUEUED`, `SCHEDULED`, `RUNNING`, and the rest of Batch's lifecycle. |
| `SUCCEEDED` | poll | yes | no | The job ran cleanly. Not a verdict about the listing — that is `verify`'s question. |
| `FAILED` | poll | yes | **yes** | Settled failure. |
| `NOT_CREATED` | submit | yes | **yes** | The provider permanently refused creation: bad request, permissions, or a failed precondition. Never probed as ambiguous. |
| `COLLISION` | submit | yes | **yes** | A job of that name exists and does *not* match recorded intent. |
| `CANCELLED` | cancel | yes | no | One-way. |
| `ACCEPTED_FAILED`, `ACCEPTED_NOT_CREATED`, `ACCEPTED_COLLISION` | accept-failure | yes | no | You declared that failure final. An absent measurement, never a passing one. |

Polling never invents a state: a describe that fails leaves the row untouched and
reports "not all terminal".

## Scope under accumulation

With many groups in one file, every command needs to say what it acts on.

| Command | Scope |
| --- | --- |
| `poll` | Everything non-terminal. One listing filtered by `labels.suite` covers every group in flight, so parallel launches need no extra machinery. |
| `status` | Optional `--group` / `--case` filters; unfiltered prints the whole history, which is the point of accumulating. |
| `retry` | One group. Rows from other groups are skipped, not refused. *Today:* raises on the first foreign row. |
| `cancel` | Requires `--group`; without one it refuses rather than cancelling the file. *Today:* cancels every non-terminal row, campaign-blind. |
| `verify` | One group. The "rows agree on image set and provider parent" check narrows to that group. *Today:* applies it to the whole file, which fails the moment a second launch lands. |

Allocating the next try is `max(submission) + 1 WHERE case_id = ?`, which is why
`submission` stays an integer column rather than something parsed back out of
the identifier.

## What is deliberately absent

No results, metrics, or verdicts. Those live in the evidence objects and are
recomputed by `verify` and `report` on demand — a cached verdict in the ledger
is a second answer to a settled question, and the two can disagree.

A `groups` table normalizing the launch-level constants (`image_uri`,
`image_set_sha256`, `harness_revision`, `project`, `location`) is the obvious
alternative. It is cleaner normalization and makes "what did this launch freeze"
a single row, at the cost of a join in every query and a second write path, for
a table holding tens of rows. The repetition is also what lets `verify` detect a
ledger whose rows disagree — a check that normalization makes structurally
impossible, which is either the point or a loss depending on taste. Stay with one
table until a query is actually awkward.

## Reading it

Nothing in this repository is needed:

```sh
sqlite3 'file:benchmark.db?mode=ro' \
  'SELECT submission_id, state, tool, mode FROM submissions ORDER BY case_id, submission'
```

Back the file up. It is authoritative controller state, not a cache: losing it
loses the *binding* rather than the evidence, but without it the objects in the
bucket cannot be tied to the plan case, toolbox, and request that produced them,
and `report` refuses results it cannot bind.
