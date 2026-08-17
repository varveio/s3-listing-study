# The state model

What the benchmark records about its own runs: how a measurement is identified,
where its evidence lands, and the table that binds the two.

[`running.md`](running.md) is how to operate a run; this page is what a run *is*
to the system.

## Status of this model: not implemented

The controller in `../src/benchmark/campaign.py` still uses the earlier
vocabulary — `campaign_id`, `base_job_id`, a case ID rendered from plan keys, a
separate fingerprint, and per-tool knowledge held by the harness. Sections below
mark what exists today only where the gap matters. Nothing here describes a
shipped schema until this line says so.

## What a case is

A case is **the tool and a hash over everything that can change the
measurement**:

```
<tool>.<hash>
aws-cli.9f300cc4d2b1
```

Three groups of inputs go into the hash:

| Group | What it covers |
| --- | --- |
| **Environment** | The values the harness acts on: executor, auth role, target bucket/region/prefix, location, machine type, vCPUs, memory, container ceiling, timeout |
| **Config** | The capsule's own keys, canonical JSON, `{}` when empty |
| **What ran it** | The tool slice and the platform slice — see below |

Anything that could make two runs non-comparable is in it by construction, and
that is the point of using one hash instead of two identifiers.

The earlier model had a readable ID rendered from plan keys *and* a fingerprint
hashed over resolved values, which forced a law: every key must move both, or
one ID renders two fingerprints and two non-comparable runs land in one
directory. `timeout_s` is barred from plan rows for exactly that reason. With
one hash the law is unnecessary — a field either changes the identity or it is
not an input. A revision counter is unnecessary too: a changed input is a
changed hash, with nothing to allocate and nothing to keep in sync. And "have we
already measured exactly this?" becomes a lookup.

**What it costs:** the identity is no longer readable in a bucket listing.
`swath.recursive-parquet-sorted.container_memory_gb-2` said what it was;
`swath.4c1e8a77b920` does not. The columns say it instead, reports render from
them, and each attempt's `result.json` carries its own provenance — so a lost
database is rebuilt by reading objects, not by parsing their names.

**Changing what goes into the hash re-identifies everything.** Old rows keep
their identity and their evidence stays put; new runs get new prefixes. Treat
the input list as a contract.

## What the harness owns, and what it carries

One question decides where a value belongs: **does the harness have to do
something different because of it?**

If yes, it is an environment field — the harness reads it and acts. If no, the
harness only carries it to the capsule, and it is tool configuration.

| Value | What the harness does | Owner |
| --- | --- | --- |
| `auth_role` | Resolves to a service account and a credential secret; null runs unsigned | harness |
| `executor` | Selects which execution environment renders and submits the job | harness |
| `location` | Chooses the region the machine runs in — the network distance to the target | harness |
| `machine_type` | The shape resolved from vCPUs and memory; what the executor allocates | harness |
| `vcpus`, `memory_gb` | The declared pair a shape is resolved from | harness |
| `container_memory_gb` | Sets the container's cgroup ceiling | harness |
| `timeout_s` | Kill deadline, and the provider's `maxRunDuration` | harness |
| `target_bucket`, `target_region`, `target_prefix` | Names the target; region reaches the subject's environment | harness |
| mode, concurrency, page size, output flags | Nothing — passed through | capsule |
| managed-runtime heap flags | Nothing — derived from the ceiling the capsule is told about | capsule |

`auth_role` is the clearest case and the reason the rule is worth stating. It is
not a flag the subject receives: it picks the *identity* the task runs as, and
IAM enforces that only that identity can read the credential. The tool only ever
sees whatever ended up in its environment.

It is a **logical role name, nullable** — not a two-valued stratum. Null means
unsigned: the anonymous worker service account, no secret attached. A name means
the harness looks it up in the deployment's role table and gets back a service
account and a secret version. Today's single credential becomes the role
`public_auth_list`; a future role reading a private corpus or a different AWS
account is a new name, not a new flag. *Today:* `auth` is `anonymous` or
`authenticated`, which works only while there is exactly one credential.

The role **name** is hashed, because what a tool is allowed to see can change
what it lists. Its resolution is recorded but not hashed, which leaves one
residual risk: repointing an existing role at different credentials changes the
measurement without changing the identity. Treat a role as immutable once used,
and mint a new name when the credential behind it changes.

Everything on the capsule side travels as one opaque, canonical JSON `config`
blob. The harness never interprets it.

**The capsule must declare the keys it accepts**, the way it already declares
its modes, and an undeclared key must be refused. Without that, `concurency: 8`
is silently ignored and a sweep produces cells that are all identical — the
failure the plan schema's closed vocabulary exists to prevent. The declaration
lives in `tools/<tool>/adapter/`, so it is covered by `adapter_bundle_sha256`
and changing it changes that tool's identity.

*Today:* the harness holds per-tool knowledge it should not. `plans/tools.yaml`
carries a `heap:` table stating that swath is a JVM wanting
`-XX:MaxRAMPercentage` and s3p is V8 wanting `--max-old-space-size` in MiB —
which is why a plan is forbidden from setting a heap share, a rule that exists
only because the knowledge sits in the wrong place. Under this model the harness
says "your ceiling is 4 GB" and the capsule answers with the environment its
runtime needs.

