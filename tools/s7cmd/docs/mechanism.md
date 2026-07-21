# s7cmd — mechanism

Source-anchored architecture of `s7cmd ls`, which is a thin wrapper around
the `s3ls-rs` crate pinned at exactly `=1.0.3`. Anchors below cite two pinned
checkouts (canonical identity in [`../data/tool.json`](../data/tool.json)):
`s7cmd @ d589df7` for the CLI/dispatch wrapper, `s3ls-rs @ bf42067` for the
listing engine itself. Every anchor here was re-checked against the pinned
source in a 2026-07-17 cross-model review and is stated as current truth;
correction history lives in each claim's `disposition` field and in
[`../research/codex-review.md`](../research/codex-review.md). Nothing below was
re-derived for this page. Full derivation and additional context:
[`../research/report.md`](../research/report.md) §2-§6, §8-§9.

Every behavioral claim carries an evidence label: `[SRC file:line @ sha]`
anchors to the pinned checkout named by its SHA, `[RUN]`/`[OBS]` are direct run
records or observations, `[INFERRED]` is a reasoned inference, and `[DOC]` is
upstream docs. References of the form claim `some-id` resolve in the canonical
ledger, [`../data/claims.json`](../data/claims.json); statuses use the canonical
vocabulary (`supported`, `confirmed`, `unverified`, `unverifiable`).

## The pipeline

`s3ls-rs` connects its stages with bounded async `tokio::mpsc` channels
(queue size `--object-listing-queue-size`, default 200000)
[DOC README §How it works][SRC s3ls-rs src/pipeline.rs:57-65 @ bf42067;
config/args/mod.rs:23-29,509-511 @ bf42067 (default value declaration)]. The
source's own diagram draws three async stages, one of which — **Lister** —
internally combines two components (the S3 page-fetcher and the
filter-applying `ObjectLister`, `src/lister.rs`) into a single pipeline box.
Read at that finer grain, there are four conceptual stages: page-fetch,
filter, aggregate, display — collapsed to three tokio tasks/channel hops
because the fetcher runs as an inner task spawned and joined by the
`ObjectLister`:

```
[Lister: page-fetch + filter chain] -> channel -> [Aggregator] -> channel -> [DisplayWriter] -> stdout
   parallel prefix discovery              sort (or stream)          format + write
```

- **Lister.** The storage layer (`ListingEngine` in `src/storage/s3/mod.rs`)
  issues the S3 API calls; `ObjectLister` (`src/lister.rs`) applies the filter
  chain inline, and entries that fail a filter are dropped before the
  aggregator ever sees them [SRC s3ls-rs src/lister.rs:45-68 @ bf42067]. The
  internal receive side of the fetcher's own channel is dropped **before**
  `ObjectLister` joins the storage task — a deliberate deadlock-avoidance
  pattern, pinned by a named regression test,
  `lister_does_not_deadlock_when_cancelled_with_full_queue`
  [SRC s3ls-rs src/lister.rs:70-74,161 @ bf42067].
- **Aggregator** buffers-and-sorts by default, or (`--no-sort`) forwards each
  entry immediately [SRC s3ls-rs src/aggregate.rs:33-83 @ bf42067] — `run()`
  dispatches to `run_streaming` vs `run_aggregate` at `:33-37`. `pipeline.rs`
  only wires the `AggregatorConfig` through (`:145-156`); it does not itself
  implement buffering, streaming, or sorting.
- **DisplayWriter** picks one of four formatters (aligned / TSV / one-line /
  JSON) once, and writes to a `BufWriter<Stdout>`
  [SRC s3ls-rs src/pipeline.rs:170-186 @ bf42067].
- Error precedence is display > aggregator > lister
  [SRC s3ls-rs src/pipeline.rs:100-107 @ bf42067]; the cancellation itself —
  the `cancellation_token.cancel()` calls that actually stop the pipeline —
  happens earlier, in the code that awaits each stage's handle and in the
  lister [SRC s3ls-rs src/pipeline.rs:68-97 @ bf42067].
- `s7cmd` is a one-shot CLI: SIGINT cancels via a `PipelineCancellationToken`
  and exits 0, with no persisted state [SRC s7cmd src/ls_bin/mod.rs:52-88 @
  d589df7][DOC s3ls-rs README §Non-Goals: no daemon]. No resume/checkpoint of
  any kind exists.

## Parallel discovery and flat drain

The key code is `ListingEngine` in `s3ls-rs src/storage/s3/mod.rs @
bf42067`.

