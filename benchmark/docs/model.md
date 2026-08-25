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

`report` considers only `measurement` rows for comparative timing and rates.
Replay canaries and diagnostics remain outside every comparison. Routine report
binds their `result.json` summaries and does not consume their retained products.

**A preparation is measured even though it is not compared.** If s3-fast-list
needs 40 seconds of `ks-tool` to list in 60, then publishing 60 against another
tool's 100 states something false about the hinted path. The preparation's
duration is recorded like any other attempt's, so the total cost of a path that
requires one is recoverable — and a report showing the listing timing alone says
which cases had a preparation behind them.

### An attempt may run one untimed exec before the timed one

Not every produced thing is worth an attempt. A capsule may declare that one of
its modes runs another of its modes `inline` — untimed, in the same container,
immediately before the subject — and s3-fast-list's hinted path does: the cut
points are made from the staged key distribution in the attempt that lists under
them ([`capsule-contract.md`](capsule-contract.md) § *A setup exec is not a chain
link*).

That gives an attempt two phases and still one clock. The setup exec's argv,
exit code, duration and output digest are recorded in `result.json` under
`setup`, beside the timing and never inside it, and its captures and sink land in
the attempt's own `inline/` directory rather than the native sink a row count is
read from. It has no identity of its own: the axes it ran at are already in the
measurement's config blob. A setup exec that fails is an attempt that fails,
because the alternative is a subject timed against hints nobody made.

### What an attempt publishes

```text
gs://<results-bucket>/<suite>/<target-bucket>/<tool>.<hash>.s<attempt>/
  result.json
  stderr.log.gz
  stdout.log.gz        -- only when stdout is a log
  native/listing.txt   -- the product, named for what is in it
```

**The product is a file the mode declares, and stdout is a log**
([`capsule-contract.md`](capsule-contract.md) § *The product travels on a
declared file*). It lands in the sink under its declared name, so
`native_manifest` binds it by digest along with everything else the subject
published, and `result.json` points at it:

```json
"product": {
  "artifact": "listing",
  "name": "native/listing.parquet",
  "channel": "file",
  "size_bytes": 137194003,
  "sha256": "…"
},
"product_error": null,
"stdout": {"name": "stdout.log.gz", "size_bytes": 402, "sha256": "…"},
"stderr": {"name": "stderr.log.gz", "size_bytes": 88, "sha256": "…"}
```

Every block describes the file it names — its size as uploaded and its digest —
which the three flat `stdout_gz` / `stdout_size` / `stdout_gz_sha256` keys it
replaces did not: one of them was the *uncompressed* size of a file the other two
described compressed.

Four things this shape is saying:

- **`channel` says which class of subject ran**, so no reader has to infer it.
  `stdout` means the worker landed fd 1 in the product file, and then `"stdout"`
  is `null` — there is no second capture, because those bytes are the product.
- **`sha256` is null for a `dataset`**, which is a directory of parts with no one
  digest. `native_manifest` binds every part of it, so nothing is unbound.
- **`product_error` is how a clean run says it published nothing.** A subject
  that exits zero and writes nothing where its mode says it writes has no
  measurement in it; the attempt settles `EXIT_ARTIFACT_UNUSABLE`.
- **`product` itself is null when nothing landed at the declared path.** A
  subject killed before it opened its output file published no product, and
  `report` reads a block with a null digest as a marker it cannot trust — which
  would blank the exit code, the wall clock and the RSS peak of an attempt whose
  whole content is *this tool ran out of memory*. A failure stays a failure.
  Whatever debris the sink does hold is still bound by `native_manifest`.

A preparation carries `"product": null`: what it publishes is an artifact for a
later case, not a measured product. That follows the **attempt's** purpose, not
the mode's ceiling — the bootstrap `list` a hinted-only plan mints runs a
measuring mode as a preparation, and nothing reads its listing. It is not row
counted either, for the same reason: a preparation is in no comparison.

**A text product uploads gzipped; a Parquet one uploads as the subject wrote
it.** The `product` a mode already declares says which class of bytes these are,
and that is the whole rule: a listing of five million keys is the most
compressible thing this study makes, and gzip over columnar Parquet spends CPU
to produce a slightly larger file — which this harness did once, under the name
`stdout.log.gz`. A mode may state `product_compress` outright when the rule is
wrong for its bytes.

Two things the rule does not get to touch. **A product something downstream
consumes is published raw**, whatever its class: the chain binds the file the
sink holds by digest and hands the consumer that file, so the loader turns
compression off for a mode whose product is its chain artifact, and refuses a
capsule that declares both. And **the block still describes the file that was
uploaded** — `native/listing.txt.gz`, its compressed size, its digest — because
naming a published artifact for what it actually is is the point the earlier
arrangement missed.

