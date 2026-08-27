# Running a campaign

The operator's runbook: what to have ready, how to submit, how to watch, what
to do when a job settles badly, and how to turn finished attempts into a report.

For *what a campaign is* — and why a plan carries no campaign ID, image digest,
or date — read [`../plans/README.md`](../plans/README.md) first. For what a case
row is and how to add one, the same file's *"A layer and a row"* and *"Cases are
an ordered union"* sections are authoritative. This page does not restate either.

## Status of this procedure: `VERIFIED: no`

The committed [`replay-canary-current-20260826`](../../receipts/replay-canary-current-20260826/)
receipt exercises the bounded replay path through submit, poll/status, report,
and receipt export for three representative capsule shapes. It is not a
benchmark, content verification, recovery exercise, staged-fixture test, or
qualification of the remaining eight tools, so the whole procedure remains
`VERIFIED: no`.

Each section carries its own marker. Promote a marker to `VERIFIED: yes` only in
the commit where a real run exercised that path, and say in the message which
group did it. Do not promote a step because a neighbouring step worked.

| Step | Exercised against real Batch? |
| --- | --- |
| Toolbox build + eleven-executable help smoke | **yes** — the `benchmark-toolbox` workflow, local Docker |
| Serial local real-S3 canary | no — runner and plan exist; no committed current-worker receipt yet |
| `submit` | **yes** — historical bounded three-tool bundled-fixture replay canary; current staged-fixture plan no |
| `poll` / `status` | **yes** — same canary |
| `retry` / `cancel` / `accept-failure` | no |
| `verify` | no — replay is deliberately outside its content-comparison path |
| `report` | **yes** — same canary, including bound replay evidence and row counts |
| receipt export | **yes** — same canary |

## Before you submit

`VERIFIED: no`

The submit command assumes all of this already exists. It is not a checklist the
tool enforces for you; a missing item surfaces as a provider error mid-campaign.

1. **Infrastructure applied.** Project, region, network/subnetwork, results
   bucket, and both worker service accounts. See
   [`../../infra/terraform/modules/gcp/s3-listing-study/README.md`](../../infra/terraform/modules/gcp/s3-listing-study/README.md).
   Both worker identities hold bucket-level `roles/storage.objectAdmin`: either
   can list, read, create, overwrite, or delete fixtures, campaign results, and
   receipts. Their bucket access is identical; only the authenticated worker can
   read the AWS credential secret.
2. **Toolbox built and smoked** at the exact revision you intend to run:

   ```sh
   uv run python benchmark/src/benchmark/build_image.py \
     --harness-revision "$(git rev-parse HEAD)" \
     --tag benchmark-toolbox:local
   ```

   The build refuses a dirty checkout and refuses a revision that is not `HEAD`.
   Commit first; there is no `--force`.
3. **Image published and pinned.** Push through an explicitly authorized registry
   operation, then record the *immutable* `@sha256:` URI. A tag is rejected.
4. **Image-set JSON emitted** by the same build tool, at the same revision, once
   the pushed digest is known:

   ```sh
   uv run python benchmark/src/benchmark/build_image.py \
     --harness-revision "$(git rev-parse HEAD)" \
     --image-set /secure/images.json \
     --image-uri "us-docker.pkg.dev/PROJECT/REPO/toolbox@sha256:DIGEST"
   ```

   It is emitted rather than written by hand: the set is the image's own
   metadata projected to the per-tool identity fields the controller accepts, and
   the emitted document is validated by the loader that will read it, so a set
   the campaign would refuse fails here instead. The revision must be clean and
   `HEAD`, exactly as for the build. Shape is in
   [`../README.md`](../README.md) § *Campaign image set*.
