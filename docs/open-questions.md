# Open questions and cross-tool leads

This page owns the study's open cross-tool questions and the inherited leads
behind them — material that belongs to no single tool and is not yet settled by
a committed run. The documented S3 mechanics these build on are in
[`s3-reference.md`](s3-reference.md).

Every item here is `unverified` until the planned cross-tool run, fault, or
source-review work settles it. No cross-cutting behavioral claim becomes
`confirmed` without an exact committed run record; editorial correction is
separate from evidence strength. The shared vocabulary is defined in
[`methodology.md`](methodology.md#evidence-language) and summarized for tool
claims in [`../tools/README.md`](../tools/README.md#what-the-evidence-labels-mean).

The numbered section headings below are stable anchors: the per-tool research
trails cite these questions by number (§1–§8).

---

## 1. Does latency matter more than throttling for parallel listing?

**Status: `unverified` study hypothesis.** No comparative or throttle campaign
has run.

Our starting hypothesis is that latency matters more, which would affect what
is worth optimizing.

The famous S3 figures — 3,500 PUT/COPY/POST/DELETE and 5,500 GET/HEAD requests
per second **per partitioned prefix** — are per-prefix *object* request rates.
Whether LIST shares the GET pool is **officially undocumented**; practitioner
consensus is to assume it does.

But the best published parallel-list rate on AWS (S3P's ~35K items/s) is only
**~35–70 LIST requests/sec** — orders of magnitude below any ceiling. If that's
right, then:

- The thing to optimize is round-trips and placement, not throttle avoidance.
- 503s should be nearly absent in our runs.
- A tool's throttle-adaptation machinery (including Swath's AIMD) may be used
  rarely in these workloads.

**How we plan to check it:** count 503s across every run at every concurrency
and record when throttle handling engages.

**Observations that point the other way.** The inherited notes carry at least two
run records that do not fit this hypothesis, and an earlier revision of this page
kept one and dropped the other:

- Spark's naive 10k-task listing is recorded as being SlowDown'd — i.e. throttling
  that is real, at high task counts.
- Hudi's 503s are recorded as *worsening with more executors* (`hudi#6048`).

Both describe many-worker listing hitting throttling, which is exactly what this
claim predicts shouldn't dominate. They may be reconcilable — those systems
parallelize over *directories/tasks* rather than key ranges, and may hit a
different regime — but that reconciliation has to be argued and tested, not
assumed. Dropping the counterexample while keeping the claim is the failure mode
this whole repo exists to prevent, and we committed it here before running
anything.

Related mechanics, all inherited and unverified:
- S3 splits hot partitions gradually under sustained load, emitting 503 SlowDown
  during the ramp; splits can occur at any point in the key string, not just at
  delimiters, and can merge back. (Sourced to 2012-era mechanism talk — treat as
  historical colour, not guarantee.)
- The 2018 announcement retired the old randomized-prefix advice.
- A 100k+-key directory under load is likely already multiple internal
  partitions, so parallel LISTs at different `start_after` can exceed one
  partition's ceiling in aggregate.
- One source attributes partition-warming behavior to AWS Knowledge Center
  (`repost.aws/.../s3-503-within-request-rate-prefix`) — a different and more
  current source than the 2012 talk, worth reading before relying on either.

---

## 2. How much does client language matter at high list rates?

**Status: `unverified` study hypothesis.** The cited rewrite result is context;
this study has not isolated language from algorithm, output, or runtime.

The Pure Storage project reported a **3.93× speedup from rewriting Python → Go**
on a workload that is nominally I/O-bound. That's a strong hint that at the top
of the range, client-side deserialization and bookkeeping dominate — not the
network.

Our tool set is an unusually good natural experiment:

| Language | Tools |
| --- | --- |
| Python | aws-cli, s4cmd |
| Go | s5cmd, rclone, mc, s3kor |
| Rust | s3ls-rs, s3-fast-list |
| Node | S3P |
| Java | Swath |

If the hypothesis holds, it predicts a language-tier stratification in the
results that's roughly independent of algorithm — and it puts the single JVM
entrant (ours) in an interesting spot. See [`../tools/pure-storage/`](../tools/pure-storage/).

---

## 3. Which tools support crash-resume?

**Status: `unverified` field hypothesis.** Neither the all-tools absence claim
nor Swath's claimed exception has passed the planned interruption test.

Our starting notes say that none of the surveyed tools except Swath claim
first-class LIST crash-recovery. That remains an open question.

Note the context this arrived in. The claim was written while Swath
was the tool being *built*, not a tool being *surveyed* — so "nobody" meant
"nobody else." Here Swath is a subject like any other, so we check its resume
claim and look for comparable features in every other tool.

Both parts need testing:

- Continuation tokens are durable — you can persist and resume them — but they're
  **opaque**, and AWS's docs warn the result is not a consistent snapshot:
  objects added during pagination may or may not appear. Fine for "list
  approximately what's there"; not fine for "produce a deterministic inventory."
- `s3-fast-list`'s `next_start = last_seen_key` is in-run only, not persisted
  across restarts.
- AWS SDK paginators wrap continuation tokens but don't expose them for
  persistence by default.
- aws-cli's `--starting-token` exists but the *caller* must persist it.

**Swath claims checkpointed crash-resume** — the exception the claim is written
around. Unverified on exactly the same terms as everything else here.

Easy and satisfying to test, and the test is identical for all of them: start a
run, `SIGKILL` it, try to resume. Then check the resumed output for completeness
and duplicates, because a resume that silently drops or double-emits keys is
worse than no resume at all — it's a resume you'd trust.

Three possible outcomes, all worth publishing:

1. No other surveyed tool supports resume, and Swath's resume works in the
   interruption test.
2. No other surveyed tool supports resume, and Swath's resume does not survive
   a forced mid-run kill (mid-checkpoint or mid-page-commit).
3. Another tool has usable resume that our starting notes missed.

---

## 4. How does S3 Express One Zone affect bisection?

**Status: per-tool impact `unverified`.** The documented API constraint —
directory-bucket results are not lexicographic and `StartAfter` is unsupported —
is recorded in
[`s3-reference.md`](s3-reference.md#s3-express-one-zone-directory-buckets).

A range-bisection engine cannot assume the general-purpose-bucket ordering and
lower-bound contract on such a bucket. Whether each tool rejects or adapts to
that surface is still open. A tool that silently applies lexicographic bisection
may return incorrect results, which is a correctness question rather than a
performance one. `s3ls-rs` is claimed to guard this explicitly (rejecting
parallel listing on `*--x-s3` buckets unless opted in); `s3-fast-list` is
claimed to have Express support only as a roadmap TODO.

---

## 5. How do versioned buckets and delete markers affect LIST latency?

**Status: `unverified` third-party lead.** The linked account has not been
reproduced in this study.

A single LIST reportedly taking up to **120 seconds**, with a measured 61.87×
slowdown, on buckets carrying many delete markers.

Source: https://xuanwo.io/2025/02-why-s3-list-objects-taking-120s-to-respond/

---

## 6. Which S3-compatible differences can affect listing?

**Status: `unverified` inherited conformance leads.** These items need direct
public-source review and endpoint-versus-real-S3 captures before they support a
finding about any tool.

Inherited from Swath's conformance work. Relevant here because several tools are
tested against non-AWS endpoints, and a "bug" we attribute to a tool may
actually be its backend.

- **LocalStack and MinIO echo back `Prefix`/`StartAfter`/`Marker`/
  `ContinuationToken`/`Delimiter` verbatim**, where real S3 percent-encodes them
  — claimed to crash decoders on a trailing `%`.
- **LocalStack 3.8 double-URL-decodes `start-after`**, causing silent
  under-counts. MinIO (latest) is claimed to decode exactly once and be
  real-S3-faithful — i.e. the defect is claimed LocalStack-specific.
- **LocalStack is claimed to mangle ≥2-byte UTF-8 keys.**
- Some S3-compatible endpoints are claimed to have shipped a bug where truncated
  results carry no continuation token. The inherited notes attribute the
  defensive bail-out to **`s3ls-rs` specifically** — no other tool is named as
  guarding it. Whether the others guard it too is a question for the study, not
  something to assume either way.
- Ordering: several stores (MinIO, Ceph, R2, Wasabi, B2, Spaces) support V2
  listing with an arbitrary start key **only if** they guarantee strict
  lexicographic byte order — and some are claimed to have shipped ordering bugs.
  Any range-splitting tool is silently wrong on a store with an ordering bug.
- Ceph RGW V2 tokens, Cloudflare R2 `startAfter`, and Backblaze B2 (claimed: no
  anonymous LIST) each diverge somehow.

**Practical consequence**: behavior observed against LocalStack is not a
tool finding until it reproduces against real S3 or a
known-conformant endpoint. This has already affected the Swath project once — an
alarming result resolved as a LocalStack artifact after cross-checking against
MinIO.

---

## 7. Non-S3 stores have their own range primitives

**Status: `unverified` and outside the v1 roster.** These are possible future
conformance targets, not properties used by the current study.

Out of v1 scope but recorded, since it bears on whether any of this generalizes:

- **GCS** native `objects.list` exposes `startOffset` (inclusive) / `endOffset`
  (exclusive) — genuine range bounds. Its **XML API** is claimed to genuinely
  support `list-type=2` / `start-after`, contradicting a "marker-only"
  characterization elsewhere in the inherited material. Requires HMAC
  interoperability credentials.
- **Azure Blob** List Blobs is claimed to document a `startFrom` — flagged in the
  source as unverified, and if true would make Azure a conformance target rather
  than an assumed prefix-only store.

---

## 8. Is there a published algorithm for this problem?

**Status: `unverified` absence claim.** One relevant paper would refute it, so
it remains open until a documented literature search is performed.

Our starting notes say there is **no published treatment** of parallel
enumeration of an opaque, cursor-paginated, sorted keyspace. The nearest analogue
they record is Google's Napa progressive range partitioning (VLDB'23), which
assumes an indexed backend and therefore addresses a different problem.

Specifically claimed to have no published evidence anywhere: `MaxKeys=1` existence
probing, interpolation search over LIST cursors, and radix estimation from sampled
pages.

If true, this means the tools grew from practical implementations rather than a
shared reference algorithm. It is also a reason to be careful with novelty
claims in any direction, including ours.

---

The sections below retain adjacent systems and third-party accounts as context.
They are not single-bucket listers and do not enter comparative runs.

## Tier 3 — adjacent systems, context only

**Status: `unverified` inherited context.** The links and leads below explain why
listing matters in larger systems; they are not study results or comparison
subjects.

Not included in the comparative runs. They are not single-bucket listers, and
forcing them into the same table would hide that difference. They are here
because they show where listing behavior matters in larger systems.

**Hadoop S3A** — flat listing over tree-walk, per-directory thread pools; never
splits a prefix. S3Guard removed post-strong-consistency. `hadoop-aws` is claimed
to pull a measured 558 MB AWS SDK v2 bundle. Pain receipts: `HADOOP-15192`.

**Spark `InMemoryFileIndex`** — spins up a distributed listing job when paths
exceed a threshold (claimed: 32); the unit of parallelism is *directories*, never
key ranges. Pain receipts: `SPARK-17593`, `SPARK-21056`.

**Iceberg / Delta / Hudi** — avoid LIST entirely via manifests / `_delta_log`,
but LIST survives in maintenance operations, which are therefore LIST-bound:

- Iceberg `remove_orphan_files` is LIST-dominated — claimed maintainer-confirmed
  (Russell Spitzer, PMC) — and accepts a file-list input
  (`apache/iceberg#13693`, also `iceberg#12765`, `trino#25847`).
- Delta `VACUUM` with inventory input: claimed 30–60min → <5min and ~44% cheaper
  (https://delta.io/blog/efficient-delta-vacuum/). Also `delta#4008`: "6+ hours
  on a 27k-partition table."
- Hudi: 503s claimed to *worsen with more executors* (`hudi#6048`) — which, if
  true, is a nice counterpoint to question #1 above and worth reconciling.

**Excluded, recorded so nobody re-investigates**: `s3pd` (a parallel downloader,
not a lister); legacy Python `s3p` (confusingly named, distinct from
`generalui/s3p`, claimed largely superseded); `s3sync` / `s3s3mirror` (sync tools
where listing isn't the optimized path).

---

## Accounts of listing at scale

**Status: `unverified` third-party context.** These accounts are retained as
leads for what "too slow" can mean operationally; the study has not reproduced
their numbers or adopted them as comparisons.

- **37signals** (5B-object exodus from S3): *"you can't list a bucket of that
  size without taking literally days"* — built an internal prefix-sharded lister,
  taking it from days to ~30min.
  https://dev.37signals.com/moving-mountains-of-data-off-s3/
- **`blog.rasc.ch/2025/07/s3-fast-list`** — an external account of hand-rolled
  prefix-sharded listers.
- **Independent re-testing of s5cmd's published speed claims (2025)** — our
  notes describe this inconsistently: as a Hacker News thread (62 points) whose
  commenters re-ran s5cmd's "Nx faster" claims, and separately as a bare link to
  a [BigGo News aggregator piece](https://biggo.com/news/202506111924_s5cmd_Performance_Claims_Tested).
  Plausibly the same event; we have read neither and record no outcome. Directly
  relevant to this study's own credibility — it is exactly what will happen to
  our numbers, and it is why we wrote the comparison plan down before running it. See
  [`../tools/s5cmd/`](../tools/s5cmd/).