### A memory figure is a figure of one invocation, above a floor

`max_rss_kb` is the subject's own `ru_maxrss`, reaped per invocation with
`wait4` so that with two phases in one container the setup exec's peak is never
published as the measurement's. What that does not buy is a number that starts
at zero. A forked child inherits the parent's `mm->hiwater_rss`, so every figure
sits on a floor equal to the fattest this worker has ever been — measured: a
worker that touched 300 MB and freed it makes `python -c pass` report 318 MB.

Two things answer it, and neither is a correction to the figure. The worker
drops its own high-water mark to its live footprint immediately before the fork,
which is the smallest floor a fork can carry; and it records what remains as
`max_rss_floor_kb` beside the measurement, with `max_rss_floor_reset` saying
whether the kernel took the write. `report` renders the floor as its own column
for the same reason the cgroup peak records `memory_peak_reset`: the reset can
fail where procfs refuses the write, and a reader cannot otherwise tell a lean
subject from one that genuinely used the worker's footprint.

So a subject whose figure is near its floor has measured nothing about itself,
and the floor is what says so. Read it as the level the figure starts from
rather than an exact bound it must clear — the floor is sampled just before the
fork, and a child that re-execs can land marginally under it. Neither number is
subtracted from the other: `max_rss_kb` stays what the kernel reported for that
invocation.

### Sometimes the failures are the measurement

The vocabulary above assumes a failed attempt is a hole: `FAILED` is an owed
retry, `ACCEPTED` is an absent measurement, and `verify` reads either as a
missing subject. For one class of case that is exactly backwards.

s3kor's listing path spawns its printer goroutine before binding the channel it
reads, so a run either succeeds, hangs, or panics. The useful result is not one
timing — it is **a rate over twenty attempts of one identical case**, and the
hangs are the finding. The same inversion applies to a diagnostic that
successfully reproduces its own failure: s4cmd exiting 124 at a 300-second
deadline is the intended outcome, not a hole to be retried.

That declaration has a home: the `statistic` column, `timing` or `rate`,
stated per row in a plan and carried onto every attempt the row books. So a
case may declare that its statistic is a **rate**, and for those:

- failed attempts are data points, not omissions — `verify` counts them rather
  than reporting the group incomplete;
- `retry` leaves them alone, because a retry would be resampling;
- `report` renders the rate and the sample size, never a mean duration over the
  survivors, which would be a survivorship result dressed as a timing.

`reps` already allocates the repeats. What is new is only that completeness and
retry stop treating a settled failure as something owed.

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

A replay result also carries the complete canonical `replay` document and a
`replay_evidence` block. Readiness and the first server-metrics scrape complete
before `started_at`; the last scrape follows `finished_at`. Long runs retain raw
10-second meter snapshots plus host-observed utilization over the declared,
disjoint server and subject cpusets, available host memory, and load. Cpuset
utilization is deliberately not called process CPU: host work scheduled on
those CPUs is inside the observation. Missing or malformed replay evidence is
a refusal, not a timing with an assumed healthy backend.

Verification requires the replay server's untagged request counter to increase
across the subject interval and its error counter not to increase. A calibrated
measurement also requires at least one interval metrics sample and one matching
cpuset-resource sample; a short uncalibrated diagnostic may have neither, but
must still prove that its requests reached the server.

The canonical replay document includes a simple capacity status. `uncalibrated`
permits diagnostics but refuses replay measurements; it becomes `calibrated`
only with a receipt-backed canary. The declared allocation is its execution
contract. Host CPU remainder and memory headroom are derived from the box and
container ceilings, not independently authored. A one-time provider canary may
inspect effective limits, but recurring attempt evidence does not attest them
and verification does not manufacture a second allocation protocol.

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

    replay              TEXT,               -- canonical resolved replay JSON; null for S3

    -- configuration: forwarded to the capsule, opaque here
    config              TEXT NOT NULL,      -- canonical JSON; holds mode and every tool knob
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
    executor_env        TEXT NOT NULL,      -- canonical JSON: project, provisioning, boot-disk type/size, network
    service_account     TEXT NOT NULL,      -- what auth_role resolved to
    secret_resource     TEXT,               -- the credential version, when a role was used
    job_name            TEXT NOT NULL UNIQUE,
    result_prefix       TEXT NOT NULL,

    -- the request and its outcome
    request_json        TEXT NOT NULL,      -- frozen provider request; a retry is diffed against it
    purpose             TEXT NOT NULL
        CHECK (purpose IN ('measurement', 'preparation', 'canary', 'diagnostic')),
    statistic           TEXT NOT NULL CHECK (statistic IN ('timing', 'rate')),
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