5. **Replay fixture identity pinned.** The replay server image and fixture digest
   in the plan bind what is served. A staged `fixture_uri` also requires that
   digest; staging recomputes it before the server starts. No correctness
   manifest is generated or bound. The worker counts rows inside the container
   after timing, uploads raw products for manual investigation, computes its
   final completion code, and publishes `result.json` last. Keep
   `replay.capacity_status: uncalibrated` until a real diagnostic capacity
   canary has a committed receipt. The staged-fixture provider path separately
   remains `VERIFIED: no` until a committed canary uses `fixture_uri`; a bundled
   fixture canary does not qualify that download and manifest-check branch. Before
   hashing or uploading a fixture, apply the separate
   [fixture-preparation and sorted-eligibility rule](../plans/README.md#fixture-preparation-and-sorted-eligibility):
   current Swath `--sort` output is already prepared, while ordered-but-unstamped
   Parquet is not.
6. **Credential secret**, if any case signs: one
   `projects/<p>/secrets/<s>/versions/<v>` resource whose payload is the
   `KEY=VALUE` lines described in
   [`../../infra/terraform/modules/gcp/s3-listing-study/aws-credentials.tf`](../../infra/terraform/modules/gcp/s3-listing-study/aws-credentials.tf).
   Only a signing case's job carries it, and only the authenticated worker
   identity can read it.
7. **A `--suite` name.** It is the results-bucket path prefix, the job label a
   polling pass filters on, and the job-name prefix — constant for the life of
   a ledger, so it is chosen once, not per launch. `--group` is optional:
   leave it unset and `submit` mints `gYYYYMMDD-HHMMSS`, or name one yourself
   when you want a handle you chose rather than one you'll have to look up.

Keep `campaign.db` — it is authoritative controller state, not a cache, and it
is not interchangeable with the evidence in GCS. Back it up. What is inside it —
the tables, their keys, and every state they record — is in
[`model.md`](model.md).

## Local Docker real-S3 canary

`VERIFIED: no`

The local executor is an operational successor candidate for the retired smoke
engine, not a rewrite of its receipts. Historical receipts remain immutable.
Promotion requires a current-worker canary whose every attempt has bound local
evidence and whose group passes explicit content verification where the emitted
strata permit it.

Build the toolbox from a clean commit, then freeze and inspect the exact order:

```sh
uv run python benchmark/src/benchmark/local_campaign.py run \
  --plan benchmark/plans/local/s3-canary/noaa-nws-rtofs-pds.yaml \
  --image benchmark-toolbox:local \
  --results-root /absolute/path/to/results \
  --suite local-rtofs-canary --group rtofs-s3-canary \
  --location ACTUAL-HOST-LOCATION --seed 982451653 \
  --allow-retired-s4cmd-s3-canary --dry-run
```

For the real run, export the one credential payload required by the four
signed-only capsules, then remove `--dry-run`:

```sh
export S3_STUDY_AWS_CREDENTIAL="$(gcloud secrets versions access VERSION \
  --secret SECRET --project PROJECT)"
```

The credential value never appears in Docker argv or the local ledger. Unset it
after the campaign. The runner executes serially, pins whole physical-core
sibling groups, caps memory and swap together, and writes these durable local
records:

- `campaign.db` — intent, exact Docker request, state, and evidence binding;
- `schedules/<group>.json` — seed, plan/image/host identities, resolved order,
  and request digests;
- `<suite>/<bucket>/<attempt_id>/` — create-only worker evidence, with
  `result.json` published last;
- `logs/<group>/` — Docker/worker controller streams, outside attempt evidence.

Run the existing readers directly over the local ledger and paths:

```sh
uv run python benchmark/src/benchmark/report.py \
  --state /absolute/path/to/results/campaign.db --group rtofs-s3-canary
uv run python benchmark/src/benchmark/verify.py \
  --state /absolute/path/to/results/campaign.db --group rtofs-s3-canary
```

The RTOFS bucket is mutable. One pass is factual canary evidence only: do not
rank durations or call disagreement a tool defect without first distinguishing
corpus drift. Local replay orchestration remains an explicit refusal in this
revision; implementing it must preserve this same worker, ledger, seeded order,
and local evidence boundary.

## Submit

`VERIFIED: yes` — `replay-canary-current-20260826`, for the historical
bundled-fixture replay canary with three independent case rows. The current
staged-fixture plan, real-S3, and dependency-slot submission remain unverified.

```sh
uv run python benchmark/src/benchmark/campaign.py submit \
  --suite s3-listing-study \
  --plan benchmark/plans/buckets/noaa-ghcn-pds.yaml \
  --project my-project --location us-central1 \
  --results-bucket my-results --image-set /secure/images.json \
  --anonymous-worker-sa anonymous-worker@my-project.iam.gserviceaccount.com \
  --authenticated-worker-sa auth-worker@my-project.iam.gserviceaccount.com \
  --secret-resource projects/varve-oss/secrets/s3-listing-study-aws-credentials/versions/latest
```

`--group` names the launch instead of minting a timestamp. `--dry-run` renders
every attempt and slot the plan expands to and journals nothing at the
provider — use it first:

```sh
uv run python benchmark/src/benchmark/campaign.py submit \
  --suite s3-listing-study --plan benchmark/plans/buckets/noaa-ghcn-pds.yaml \
  --project my-project --location us-central1 \
  --results-bucket my-results --image-set /secure/images.json \
  --anonymous-worker-sa anonymous-worker@my-project.iam.gserviceaccount.com \
  --dry-run
```

prints one line per resolvable attempt (`attempt_id`, `job_name`, the frozen
Batch request), one line per slot the plan cannot yet identify (`slot
<group>/<n> <tool> <mode> <purpose> awaiting step <m> (<tool> <mode>)`), and a
closing count — "N plan row(s) expand to M attempt(s) and K slot(s)" — so a reviewer sees
the true shape of a launch before anything is created. **Read the slot lines**:
they name the step that will pay each slot, which is where a plan that states a
knob the producing mode ignores shows up as an extra bootstrap listing rather
than as a silent duplicate hours later.

To submit only an exact resolved row from the same plan, copy its `case` value
from `resolve-plan --json` and pair it with the tool:

```sh
uv run python benchmark/src/benchmark/campaign.py submit \
  ... \
  --case 'swath:recursive-parquet.purpose-diagnostic.concurrency-64...'
```

Repeat `--case TOOL:LABEL` to select more than one row. Selection happens only
after the complete plan has loaded and validated; it is an operational filter,
not a way to hide an invalid row or create a second plan file. Always dry-run
the filtered submission before creating jobs.

Two flags change what a repeated `submit` does with what it finds:

- **`--repeat`** submits a case that already has a successful attempt.
  Without it, `submit` refuses: re-measuring is `reps` in the plan, or this
  flag, never a silent extra run.
- **`--reuse-preparations`** binds an artifact an *earlier launch* already
  produced instead of refusing. Reuse within one launch is always free — every
  consumer of one preparation step shares it — but reuse across launches is a
  decision, because the digest cannot tell you the corpus moved
  ([`identity.md`](identity.md) § *What identity cannot cover*). Cross-launch
  reuse also requires the same `campaign.db`: the ledger is what discovers and
  binds the earlier successful producer; this flag does not search GCS or import
  evidence from another ledger. Keep using the campaign's SQLite file and pass
  the flag when reuse is intended. The producer case must still be identical —
  changing its fixture, replay image or latency treatment mints a different
  producer even when an operator expects the resulting artifact bytes to match.
- **`--skip-measured`** binds an existing SUCCEEDED attempt of a case *from any
  group*, instead of refusing the whole launch. This is what makes one
  checked-in plan file the single source of truth across many sessions: add a
  case to the file and resubmit it whole, and only the new rows book or
  submit — everything already measured is skipped, not re-run. Refused
  together with `--repeat`: one forces a new attempt, the other avoids one.

Defaults worth knowing: `--provisioning` is `SPOT`, so preemption is expected
rather than exceptional. `--network`/`--subnetwork` must be supplied together.

**`--stagger-seconds`** sleeps that long before each submission after the
launch's first. A plan with several fan-out subjects — several
64-to-256-way parallel listers among them — otherwise converges all of them on
one bucket within the same few seconds of wall clock, which is enough
aggregate request rate to draw an S3 SlowDown even from a bucket no single
tool would trouble alone. Submission-side only: it cannot control Batch's own
queueing or Spot provisioning delay, only the one variable this harness does
control. Zero by default; use it for any bucket where a first pass shows self-
induced throttling.

Staggering is an operational guard, not the isolation rule for the real-S3
validation stage. Batch controls VM start time, so spaced submissions can still
overlap. Finalist validation runs one subject at a time: wait until the current
subject has stopped driving the bucket before submitting the next validation
case. A wide real-S3 launch is diagnostic only and cannot produce the study's
comparative S3 result.

The controller **records intent before it creates a job** — a row is journaled
`SUBMITTING` inside the same transaction that allocates its ordinal, before the
provider is ever called. If the process dies between those two steps, the
ledger knows about a submission the provider may or may not have; `poll` and
`retry` are written to resolve that, which is why they consult recorded intent
rather than trusting the provider's view. `submit` also prints
`campaign: group <id>` on the way out — the handle `retry`, `cancel`, `status`,
`verify` and `prune` all take as their scope.

## Watch it

`VERIFIED: yes` — `replay-canary-current-20260826`, through `poll --watch` and
the final read-only `status` view.

```sh
# One pass, updates the ledger and exits.
uv run python benchmark/src/benchmark/campaign.py poll

# Block until every submission is terminal (default 30s between passes).
uv run python benchmark/src/benchmark/campaign.py poll --watch --interval 30

# Read the ledger only. No provider calls, no credentials needed.
uv run python benchmark/src/benchmark/campaign.py status --group g20260817-120000
```

`poll` takes no `--project`/`--location` — every non-terminal row already
carries its own `executor_env` and `location`, so a poll pass reads those from
the ledger rather than being told them again. One listing filtered by
`labels.suite=<suite>` covers every group in flight, so parallel launches need
no extra machinery. Every slot the group still owes is rechecked in the same pass
that noticed an attempt settle — the question is "does an attempt satisfying this
slot exist now", so a retry of a failed producer pays the slot its predecessor
left owed.

`status` is the safe command: it opens `campaign.db` read-only and prints one
line per attempt (`attempt_id`, `state`, `purpose`, `group_id`, `job_name`) and
one line per pending slot (`slot <group>/<n>`, its state, `purpose`, and what
it is awaiting — an earlier slot, or the shape of any producer in its group).
A slot no attempt in its group can pay any more prints `OWED, nothing can pay
it:` and the reason, because a slot blocking quietly forever is the failure it
exists to prevent. Unfiltered it prints the whole file's history; `--group` and
`--case` narrow it. A group is not understood from its attempts alone while it
still owes a slot — that is why slots print alongside attempts rather than in
a separate command.

### The states

Two vocabularies share the `state` column: the controller's own relationship
with the provider, and the provider's lifecycle passed through as seen.
Full rationale is [`model.md`](model.md) § *The state column*.

| State | Terminal | Retryable | Meaning |
| --- | --- | --- | --- |
| `SUBMITTING` | no | no | Intent is durable; the provider has not been called yet. |
| `SUBMITTED` | no | no | Created — by this run, or found already matching recorded intent. |
| *(provider states)* | no | no | `QUEUED`, `SCHEDULED`, `RUNNING`, and the rest of Batch's own lifecycle. |
| `SUCCEEDED` | yes | no | The job ran cleanly. Not a verdict about the listing — that is `verify`'s question. |
| `FAILED` | yes | **yes** | Settled failure. |
| `NOT_CREATED` | yes | **yes** | The provider refused creation, or a job of that name exists and does not match recorded intent. |
| `CANCELLED` | yes | no | Set by `cancel`, or by the provider. One-way. |
| `ACCEPTED` | yes | no | Set by `accept-failure`. An absent measurement, never a passing one. |

A describe failure during `poll` prints to stderr and leaves the row alone
rather than inventing a state — the pass simply reports "not all terminal", so
`--watch` keeps going.

## When something settles badly

`VERIFIED: no`

Three commands, three different meanings. Choosing the wrong one is how a
campaign's evidence becomes ambiguous.

### `retry` — run it again under a fresh ordinal

```sh
uv run python benchmark/src/benchmark/campaign.py retry \
  --group g20260817-120000 \
  --project my-project --location us-central1 \
  --results-bucket my-results --image-set /secure/images.json \
  --anonymous-worker-sa anonymous-worker@my-project.iam.gserviceaccount.com
```

`--group` is required — `retry` scopes to one group and skips rows from
others rather than refusing outright. It considers only each case's latest
ordinal: a latest `FAILED` or `NOT_CREATED` row is retried once, while a live,
successful, cancelled, or accepted latest row suppresses every older failure
for that case. This prevents one sweep from launching a parallel job for every
historical preemption. There is no per-attempt form. A row whose case declared
`statistic: rate` is left alone and reported as such — its
failures are the finding, so retrying one would be resampling
([`model.md`](model.md) § *Sometimes the failures are the measurement*).

Each retry re-renders the frozen request from the recorded row with only the
new attempt's identities rewritten (`--job-name`, `--destination`,
`--attempt-id`), and refuses if that re-render disagrees with what was
recorded — a plan or image edited since submission cannot silently drift a
retry onto a different measurement; that is a new campaign, not a retry. The
new attempt gets its own ordinal, its own job name, and its own result prefix;
nothing is overwritten.