### The cost: cross-tool dimensions become a convention

Concurrency is the case in point. Its purpose is to make "every tool at logical
concurrency 8" expressible, and that only means something if every capsule means
the same thing by the number. As config, that rests on a documented convention
and each capsule's declaration rather than on a column.

That is where it rests already: the translation into `--numworkers`,
`--checkers`, or `--list-concurrency` is per-adapter, no capsule declares a
supported range, and nothing enforces that two of them agree. Naming it a
convention states the situation honestly instead of implying a guarantee the
code does not provide.

### What is recorded but not hashed

| Value | Why it is not identity |
| --- | --- |
| `executor_env` (project, provisioning, boot disk, network) | Estate detail. Moving projects does not change how fast a bucket lists, and re-identifying every case because an account was reorganized is over-invalidation with no measurement behind it. |
| Provisioning model | SPOT changes how likely an attempt is to survive, not what it measures. A preemption is a failed attempt, not a different case. |
| `service_account`, `secret_resource` | What `auth_role` resolved to; the role name carries the meaning. |
| `image_uri`, `image_set_sha256` | You need to know exactly what ran and be able to reproduce it, but the slices are what identify it. Two attempts of one case may have run on different images, and the row says which. |

`network` and `subnetwork` are arguable — an egress path could matter — but they
follow from the executor's project and location today, so they stay in
`executor_env` until a run crosses VPCs.

## The tool and platform slices

The toolbox is one image holding all eleven tools, so its digest is the wrong
granularity for a case. Bump rclone, rebuild, and every tool's hash would change
— new prefixes and lost comparability for ten tools that did not change.

Two digests replace it:

- **tool slice** — that tool's artifact, capsule recipe, build inputs, adapter
  bundle, its stage in the toolbox recipe, and the `COPY --from` lines that
  install it.
- **platform slice** — the shared base image digest, the APT snapshot pin, the
  worker's pinned Python requirements, the harness revision, and the final
  toolbox stage with those per-tool `COPY` lines removed.

rclone's bump then leaves aws-cli's identity untouched, because aws-cli's
artifact, adapter, and stage are unchanged and so is the base. A Debian snapshot
bump or an edit to `measure.py` correctly re-identifies everything, because that
moves the floor under every measurement.

**Both are computable from what the build already has.** The per-tool digests —
artifact, capsule recipe, build inputs, adapter bundle — are already in the
toolbox manifest, and `build_image.py` already extracts each tool's Dockerfile
stage by name, because `validate_executed_sources` checks that a stage installs
the artifact its capsule declared. The platform inputs are all pinned in that
same Dockerfile: the `runtime_base` digest, the `snapshot.debian.org` timestamp,
and `requirements-worker.txt`. The build should compute both and write them into
the schema-4 image set as `tool_slice_sha256` and `platform_sha256`, so the
controller reads them rather than reparsing a Dockerfile at submit time.

**Attributing the `COPY --from` lines to their tool is what keeps the roster
additive.** All of them live in the final stage, so hashing that stage whole
would put every tool's install line into the platform — and adding a twelfth
tool would re-identify all eleven existing ones, losing comparability for tools
that did not change. With each `COPY --from=<tool stage>` line attributed to its
own slice, adding a tool leaves the platform digest and every existing slice
byte-identical.

**The error directions are not symmetric.** Over-invalidating costs re-runs.
Under-invalidating means two different binaries share an identity and a
comparison silently mixes them, which cannot be repaired afterwards. When it is
unclear whether something belongs in the platform slice, put it in.

This separation holds only while the eleven builds are independent stages over a
shared base. A recipe that made one tool's stage depend on another's would make
the slices inseparable, and identity would fall back to the whole image.

## Attempts

Two runs of one case have identical inputs and therefore the identical hash.
They are told apart by an ordinal, always present:

```
<tool>.<hash>.s1       first attempt
<tool>.<hash>.s2       it failed; this is the retry
<tool>.<hash>.s3
```

`.s1` is written out rather than implied. A suffix appearing only on retries
would make every consumer — the path builder, the parser, a glob, a person
reading a listing — carry the same special case, and a prefix could no longer be
read by shape.

The manager allocates the next ordinal as `max(attempt) + 1`. Deliberate repeats
work the same way: `reps: 3` allocates three attempts of one case, each its own
job on its own fresh machine, which is what makes the spread mean anything.
Repeats share *declared* inputs, not conditions — different machine, different
host, different time of day — so a spread across attempts measures the
environment as much as the tool.

Which kind an attempt is cannot be inferred, which is why `origin` records it. A
retry is allocated after its predecessor settles, so the predecessor's state
would identify it — but three planned repeats are created together, before any
of them finishes. Recording it also makes "how many retries did this group need"
a query rather than an investigation.

Failed attempts can be pruned later, keeping only evidence that settled
successfully. The row stays, so "this took three tries" survives even when the
bytes from the first two do not.

## suite and group

