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

Case identity is also stated authoritatively in
[`../plans/README.md`](../plans/README.md) § *A layer and a row*. That page and
this one must change in the same commit, or the two will disagree about what a
case is.

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
| **Config** | The capsule's own keys, `{}` when empty |
| **What ran it** | The tool slice and the platform slice |

Anything that could make two runs non-comparable is in it by construction. One
hash rather than a readable ID beside a fingerprint removes the law that the two
must move together — a field either changes the identity or it is not an input —
and removes the need for a revision counter, since a changed input is already a
changed hash. It also makes "have we already measured exactly this?" a lookup.

### The hash, normatively

An unspecified encoding will be re-derived differently by the next reader, so:

```python
CASE_HASH_V1 = b"s3-listing-study-case-v1\0"

def case_hash(environment: dict, config: dict, tool_slice: str, platform: str) -> str:
    document = json.dumps(
        {
            "environment": environment,   # the table above, absent keys omitted
            "config": config,             # the capsule's blob, as an object
            "tool_slice_sha256": tool_slice,
            "platform_sha256": platform,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(CASE_HASH_V1 + document).hexdigest()[:12]
```

- **Domain separation and version** lead the input, as
  `_input_digest` already does for build inputs (`build_image.py:78`).
- **Canonical JSON** means `sort_keys=True`, `separators=(",", ":")`, ASCII
  escaping, and no non-finite numbers — the form `build_image.py:147` already
  uses.
- **12 hex digits (48 bits)** is the identifier length. Collisions are a
  correctness failure, not a nuisance: two cases sharing a prefix would merge
  their evidence. The `UNIQUE` constraint on `job_name` and the primary key both
  refuse the second one loudly, and 12 is chosen to match the existing job-ID
  digest; raise it if a study ever holds enough cases to make the birthday bound
  uncomfortable.
- **Absent, null, and empty are different.** A key with no value is omitted; a
  key that is explicitly null is present with `null`. `auth_role` null (unsigned)
  and `container_memory_gb` null (no ceiling) are values, not absences.
- `tool` and `suite` are **not** hash inputs. The tool prefixes the identifier,
  and the suite prefixes the path.

**Changing the input list re-identifies everything.** Old rows keep their
identity and their evidence stays put; new runs get new prefixes. Bump the
version constant when it changes, so the two generations are distinguishable.

**What the hash costs:** the identity is no longer readable in a bucket listing.
`swath.recursive-parquet-sorted.container_memory_gb-2` said what it was;
`swath.4c1e8a77b920` does not. The columns say it instead, and reports render
from them.

## What the harness owns, and what it carries

One question decides where a value belongs: **does the harness have to do
something different because of it?**

| Value | What the harness does | Owner |
| --- | --- | --- |
| `auth_role` | Resolves to a service account and a credential secret; null runs unsigned | harness |
| `executor` | Selects which execution environment renders and submits the job | harness |
| `location` | Chooses the region the machine runs in — the network distance to the target | harness |
| `machine_type` | The shape resolved from vCPUs and memory; what the executor allocates | harness |
| `vcpus`, `memory_gb` | The declared pair a shape is resolved from | harness |
| `container_memory_gb` | Sets the container's cgroup ceiling | harness |
| `timeout_s` | The worker's kill deadline, and the basis of the provider's run duration | harness |
| `target_bucket`, `target_region`, `target_prefix` | Names the target; region reaches the subject's environment | harness |
| mode, concurrency, page size, output flags | Nothing — forwarded, never read | capsule |
| managed-runtime heap flags | Nothing — derived from the ceiling the capsule is told about | capsule |

`mode` looks like an exception and is not one. Verification does need it —
`verify` normalizes both sides through a capsule's `normalize.py`
(`verify.py:468`) — but it arrives there as a **pass-through**:
`adapters.normalize_to_path(adapter_dir, tool, mode, …)` hands the value on
without the harness ever branching on it. Forward the whole config blob to both
capsule entry points, `command.py` and `normalize.py`, and the harness needs to
read nothing at all.

### auth_role, and the part the capsule still needs

`auth_role` is a **logical role name, nullable**, not a two-valued stratum. Null
means unsigned: the anonymous worker service account, no secret attached. A name
resolves through the deployment's role table to a service account and a secret
version. Today's single credential becomes the role `public_auth_list`; a future
role reading a private corpus or a different AWS account is a new name rather
than a new flag. *Today:* `auth` is `anonymous` or `authenticated`, which works
only while there is exactly one credential.