### `cancel` — stop one group now

```sh
uv run python benchmark/src/benchmark/campaign.py cancel --group g20260817-120000
```

`--group` is required — an unscoped cancel over an accumulating file is
refused rather than performed. Cancels every non-terminal attempt in that
group and marks each `CANCELLED`. There is no per-attempt form and no
confirmation prompt. `CANCELLED` is terminal and **not** retryable.

### `accept-failure` — declare one measurement absent

```sh
uv run python benchmark/src/benchmark/campaign.py accept-failure --attempt aws-cli.9f300cc4d2b1.s2
uv run python benchmark/src/benchmark/campaign.py accept-failure --slot g20260817-120000/1
```

One of the two forms is required, and they answer different questions.

**`--attempt`** moves one settled `FAILED` or `NOT_CREATED` attempt to
`ACCEPTED`. It changes no cloud state whatsoever — it is a bookkeeping
declaration that you are not going to retry this one, which takes it out of
`retry`'s sweep. Any slot **this attempt was a candidate for** and which nothing
can pay any more is recorded `ABANDONED` in the same call, along with whatever
was chained behind it. Two limits on that cascade:

- A slot is exhausted only when *every* candidate of its producer's shape has
  failed, been accepted, or published nothing usable, so accepting one failure
  while a sibling attempt of the same shape is still live leaves the slot owed.
