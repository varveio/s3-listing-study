# The state model

What the benchmark records about its own runs: the attempts it made, where their
evidence landed, and the tables that bind the two.

[`running.md`](running.md) is how to operate a run; this page is what a run *is*
to the system. [`architecture.md`](architecture.md) is why the shape is this
shape, and [`identity.md`](identity.md) is what a `case_id` means.

This page says **`campaign.db`** for the file and **the ledger** for the record
inside it, following the repository's existing usage — see
[`architecture.md`](architecture.md) § *One thing, three names*.

## Attempts

Two runs of one case have identical inputs and therefore the identical hash.
They are told apart by an ordinal, always present:

```
<tool>.<hash>.s1       first attempt
<tool>.<hash>.s2       it failed; this is the retry
```

`.s1` is written out rather than implied: a suffix appearing only on retries
makes every consumer — path builder, parser, glob, human — carry the same
special case, and a prefix can no longer be read by shape.

The manager allocates the next ordinal as `max(attempt) + 1` **inside the same
transaction that inserts the row** (`BEGIN IMMEDIATE`), because groups may be
submitted concurrently. The primary key makes a lost race an integrity error
rather than a silent overwrite, but the transaction is what stops the race.

`reps: 3` allocates three attempts of one case, each its own job on its own
fresh machine. Which kind an attempt is cannot be inferred — a retry follows a
settled failure, but three planned repeats are created together, before any of
them finishes — so `origin` records it, which also makes "how many retries did
this group need" a query.

**Submitting a case that already has a successful attempt is a refusal**, not a
silent no-op and not an implicit repeat. Re-measuring is `reps`, or an explicit
flag; the identity model makes the question answerable, and the answer is stated
rather than guessed.

Failed attempts can be pruned from the bucket later, keeping only evidence that
settled successfully. The row stays, so "this took three tries" survives even
when the bytes from the first two do not.

### Not every attempt is a measurement

Some runs exist to prove a path works, or to produce something, and their
timings must never reach a comparison. The signing paths in swath, s7cmd and
s3-fast-list have code but have never executed, and the first thing to do with
them is a canary — a real job, whose duration is meaningless. So `purpose`
records what an attempt was for:

| `purpose` | In a comparison | What it is |
| --- | --- | --- |
| `measurement` | yes | The default. A timing the study stands behind. |
| `preparation` | no | Produces an artifact a later case consumes. Measured, but not a listing timing. |
| `canary` | no | Proves a path executes at all — a new signing route, a new executor, a machine shape nobody has run. |
| `diagnostic` | no | Reproduces a failure or probes a limit. `s4cmd` hitting a 3600s timeout is worth re-running under observation; it is not worth publishing as a timing. |

`verify` and `report` consider `measurement` rows only. A canary is not a stray
row for completeness to complain about, and not a subject for a comparison to
miss — it is simply not in the population.

**A preparation is measured even though it is not compared.** If s3-fast-list
needs 40 seconds of `ks-tool` to list in 60, then publishing 60 against another
tool's 100 states something false about the hinted path. The preparation's
duration is recorded like any other attempt's, so the total cost of a path that
requires one is recoverable — and a report showing the listing timing alone says
which cases had a preparation behind them.

What `purpose` is *not* is a mode. Swath's `recursive-parquet` versus
`recursive-parquet-sorted`, or TSV versus Parquet output, are the capsule's own
vocabulary: they change what the subject does, they live in `config`, they are
hashed, and each is a measurement in its own right. `purpose` answers "should
this timing be compared?", `mode` answers "what did the tool do?", and the two
never substitute for each other.

Why a preparation may be a separate attempt at all, and what the planner does
with one, is in [`architecture.md`](architecture.md) § *What the planner does*.
How it is hashed differently from a measurement is in
[`identity.md`](identity.md) § *Two identities, two questions*.

## suite and group

**`suite`** is the namespace, and it is one value used three ways: the first
path segment in the results bucket, the job label a polling pass filters on, and
the job-name prefix keeping this study's jobs disjoint from anything else in the
project. Because the label carries the suite itself, a polling pass filters
exactly — `labels.suite=<value>` — rather than scanning for anything
benchmark-shaped. It is constant for the life of a file, so it lives in `meta`.