Schema 3 adds `replay`. Schema-2 attempts remain readable through a read-only
projection where it is null; neither schema 1 nor 2 is writable by the current
controller. The canonical document is identity and rendering input, not a
metric or verdict. A delayed slot carries the same document inside its resolved
case, so it never reloads the plan.

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
        CHECK (purpose IN ('measurement', 'preparation', 'canary', 'diagnostic')),
    known_inputs  TEXT NOT NULL,      -- canonical JSON: every input resolved so far
    producer      TEXT,               -- canonical JSON: the shape an acceptable producer has
    awaiting      TEXT,               -- (group_id, slot) of an earlier slot, or what it became
    disqualified  TEXT,               -- canonical JSON: candidate attempt_id -> why it cannot pay
    state         TEXT NOT NULL CHECK (state IN ('BLOCKED', 'RESOLVED', 'ABANDONED')),
    became        TEXT,               -- the attempt_id it minted: claimed, then paid
    recorded_at   TEXT NOT NULL,
    settled_at    TEXT,

    CHECK ((producer IS NULL) <> (awaiting IS NULL)),
    PRIMARY KEY (group_id, slot)
);
```

A **slot** is a measurement a launch intended and cannot yet identify, because
one of its inputs is an artifact nothing has produced yet. It cannot be an
`attempts` row: that table is keyed by identity and this case has none yet.

`ABANDONED` is what a slot becomes when nothing can produce that artifact any
more and the failure is accepted — the same declaration `ACCEPTED` makes about
an attempt, applied to a measurement that never got to exist. An absent
measurement, recorded as absent.

**A slot may wait on a slot**, and `purpose` therefore includes `preparation`,
because a declared chain can be more than one link, and a middle link cannot be
identified until the one before it settles either. What makes this safe is not
the depth but the *declaration* — the chain is stated in the capsule and expanded
offline, so the whole shape is knowable before anything is submitted. A slot
waiting on something discovered at run time would be a workflow; a slot waiting
on something a capsule declared is a bounded expansion.

### What a slot waits for is a shape, not a name

A slot names its producer by **spec** — the tuple that says what an acceptable
producer is:

```
{tool, mode, config, target_bucket, target_prefix, target_region,
 tool_slice_sha256, platform_sha256, replay}