**`suite`** is the namespace: the first path segment in the results bucket, the
job label a polling pass filters on (`labels.suite=<value>`, server-side), and
the job-name prefix keeping this study's jobs disjoint from anything else in the
project. Constant for the life of a file, so it lives in `meta`. *Today:* all
three are the hardcoded string `benchmark`.

**`group_id`** records what was launched together. A column only: nothing in the
object layout needs it, because everything a launch froze is already inside each
case hash.

## Object layout

```
gs://<results-bucket>/<suite>/<target-bucket>/<tool>.<hash>.s<attempt>/
```

Deterministic, so evidence is computed from a row rather than discovered by
listing. The worker writes `result.json` last; its presence is what makes an
attempt complete.

*Today:* `campaigns/<run>/results/<bucket>/<tool>/<case>/run-<rep>/submission-<n>/<uuid>/`
— seven levels ending in a random per-execution UUID. Dropping that leaf deletes
machinery: `resolve_leaf`, `list_leaves` with its "exactly one leaf, refuse zero
or 2+" rule, and the `AMBIGUOUS` branch of the retry evidence check, which
becomes one existence check on `<prefix>/result.json`.

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
    attempt_id          TEXT GENERATED ALWAYS AS (case_id || '.s' || attempt) VIRTUAL,
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
    timeout_s           INTEGER NOT NULL,
    target_bucket       TEXT NOT NULL,
    target_region       TEXT NOT NULL,
    target_prefix       TEXT NOT NULL,

    -- configuration: carried to the capsule, never interpreted here
    config              TEXT NOT NULL,      -- canonical JSON; {} when empty

    -- what ran it
    tool_slice_sha256   TEXT NOT NULL,      -- artifact, recipe, build inputs, adapter, stage
    platform_sha256     TEXT NOT NULL,      -- base image, APT pin, worker deps, harness, final stage
    image_uri           TEXT NOT NULL,      -- pinned @sha256 toolbox; recorded, not identity
    image_set_sha256    TEXT NOT NULL,      -- the eleven-tool provenance document

    -- context: recorded, not hashed
    executor_env        TEXT NOT NULL,      -- canonical JSON: project, provisioning, boot disk, network
    service_account     TEXT NOT NULL,      -- what auth_role resolved to
    secret_resource     TEXT,               -- the credential version, when a role was used
    job_name            TEXT NOT NULL UNIQUE,  -- provider job ID: sanitized, <= 63 chars
    result_prefix       TEXT NOT NULL,      -- gs://.../<tool>.<hash>.s<n>/

    -- the request and its outcome
    request_json        TEXT NOT NULL,      -- frozen provider request; a retry is diffed against it
    origin              TEXT NOT NULL,      -- planned | retry
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

The hashed values are stored as columns as well: the hash makes them comparable,
the columns make them readable, and reports render from the columns. A config key
worth querying can be projected without leaving the blob —

```sql
concurrency INTEGER GENERATED ALWAYS AS (json_extract(config, '$.concurrency')) VIRTUAL
```

— which indexes, so `WHERE concurrency > 10` stays a real question.

### What is stored, and what is not

Store what the row cannot regenerate, plus anything whose derivation rule may
change: history outlives the code that wrote it.

- `attempt_id` is **not** stored: a generated column over the two columns it
  composes. Greppable and indexable, with no way to drift from its parts.
- `job_name` is stored, though it is derivable. It is the join to the provider's
  world — logs, the console, `gcloud batch jobs describe` — and it must be
  `UNIQUE`, so two rows cannot claim one job. Its derivation is also a rule that
  has already changed once: the retired controller and the current one name jobs
  differently, so recomputation would not find what an old row submitted.
- `result_prefix` is stored for the same reason applied to the layout: rows
  written under an earlier layout must stay resolvable after it changes.

### The provider's ID cannot be the key

It is unique, so it is tempting. But **a row exists before its job does**:
intent is journaled first, and `NOT_CREATED` records a job that never came into
being — so keying on the remote name leaves exactly the failures unkeyed. It is
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
| `CANCELLED` | cancel | yes | no | One-way. |
| `ACCEPTED_FAILED`, `ACCEPTED_NOT_CREATED`, `ACCEPTED_COLLISION` | accept-failure | yes | no | You declared that failure final. An absent measurement, never a passing one. |

Polling never invents a state: a describe that fails leaves the row untouched and
reports "not all terminal".

## Scope under accumulation

One file accumulates every group, and several groups may be in flight at once.
Each command says what it acts on.

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

A `groups` table normalizing the launch-level constants is the obvious
alternative to repeating them per row. It is cleaner normalization, at the cost
of a join in every query and a second write path, for a table holding tens of
rows. Stay with one table until a query is actually awkward.

## Reading it

```sh
sqlite3 'file:benchmark.db?mode=ro' \
  'SELECT attempt_id, state, tool, target_bucket, config FROM attempts ORDER BY case_id, attempt'
```

Back the file up. Losing it loses the *binding*, not the evidence: the objects
survive, but nothing then ties them to the case, toolbox, and request that
produced them, and `report` refuses results it cannot bind.