The role **name** is hashed, because what a tool may see can change what it
lists. Its resolution is recorded but not hashed, which leaves one residual
risk: repointing an existing role at different credentials changes the
measurement without changing the identity. Treat a role as immutable once used.

**The capsule needs the stratum too, as a boolean.** Selecting a service account
is a harness act, but signing is also an argv decision inside six capsules
today: `--no-sign-request` in aws-cli (`command.py:36`), s5cmd (`:22`),
s3-fast-list (`:20`) and swath (`:58`), `--target-no-sign-request` in s7cmd
(`:32`), and a branch in rclone (`:31`). So the harness passes a derived
`signed` boolean — `auth_role is not None` — into the capsule's request. It is
derived, so it is not a separate hash input.

### Configuration

Everything on the capsule side travels as one canonical JSON `config` blob, and
the harness treats it as **opaque bytes**: it hashes them for identity, stores
them, and forwards them to the capsule's entry points. Nothing else needs the
contents — a value that changes what the *subject* does is the tool's business,
and the harness's interest ends at "these bytes differ, so this is a different
case".

**Remove `concurrency` from the shared request contract.** `CommandRequest`
carries a typed `concurrency` field and `command_adapter.py` a shared
`validate_concurrency` helper, both left over from when concurrency looked like
a universal dimension. Neither earns its place: the harness does nothing with
the value, and the guarantee the helper provides — an explicit knob a capsule
never declared is refused — is exactly what per-capsule key declaration
provides, stated once per capsule instead of in shared runtime code.

**The capsule declares the keys it accepts, and an undeclared key is refused.**
This pattern already exists rather than needing invention: `s4cmd` declares
`CONCURRENCY_RANGE = (1, 8)` (`command.py:14`), the runtime reads it off the
module (`command_adapter.py:125`), and an explicit concurrency for a capsule
that declares no range is refused outright — *"{tool} does not support logical
concurrency"* (`command_adapter.py:95`). Generalise that to the whole config
blob. Without it, `concurency: 8` is silently ignored and a sweep produces cells
that are all identical.

The declaration must live **inside `command.py`**, beside `MODES` and
`CONCURRENCY_RANGE`. `adapter_bundle_sha256` covers a closed tuple —
`ADAPTER_FILES = ("command.py", "normalize.py")` (`build_selection.py:18`) — so
a declaration in a new file under `adapter/` would change no identity at all.

Concurrency is the case in point for what that costs. "Every tool at logical
concurrency 8" is only meaningful if every capsule means the same thing by the
number, and no schema can make them: the translations are `-c`, `--checkers`,
`--list-concurrency`, `--concurrency`. That comparability rests on convention
plus each capsule's declaration — which is where it rests today, since the
shared field enforces a range but never a meaning.

*Today:* the harness holds per-tool knowledge it should not. `plans/tools.yaml`
carries a `heap:` table stating that swath is a JVM wanting
`-XX:MaxRAMPercentage` and s3p is V8 wanting `--max-old-space-size={mib}`, and a
plan may not set a heap share. The stated reason is that nine of eleven tools
have no heap to size, so the knob would be one most cases ignored and every plan
restated (`plans/tools.yaml:31-34`). Under this model the reason disappears with
the table: the harness says "your ceiling is 4 GB" and the capsule answers with
the environment its runtime needs.

### What is recorded but not hashed

| Value | Why it is not identity |
| --- | --- |
| `executor_env` (project, provisioning, boot disk, network) | Estate detail. Moving projects does not change how fast a bucket lists, and re-identifying every case because an account was reorganised is over-invalidation with no measurement behind it. |
| Provisioning model | SPOT changes how likely an attempt is to survive, not what it measures. A preemption is a failed attempt, not a different case. |
| `service_account`, `secret_resource` | What `auth_role` resolved to; the role name carries the meaning. |
| `image_uri`, `image_set_sha256` | You need to know exactly what ran and be able to reproduce it, but the slices identify it. Two attempts of one case may have run on different images, and the row says which. |

`network` and `subnetwork` are arguable — an egress path could matter — but they
follow from the executor's project and location today, so they stay in
`executor_env` until a run crosses VPCs.