**Parallel vs sequential decision** [SRC s3ls-rs src/storage/s3/mod.rs:409-413]:
```
use_parallel = max_parallel_listings > 1
            && delimiter.is_none()                 // i.e. recursive (-r) mode
            && (!express_one_zone || allow_flag)
```
- **Non-recursive** always sets `delimiter = "/"` [SRC ...:783-787] — a single
  **sequential** paginated `ListObjectsV2` at one level, returning objects +
  `CommonPrefixes` (the `PRE` rows).
- **Recursive** (`-r`) sets no delimiter — **parallel** listing (unless
  `--max-parallel-listings 1` or a flat/Express-One-Zone bucket forces
  sequential).

**Parallel algorithm** — recursive prefix discovery via a `tokio::task::JoinSet`
bounded by an `Arc<Semaphore>` sized `max_parallel_listings`; acquisition
happens at dispatch and again per spawned task, and the semaphore itself is
constructed once [SRC s3ls-rs src/storage/s3/mod.rs:409-427 (dispatch
acquisition), :701-727 (per-task acquisition), :825 (construction) @ bf42067]:

1. **Discovery phase** (recursion depth <= `max_parallel_listing_max_depth`,
   default 2 [SRC config/args/mod.rs:24,506-507 @ bf42067]): paginate the
   current prefix **with `delimiter="/"`**, emit objects at that level
   immediately, collect sub-prefixes [SRC ...:596-674].
2. Each sub-prefix is spawned as a task that acquires its own semaphore
   permit and recurses; the parent **drops its own permit before spawning
   children** — the deadlock-avoidance rule (a parent must not hold a
   worker-semaphore permit while blocked waiting on children that also need
   permits) [SRC ...:677,701-727].
3. **Listing phase** (recursion depth > `max_parallel_listing_max_depth`):
   drop the delimiter and switch to `list_sequential` — a full no-delimiter
   pagination of that leaf prefix [SRC ...:581-586].

So the keyspace is divided by **delimiter-based common-prefix recursion to a
fixed depth, then a flat sequential drain of each leaf** — not bisection, not
cut-points. **Consequence** (documented and source-supported): a bucket with
**no `/` hierarchy** discovers zero sub-prefixes at the delimiter-discovery
loop that would otherwise populate `all_sub_prefixes`, and the whole listing
collapses to sequential pagination [DOC s3ls-rs README §High performance]
[SRC s3ls-rs src/storage/s3/mod.rs:590-674 @ bf42067 (the delimiter-discovery
loop; finds no sub-prefixes on a flat keyspace)] (claim
`flat-bucket-collapses-to-sequential`). Unmeasured at smoke scale — the
registered smoke bucket is hierarchical and the flat-keyspace regression is
`unverified`, handed to the benchmark phase (claim
`flat-bucket-speedup-unmeasured`).

Two distinct "depth" concepts exist and are easy to conflate:
`max_parallel_listing_max_depth` (internal fan-out depth, above) versus the
user-facing `--max-depth` (a content depth limit that synthesizes a
`CommonPrefix` entry at the boundary instead of recursing further)
[SRC s3ls-rs src/storage/s3/mod.rs:333-350,682-699 @ bf42067] — smoked and
confirmed: `--max-depth 1` at the bucket root makes exactly 1 API call and
emits `PRE` rows at the boundary without recursing (claim
`max-depth-one-emits-pre-one-call`)
[RUN ../receipts/smoke/max-depth/root].

## Pagination and anti-stuck-token bails

One `ListObjectsV2` per page for object listing; `ListObjectVersions` for
`--all-versions`. Continuation via `next_continuation_token` (objects) or
`next_key_marker`/`next_version_id_marker` (versions)
[SRC s3ls-rs src/storage/s3/mod.rs:146-153,238-245 @ bf42067]. Two robustness
guards, both a deliberate `bail!` rather than looping forever: a truncated
page with **no** continuation token, or the **same token twice** in a row
[SRC s3ls-rs src/storage/s3/mod.rs:506-522,637-666 @ bf42067] — source-
supported, not runtime-triggered against real S3, which is well-behaved.
`max_keys` default 1000 (= the S3 maximum) [DOC].

