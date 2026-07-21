# Swath — mechanism

Source-anchored architecture, consolidated from the groundwork report
(`../research/report.md`) and its critical cross-check (`../research/codex-review.md`,
12 review items addressed). Evidence labels are carried through exactly as they stand
post-review: `[DOC]` docs, `[SRC file:line @ sha]` pinned source, `[RUN receipt]`
a committed smoke run, `[OBS]` observed but not wrapper-recorded, `[3P]`
third-party, `[INFERRED]`. Pinned commit for every `[SRC]` anchor: **`f1009db`**
(`f1009db599861a7e905a539778d915f1bb5426eb`). References of the form claim
`some-id` resolve in the canonical ledger, [`../data/claims.json`](../data/claims.json),
and use the canonical status vocabulary (`unverified`, `unverifiable`,
`supported`, `confirmed`); review-round labels stay in `../research/`.

This page reorganizes the existing groundwork; it adds no new findings. In
particular, source reading here **never** promotes a claim past `unverified`
(per `AGENTS.md`), and any Swath performance number in the checkout is `[DOC]`
self-published, never comparative evidence.

## Parallel scan engine

`swath list` always drives a single work-stealing parallel-scan engine
(`WorkStealingScan`) — there are no alternate list strategies and no router.
`STRATEGY_WORK_STEALING` is the only strategy the command emits `[DOC
docs/design/algorithms.md:12-20]` `[SRC ListCommand.java:62-63]`, and every run
logs `strategy=WORK_STEALING` `[RUN receipts/smoke/recursive-tsv/full]`.

**Parallelism is of the LISTs themselves**, not just of transfers or output.
Idle workers become *thieves* that steal the upper half of the busiest peer's
range by probing a midpoint key (`ListObjectsV2 start_after=m max_keys=1`) and
atomically splitting the range at a page boundary `[DOC algorithms.md:146-234]`.
Confirmed at runtime on the full-bucket run: `peak_in_flight=8` at
`--max-parallel-listings 8`, `splits=7`, `steals=98` `[RUN
recursive-tsv/full]` (Swath's own counters — see § Counters; claim
`full-run-reported-parallel-listings`). **Behaviour is scope-dependent, not
uniform:** the 2,549-key `hourly` run still reached the same `peak_in_flight=8`
cap but did so entirely through steals (`splits=0`), while the un-seeded
`seed-none` runs peaked lower — `peak_in_flight=6` (full) / `4` (monthly) `[RUN
hourly, seed-none/*]`. So splitting is scope-dependent and the un-seeded path
reaches lower peak concurrency; a small scope does **not** by itself mean less
parallelism (`hourly` hit the cap) — claim `peak-concurrency-is-scope-dependent`.
Whether the full T=8 ratio holds at higher `T` and larger scopes is unverified
(claim `parallelism-ratio-at-higher-concurrency`), benchmark phase; the broader
"adaptive, density-aware" characterization is likewise `unverified` (claim
`listing-is-adaptive-density-aware`).

## The range model

The keyspace is tiled into adjacent half-open key intervals `(A, B]`. A worker owns
`(A, B]`: it issues `ListObjectsV2` with `start-after = A` and emits every
returned key `k` with `A < k <= B`; the boundary key belongs to the **left**
interval, which is the key no-gap/no-overlap invariant designed to yield
exactly-once output with no separate deduplication pass (claim
`no-dedup-pass-by-construction`) `[DOC algorithms.md:53-70]`. Keys are handled as **raw bytes** (`KeyBytes`,
unsigned-byte lexicographic order) end-to-end, because S3 orders keys in UTF-8
binary order, which diverges from Java `String`/UTF-16 order for supplementary
code points `[DOC algorithms.md:25-46]`.

**This internal tiling is `unverified`** (claim `internal-tiling-is-disjoint`).
Smoke settles the *output* (no missing/extra/duplicate rows for the tested runs;
claim `smoke-output-complete-no-duplicates`); it does **not** establish that the
internal ranges were disjoint, that no-gap/no-overlap "falls out by
construction," or that the invariant holds for other keyspace shapes. The
invariant is a `[DOC]`/`[SRC]` design claim, not a receipt-settled one — the
critical cross-check explicitly removed a smoke-to-tiling promotion, and it is not
reinstated here.