## The tool and platform slices

The toolbox is one image holding all eleven tools, so its digest is the wrong
granularity: bump rclone, rebuild, and every tool's hash would change — new
prefixes and lost comparability for ten tools that did not change.

Two digests replace it. **Both are defined over stage closures, not stage
bodies**, because the pinned base of a stage is part of what ran:

- **tool slice** — that tool's artifact, capsule recipe, build inputs, adapter
  bundle, the transitive `FROM` closure of its build stages *including the
  pinned digests on those `FROM` lines*, and the lines of the final stage that
  install or configure it.
- **platform slice** — the `runtime_base` digest, the APT snapshot pin, the
  worker's pinned Python requirements, the harness revision, and the remainder
  of the final stage.

The closure requirement is load-bearing, and the obvious implementation misses
it. The existing extractor captures a stage body *after* its `FROM` line
(`build_image.py:92-99`), so three externally pinned bases would fall outside
both slices: `rust@sha256:cf9dd0…` (s3-fast-list), `node@sha256:2cf067cf…`
(s3p), and `eclipse-temurin@sha256:2f1da100…`, which reaches swath through a
second stage — `swath_jre` — that `TOOL_STAGES` does not map
(`build_image.py:42`). A JRE bump would then change nothing about swath's
identity, which is the unrepairable direction of error.

The same applies to the final stage. It is not only `COPY --from` lines: one
`RUN` block symlinks `aws`, writes the `s3p` shim, and creates `/home/s7cmd`
(`Dockerfile:121-125`). Those lines belong to their tools.

**Attribution is what keeps the roster additive.** Hash the final stage whole
and every tool's install line lands in the platform, so adding a twelfth tool
re-identifies all eleven — losing comparability for tools that did not change.
With per-tool lines attributed to their own slices, adding a tool leaves the
platform digest and every existing slice byte-identical.

Erring coarse is correct when attribution is unclear: over-invalidating costs
re-runs, while under-invalidating means two different binaries share an identity
and a comparison silently mixes them, which cannot be repaired afterwards.

**Publishing the slices needs an image-set version bump.** Adding
`tool_slice_sha256` and `platform_sha256` is not a schema-4-compatible change:
the top-level and per-tool key sets are compared for exact equality in three
places (`campaign.py:168`, `:196`, `Dockerfile:159-165`), and the in-image
recompute strips only `adapter_bundle_sha256` before digesting
(`Dockerfile:198-200`), so a new per-tool key must join that strip list or the
manifest check fails. Note what that strip implies today: `adapter_bundle_sha256`
is deliberately outside the toolbox manifest, so an adapter-only edit currently
changes no digest the controller verifies — which is precisely the gap the tool
slice closes.

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
flag; the identity model makes the question answerable, and the answer should be
stated rather than guessed.

Failed attempts can be pruned from the bucket later, keeping only evidence that
settled successfully. The row stays, so "this took three tries" survives even
when the bytes from the first two do not.

## suite and group

**`suite`** is the namespace: the first path segment in the results bucket, the
job label a polling pass filters on, and the job-name prefix keeping this
study's jobs disjoint from anything else in the project. Constant for the life
of a file, so it lives in `meta`.

*Today:* none of the three is a suite value. The path segment is the literal
`campaigns` (`campaign.py:703`), the poll filter is an existence test on a label
whose value is a per-job hash — `labels.benchmark-intent:*` (`campaign.py:59`,
`:408`) — and only the job-name prefix is the literal `benchmark`
(`campaign.py:258`). Introducing `suite` makes the label filter exact
(`labels.suite=<value>`) instead of a scan for anything benchmark-shaped.

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

*Today:* `campaigns/<run>/results/<bucket>/<tool>/<case>/run-<rep>/submission-<n>/<uuid>/`,
ending in a random per-execution UUID. Dropping that leaf deletes machinery:
`resolve_leaf`, `list_leaves` with its "exactly one leaf, refuse zero or 2+"
rule, and the `AMBIGUOUS` branch of the retry evidence check, which becomes one
existence check on `<prefix>/result.json`.