- A slot the accepted attempt was never a candidate for is **untouched**. A
  group is what went out together, not what depends on what, and abandoning an
  unrelated hinted arm because an `aws-cli` row was accepted would take a
  decision the slot went loud precisely in order to ask you for.

**`--slot`** takes `<group>/<n>` and declares that slot's measurement absent
directly. This is the form for the case `--attempt` cannot reach: a slot whose
only candidate **succeeded** and was disqualified — it published nothing the
chain could use — has no failed attempt to accept, because the producer's timing
is honest and stays `SUCCEEDED` ([`model.md`](model.md) § *A slot nothing can pay
says so*). `status` and `report` name those slots as owed. The command refuses a
slot that is not `BLOCKED`, and refuses one anything could still pay: it is for a
measurement that cannot happen, not one that is slow.

Use either when a case genuinely cannot run. Neither is a way to make a red
campaign look green: `verify` and `report` still see the accepted state, and
an accepted failure is an absent measurement, not a passing one.

## Verify

`VERIFIED: no`

```sh
uv run python benchmark/src/benchmark/verify.py --state campaign.db --group g20260817-120000
```

`verify` is a standalone CLI, not a `campaign.py` subcommand, bound to one
group at a time via `--group` (required) and the same `--state` ledger. It
reads that group from the recorded rows — never a re-resolved plan
([`model.md`](model.md) § *What verify binds against*). It
compares every `purpose = 'measurement'` attempt within a stratum — one
target bucket, one `(product, fields)`. For real S3, attempts are compared with
the other subjects in that stratum: `PASS` means agreement over a live corpus,
not independent ground truth. Replay is outside this content-verification path:
`verify` refuses it without staging raw products, while routine replay reporting
uses the row count in bound `result.json`.