```

Machine, vCPUs, memory, timeout, auth role and purpose are excluded — the
exclusions [`identity.md`](identity.md) § *Two identities, two questions* already
makes for a preparation, because the bytes do not depend on them. `replay` is
null for S3 and the complete resolved document when the producer talks to replay;
those bytes can depend on the frozen fixture even though they do not depend on
the machine executing the producer. Every key is a column of `attempts`, so the
document a slot stored is exactly what the satisfaction query compares.

Naming one attempt id instead does not survive a retry: the replacement settles
under a new ordinal, nothing rewrites `awaiting`, and the slot blocks forever
under a docstring promising the opposite. By shape, a retry of the producer
satisfies the same slot — and so does a plan's own `list` measurement, which is
why a plan carrying both a `list` row and a `list-hinted` row lists the bucket
once instead of twice with byte-identical argv.

**The spec is written at booking**, not derived when a poll pass rechecks it: a
capsule edited between launch and poll would otherwise silently change what
satisfies the slot, which is the frozen intent `known_inputs` exists to hold.

**The match carries the slot's own `group_id`, and that is load-bearing.**
Unscoped, the spec matches attempts from any launch in the file, and a second
campaign silently binds the first one's hours-old bytes — the decision
`--reuse-preparations` exists to force an operator to make. Group scoping costs
nothing, because every legitimate candidate is journaled in the slot's own group
at launch time.

Candidates are ordered by `(settled_at, attempt_id)` and the earliest settled
wins. The *ordering* is monotone-stable: `settled_at` only appends later values,
so nothing settling afterwards moves ahead of a candidate that already has, and
sibling slots resolved on different poll passes bind one producer — one corpus
snapshot across the sweep. The `attempt_id` tiebreak makes it a total order,
because millisecond ties are real.

**The winner, though, is contingent, and the sweep can straddle two producers.**
What binds is the earliest candidate the harness can *accept on that pass*, and
acceptance is not a pure function of the producer's evidence: a `result.json`
that will not download disqualifies its candidate for that pass, and every pass
recomputes the verdict on purpose, in case what was unreadable was the bucket
rather than the artifact. So if candidate X settled first but is transiently
unreadable while four of six sibling slots resolve, those four bind Y — and the
remaining two bind X on the next pass, once X reads. Nothing pins the winner;
the shared snapshot is the ordinary outcome rather than an invariant, and
`produced_by` on each consumer's row is what says which producer it actually
got.

`awaiting` is left for a **mid-chain** link, which cannot be described by shape:
its own `input_artifact_sha256` is not knowable at booking. It names the earlier
slot, and is rewritten to the attempt that slot became.

So *"a retry pays its slot"* holds for a **root** producer, which is where the
orphan was and where every shipped chain ends. It does not hold mid-chain: that
rewrite names one ordinal, and a retry of the attempt a middle slot resolved into
settles under another. Deliberately, because the spec is root-only for the reason
above — no shipped capsule declares a chain two links deep, and the day one does,
this is the paragraph to come back to.

### A claim commits with the attempt it names

Resolving a slot mints an identity and submits a job, and a second pass over the
same settled producer must not submit that measurement twice. So the slot is
**claimed**: `became` is written while the slot is still `BLOCKED`, and a pass
that finds a claim already there journals nothing.

The claim is written *inside the transaction that journals the attempt it names*,
which is the whole of why it is safe. The two commit together or neither does, so
a `BLOCKED` slot carrying a `became` always names a row the ledger holds, and
the next pass **finishes** that claim — marks the slot `RESOLVED` from the row —
rather than re-running it. A provider call that never landed leaves a
`SUBMITTING` row, which is what `poll` exists to chase; a pass that dies between
journaling and resolving leaves a claim the next pass pays.

A claim written *before* the row, by contrast, is a claim nothing can redeem: the
pass that failed after claiming is gone, the next pass sees `became` set and
declines, and nothing anywhere reports a problem, because the producer is fine
and the candidate is fine. That is a slot wedged permanently and silently — the
same shape as the retry orphan above, and refused the same way: by making the
database hold the invariant instead of the convention.

### A slot nothing can pay says so

A candidate may succeed and still publish nothing the chain can use. Two rules
collide there: a measurement's timing number is honest whatever its sink holds,
so it must not be flipped to `FAILED` to express an artifact complaint — and
"every candidate failed or was accepted" never fires while the candidate is
`SUCCEEDED`.

So an unusable artifact **disqualifies the candidate without touching its
state**, and the reason is recorded against the slot in `disqualified`. A
preparation is the one exception: publishing the artifact is the whole of what it
was for, so its refusal is its own and `FAILED` keeps it retryable.

When no candidate in the group is live, payable by retry, or usable, the slot is
**owed** — reported loudly by `status` and `report` rather than blocking quietly,
which is the "evidence that looks fine and is not" failure a slot exists to
prevent. That same predicate is what `accept-failure` cascades on: exhaustion is
evaluated per slot, not off the one attempt id someone accepted.

Going loud is only half of it, because abandoning an owed slot is a **deliberate
step** an operator takes, never one a neighbouring decision takes for them.
`accept-failure --attempt` therefore abandons only the slots that attempt was a
candidate for, plus the chain behind them; a slot owed by an unrelated shape in
the same group stays owed. And when the disqualified candidate is `SUCCEEDED`
there is no failure to accept at all, so `accept-failure --slot <group>/<n>`
names the slot itself — refused unless the slot is `BLOCKED` and nothing can pay
it. See [`running.md`](running.md) § *`accept-failure`*.

### Where a chain refuses

Four questions a chain could answer by guessing, and does not. Each was a real
silent failure before it was a refusal.

| Refused | When | What it prevents |
| --- | --- | --- |
| A `REQUIRES` entry naming a bare mode, an artifact the producing mode does not declare, a producing mode declaring none, or two consumers wanting different artifacts of one mode | load | *"the manifest holds one file, take it"* — which stages a 131 MB listing where a 679 KB hints file belongs the day a mode publishes two, under a digest that checks out |
| A measured mode naming no `product_artifact`, a `preparation`-capped one naming a product, or a `product_channel` the worker does not know | load | A product whose location is inferred from whatever the sink happens to hold, which routed every `s3-fast-list` listing into a normalizer that refuses it |
| A subject that exits clean and wrote nothing at the declared path | worker | A timing published over a listing nobody made |
| A producer spec whose keys are not the ones this code compares; a candidate outside the slot's own group; a candidate whose evidence does not hold the named artifact, or holds one a validator refuses | poll | A slot paid by bytes nothing checked — including another launch's hours-old bytes, which is the decision `--reuse-preparations` exists to force |
| `accept-failure --slot` on a slot that is not `BLOCKED` or that something could still pay | operator | Declaring a measurement absent while it is merely slow |

Two of those are worth stating as positives, because they are what the shape
buys. A **disqualified candidate never changes state**: a measurement's timing is
honest whatever its sink holds, so the complaint lands on the slot. And
**producer steps are expanded ahead of the slots that consume them**, whatever
order the plan lists its rows in, so a launch that dies mid-expansion cannot
leave a slot with no candidate in its group at all.

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

### What was asked for, and what happened

`config` records what a case *asked for*. For several subjects that is a ceiling
rather than a setting: swath's `--concurrency` is an AIMD limit starting at
`min(4, N)`, s5cmd's effective width is `min(numworkers, shards)`, and
s3-fast-list's is `min(c, N+1)`. Writing the nominal number into a hashed blob
and calling it *the* concurrency would be the same lie the axis states exist to
prevent, one column over.

So the **achieved** value is a fact about the run and belongs in evidence beside
the timing, never in `config`. A capsule declaring a `Ceiling` axis is saying
the two can differ; a report quoting a concurrency without saying which one it
means is quoting nothing.

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
- A mode's **product and emitted fields are not stored.** They are a pure
  function of `(tool, mode)` at the capsule revision the row already pins
  through `tool_slice_sha256`, so `report` resolves them from the capsule the
  way it already resolves `normalize.py`. Storing them would be a second answer
  to a settled question, and the two could disagree.
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

**`job_name` is `<suite>-<tool>-<hash12>-s<attempt>`**, lowercased with dots
turned to hyphens, derived from the identity rather than typed — two rows
cannot claim one job because they cannot derive the same name. A name that
would not fit or would not satisfy Batch's character rule is refused rather
than truncated, since a truncated name is a name two attempts could collide
on.

**`group_id` is `gYYYYMMDD-HHMMSS`**, minted locally at submit time — no round
trip to the provider, so it is ready before the first job is created — or the
operator's own name, when `submit --group` states one. Either way it must be
unique within the file; two launches minted in the same second are
suffixed rather than merged, because a group is what was launched together.

## The state column

Two vocabularies share it: the controller writes its own relationship with the
provider, and polling writes the provider's lifecycle states through as it sees
them.

| State | Written by | Terminal | Retryable | Meaning |
| --- | --- | --- | --- | --- |
| `SUBMITTING` | intent journaling | no | no | Intent is durable; the provider has not been called. A row left here means the process died in that window. |
| `SUBMITTED` | submit | no | no | Created. Covers a job this run created and one of that name it found already matching the recorded request — the distinction changes nothing anyone does. |
| *(provider states)* | poll | no | no | `QUEUED`, `SCHEDULED`, `RUNNING`, and the rest of the provider's lifecycle. |
| `SUCCEEDED` | poll | yes | no | The job ran cleanly. Not a verdict about the listing — that is `verify`'s question. |
| `FAILED` | poll | yes | **yes** | Settled failure. |
| `NOT_CREATED` | submit | yes | **yes** | The provider refused creation, or a job of that name exists and does *not* match recorded intent. Either way nothing of ours ran. |
| `CANCELLED` | cancel, or the provider | yes | no | One-way. |
| `ACCEPTED` | accept-failure | yes | no | You declared a failure final. An absent measurement, never a passing one; `state_detail` says which failure it was. |

**The vocabulary is deliberately small.** An earlier draft separated `ADOPTED`
from `SUBMITTED`, `COLLISION` from `NOT_CREATED`, and three `ACCEPTED_*`
variants — eleven states guarding create races that a study launching tens of
hand-written cases does not have. Every one of those distinctions is recoverable
from `state_detail` and `request_json`, and none of them changed what an
operator or a report would do. A state earns its place by changing a decision.

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
| `verify` | One group. Explicit real-S3 content comparison; replay is refused without staging raw products. |
| `prune` | Deletes evidence objects for attempts that settled unsuccessfully, leaving every row. Requires `--group`, for the same reason `cancel` does: an unscoped delete over an accumulating file is the one mistake with no undo. |

### What verify binds against

**The group, through the recorded rows — not a re-resolved plan.**

`case_id` folds in the tool and platform slices and the machine type, so
reproducing one from a plan would mean re-resolving that plan *and* holding the
exact image set the launch used.
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

## What is deliberately absent

No results, metrics, or verdicts. Those live in the evidence objects. Routine
reporting reads bound `result.json` summaries; explicit real-S3 verification may
derive a separate content verdict.

Back the ledger up. Losing it does not destroy the evidence, but it costs the
binding: `report` refuses results it cannot tie back to a recorded row.