It requires **create-only writes** in exchange. `gcs.py` documents plain
overwrites as deliberate (`gcs.py:5-7`), which a random leaf made survivable.
With deterministic prefixes a second execution of one attempt would silently
merge — overwriting `result.json` while leaving behind any file the first wrote
and the second did not. An `ifGenerationMatch=0` precondition makes that a loud
failure.

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
`open_db` adds missing columns with a bare `ALTER TABLE` (`campaign.py:555-566`).

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

    -- configuration: forwarded to the capsule, opaque here
    config              TEXT NOT NULL,      -- canonical JSON; holds mode and every tool knob

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
the columns make them readable. A config key worth querying can be projected and
indexed without leaving the blob:

```sql
ALTER TABLE attempts ADD COLUMN mode TEXT
    GENERATED ALWAYS AS (json_extract(config, '$.mode')) VIRTUAL;
CREATE INDEX attempts_by_mode ON attempts (mode);
```

That is a read-side projection for display and querying, not the harness
interpreting a run-time input: the value it reaches for is one a report prints,
never one that decides what the harness does.

### What is stored, and what is not

Store what the row cannot regenerate, plus anything whose derivation rule may
change: history outlives the code that wrote it.

- `attempt_id` is **not** stored: a generated column over the two columns it
  composes, with no way to drift from its parts.
- `job_name` is stored, though derivable. It is the join to the provider's world
  — logs, the console, `gcloud batch jobs describe` — and `UNIQUE`, so two rows
  cannot claim one job. Its derivation is also a rule that has already changed
  once: the retired controller named jobs `c-<campaign>-<tool>-…`, the current
  one `benchmark-<slug>-<hash>`, so recomputation would not find what an old row
  submitted.
- `result_prefix` is stored for the same reason applied to the layout: rows
  written under an earlier layout must stay resolvable after it changes.

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
| `poll` | Everything non-terminal. One listing filtered by `labels.suite` covers every group in flight, so parallel launches need no extra machinery. |
| `status` | Optional `--group` / `--case` filters; unfiltered prints the whole history, which is the point of accumulating. |
| `retry` | One group. Rows from other groups are skipped, not refused. |
| `cancel` | Requires `--group`; without one it refuses rather than cancelling the file. *Today:* cancels every non-terminal row, group-blind (`campaign.py:955-972`). |
| `verify` | One group. |

*Today:* `verify` is the harder blocker of the two. Its plan binding demands the
ledger's `(tool, case_id, rep)` roster equal the plan's exactly — reporting
"missing ledger case" or "unexpected ledger case" (`campaign.py:1026-1029`) —
before it ever reaches the agreement check on `campaign_id` and image set. Under
accumulation the roster equality fails first and hardest.

**Open: what `verify` binds against.** Today it re-resolves a plan and matches
on a plan-derived fingerprint. Under this model `case_id` folds in the tool and
platform slices, the executor, and the machine type, so re-resolving a plan
cannot reproduce a `case_id` without also holding the exact image set and
executor configuration. Either verification binds through the recorded columns
instead of a re-resolved plan, or a group records the resolved case IDs it
submitted. Also unsettled: whether one group may span several plans and buckets
— `verify` and `retry` each take exactly one `--plan` today.

## Migration

The one campaign that exists — `2026-08-17-ghcnsmoke2`, fourteen attempts —
predates every identity input in this model. Its rows carry no group, no slices,
and no computable case hash, and its evidence sits under the old seven-segment
layout.

It is not backfilled. That database and its objects are retained as they are,
readable by the code that wrote them; the new file starts empty. Nothing is
gained by inventing identities for rows whose inputs were never recorded, and
`result_prefix` exists precisely so that rows written under an earlier layout
stay resolvable rather than needing rewriting.

`../README.md` still says no campaign has run. That is now false and should be
corrected in whichever commit lands first.

## Open questions

- **Where the role table lives.** `auth_role` → service account + secret version
  is deployment configuration, not plan content. It needs a file, a schema, and
  a validation point.
- **The `executor` vocabulary.** What names exist, and how a name resolves to
  the code that renders and submits a job.
- **The `job_name` derivation.** It must be collision-free across an
  accumulating file, fit 63 characters, and stay disjoint from the retired `c-`
  namespace.

## What is deliberately absent

No results, metrics, or verdicts. Those live in the evidence objects and are
recomputed by `verify` and `report` on demand — a cached verdict is a second
answer to a settled question, and the two can disagree.

Back the state file up. Losing it does not destroy the evidence, but it costs
the binding: `report` refuses results it cannot tie back to a recorded row.