**`group_id`** records what was launched together. A column only: nothing in the
object layout needs it, because everything a launch froze is already inside each
case hash.

## Object layout

```
gs://<results-bucket>/<suite>/<target-bucket>/<tool>.<hash>.s<attempt>/
```

Deterministic, so evidence is computed from a row rather than discovered by
listing. The worker writes `result.json` last; its presence is what makes an
attempt complete, and checking it is one existence test on a known prefix rather
than a listing that has to resolve which leaf is authoritative.

`<target-bucket>` is already inside the hash, so it identifies nothing the leaf
does not. It stays because a results bucket is browsed by humans and read by
`gsutil`, and one level of grouping by target is what makes that bearable — the
same reason `<suite>` leads. Every other segment identifies something the hash
now covers, so no other segment exists.

Two properties the prefix depends on, both argued in
[`architecture.md`](architecture.md) § *What the object store holds*:

- **Writes are create-only** — `ifGenerationMatch=0`. A deterministic prefix and
  overwrite semantics together let a second execution merge into the first.
- **`result.json` carries `attempt_id` and the `case_id` digest**, and `report`
  refuses evidence whose recorded identity disagrees with the prefix it was
  found under.

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
a missing or misspelled `suite` fails at open time as a schema error rather than
surfacing as a `None` far from the problem. `CHECK (id = 1)` makes
single-rowness a database invariant, and `schema_version` means a file states
its own shape instead of being probed for columns.

**A file whose `schema_version` the code does not recognise is refused.** The
guarantee is that any file a command opens, it fully understands — a command
that adapted to whatever columns it found would write rows that are quietly
incomplete.

```sql
CREATE TABLE attempts (
    -- identity
    case_id             TEXT NOT NULL,      -- <tool>.<hash>
    attempt             INTEGER NOT NULL,   -- ordinal, 1-based
    attempt_id          TEXT GENERATED ALWAYS AS (case_id || '.s' || attempt) VIRTUAL,
    case_inputs         TEXT NOT NULL,      -- the canonical document case_id digests
    group_id            TEXT NOT NULL,      -- the launch this went out with
    tool                TEXT NOT NULL,

    -- environment: the harness reads these and acts
    auth_role           TEXT,               -- logical role name; null runs unsigned
    executor            TEXT NOT NULL,      -- which execution environment ran it
    location            TEXT NOT NULL,      -- region: the network distance to the target
    machine_type        TEXT NOT NULL,      -- resolved shape; what the executor allocated
    vcpus               INTEGER NOT NULL,   -- the declared pair the shape was resolved from
    memory_gb           INTEGER NOT NULL,
    container_memory_gb INTEGER,            -- null means no ceiling
    heap_percent        INTEGER NOT NULL,   -- share of the visible ceiling a managed runtime may take
    timeout_s           INTEGER NOT NULL,
    target_bucket       TEXT NOT NULL,
    target_region       TEXT NOT NULL,
    target_prefix       TEXT NOT NULL,

    -- configuration: forwarded to the capsule, opaque here
    config              TEXT NOT NULL,      -- canonical JSON; holds mode and every tool knob
    product             TEXT NOT NULL,      -- output artifact: text | parquet | parquet-sorted
    fields_emitted      TEXT NOT NULL,      -- canonical JSON array; which columns this mode populates
    -- ...except its reserved axis names, projected out for querying
    mode                TEXT    GENERATED ALWAYS AS (json_extract(config, '$.mode')) VIRTUAL,
    concurrency         INTEGER GENERATED ALWAYS AS (json_extract(config, '$.concurrency')) VIRTUAL,

    -- dependency: null for a case that consumes nothing
    input_artifact_sha256 TEXT,             -- content digest of a consumed artifact; a hash input
    produced_by           TEXT,             -- attempt_id that made it; lineage, not identity
    artifact_sha256       TEXT,             -- what THIS attempt produced, once it settled

    -- what ran it
    tool_slice_sha256   TEXT NOT NULL,
    platform_sha256     TEXT NOT NULL,
    image_uri           TEXT NOT NULL,      -- pinned @sha256 toolbox; recorded, not identity
    image_set_sha256    TEXT NOT NULL,

    -- context: recorded, not hashed
    executor_env        TEXT NOT NULL,      -- canonical JSON: project, provisioning, boot disk, network
    service_account     TEXT NOT NULL,      -- what auth_role resolved to
    secret_resource     TEXT,               -- the credential version, when a role was used
    job_name            TEXT NOT NULL UNIQUE,
    result_prefix       TEXT NOT NULL,

    -- the request and its outcome
    request_json        TEXT NOT NULL,      -- frozen provider request; a retry is diffed against it
    purpose             TEXT NOT NULL
        CHECK (purpose IN ('measurement', 'preparation', 'canary', 'diagnostic')),
    origin              TEXT NOT NULL CHECK (origin IN ('planned', 'retry')),
    state               TEXT NOT NULL,      -- open vocabulary; see "The state column"
    state_detail        TEXT,               -- the provider's message, when it failed
    recorded_at         TEXT NOT NULL,      -- intent journaled, before the provider was called
    updated_at          TEXT NOT NULL,
    settled_at          TEXT,

    PRIMARY KEY (case_id, attempt)
);
```