A `statistic: rate` case is reported as successes over attempts and takes no
part in real-S3 cross-tool agreement.

Exit code is worst-wins across the whole group:

| Code | Verdict | Meaning |
| --- | --- | --- |
| `0` | `PASS` / `UNCOMPARED` | Every stratum agrees, or has only one subject to agree with. |
| `1` | `FAIL` | A field mismatch beyond `mtime`. |
| `2` | `DRIFT` | The only mismatches are `mtime` — deliberately still nonzero. |
| `9` | `INCOMPLETE` | The group owes a subject — a `BLOCKED` slot, or a comparison `verify` refused. |

A refusal — a missing result marker, an identity mismatch between recorded
intent and the object it was found under, a failed or timed-out subject, a
`normalize.py` error, or a `NULL` field in a normalized row — becomes a gap in
the printed report and drives the group to `INCOMPLETE` rather than being
guessed past; each gap's own reason and finer-grained code are in the printed
detail, not in the process's exit status. Pass `--no-write` to compare without
writing `verify.json` back under each compared attempt's own result prefix.

## Report

`VERIFIED: yes` — `replay-canary-current-20260826` exited 0 with three bound
results, complete replay evidence, subject and worker exit 0, and 2,048 rows
counted inside each worker.

```sh
uv run python benchmark/src/benchmark/report.py --state campaign.db --group g20260817-120000
```

