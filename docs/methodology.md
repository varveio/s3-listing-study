# Methodology

This is the study's measurement plan — what counts as a run, how comparisons
are controlled, and what gets published. It was committed *before* comparative
results existed, on purpose — see
[We wrote the plan down first](#we-wrote-the-plan-down-first).

**Status: plan written before comparisons; measurement not yet executed.** No
benchmark has been run and no comparative performance result exists in this repo (smoke receipts
carry per-run wall-clock and RSS, but never as a comparison). All eleven
subjects have now run at smoke on amd64 through the attempt engine: seven
anonymously and `s3p`, `s3kor`, `s4cmd`, and `ps3` with a scoped credential.
Those attempts establish that the execution path works, not correctness: none
has a verifier verdict because auditing an attempt against a reference manifest
is not implemented yet. See `../tools/README.md` for per-tool status and
`smoke-bucket.md` for the historical reference snapshot.

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
only a count. The worker retains and uploads the original stdout, stderr, and
native directory output. Five-field normalization is manager-side work invoked
only for explicit correctness verification.

Required routine campaign reporting is summary-only. The campaign model owns a
`run-<n>` ordinal for each scheduled run (`run-1` under the current policy),
while every worker-container execution mints its own attempt UUID below that
prefix. The manager reconciler still to be implemented must discover only
immediate UUID children with a delimiter listing and read their exact
`result.json` summaries. Raw listings remain in the same GCS attempt trees and
are fetched only for correctness verification or investigation. Multiple UUID
children under one run are duplicate executions; the reconciler must surface
all and select none as canonical.

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
cost analysis. Where a tool doesn't expose one, we do **not** block on it in
Phase 1. Externally instrumented call counting is deferred to Phase 2 (below),
because the instrument for it is a piece of our own software that has to earn
trust first.

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
must be stated as a limitation or controlled by running in-region.

### 3. Keeping comparisons useful

- **Same declared resources, all tools.** Every published comparison uses fresh
  Batch VMs of the same declared machine type, vCPU count, and memory shape.
  Third-party published numbers (s3-fast-list's 3.1M/s, S3P's 35K/s, PS3's 94K/s)
  are **context, never comparison** — different hardware, different buckets,
  different years, mostly self-reported.
- **Same bucket, same window.** Bucket contents drift; tools run against the same
  bucket at wildly different times aren't comparable.
- **One fresh VM and one scheduled run per case.** `reps` is 1 and there are no
  cold/warm arms. VM/container boot is outside the timer; network setup performed
  by the subject remains part of the subject's own elapsed time.
- **Concurrency swept, not fixed.** A single concurrency number is a choice that
  can change the result. Sweep each tool across its range and report its best.
- **Large buckets amortize fixed launch cost.** `elapsed_ns` necessarily includes
  launching the subject process. The benchmark uses buckets large enough that
  this fixed cost is insignificant beside the listing.
- **Pinned versions.** Every tool's exact version and build recorded in the
  receipt.

### 3a. Everything runs in a container

**Settled: no tool is run directly on the host.** Each gets a pinned image, and
every receipt records the image digest.

**Material methodology change — 2026-08-10.** Every comparative image now uses
the same published shared-base digest and a separately pinned tool payload.
Final assembly adds the current worker and exactly one adapter, producing one
execution image and digest per tool. We prefer the selected release's official
binary, archive, or package; `s3-fast-list` is the only native source-build
exception because its selected fork publishes no matching binary.

This replaces the earlier preference for upstream tool containers. It holds the
base filesystem, CA input, and shared libraries constant where tools consume
them while keeping upstream provenance at the tool-artifact boundary. It does
not make bundled or static runtimes identical: Node, Python, JRE, resolver, TLS,
and allocator behavior carried by a tool payload remain part of that subject.

The final OCI digest is the execution identity. Image sets and results also
record the base digest and source identity, tool artifact and build identities,
adapter bundle, and harness revision. Historical receipts continue to describe
the older images they name. The exact build and registration contract lives in
[`tool-structure.md`](operating/tool-structure.md) § Executable integration and
builds; availability remains a separate question in
[`artifact-availability.md`](operating/artifact-availability.md).

Rebuild scope follows the changed component:

| Change | Required image work |
| --- | --- |
| manager or benchmark plan | none |
| worker or one adapter | reassemble affected final image(s) |
| one tool version or recipe | rebuild that tool payload and final image |
| shared runtime | rebuild the base and every final image |

Avoiding tool recompilation during final reassembly requires retained BuildKit
cache. A fresh builder needs registry-backed cache, which is not implemented
yet. Container resource limits remain the mechanism used for limits testing.

Two details to handle up front:

- **Use the execution profile for the scheduler.** Production comparisons use
  the cooperative GCP Batch profile in
  [`runner-security.md`](operating/runner-security.md): one task on one fresh VM,
  a least-privilege task identity, and metadata access so the worker can obtain
  the token that uploads to GCS. The firewall-backed bridge and metadata-denial
  gate are retained for local Docker runs on the more-privileged manager/runner
  host; they are not a Batch prerequisite or provider adapter.
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

## Phase 1 (real S3) and Phase 2 (replay server)

**Phase 1 — real S3. Everything above.** Real buckets, real network, real
failures. We start here because a result on real S3 does not depend on how
closely a local endpoint reproduces the service.

**Phase 2 — the replay server.** Deferred, and gated on Phase 1.

Swath ships a [replay server](https://github.com/varveio/swath/blob/main/docs/swath-replay-server.md)
that serves a Parquet listing over HTTP as an S3 `ListObjectsV2` endpoint. It is
not a general S3 emulator — path-style only, no auth or SigV4 validation, no
`GetObject`/`PutObject`/versions, listing metadata only. But for this study it is
useful for several kinds of observation because it sits *outside* the tool being run:

- **Tool-agnostic API-call counting** — every request a tool makes, counted, with
  no cooperation from the tool required. This is what makes S3P's ~50%
  overlap-waste claim exactly measurable rather than estimable, and it works
  identically for tools that expose no counter of their own.
- **Request-shape capture** — not just how many calls, but what they *are*:
  `start_after` values, `max-keys`, delimiters, prefixes. That reveals the
  algorithm from the outside, independent of what the source says it does, and
  independent of whether we read the source correctly.
- **Deterministic fault injection** — 503s, slow pages, truncation weirdness,
  stuck continuation tokens, on demand and reproducibly. Several inherited
  claims (retry behaviour, 503 adaptation, the stuck-token defence) are hard to
  test on real S3 precisely because real S3 mostly behaves.
- **Purpose-built shapes on demand** — bucket layouts that would be expensive or
  slow to build for real.

**Why it is Phase 2 and not Phase 1:** *the replay server is our own software.* Using it to
measure other tools means our testing endpoint is our code. If it is subtly
wrong — a divergence from real S3's pagination semantics, encoding, or ordering
— we could describe another tool incorrectly even though the measurements look
precise.

So Phase 2 has a firm prerequisite: **the replay server must first be shown to
match real-S3 captures** on the same shapes we intend to test. And Phase 1 must
already show the picture looks right on real S3, so
Phase 2's numbers have something to be checked against rather than being the
only evidence.

Known frictions to resolve before Phase 2 (recorded now, not solved):

- **No auth/SigV4 validation** — some clients may refuse to talk to an
  unauthenticated endpoint, or may require dummy credentials.
- **Path-style only** — any tool that forces virtual-hosted-style addressing
  can't be pointed at it. `s3-fast-list` is reported to *auto-enable*
  force-path-style whenever `--endpoint` is set, with no override, which happens
  to suit this endpoint.
- **`ListObjectsV2` only** — a tool that probes with `HeadBucket`,
  `GetBucketLocation`, or `ListObjectVersions` will break on it.
- **Divergence from real S3 is the risk**, not fidelity of the data. The listing
  content is real; the protocol surface is a reimplementation, and the inherited
  conformance notes in [`open-questions.md`](open-questions.md) §6
  are a catalogue of exactly how S3-compatible endpoints get this wrong.

## Run records (receipts)

Every run-dependent observation promoted to `confirmed` needs a committed receipt containing:
exact invocation, tool version/build, declared machine type and resources (arch,
cores, RAM, region), bucket
identity and measured shape, raw output or a pointer to it, exit code, wall-clock,
peak RSS, API call count where obtainable, and the date.

A receipt is the detailed run record, not just a summary. It gives someone else
what they need to rebuild the run.

## Execution order

1. Build and validate the shared run wrapper and correctness verifier once.
2. At smoke scale, work per tool: read its docs and source, select or build its
   pinned image, then execute every supported listing mode. Reading and smoke
   necessarily interleave within a tool because its invocation cannot be
   designed responsibly before its interface is understood.
3. Reconcile the inherited notes against that source-first work and the exact
   receipts. Groundwork ends here and produces no comparative number.
4. Before comparative work, settle the roster and output-work decisions, select
   the benchmark buckets, and record their shapes independently before any
   subject touches them.
5. **Smoke the frozen measurement path first.** A small canary must show that
   the campaign configuration, runner, receipts, and verifier work together
   before scale spending begins.
6. Run the benchmark sweeps under the frozen campaign rules.
7. Run limits-and-interruption scenarios under their separately declared
   workloads and update the inventory from the resulting evidence.

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