No secondary indexes. This table holds hundreds of rows and will hold thousands
after years of campaigns, which SQLite scans faster than it parses the query
that asked. `UNIQUE (job_name)` is there as a constraint — two rows must not
claim one job — not as an access path.

**A row is one attempt, not one case.** Nothing is overwritten and no row is
deleted, so the table is the study's full run history even after failed evidence
is pruned from the bucket.

The three dependency columns split by role, which is the distinction
[`identity.md`](identity.md) turns on: `input_artifact_sha256` is content and
therefore a hash input; `produced_by` is lineage and therefore recorded only;
`artifact_sha256` is what this attempt *made*, written when it settles, and is
what a later case's `input_artifact_sha256` is copied from.

```sql
CREATE TABLE pending (
    group_id      TEXT NOT NULL,
    slot          INTEGER NOT NULL,   -- ordinal within the group
    tool          TEXT NOT NULL,
    purpose       TEXT NOT NULL
        CHECK (purpose IN ('measurement', 'canary', 'diagnostic')),
    known_inputs  TEXT NOT NULL,      -- canonical JSON: every input resolved so far
    awaiting      TEXT NOT NULL,      -- attempt_id of the preparation this waits on
    state         TEXT NOT NULL CHECK (state IN ('BLOCKED', 'RESOLVED', 'ABANDONED')),
    became        TEXT,               -- the attempt_id it minted, once RESOLVED
    recorded_at   TEXT NOT NULL,
    settled_at    TEXT,

    PRIMARY KEY (group_id, slot)
);
```

A **slot** is a measurement a launch intended and cannot yet identify, because
one of its inputs is an artifact a preparation has not produced. It cannot be an
`attempts` row: that table is keyed by identity and this case has none yet.

`ABANDONED` is what a slot becomes when its preparation settles unsuccessfully
and the failure is accepted — the same declaration `ACCEPTED_FAILED` makes about
an attempt, applied to a measurement that never got to exist. An absent
measurement, recorded as absent.

`purpose` here omits `preparation`: a preparation is what a slot waits *on*,
never what a slot becomes. A slot awaiting another slot would be a workflow.

`awaiting` is what the planner reads when an attempt settles — which slots does
this unblock? — and the fan-out is the normal case rather than the exception,
because one preparation typically unblocks every cell of a sweep.

A slot is scaffolding rather than evidence, so it is the one structure here that
may be deleted once a group is long settled. `became` points at the attempt it
turned into, so nothing is lost that the attempt does not already say.

### Why mode and concurrency are generated rather than stored

`config` has to be a blob: it is a hash input, and a hashed document is stored
byte-exactly rather than reassembled, for the same reason `case_inputs` is. Any
difference in how it was rebuilt would silently change the `case_id`.