Omit `--group` to report the whole ledger. Prints a Markdown summary of every
attempt's state and evidence binding, and exits nonzero if the scope still
owes a `BLOCKED` slot, any row is non-terminal, any row's state is outside
`SUCCEEDED`/`CANCELLED`/`ACCEPTED` (a settled `FAILED`/`NOT_CREATED` must be
retried or accepted first), or any `SUCCEEDED` row's evidence is not bound —
or, outside preparation and rate cases, its subject did not complete with exit
0 and a row count. A report that exits `0` is a report whose inputs agree with
the ledger and whose canary/diagnostic/timing subjects succeeded.

For a replay attempt, `report` applies the same evidence acceptance rule as the
worker: readiness, an increase in the untagged request counter, no increase in
the error counter, and interval/cpuset samples for a calibrated measurement. It
also renders both the subject exit and `worker_exit`; a clean subject whose
postprocessing or replay evidence was refused is not shown as a clean worker.

## Export a receipt draft

`VERIFIED: yes` — `replay-canary-current-20260826`; the committed draft is
[`../../receipts/replay-canary-current-20260826/`](../../receipts/replay-canary-current-20260826/).

```sh
uv run python -m benchmark.receipt \
  --state campaign.db \
  --group g20260817-120000 \
  --output receipts/g20260817-120000
```

The group must be settled and have no blocked slot. The command writes a
deterministic `receipt.json` plus a compact `README.md`. It freezes the resolved
case documents, provider requests, attempt states and locations, bound result
digests, both exit codes, timing/RSS/row counts, replay evidence summary, and any
existing verification record. It copies no listing product and makes no claim:
the output is a factual draft for review and commit, with `diagnostic`, `canary`,
`preparation`, and `measurement` labels left intact.

## Prune

`VERIFIED: no`

```sh
uv run python benchmark/src/benchmark/campaign.py prune --group g20260817-120000
```

Deletes the evidence objects of every attempt in that group that settled
*unsuccessfully* — `FAILED`, `NOT_CREATED`, `CANCELLED`, `ACCEPTED` — leaving
every row. `--group` is required, for the same reason `cancel`'s is: an
unscoped delete over a file that accumulates every group ever launched is the
one mistake with no undo. A `SUCCEEDED` attempt's evidence is never a target.

## Things that will bite

`VERIFIED: no` — inferred from the code paths, not from a real run.

- **A dirty checkout cannot build an attestable image.** Commit first.
- **A tag where a digest belongs is rejected.** Campaigns consume `@sha256:` only.
- **`poll` and `cancel` need no `--project`/`--location`** — they read each
  row's own `executor_env` and `location` — but `submit` and `retry` do, and a
  copy-pasted `--project`/`--location` aimed at the wrong estate creates a
  stray job there rather than failing closed.
- **SPOT is the default.** Preemption is a normal outcome; plan retries for it.
- **`cancel` is one group and one-way.**
- **`retry` on a `statistic: rate` case is a no-op**, by design — its failures
  are data, not an owed retry.
- **Losing `campaign.db` loses the binding**, not the evidence — but without it
  the evidence in GCS cannot be tied to intent, and `verify`/`report` will
  refuse it.
- **A ledger written before the producer-spec change is readable, not
  reportable.** Schema 1 files open read-only, so `status`, `report`, `verify`
  and `prune` can be pointed at them; nothing writes to one, and there is no
  migration. But the evidence those campaigns published is a *different shape*
  from what `report` now reads: `result.json` carried flat `stdout_gz` /
  `stdout_size` / `stdout_gz_sha256` keys where it now carries nested `stdout`
  and `product` blocks, and the marker is not versioned. So `report` on a
  pre-existing group opens the ledger, binds the rows, and then flags every one
  of them — expect `RESULT_MISMATCH`, not a clean historical report. Read those
  campaigns from the objects themselves, or re-run them.
