# Methodology

This is the study's measurement plan — what counts as a run, how comparisons
are controlled, and what gets published. It was committed *before* comparative
results existed, on purpose — see
[We wrote the plan down first](#we-wrote-the-plan-down-first).

**Document status — 2026-09-01.** This file is two things at once, and the
line between them matters when reading it:

- **Preregistration.** [Where we started](#where-we-started), [We wrote the
  plan down first](#we-wrote-the-plan-down-first), [The five decisions that
  shape everything](#the-five-decisions-that-shape-everything),
  [Run records (receipts)](#run-records-receipts) and [What this setup cannot
  tell us](#what-this-setup-cannot-tell-us) were committed *before* comparative
  results existed and are unchanged by them. They say what the study intended
  to measure and on what terms.
- **What has since run.** [Replay screening, then real-S3
  validation](#replay-screening-then-real-s3-validation) and
  [Execution order](#execution-order) describe a funnel that has now executed,
  and the dated material-change notes below record every rule change in the
  order it happened.

Comparative runs have executed and are published as a **diagnostic** release,
`2026-09-scale-diagnostics`. No attempt in it carries `purpose = measurement`:
the replay instrument overran its declared budget on one request shape, which
this document's own gate treats as disqualifying for a measurement, so the
release publishes controlled diagnostics and says so in
`manifest.json.claim_ceiling`. The plan in this file is therefore *not* yet
fully executed as written — the calibrated comparison it describes remains
future work. What ran, what it settled and what it did not is
[`../results/2026-09-scale-diagnostics/REPORT.md`](../results/2026-09-scale-diagnostics/REPORT.md).

Smoke receipts carry per-run wall-clock and RSS, but never as a comparison. All
eleven subjects ran at smoke on amd64 through the attempt engine: seven
anonymously and `s3p`, `s3kor`, `s4cmd`, and `ps3` with a scoped credential.
Those attempts establish that the retired groundwork path worked, not
correctness: none is bound to the current comparative verifier. The replay path
records in-container row counts, logs, and bound result markers. See
`../tools/README.md` for per-tool status and the current release outcome, and
`smoke-bucket.md` for the historical reference snapshot.

**Qualification status — 2026-08-26.** A committed bounded three-tool replay
canary now exercises submit, poll/status, summary reporting, worker-side row
counting, raw-product retention, and receipt export for those exact capsule
shapes. It is an uncalibrated 2,048-row integration canary, not a comparative
benchmark, content-verification result, replay-capacity qualification,
staged-fixture run, or qualification of the other eight capsules.

**Material evidence-retention change — 2026-08-28.** Native listing products
are no longer uploaded by default. The worker still counts them locally after
timing, retains stderr and any genuine stdout/setup logs, records replay and
resource evidence, and uploads `result.json` last. `campaign submit
--retain-products` opts native products back in for a group that needs content
investigation or explicit verification. Secret-pattern scanning was removed
entirely; credential delivery remains scoped to the subject environment and is
never written into result metadata.

**Protocol note.** The comparative measurement plan predates comparative
results. The groundwork procedure was improved after the aws-cli and
s3-fast-list pilots, before the wider groundwork wave; the current brief is
therefore the final procedure, not the original pre-pilot text. No comparative
benchmark had begun, so those operational fixes were not fitted to benchmark
results. Future material measurement-rule changes are dated in this document.

**Material methodology change — 2026-08-10.** The production campaign shape is
now one scheduled run per case (`reps: 1`), each on a fresh GCP Batch VM of the
same declared machine type and resources as its comparison peers. There are no
cold/warm arms. VM and container startup, worker native-row counting,
compression, upload, and manager post-processing are outside `elapsed_ns`;
`elapsed_ns` covers the subject child from launch through reap, including its
own output work. Benchmark buckets must be large enough that fixed process-launch
overhead is insignificant. This replaces the earlier one-box, cold/warm, and
repeat-to-estimate-variance rules below; the output-mode and concurrency-sweep
decisions are unchanged.

Routine attempts do not normalize or convert listing output. After the timed
subject exits successfully, the selected adapter's `count_rows` path applies
the same mode-specific row selection as its verifier normalizer but computes
only a count. The worker counts the native product locally, retains stderr and genuine
stdout logs, and uploads native products only when the group was submitted
with `--retain-products` (the 2026-08-28 retention change above). Five-field normalization is benchmark-verifier work
invoked only for explicit correctness verification.

Routine campaign reporting is summary-only. The campaign model owns a
`run-<n>` ordinal for each scheduled run (`run-1` under the current policy),
while every worker-container execution mints its own attempt UUID below that
prefix. The benchmark controller discovers only immediate UUID children with a
delimiter listing and reads their exact `result.json` summaries, which the
worker uploads last as completion markers. Raw listings remain in the same GCS
attempt trees. Replay reporting never fetches them; explicit real-S3 correctness
work or manual investigation may. Multiple current-submission UUID
children under one run are duplicate executions; reporting surfaces all and
selects none as canonical. Historical retry leaves remain visible without
competing with current-submission evidence.

**Material reporting-safety change — 2026-08-11.** Controller completion is
separate from provider settlement. A report is final and publishable only after
every deterministic Batch effect is terminal, or a definitive create rejection
proves `NOT_CREATED`; controller failure alone cannot authorize absent evidence.
Finality and operational success are reported separately, so an explicitly
accepted failure can produce a final report without being called successful.

**Material experiment-order change — 2026-08-20.** Comparative work now starts
with controlled screening and configuration search against the Swath replay
server. The broad real-S3-first launch was not an isolating experiment: several
high-fan-out subjects can converge on the same live key ranges and impose shared
throttling on one another, while ambient S3 load remains outside the study's
control. Replay therefore narrows the candidate set first; a small finalist set
then runs against real S3, one subject at a time, to validate the selected
configurations and the conclusions that will be stated about S3. Replay-only
numbers remain synthetic and cannot by themselves support a claim about
real-S3 performance. This supersedes the former real-S3 Phase 1 / replay Phase
2 order; the detailed funnel and disagreement rule are recorded below.

## Evidence language

Current documents use one evidence-strength vocabulary:

| State | Meaning |
| --- | --- |
| `unverified` | Testable, but the available evidence does not settle the proposition. |
| `supported` | Public source, documentation, or a clearly bounded observation supports the proposition; this is not a claim that the behavior was reproduced by the study. |
| `confirmed` | The exact run or build was reproduced and is backed by a committed receipt. |
| `unverifiable` | The proposition cannot be settled from the surviving public evidence or available resources; the reason is recorded. |

Editorial disposition is separate: a statement can be retained, corrected, or
contradicted without changing what kind of evidence supports the current
wording. Frozen research and repository law still use the legacy uppercase
forms: `VERIFIED: no` maps to `unverified`, `CONFIRMED` to receipt-backed
`confirmed`, and `UNVERIFIABLE` to `unverifiable`; a runtime `CORRECTED`
promotion likewise requires a receipt, while current ledgers record correction
separately from evidence strength. A reputable source, including AWS
documentation, can make a reference or mechanism claim `supported`; it cannot
make a run-dependent claim `confirmed`.

## Where we started

The study, as scoped by the project owner:

> Stop trusting our own notes. Take each tool, install it for real,
> read its docs, and read its source where the docs don't settle a claim. Then
> run each one over a set of sample buckets, in the modes it actually offers — if
> it has parallelism, try that; if it can be fed hints from a first listing, try
> that too. See how the others behave under limited memory and whether they crash
> on certain buckets. The goal is an honest comparison of Swath to the others,
> with benchmarks to follow.

Everything below is the implementation of that, plus the decisions it forces.

## We wrote the plan down first

We build one of the tools included here, so we know it better than the others.
Many small choices — which bucket, flag, metric, or run to use — can tilt a
comparison even when everyone involved is trying to be careful.

We therefore wrote down the rules in git before comparative numbers existed.
That makes it harder to reshape the plan around a result after seeing it, and
lets anyone follow later changes. The formal name for this is
"pre-registration," but the useful part here is the visible history.

What that means in practice:

- **Swath results are published on the same terms whether or not they favor it.**
  `s3-fast-list` has a published 3.1M objects/sec at c=1000. Swath's own design target is only "within
  ~10% of `s3-fast-list` at equal concurrency" — so we already expect its raw
  hinted throughput to be lower or roughly the same. We will report what we see.
- **We put comparable effort into finding a supported setup for every tool.**
  Where a tool has a concurrency knob, a fast-path flag, or a hints mode, it gets used.
  Where we're unsure of a tool's best configuration, we ask its maintainers rather
  than guessing badly in our own favour.
- **Swath's existing internal benchmark history is not used here.** It was
  produced by us, on our corpus, with our tuning. It must be run again on this
  harness before it is cited.
- **We share reproducible problems upstream.** If a tool crashes in our setup,
  we bring the run to its maintainers rather than only writing about it here.

## The five decisions that shape everything

These need settling before the first comparative run. Some are owner calls.

### 1. What counts as "a run" — the definition-of-done problem

**This choice sets what every timing number means.**

These tools do not do the same work:

- `s5cmd ls` writes formatted lines to stdout.
- `rclone lsjson` serializes JSON.
- Swath writes Parquet with a schema.
- **S3P is fundamentally a copier** whose listing is an internal phase — it may
  not even expose listing as a first-class operation.

Comparing these naively flatters whoever writes the least output. "Enumerate 100M
keys and print nothing" and "enumerate 100M keys and write typed Parquet" are
different jobs, and the second is the one people actually need.

**Proposed rule — two metrics, always reported together:**

| Metric | Definition | What it isolates |
| --- | --- | --- |
| **Enumeration time** | Every key retrieved from S3, output to `/dev/null` or the tool's cheapest sink | The listing algorithm, stripped of serialization |
| **Useful-output time** | Every key durably written in the tool's native richest format | What an operator actually experiences |

Neither alone tells the whole story. Enumeration time favors tools with simpler
output formats; useful-output time includes work that is arguably separate from
listing speed. Reporting both, side by side, every time, keeps that tradeoff
visible — and where a tool can't produce a useful output at all (no Parquet, no
durable sink), that is recorded as a capability gap rather than folded into a
time.

**Open question for the owner:** does S3P get benchmarked at all if listing isn't
separable from copying? Options: (a) exclude it from timing and evaluate it only
mechanically (algorithm, API-call count, crash behaviour), (b) time its listing
phase via instrumentation and label the number as not-like-for-like, (c) time the
full copy and report it as a different measurement entirely. **Recommendation:
(a) plus API-call counting** — S3P's interesting question is the ~50% overlap
waste, which can be measured exactly as a call count without treating the
wall-clock measurements as like-for-like.

### 1a. API-call counting — what we collect when

Settled: **if a tool exposes its own call count, collect it.** Some do — `s3ls-rs`
is claimed to keep an atomic counter and log it at end of run specifically for
cost analysis. Where a tool doesn't expose one, we do **not** block groundwork
smoke on it. The replay-screening stage supplies the external counter, after the
endpoint has passed the protocol, correctness, identity, and capacity gates
below.

### 2. The bucket sample

Supplied by the owner; not yet received. Requirements:

- **Shapes must differ deliberately**, because keyspace shape is the independent
  variable the whole field turns on: flat (no delimiter structure), deep tree,
  dense-tailed, sparse/clustered, and ideally one with non-ASCII keys (which is
  the only way to test S3P's claimed ASCII-alphabet throw).
- **Classify each bucket against the flat / deep / dense-tailed / sparse taxonomy
  above**, and publish the classification with the results, so findings generalize
  past the specific sample rather than being anecdotes about five buckets. The
  taxonomy is defined here and in the open; no external or private corpus is
  required to apply it.
- **Record shape independently** — key count, depth distribution, prefix fan-out,
  byte-range density — *before* any tool runs, so results can be explained rather
  than just ranked.

Questions outstanding: are the buckets **public** (LIST is free; runs bounded only
by time and disk) or **owner-controlled** (LIST costs money; needs a budget)? What
**region** are they in relative to the runner box? Cross-region latency can
dominate and swamp genuine algorithmic differences — if the sample is remote, that
must be stated as a limitation or controlled by running in-region. Replay
deadlines are derived per fixture from the capture's own round trip, so a
cross-region bucket's fixture carries its cross-region floor (real-changesets
in `us-west-2`: 122 ms against 85–94 ms for the east fixtures).

### 3. Keeping comparisons useful

- **Same declared resources, all tools.** Every published comparison uses fresh
  Batch VMs of the same declared machine type, vCPU count, and memory shape.
  Third-party published numbers (s3-fast-list's 3.1M/s, S3P's 35K/s, PS3's 94K/s)
  are **context, never comparison** — different hardware, different buckets,
  different years, mostly self-reported.
- **Same fixture during replay; same bucket and a tight window during S3
  validation.** Replay candidates use the same immutable fixture and latency
  treatment. Finalists run against the same live bucket close together, but one
  subject at a time so the study does not manufacture shared throttling.
- **One fresh VM and one scheduled run per case.** `reps` is 1 and there are no
  cold/warm arms. VM/container boot is outside the timer; network setup performed
  by the subject remains part of the subject's own elapsed time.
- **Concurrency swept during screening, then validated.** A single concurrency
  number is a choice that can change the result. Sweep each tool across its
  declared replay range, select configurations under the advancement rule
  below, and take the selected configuration — plus a nearby contender when
  needed to validate the selection — to real S3.
- **Large buckets amortize fixed launch cost.** `elapsed_ns` necessarily includes
  launching the subject process. The benchmark uses buckets large enough that
  this fixed cost is insignificant beside the listing.
- **Pinned versions.** Every tool's exact version and build recorded in the
  receipt.

### 3a. Everything runs in a container

**Settled: no tool is run directly on the host.** Each gets a pinned image, and
every receipt records the image digest.

**Material methodology change — 2026-08-10, superseded 2026-08-16.** The first
container plan used one shared base, separately published tool payloads, and one
execution image per tool. No comparative run used that design.

**Material methodology change — 2026-08-16.** The production benchmark now
builds one self-contained toolbox directly from all eleven checked-in capsule
recipes. Compiler and package-manager work remains isolated in build stages;
the final runtime contains every subject but each Batch task selects and runs
exactly one. There are no separately published parent images or legacy image
jobs. We still prefer checksum-pinned official distributions; `s3-fast-list`
remains the native source-build exception.

This replaces the earlier preference for upstream tool containers. It holds the
base filesystem, CA input, and shared libraries constant where tools consume
them while keeping upstream provenance at the tool-artifact boundary. It does
not make bundled or static runtimes identical: Node, Python, JRE, resolver, TLS,
and allocator behavior carried by a tool payload remain part of that subject.

The final toolbox OCI digest is the execution identity. Image sets and results
also record the executed toolbox-recipe digest, each tool artifact/build/recipe
and consolidated build-input identity, adapter bundle, and harness revision.
Historical receipts continue to describe
the older images they name. The exact build and registration contract lives in
[`tool-structure.md`](operating/tool-structure.md) § Executable integration and
builds; availability remains a separate question in
[`artifact-availability.md`](operating/artifact-availability.md).

Rebuild scope follows the changed component:

| Change | Required image work |
| --- | --- |
| benchmark plan | none |
| worker, runtime, or one adapter | rebuild the toolbox |
| one tool version or recipe | rebuild the toolbox |

BuildKit caches the isolated build stages where available. Container resource
limits remain the mechanism used for limits testing.

Two details to handle up front:

- **Use one fresh Batch VM and least-privilege task identity per scheduled run.**
  The worker uses that identity only to upload its own result prefix. Current
  commands and controller state are owned by [`../benchmark/`](../benchmark/).
- **The JVM sees cgroup limits and reacts.** Under `--memory`, a modern JVM sizes
  its heap from the container limit. Swath is the only JVM entrant, so
  memory-capped runs are not a neutral environment for it — its behaviour under a
  cap is *different in kind*, not just degree, from a Go binary hitting the OOM
  killer. Record heap settings explicitly and don't let Swath silently benefit (or
  suffer) from adaptive sizing nobody else has.

### 4. Behavior under limits and interruption

The owner explicitly wants this, and it may be more useful than the benchmarks.
Throughput changes with every release, while incomplete output under memory
pressure is important behavior for users to understand.

- **Constrained memory** via `docker --memory` (cgroup v2). Multiple tools are
  claimed to OOM: rclone at 100M, S3P at ~100M, s3-fast-list by design
  (accumulate-then-dump). Cap memory, scale the bucket, find the actual cliff for
  each. Report the cliff as a *range* between the largest bucket that survived and
  the smallest that died — a single number implies precision we won't have.
- **Docker gives us a direct OOM signal.** `docker inspect` exposes `.State.OOMKilled` as a
  boolean straight from the kernel. That turns the rclone exit-0-on-OOM claim from
  something we'd otherwise have to *infer* — from dmesg, from a truncated output,
  from a guess — into a two-field observation: `OOMKilled=true` alongside
  `ExitCode=0` shows whether the earlier observation occurs in our setup. Capture
  both fields on every limits-and-interruption run regardless of tool.
- **The exit-code question is the priority.** One GitHub issue reports rclone
  being **OOM-killed while exiting 0**, which could leave incomplete output
  looking successful. We do not describe that as current behavior until we have
  reproduced it with a recorded exit code.
- **Interruption and resume.** `SIGKILL` mid-listing, then attempt resume. Tests
  the "nobody has crash-resume" claim directly — and Swath's checkpointed resume
  is a headline claim, so it faces the same kill.
- **Correctness under stress.** Speed is worthless if the output is wrong. Every
  timed run should be checkable for **completeness** (did it get every key?) and
  **exactly-once** (did it emit duplicates?). Tools using overlapping ranges (S3P)
  may duplicate unless they dedup; we measure that rather than assume it.

### 5. What we plan to publish

The planned outputs are:

1. **A review-and-update pass** over every observation in the
   [tool pages](../tools/README.md), with each atomic claim carrying its
   evidence strength, editorial disposition, and typed evidence or a recorded
   reason none exists. Groundwork completed the first pass; later runs can add
   receipt-backed confirmation without erasing the source-first record.
2. **Benchmarks**, with the comparison controls above.
3. **A capability matrix** — parallel LIST, flat-prefix splitting, crash-resume,
   exactly-once, bounded memory, output formats, and 503 handling, each linked
   to how we checked it.
4. **Upstream issues** for reproducible problems we find.

## Replay screening, then real-S3 validation

The comparative campaign is a funnel. Replay is the controlled search
environment; real S3 validates the finalists. The two stages answer different
questions and their results remain visibly separate.

### Stage 1 — controlled replay screening

Swath ships a [replay server](https://github.com/varveio/swath/blob/main/docs/swath-replay-server.md)
that serves a captured Parquet listing over HTTP as an S3 `ListObjectsV2`
endpoint. It is not a general S3 emulator — path-style only, no auth or SigV4
validation, no `GetObject`/`PutObject`/versions, and listing metadata only. Its
value here is control: every candidate sees the same immutable fixture, latency
treatment, backend state, and declared machine allocation without competing for
a live S3 key-range budget.

The initial cross-tool endpoint observations and reproducer are retained in the
[`replay-endpoint-compat` receipt](../benchmark/pilots/replay-endpoint-compat/RECEIPT.md).

Replay supplies four observations that the wide real-S3 search could not isolate:

- **Configuration and concurrency search** — compare a tool's supported modes
  and tunings under one repeatable service envelope.
- **Tool-agnostic API-call and request-shape capture** — count every request and
  record `start_after`, `max-keys`, delimiters, and prefixes without requiring
  cooperation from the subject.
- **Purpose-built shapes** — run immutable layouts that would be costly or slow
  to create repeatedly in S3.
- **Deterministic fault injection** — exercise 503s, slow pages, malformed
  pagination, and transport failures separately from the clean throughput
  screen.

The server is part of the measuring instrument and must earn trust for every
screening attempt. Before a replay result can advance or eliminate a candidate:

1. the fixture has an immutable content identity;
2. the worker records an in-container row count after timing and retains the
   attempt logs and bound result marker; a run needing content inspection opts
   into native-product upload before submission;
3. the complete server image, serving mode, latency treatment, and allocation
   are part of case identity and the receipt;
4. the attempt retains server meters and interval-aligned cpuset-utilization
   samples sufficient to compare a candidate server with an overprovisioned
   control; and
5. any known protocol divergence affecting that subject's request pattern is
   fixed or makes the attempt ineligible for comparison.

**CPU-isolation correction, 2026-08-28.** Disjoint logical CPU numbers are not
enough on an SMT machine. An N4 topology receipt showed that the lower and upper
halves are sibling threads of the same physical cores; the earlier contiguous
replay/subject split therefore shared cores. Replay allocations now keep both
sibling threads of a physical core in one controlled group, and replay and
subject receive different physical-core sets. Results made under the earlier
split remain diagnostics but cannot establish subject CPU response.

A diagnostic canary checks the endpoint, backend binding, replay evidence,
row-count, upload, and result/report path, but creates no comparative timing or
rate result. Replay capacity is **UNCALIBRATED** until a diagnostic capacity
canary has a committed receipt. A plan states that condition explicitly as
`replay.capacity_status: uncalibrated`; no replay measurement is eligible before
the status is changed to `calibrated`.

**Delivered-treatment correction, 2026-09-01.** Declaring a latency profile
does not prove that a colocated replay server delivered it without queueing.
Reporting therefore classifies every latency-injected replay attempt from its
retained per-shape meters and 10-second replay-cpuset samples:

- `TIMING_VALID`: each observed shape's request-service mean is at most 110% of its
  deadline and fewer than 1% of its requests overran; at least five resource
  samples exist, with fewer than 20% at or above 90% replay CPU;
- `PRESSURE_DEGRADED`: the attempt misses that gate, but no shape exceeds a 10%
  overrun fraction or 125% of its requested mean and replay CPU is not sustained
  at the ceiling;
- `CAPACITY_FAILED`: any shape exceeds either of those latter limits, or at
  least 20% of five or more resource samples are at or above 90% replay CPU;
  and
- `INSUFFICIENT_EVIDENCE`: the injected attempt has no observed request shape,
  incomplete/non-monotonic meters, or fewer than five resource samples.

An attempt without latency injection is `NOT_APPLICABLE`; it may characterize a
raw ceiling but does not pass the injected-treatment timing gate. These are
provisional capacity-calibration thresholds, dated before the next rung rather
than fitted to it. They classify instrument pressure separately from subject
success: a pressure-degraded or capacity-failed attempt may still establish
exit behavior and row count, but it cannot supply comparative timing.

`capacity_status: calibrated` additionally requires a committed diagnostic
receipt showing that one common replay allocation passes this gate for the
heaviest included client shape and that its subject wall time agrees, within a
predeclared uncertainty band, with a paired materially overprovisioned replay
control. Contact with a configured reader-pool limit is reported but is not by
itself a failure. Historical replay rows may be labeled by this same derived
rule without rewriting their raw `result.json` evidence. Earlier wording that
treated any single overrun as an automatic timing failure is superseded by this
magnitude-and-sustained-pressure rule.

**Replay warm-up, 2026-09-01.** A local isolation study of the replay server
found that its delimiter path costs 1–2 ms per request once warm but that a
campaign issues only a few hundred structure probes against millions of pages,
so that path may never leave the JIT's interpreted tiers during a run; the
server also opens its per-file delimiter reader pools lazily inside the first
request that needs them. A plan may therefore declare a `replay.warmup`: a
deterministic breadth-first delimiter walk from the fixture root plus pivot and
page requests at keys that walk returned, driven by the worker after readiness
and before the `before` metrics snapshot. The warm-up applies identically to
every tool in the plan, is part of the treatment identity (a warmed server is
a different instrument), never enters a treatment meter, and is recorded with
its issued counts and duration in the attempt's replay evidence. Rows without
a declared warm-up keep their identities and their cold-server classification.

Latency injection is a declared experimental treatment. A fixed-latency profile
is valid for a controlled screen when it is applied identically and reported as
such; it is not described as reproducing S3's full latency distribution unless
that stronger fidelity has separately been demonstrated.

**Latency-treatment provenance, 2026-09-02.** A fixture's fixed deadlines are
the rounded p50 of one request's client-observed round trip per shape, read
from the phase timers of the Swath run that captured the fixture. A capture may
supply deadlines only if its median connection-pool wait is negligible and its
total is within a few milliseconds of its time-to-first-byte, so that no client-side
connection queue is inside the number. That test does not detect client CPU
starvation, which inflates time-to-first-byte itself: a 2026-08-06 FourCast
reference reported a 123.7 ms worker-page p50 where a 2026-08-26 control on
the same bucket reported 86.0 ms, and only a cross-check against a non-Swath
client would have rejected it. A capture must therefore pass both the phase
test and the cross-check. Because a
client holding many requests in flight can load S3 in a way a serial client
never does, and the capturing client cannot see that from inside, the floor is
cross-checked against clients other than Swath: directly, by a same-bucket
serial sample of about 100 unsigned pages over one keep-alive connection from
the runner's zone, whose p50 must be within about 15% of the deadline, a deadline above the
serial p50 being conservative and one below it optimistic; and in
aggregate against the roster's serial tools on live S3 in the study's August
basic pass, whose wall time per page minus client cost per page must leave a
residual that brackets the deadline for that region within a few
milliseconds and never one materially below it. That residual is a run mean, not a p50; the check establishes
that replay charges a serial tool no more per page than live S3 did. The
August pass predates the campaign ledger and is not exported; the cross-check
is recorded in the study's working notes, and no published row depends on it.
The treatment is therefore a floor for subjects under roughly 1,000 requests
per second on plain pages. A subject above that rate sees a floor that live S3
does not give it, and its replay throughput is an overstatement by an
unmeasured factor; in this study only Swath can reach that rate, and no
published row has. Fixed p50
deadlines carry no tail and no throttling, and the server prices a request by
its syntax, so a `delimiter=/` page that returns objects draws the structure
deadline rather than the page deadline.

The plan fixes its advancement rule before the relevant replay results are
examined. Capacity-calibration rows are diagnostics and cannot eliminate a
candidate. Once paired controls establish the rule and its uncertainty, replay
may remove clearly slower tool or configuration candidates from the expensive
S3 stage. Close candidates advance together; the screen is not allowed to
manufacture a precise podium from differences within its predeclared
uncertainty or elimination boundary. Every replay number is labeled as
synthetic throughput against the named replay fixture and latency treatment,
never as “speed against bucket X.”

### Stage 2 — focused real-S3 validation

After replay identifies the strongest configuration or small candidate set for
a tool, those finalists run against real S3. These runs are deliberately few,
use fresh VMs and the public child-process wall-clock boundary, and execute one
subject at a time. Submission staggering alone is not isolation because Batch
startup time is uncontrolled; the next validation run starts only after the
previous subject has stopped driving the bucket.

At minimum, validate the selected configuration. When replay chose between
nearby configurations or candidates and the study intends to rely on that
choice, carry the closest contender or boundary candidate into the same S3
validation set. A winner-only S3 run establishes that the winner operates on
S3; it does not establish that replay selected the right winner.

Real-S3 validation answers whether the selected configurations and the
directional conclusions from replay survive the live service's latency,
throttling, retry, and ambient-load behavior. It does not retroactively turn
excluded candidates' replay numbers into S3 measurements. The receipt identifies
the live snapshot window and any observable throttling; correctness verification
remains mandatory.

If replay and real S3 disagree materially, the S3 observation governs every
claim about S3. The result is not averaged into a composite score: freeze the
discrepancy, expand the validation set enough to distinguish variance from a
systematic mismatch, and either revise the selected configuration or narrow the
replay claim before publication.

### Stage 3 — failure and congestion behavior

Retry depth, congestion control, recovery, interruption, and malformed-response
handling are separate workloads. Run them deterministically against the replay
fault injector for finalists, with targeted real-S3 observations only where the
service can be exercised safely and the behavior is observable. Do not fold
failure survival into the clean-throughput number or treat a non-throttling
replay winner as automatically best under S3 congestion.

The replay endpoint's limited surface remains a study limitation. The committed
compatibility receipt records which subjects can use it today; a subject that
requires unsupported `ListObjects` v1 or off-protocol calls cannot be silently
treated as a performance loser. The inherited conformance questions in
[`open-questions.md`](open-questions.md) §6 remain the checklist for protocol
risks.

## Run records (receipts)

Every run-dependent observation promoted to `confirmed` needs a committed receipt containing:
exact invocation, tool version/build, declared machine type and resources (arch,
cores, RAM, region), bucket
identity and measured shape, raw output or a pointer to it, exit code, wall-clock,
peak RSS, API call count where obtainable, and the date.

A receipt is the detailed run record, not just a summary. It gives someone else
what they need to rebuild the run.

## Execution order

1. Build and validate the benchmark toolbox and measurement worker once.
2. At smoke scale, work per tool: read its docs and source, select or build its
   pinned image, then execute every supported listing mode. Reading and smoke
   necessarily interleave within a tool because its invocation cannot be
   designed responsibly before its interface is understood.
3. Reconcile the inherited notes against that source-first work and the exact
   receipts. Groundwork ends here and produces no comparative number.
4. Before comparative work, settle the roster and output-work decisions, select
   the benchmark buckets, and record their shapes independently before any
   subject touches them.
5. **Qualify replay before measuring it.** A diagnostic canary first shows that
   the endpoint, backend binding, campaign configuration, runner, and reporter
   work together without producing a comparison. That diagnostic
   capacity canary receives a committed receipt before its plan is marked
   `calibrated` and scale spending begins.
6. Run the replay screening and configuration sweeps under their predeclared
   advancement rules. Freeze the finalists before looking at the S3 validation
   results.
7. Run the small real-S3 validation set one subject at a time. Include a nearby
   contender wherever the study needs to validate replay's selection rather
   than merely the selected configuration's ability to run on S3. Reconcile
   material disagreements before publishing a conclusion about S3.
8. Run limits, congestion, and interruption scenarios under their separately
   declared workloads and update the inventory from the resulting evidence.

## What this setup cannot tell us

These limits are part of how readers should understand the results:

- **Varve builds Swath and maintains this repo.** Our earlier research into
  existing listing tools also helped shape Swath's design. We are participants
  in this space, and the raw receipts let readers inspect our work directly.
- **We know Swath better than the other tools.** We know Swath's
  performance envelope intimately. We know the other tools through their public
  docs and source, the earlier design research, and the runs in this repo — but
  not with the same day-to-day familiarity. We are likely to tune Swath better
  than we tune rclone. We use each tool's documented best practices, ask
  maintainers where unsure, and state this limitation with published results.
- **Small bucket samples don't generalize.** Five buckets are five anecdotes
  unless mapped to a shape taxonomy.
- **Tools keep changing.** Every number is version-stamped and will age.
- **Our declared machine shape is one shape.** `s3-fast-list`'s 1000-way
  concurrency claim may need more resources than the campaign declares; if so,
  that is a stated limitation of the study, not evidence against the tool.