Given that, a plain `mode` column beside it would be a second copy of a value
that already lives in the blob, kept in step by nothing. A generated column
cannot drift, because SQLite computes it from `config` on every read — so the
projection is the only way to get a queryable column without a duplicate.

**`mode` and `concurrency` get one because they are the two axes a comparison is
read along** — *what did it do* and *how wide did it go*. That is the return on
reserving their key names in
[`capsule-contract.md`](capsule-contract.md): a question asked across eleven
tools wants a column, not eleven JSON paths. Every other config key stays in the
blob, where a report can reach it if it ever needs to.

`NULL` is meaningful: a capsule with no such knob projects `NULL`, and the six
tools exposing no concurrency control are exactly the rows where a concurrency
comparison has to declare a hole rather than assume a value.

### What is stored, and what is not

Store what the row cannot regenerate, plus anything whose derivation rule may
change: history outlives the code that wrote it.

- `attempt_id` is **not** stored: a generated column over the two columns it
  composes, with no way to drift from its parts.
- `job_name` is stored, though derivable. It is the join to the provider's world
  — logs, the console, `gcloud batch jobs describe` — and `UNIQUE`, so two rows
  cannot claim one job.
- `result_prefix` is stored, though derivable. A row states where its evidence
  actually went, rather than where today's rule says it should be.
- `product` and `fields_emitted` are stored although the capsule declares them,
  because a capsule edit can change what a mode emits and an old row must keep
  the answer that was true when it ran. They are the two facts a report needs to
  know which attempts may sit in one table — a text stratum, a Parquet stratum,
  and never a key-only row ranked against a four-column one.
- `case_inputs` is stored although `case_id` is a pure function of it, because
  the function is one-way. It is what makes a hash collision loud: an insert
  naming an existing `case_id` compares the two documents and refuses a
  mismatch. It also answers "why is this a different case from that one?" by
  diffing, which reading two hashes cannot.

`origin` and `purpose` carry `CHECK`s; `state` does not. Both of the first two
are closed vocabularies this harness owns entirely, and a typo in either is
worth refusing at write time — a misspelled `purpose` would silently drop an
attempt out of every comparison. `state` deliberately passes the provider's
lifecycle words through as it sees them, so a constraint there would turn a
provider adding a state into a write failure in the middle of a campaign, which
is the worst possible moment to discover it.

### The provider's ID cannot be the key

It is unique, so it is tempting. But **a row exists before its job does**:
intent is journaled first, and `NOT_CREATED` records a job that never came into
being, so keying on the remote name leaves exactly the failures unkeyed. It is
also truncated and hashed, provider-scoped, and outlived by the evidence.

`case_id` cannot serve as the job name either: Batch caps job IDs at 63
characters of lowercase alphanumerics and hyphens, no dots. You grep
`attempt_id`; you paste `job_name` into `gcloud batch jobs describe`.

## The state column

Two vocabularies share it: the controller writes its own relationship with the
provider, and polling writes the provider's lifecycle states through as it sees
them.

| State | Written by | Terminal | Retryable | Meaning |
| --- | --- | --- | --- | --- |
| `SUBMITTING` | intent journaling | no | no | Intent is durable; the provider has not been called. A row left here means the process died in that window. |
| `SUBMITTED` | submit | no | no | Created, and the provider's copy matches the recorded request. |
| `ADOPTED` | submit | no | no | A job of that name already existed and matched the recorded request exactly. |
| `AMBIGUOUS` | submit | no | no | The create outcome could not be established. Re-running submit reconciles the row rather than duplicating it. |
| *(provider states)* | poll | no | no | `QUEUED`, `SCHEDULED`, `RUNNING`, and the rest of the provider's lifecycle. |
| `SUCCEEDED` | poll | yes | no | The job ran cleanly. Not a verdict about the listing — that is `verify`'s question. |
| `FAILED` | poll | yes | **yes** | Settled failure. |
| `NOT_CREATED` | submit | yes | **yes** | The provider permanently refused creation. Never probed as ambiguous. |
| `COLLISION` | submit | yes | **yes** | A job of that name exists and does *not* match recorded intent. |
| `CANCELLED` | cancel, or the provider | yes | no | One-way. |
| `ACCEPTED_FAILED`, `ACCEPTED_NOT_CREATED`, `ACCEPTED_COLLISION` | accept-failure | yes | no | You declared that failure final. An absent measurement, never a passing one. |