**Parallelism is listing-only.** The parallel machinery is entirely inside
the lister; there is no per-object work fan-out. The only AWS SDK calls in
the storage layer are `.list_objects_v2()`
[SRC s3ls-rs src/storage/s3/mod.rs:93 @ bf42067] and
`.list_object_versions()` [SRC s3ls-rs src/storage/s3/mod.rs:175 @ bf42067]
— `s3ls`/`s7cmd ls` never issues `HeadObject`/`GetObject` (absent from that
file on inspection) [DOC s3ls-rs README §API request calculation].

## Retry model

SDK **standard** retry mode, `max_attempts` default 10, initial backoff
100 ms [SRC s3ls-rs src/storage/s3/client_builder.rs:154-160 @ bf42067]
[DOC help]. No operation/connect/read timeout is set unless the user passes
one — `build_timeout_config` returns `None` when all four are unset
[SRC s3ls-rs src/storage/s3/client_builder.rs:164-191 @ bf42067].
**Stalled-stream protection is ON by default**
(`--disable-stalled-stream-protection` to turn off)
[SRC s3ls-rs src/storage/s3/client_builder.rs:36-46 @ bf42067]. `s3ls-rs`
does not implement its own retry loop; it configures the AWS SDK's retry
strategy and lets the SDK own it.

Any stage error cancels the whole pipeline
[SRC s3ls-rs src/pipeline.rs:68-97 @ bf42067]. Exit-code mapping is **not**
in `s3ls-rs`'s pipeline at all — `pipeline.rs` never touches process exit
codes. The real mapping lives in `s7cmd`'s own dispatch/wrapper: argument
errors from `Config::try_from` map to exit 2, and `ls_bin::run` maps
pipeline/SDK errors through `exit_code_from_error` (default 1)
[SRC s7cmd src/dispatch.rs:61-76 @ d589df7; s7cmd src/ls_bin/mod.rs:66-77 @
d589df7; s3ls-rs src/types/error.rs:33-37 @ bf42067].

## Memory model

Default sorted mode: the **Aggregator buffers every entry** into a `Vec`
before sorting or emitting anything (~700-860 B/object + ~15 MB baseline)
[DOC s3ls-rs README §Low memory usage][SRC s3ls-rs src/aggregate.rs:33-83 @
bf42067]. `--no-sort` streams at a near-constant ~84 MB regardless of count
[DOC]. Ordering: default output is sorted by key in the aggregator; parallel
tasks emit interleaved, so order is only guaranteed after the sort.
`--no-sort` emits in arrival order (lexicographic within one listing op,
interleaved across parallel ops)
[DOC s3ls-rs README §Streaming mode][SRC s3ls-rs src/aggregate.rs:33-83
(streaming vs buffering), :144-168 (sort implementation) @ bf42067]. The
study's verifier is order-insensitive, so both sorted and `--no-sort` output
were accepted in smoke.

Parallel sort (`rayon::par_sort_by`) kicks in only past
`--parallel-sort-threshold` (default 1,000,000 — declared in
[SRC s3ls-rs config/args/mod.rs:26,523 @ bf42067], switch implemented in
[SRC s3ls-rs src/aggregate.rs:163-168 @ bf42067]); below the threshold,
stdlib stable `sort_by` runs instead (claim `sort-uses-rayon-past-threshold`).
Smoke (2,549-148,917 keys) never crosses the threshold — the resulting step in
the latency curve at 1M keys is `unverified`, handed to the benchmark phase
(claim `sort-threshold-latency-step-unmeasured`). Smoke's own numbers: 120.8 MB
peak RSS at 148,917 keys sorted [RUN ../receipts/smoke/recursive-tsv/full]
(claim `full-bucket-smoke-peak-rss`), consistent with the linear model but not
itself a measurement of it at scale (claim `memory-scaling-unmeasured`);
`--no-sort` peaked marginally lower than sorted at 2,549 keys
(22.4 vs 23.1 MB) [RUN ../receipts/smoke/recursive-tsv-nosort/normals-hourly,
../receipts/smoke/recursive-tsv/normals-hourly] — the documented ~9x gap
appears only near the ~1M-key threshold, a scale hypothesis, not smoke-
settled.

## The api_calls counter