## Keyspace division

The default cut is a **byte-midpoint** pivot computed over Unicode code points
(`ByteMidpoint`), constrained to a "safe set" of scalars so the synthesized
boundary is valid UTF-8 **and** an XML-1.0-legal `start-after` that real S3 will
not 400 on `[DOC algorithms.md:235-380]`. Runtime **mass-aware sampling** exists
and is **default-on** (`mass_aware_seed`), and an empirical-CDF
`--seed-scatter-scout` is experimental `[DOC usage.md:322-389]` `[SRC
ListCommand.java:257-281]`.

The nuance the review pinned down (claim `sampling-replaces-blind-midpoints`):
the inherited "samples the keyspace rather than blind midpoints" **overstates the
default** — the base pivot *is* a byte-midpoint bisection, with density-reflected
placement and demand-driven stealing layered on, and mass-aware sampling running
alongside it. Both mechanisms **coexist**; neither replaces the other.
`mass_aware_seed` + `radix_bands` + demand-driven `structure_probes` handle
dense/flat regions `[DOC usage.md:346-378]`.

The seed step (`--seed shallow`, default) runs one `delimiter=/` pass to discover
top-level prefixes and create initial parallel ranges `[DOC usage.md:322]` `[SRC
ListCommand.java:257-260]`. `--seed none` skips it (single root range,
parallelized by stealing alone); `--seed hints` **throws — unimplemented**, so
there is no Swath-hinted mode (claim `seed-hints-unimplemented`) `[SRC
ListCommand.java:257-260]` `[DOC usage.md:322]`. On this flat-root corpus the
seed *reduced* total calls (339 shallow vs 516 none; claim
`seed-cost-direction-at-smoke`) — see [`running.md`](running.md).

## Request-level behaviour

- **One `fetchPage` = one `ListObjectsV2` call** `[SRC S3PageFetcher.java:56,157,215]`,
  AWS SDK v2 **sync** client. `encoding-type=url` is always set; the SDK's own
  interceptor url-decodes the response and Swath re-encodes the decoded string to
  raw bytes via UTF-8 (a single decode, not double) `[SRC
  S3PageFetcher.java:53-56,162,694-695]`.
- **Pagination is by `start-after = last emitted key`, not the continuation
  token** — "there is no `continuation_token` in the model" `[DOC
  algorithms.md:129-133]` `[SRC S3PageFetcher.java:173-175]`. The key is the
  single source of truth for both pagination and resume: it survives token
  expiry and re-splitting, and is portable across hosts. This is the crux design
  choice — it is what makes ranges splittable and resume portable, but it also
  creates the documented XML-illegal-key pagination limitation (below).
- **Page size:** `maxKeys=1000`, **hardcoded** as the S3 page cap `[SRC
  ListCommand.java:381]` (`int pageMax = 1000; // S3 page cap`). Not a CLI knob —
  and 1,000 is S3's own ceiling, so no real-S3 client exceeds it.

## AIMD and retries