`SUCCEEDED`, `FAILED`, and `CANCELLED` exist in both vocabularies, so a job
cancelled from the console is indistinguishable from one this harness cancelled.
That is accepted rather than solved: `state_detail` carries the provider's own
message, and the alternative is namespacing every controller state.

Polling never invents a state: a describe that fails leaves the row untouched and
reports "not all terminal".

## Scope under accumulation

One file accumulates every group, and several groups may be in flight at once.

| Command | Scope |
| --- | --- |
| `poll` | Everything non-terminal. One listing filtered by `labels.suite` covers every group in flight, so parallel launches need no extra machinery. A settled preparation also unblocks whatever slots awaited it. |
| `status` | Optional `--group` / `--case` filters; unfiltered prints the whole history, which is the point of accumulating. Blocked slots are shown alongside attempts — a group is not understood from its rows alone while it still owes one. |
| `retry` | One group. Rows from other groups are skipped, not refused. |
| `cancel` | Requires `--group`; without one it refuses rather than cancelling the file. |
| `verify` | One group, `purpose = 'measurement'` only. |
| `prune` | Deletes evidence objects for attempts that settled unsuccessfully, leaving every row. Requires `--group`, for the same reason `cancel` does: an unscoped delete over an accumulating file is the one mistake with no undo. |

### What verify binds against

**The group, through the recorded rows — not a re-resolved plan.**

`case_id` folds in the tool and platform slices, the executor, and the machine
type, so reproducing one from a plan would mean re-resolving that plan *and*
holding the exact image set and executor configuration the launch used.
Rebuilding an identity in order to check it is the wrong direction anyway — it
re-derives from a file that may have been edited since, to confirm something the
ledger already recorded.

So the roster is the group. Every attempt that went out is a row carrying its
`case_id`, `result_prefix`, and settled `state`, which is precisely what
completeness needs: no subject missing, none stray, each one's evidence where
the row says it is.

A group's roster is its attempts **plus its unresolved slots**. A `BLOCKED` slot
is a measurement the launch intended and has not got, so a group holding one is
incomplete and `verify` says so rather than reporting on the subset that
happened to make it. An `ABANDONED` slot is incompleteness someone declared
final, which reports as an absent subject — never as a passing comparison with
one fewer tool in it.

That leaves the plan doing what a plan is for. **Intent is checked at submit,
against the launch it produced** — the moment a mismatch is cheap to fix and the
plan file is the one that was actually read.

Comparability moves the same way. "Are these two attempts comparable?" is a
column comparison — same `platform_sha256`, same environment, differing only
where the study means them to differ — which a reader can run by hand in SQL,
rather than a fingerprint equality test only the harness can evaluate. What
column equality still cannot tell you is whether the corpus moved between them;
see [`identity.md`](identity.md) § *What identity cannot cover*.

Two consequences fall out. A group **may** span several plans and target
buckets, because nothing in the roster needs them to agree. But a **comparison**
is scoped to one target bucket, since comparing listings of different corpora is
not a comparison — so `verify` reports per bucket within the group it was given.

## Open questions

- **The `job_name` derivation.** It must be collision-free across an
  accumulating file and fit Batch's 63 characters of lowercase alphanumerics
  and hyphens.
- **How `group_id` is minted.** It has to be unique within an accumulating file,
  meaningful enough to type at a prompt, and assigned without a round trip —
  `retry`, `cancel` and `prune` all take it as their scope, so it is the handle
  an operator uses under pressure.

## What is deliberately absent

No results, metrics, or verdicts. Those live in the evidence objects and are
recomputed by `verify` and `report` on demand — a cached verdict is a second
answer to a settled question, and the two can disagree.

Back the ledger up. Losing it does not destroy the evidence, but it costs the
binding: `report` refuses results it cannot tie back to a recorded row.