An internal `AtomicU64` is incremented **once, immediately before** each call
to `fetch_page` — in both `list_sequential` and `list_with_parallel`
[SRC s3ls-rs src/storage/s3/mod.rs:466,604 @ bf42067]. That makes it a count
of **logical page-fetch operations**, not a direct count of wire-level S3
HTTP requests. Logged at completion as
`Listing pipeline completed api_calls=N` under `-vv` (debug)
[SRC s3ls-rs src/pipeline.rs:109-112 @ bf42067]. The client is built with the
AWS SDK's standard retry mode, up to 10 attempts by default (see Retry model
above), so **a single counted page fetch can correspond to more than one
chargeable HTTP request** under retries — claim `api-calls-is-page-fetch-count`,
a `corrected` scoping re-checked against the pinned source (further citing
locations in `../research/report.md` §5 metrics, §8 request-behavior, §10 open
question 8). Establishing an exact wire-level request count needs SDK tracing or
an external counter — flagged as a natural hand-off to the study's Phase 2
replay-server instrument, not something `api_calls` itself can supply.

Smoke corroborates the counter as a **page-fetch** signal, not a request
count: the full-bucket recursive run took **204** counted page fetches
against an **~149-page sequential floor** (148,917 keys / 1000 max_keys) —
the documented effect of delimiter-discovery pages mixing objects and
prefixes on top of the leaf drain (claim `full-bucket-took-204-page-fetches`)
[DOC s3ls-rs README §API request calculation][RUN
../receipts/smoke/recursive-tsv/full]. Leaf-vs-branch prefix shape shows in the
counts too: a depth-3 leaf prefix (9,839 keys) took only 10 calls (~pure
sequential drain), while a shallower prefix (15,625 keys) took 19
[RUN ../receipts/smoke/recursive-tsv/normals-annualseasonal-1981-2010-access,
../receipts/smoke/recursive-tsv/normals-monthly-1991-2020]. Shallow and
`--max-depth 1` listings at the bucket root each make exactly 1 API call —
confirming the boundary-synthesis path emits `PRE` without recursing
(claim `max-depth-one-emits-pre-one-call`)
[RUN ../receipts/smoke/max-depth/root, ../receipts/smoke/shallow-tsv/root].

## all-versions output contract

`-r --all-versions` switches the API call to `ListObjectVersions`, adding
`VersionId` and possible delete-marker rows to the output
[SRC s3ls-rs src/storage/s3/mod.rs:156-246 @ bf42067]. The **`IsLatest`**
column is *not* part of that by default: it requires the separate
`--show-is-latest` flag, which itself `requires = "all_versions"`
[SRC s3ls-rs config/args/mod.rs:303-312 @ bf42067], and the aligned
formatter only emits the `IS_LATEST` column when
`opts.show_is_latest && obj.version_id().is_some()`
[SRC s3ls-rs display/columns.rs:163-172 @ bf42067]. The committed smoke
receipt's TSV payload has `VersionId` but no `IsLatest` column, consistent
with this — smoke did not pass `--show-is-latest`.

**Versioned-bucket fidelity is deferred, not settled.** The registered smoke
bucket (`noaa-normals-pds`) is not itself a versioned bucket in the sense
that matters here: every object has a single `null` version, so
`all-versions` mode cannot exercise genuine multi-version collapse,
delete-marker rows, or `IsLatest` semantics against it. The study's
`normalize.sh` contract and verifier are keyed on `key` alone (no version
axis), which is why the `all-versions` smoke run **PASSED** — there was
nothing to collapse. `EDGE_BUCKET=none` for this groundwork pass, so a
genuinely versioned/edge bucket was never exercised; a version-aware
manifest and a versioned reference bucket are prerequisites for validating
`all-versions` fidelity, and that work is handed to the benchmark phase.

## Container and process notes

- **Express One Zone**: detected via a `--x-s3` bucket-name suffix; parallel
  listing on such buckets is rejected by default (`CommonPrefixes` pollution
  during multipart uploads), opt in with
  `--allow-parallel-listings-in-express-one-zone`
  [DOC s3ls-rs README §S3 Express One Zone][SRC s3ls-rs src/storage/s3/mod.rs:302-303,409-413
  @ bf42067]. Not testable on the registered smoke bucket (not Express One
  Zone).
- `s7cmd`'s own process-level wrapper diverges from a stock `s3ls-rs`
  consumer: `src/ls_bin/mod.rs` documents dropping upstream's
  `load_config_exit_if_err` helper (which called `std::process::exit`), so a
  bad `Ls` config inside a `batch-run` script doesn't kill the whole batch
  [SRC s7cmd src/ls_bin/mod.rs:1-12 @ d589df7]. The same file's header
  comment claims it was vendored from `s3ls-rs@0.4.1` — a stale comment; the
  actual pinned dependency is `=1.0.3` [SRC s7cmd Cargo.toml, src/ls_bin/mod.rs:1
  @ d589df7].