- **The AWS SDK's internal retry is disabled** (`maxAttempts=1`) `[SRC
  S3Config.java:66-71]` `[SRC S3ClientFactory.java:23,105-107]` — Swath's own
  gauge-aware loop is the **sole retrier**, so the AIMD controller sees every
  real 503 immediately. This choice matters to retry behavior.
- Transient classes (503 SlowDown, 5xx, attempt-timeout, network,
  socket-closure) are individually classified and retried `[SRC
  S3PageFetcher.java:300-478]`. Specific permanent failures get a **typed** fatal
  subtype only on an exact `(status, code)` pair — `(403,AccessDenied)`,
  `(404,NoSuchBucket)`, `401`, `301 PermanentRedirect` `[SRC
  S3PageFetcher.java:633-671]`; **any other 4xx** falls through to the generic
  fatal `ListingException` arm — not every 403/404 is specifically typed (claim
  `error-classification-is-specific`).
- **Timeouts:** per-attempt `apiCallAttemptTimeout=10s` (worker pages), a shorter
  `3s` for speculative probe calls, and an overall per-call `apiCallTimeout=60s`
  liveness ceiling `[SRC S3Config.java:48,59,65]`. In-JVM watchdogs bound a
  wedged run: `--stall-timeout` (120s no-progress) and `--no-progress-timeout`
  (10m active-but-zero-progress) turn a hang into a resumable exit-75 `[SRC
  ListCommand.java:129-148]`.
- **AIMD:** live concurrency `T` is a resizable permit gauge in `[1,
  --max-parallel-listings]`. Slow-start ramps `min(4,Tmax)` multiplicatively
  until the first congestion signal, then additive `+1` per clean 10s window;
  decrease `T:=max(1,floor(0.7·T))` on a 503/`Retry-After`, plus a distinct
  sustained-attempt-timeout shed `[DOC algorithms.md:752-831]` `[SRC
  S3PageFetcher.java:300-331]` (claim `aimd-adapts-to-503`). Observed ramp to
  `peak_in_flight=8` with `aimd_votes=0`, `throttle_events=0`, `errors=0` on this
  clean public bucket `[RUN]` — the controller **never engaged** (claim
  `aimd-idle-at-smoke`). Whether it is therefore dead weight is a
  scale/concurrency question smoke (T≤8) cannot reach; `unverified` (claim
  `aimd-necessity`).

## Resume design — SQLite checkpoint (not crash-tested)

Crash/Ctrl-C resumable via an SQLite checkpoint whose `listing_node` table **is**
the worklist; a single checkpoint-writer thread serialises `commitPage` and the
CAS-guarded `splitTxn` (SQLite WAL is single-writer) `[DOC
algorithms.md:611-748]` (claim `checkpoint-resume-design-exists`). Parquet resume
is **exactly-once** (finalized parts never rewritten; re-list from
`durable_cursor`); stdout/text is **at-most-once** `[DOC usage.md:477-481]`.
`--checkpoint none` runs the identical engine against an ephemeral in-memory
store (not resumable) `[SRC ListCommand.java:244-249]`; smoke used `--checkpoint
none` for the text modes.

**`unverified`** (claims `crash-resume-works`, `exactly-once-under-crash`). This
is a design read only — no crash/SIGKILL/resume run was performed, so neither
resumability nor crash-path exactly-once is receipt-settled. Routed to the
benchmark queue.

## Output formatters

Four formatters: `parquet | jsonl | tsv | aligned`; default **aligned on a TTY,
jsonl otherwise** `[SRC OutputFormat.java:11-13]`.

| Formatter | Fields emitted | Anchor |
| --- | --- | --- |
| tsv | `key/size/last_modified/etag/storage_class/row_type` | `[SRC TsvFormatter.java:19]` |
| jsonl | one JSON object/line, all fields | `[SRC JsonlFormatter.java:33-63]` |
| aligned | fixed-width `size/time/key` **only** (no etag/storage_class) | `[SRC AlignedFormatter.java:36-61]` |
| parquet | multi-part Parquet dataset dir (`-o DIR`); `--sort` adds a staged k-way merge to globally key-sorted parts | `[DOC usage.md:66-232]` |

Control-character escaping is on by default for text sinks (`--raw-output`
disables); JSONL is always validly escaped regardless `[SRC
TsvFormatter.java:53-55]` `[SRC JsonlFormatter.java:13-17]` — so byte-exact key
fidelity holds only for control-character-free keys (claim
`text-sink-key-fidelity-ascii-only`). Timestamps render via
`DateTimeFormatter.ISO_INSTANT` (`Fields.isoMicros`); S3 `LastModified` is
second-precision, so the value is `YYYY-MM-DDTHH:MM:SSZ` with no fractional part
`[SRC Fields.java:13-21]` — the aligned adapter's fixed-column parse relies on
that width, and a sub-second `ISO_INSTANT` would shift columns (claim
`aligned-fixed-column-timestamp-assumption`; safe for S3 today). ETag quotes are
stripped; multipart `hex-N` kept verbatim `[SRC S3PageFetcher.java:714-722]`.
Swath emits **full keys** (not path-relative).

Swath exposes **no `ls`-style delimiter/CommonPrefix *output* mode**: `swath
list` always fully enumerates objects; `delimiter=/` is used only internally
(seeding, structure probes) — claim `no-shallow-listing-mode`. The `inspect`
(shape probe) and `diff` subcommands appear in `--help` but are **stubs**
printing *"not yet implemented"* `[SRC InspectCommand.java:25]` `[SRC
DiffCommand.java:28]` (claim `inspect-diff-are-stubs`).

## Counters — self-reported, a caveat not a promotion

Swath exposes a strong native counter surface on stderr (at `-v`): a
`list_run_summary` line with `objects`, `api_calls`, `api_calls_per_1k_objects`,
`pages`, `peak_in_flight`, `steals`, `splits`, `errors`, `keys_per_sec`,
`peak_rss_bytes`, `peak_heap_bytes`, `cpu_seconds`, `cpu_efficiency`; and a
`list_run_diagnostics` line with `steal_reasons{}`, `probe_fetches`,
`empty_upper_bisections`, `throttle_events`, `transient_events` `[RUN]` `[DOC
usage.md:555-573]`. Micrometer meters and optional OTLP export exist; a
Prometheus scrape port is v1.1-planned `[DOC usage.md:576-592]`.

**Caveat (review-downgraded, kept downgraded):** these are the tool's **own
self-reported** counters. They are what the smoke observations rest on
(`peak_in_flight`, `splits`, `steals`, `api_calls`), and they are internally
consistent with the manifest-verified output — but they are not an independent
wire-level measurement. Treat them as the tool's account of its own behaviour;
the study's replay-server phase is where request shape gets captured
independently. This caveat is owned here and cited tersely by the runtime claims
in [`../data/claims.json`](../data/claims.json).

## Memory model

Streaming — "bounded by configuration, not bucket size"; no
accumulate-then-dump, memory bounded by queue sizes and the Parquet writer pool
`[DOC README.md:22-23]` `[DOC usage.md:604-617]`. Smoke peak RSS held ~320–560 MB
across all runs regardless of scope size `[RUN]` — JVM baseline dominates at this
scale; **not a scale claim**. Sort-memory and bounded-memory behavior at scale
are untested in this study and stay `unverified` (claim `bounded-memory-at-scale`).
They must be reproduced under this harness before their growth or failure
behavior is classified.

## Endpoint quirks / capability limitation

`%`/C1/noncharacter code points are excluded from *synthesized* pivots for
LocalStack/MinIO and real-S3 XML compatibility `[DOC algorithms.md:242-269]`.
**Capability limitation:** buckets whose real keys contain XML-illegal control
bytes cannot be fully paginated via `start-after` (the cursor itself would 400) —
documented, mitigation is future `ContinuationToken` work `[DOC
algorithms.md:382-387]`. Not exercised here (noaa-normals-pds keys are clean
ASCII; `EDGE_BUCKET=none`; claim `control-char-key-fidelity-untested`).

## Source anchors

Primary, pinned checkout @ `f1009db`: `ListCommand.java` (engine selection,
seed, page cap, watchdogs, credentials), `S3PageFetcher.java` (one call per
fetch, `start-after` pagination, error classification, ETag handling),
`S3Config.java` / `S3ClientFactory.java` (retries disabled, timeouts),
`docs/design/algorithms.md` (range model, stealing, AIMD, resume), `docs/usage.md`
(seed modes, formatters, counters, memory and output behavior), the four output formatters
(`TsvFormatter`, `JsonlFormatter`, `AlignedFormatter`, `Fields`, `OutputFormat`),
`InspectCommand.java` / `DiffCommand.java` (stubs), `THIRD_PARTY_NOTICES.md`
(dangling LICENSE ref; claim `no-license-dangling-reference`). Full list in
`../research/report.md` §11.
