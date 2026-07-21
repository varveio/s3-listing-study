# s3-fast-list

> **Historical landing page (2026-07-20, capsule migration).** This is the full
> pre-restructure landing page. Any `current-state` wording below is historical
> as of the date it records and is superseded by the root README and `data/`.
> Only this banner and link targets changed; body prose and evidence
> qualifications are preserved.

**Status: partially run (groundwork, 2026-07-17) — current-state page.** This
page was rebuilt from the groundwork pass's mixed-provenance tool page into a
clean current-state summary (not new research — every claim below was already
receipt- or source-backed before this rewrite). Full detail lives in three
companion documents: [`mechanism.md`](../docs/mechanism.md) (source-anchored
architecture), [`running.md`](../docs/running.md) (build, every smoked/blocked mode,
the harness incompatibility and direct-capture procedure, arch matrix), and
[`research/`](./) (the source-and-run [`report.md`](report.md),
the row-by-row [`reconciliation.md`](reconciliation.md), and the
two-round [`codex-review.md`](codex-review.md) whose source-anchored
**Round 2** corrections this page reflects). These preserved research files may
use the project's older terminology; this page is the current summary.

## What we saw

**We smoked Varve's fork, not upstream.** The built image is the fork branch
`feat/no-sign-request @ 6c72f59` = upstream
`b11e385` (current upstream `main` HEAD) **+ a 51-line `--no-sign-request`
patch** [SRC main.rs:56-58, core.rs:661-664 @ 6c72f59]. The patch is **not
merged upstream; contribution pending.** The planned benchmark phase uses what
upstream ships, so every smoke fact here is fork-based groundwork and should be
revisited if the patch merges.

**We only ran the plain serial `list` mode.** Its run facts (builds, runs, and
reaches the public bucket unsigned) are receipt-backed. **Everything else stays
`VERIFIED: no`:** the `-k` hinted/**parallel** path (the tool's entire value
proposition), the throughput/scaling ladder, `diff` mode, `ks-tool`, and all
scale hypotheses. And **listing correctness itself is not promoted out of
`VERIFIED: no`** — the shared harness's `docker logs` capture corrupts this
tool's binary Parquet, so the standard verifier returned **BLOCKED** for every
run and correctness rests only on a labelled **[OBS] manifest-diff** against
faithful direct captures (see [`running.md`](../docs/running.md) and the
harness-capture caveat below).

|  |  |
|---|---|
| **Repo** | <https://github.com/aws-samples/s3-fast-list> — canonical (AWS Samples org), confirmed sole home, not a fork of anything else [DOC] |
| **Language** | Rust (99%+); Cargo workspace, resolver 2 [SRC Cargo.toml @ 6c72f59] |
| **License** | MIT-0 (MIT No Attribution) [SRC LICENSE @ 6c72f59] |
| **Crate versions** | `s3-fast-list` 1.1.0, `ks-tool` 1.2.0 [SRC @ 6c72f59] |
| **Pinned checkout** | `6c72f596e2ffe7311dec8cb7de29b114c0251207` — fork branch `feat/no-sign-request` |
| **Upstream base** | `b11e385ec6e32122aa01b98a3465a99e96df8b09` ("Specify output file paths", 2025-04-20) — current upstream `main` HEAD |
| **Upstream health** | 40 stars, **0 open issues, 0 open PRs**, last commit 2025-04-20 — a quiescent sample project (~15 months stale at the study date) [DOC repo + commits API] |
| **Image** | `s3-fast-list@sha256:6246ee51…` (arm64, built locally from the pinned checkout — see [`running.md`](../docs/running.md)) |
| **Tier** | Tier 1 — included in the planned comparative runs; its hinted mode is an important approach for us to understand |
| **Testability** | Needs a Rust toolchain; upstream ships only a Dockerfile that no longer builds at its pinned SHA (fixed here). Two code paths must both be exercised (serial fallback + `-k` parallel), plus the `ks-tool` companion — only the serial path was reachable through the shared harness. |

## What we tried and saw

- **The main parallel-listing mode needs hints and is off by default.**
  Concurrency engages only when fed a keyspace-hints file, which is
  typically produced by a prior full listing or an S3 Inventory (hand-written
  hints are also accepted). A naive single invocation is an ordinary serial
  paginator [SRC main.rs:191-218, tasks_s3.rs:18-89 @ 6c72f59; CONFIRMED by
  smoke — [OBS] 1 flat-list task / 1 keyspace pair].
- **Serial `list` PASSed the [OBS] manifest-diff, full bucket, 20.06 s / 65.1
  MB.** Full bucket 148,917 = 148,917 keys, 0 missing/extra/field-mismatch/dup,
  and three scoped prefixes clean [OBS `receipts/smoke/list/*`]. **Caveat:** this
  is a direct-capture manifest-diff, **not** a certified verifier PASS — the
  standard verifier was BLOCKED by the harness capturing binary Parquet through
  `docker logs` (below).
- **The `-k` parallel mode was NOT smoked** — the wrapper mounts nothing, so a
  hints input file cannot reach the container. Its mechanism is source-
  established only; a new correctness hypothesis (next bullet) must be settled
  before any hinted correctness claim.
- **NEW, source-derived (via Round 2 review): the range model can silently drop
  keys equal to a cut-point (F1).** `start_after` is exclusive and the boundary
  break fires before insertion, so an object whose key exactly equals a hint is
  fetched by neither adjacent slice — invalidating "correctness regardless of
  balance" for hinted runs. Serial runs (no cut-point) are unaffected [SRC
  tasks_s3.rs:111-114,261-269 @ 6c72f59]. `[SRC]`-hypothesis, not run-proven.
- **NEW, source-derived (via Round 2 review): a fatal slice error can ship a
  partial listing with exit 0 (F2).** A fatal S3 error completes the slice
  normally, dumps what accumulated, and returns exit 0 with no error marker [SRC
  tasks_s3.rs:95-104, data_map.rs:372-376, main.rs:346 @ 6c72f59].
  `[SRC]`-hypothesis; it needs a fault-injection run before we present it as
  something we observed.
- **Upstream's pinned Dockerfile no longer builds at its pinned SHA.** No
  committed `Cargo.lock` + loose semver → transitive deps float forward and
  demand rustc ≥ 1.94.1; `FROM rust:1.86-slim` fails at exit 101 [OBS
  `receipts/smoke/_build/build-rust1.86-FAIL.txt` — a build-log tail, provenance
  header per F5; **partially** settled, not a provenance-complete receipt]. A
  build-reproducibility observation, separate from runtime behavior.

## Notes, questions, and observations

Every claim in the inherited tool page, walked individually against the
source-and-run groundwork. **Nothing here was re-derived** — statuses and supporting records
are carried from [`research/reconciliation.md`](reconciliation.md),
corrected to Round 2 wording where the cross-model review changed a claim's
scope. **Status routing (receipt-backed only):** a claim reaches **Settled by
smoke** only where a committed receipt decides it; **Corroborated** marks a
documentary/source fact (existence, structure, versions) — per `AGENTS.md`,
source reading alone never promotes a *behavioral, correctness, or performance*
claim past **VERIFIED: no** (= **Unaddressed**). **Correctness/completeness is
Unaddressed everywhere** — the verifier was BLOCKED (see caveat below).
**Corrected / Contradicted** mark inherited claims this groundwork found wrong.

### Mechanism (M1-M17)

| # | Inherited claim | Status | Evidence |
| --- | --- | --- | --- |
| M1 | `start_after=A`, stop at first key `>=B`, covers `[A,B)`; disjoint ranges never overlap | **Corrected (F1)** | Partitioning + UTF-8 basis right; coverage is the **open** `(A,B)` (StartAfter exclusive, break pre-insert), slices strictly disjoint [SRC tasks_s3.rs:111-114,261-269] |
| M2 | Cargo workspace, two crates `s3-fast-list` + `ks-tool` | **Settled by smoke** | Both compile in the build receipt [SRC Cargo.toml; RUN `_build`] |
| M3 | N+1 half-open ranges; correctness regardless of balance | **Contradicted (F1, source-derived)** | Ranges open at the cut-points → a key equal to a cut-point is dropped from every slice [SRC tasks_s3.rs:111-114,261-269] |
| M4 | No hints → single serial pair; `-c` no effect; accumulation unconditional ("worst of both worlds") | **Settled by smoke** (serial) / **Unaddressed** (O(bucket) RAM) | [OBS] 1 flat-list task, 1 pair `(start="",end=None)`; RAM-scale not settleable at 19-65 MB smoke [SRC main.rs:191-218] |
| M5 | Three cut-point sources (prior `.ks`; Inventory; hand-written) | **Corroborated** (existence) / **Unaddressed** (ks-tool internals) | [DOC README; SRC ks-tool/main.rs:15-39] |
| M6 | Custom multi-thread runtime; `JoinSet` of 3 (List)/4 (Diff) tasks | **Corroborated** | [SRC main.rs:28-30,248-345] |
| M7 | `tokio::sync::Barrier` synchronizes start | **Corroborated** | [SRC core.rs:455-461,509-511] |
| M8 | Hand-rolled reactor (vector, not Semaphore); `-c` default 100 | **Corroborated** | [SRC tasks_s3.rs:18-89] |
| M9 | Per-pair loop; 5 s page timeout; `next_start`; unbounded mpsc; errno `<0x10`/`>=0x10` | **Corrected (F2)** | All right, but "fatal" = `ctx.complete()` + normal return → partial output, exit 0 [SRC tasks_s3.rs:95-104,108-136; error.rs:36-37] |
| M10 | Two retry layers (SDK 10/30 s + `next_start`) | **Corroborated** | [SRC core.rs:649-659; tasks_s3.rs:91-106] |
| M11 | `GlobalState` bitmap; polled quit `AtomicBool`; no `CancellationToken`; three atomic error counters, not surfaced prominently | **Corroborated** | [SRC core.rs:485-544, main.rs:242-246]; counters `task_next_stream_timeout`/`s3_client_timeout`/`s3_client_generic_error` [SRC core.rs:490-492] |
| M12 | Two-level accumulation map (`RwLock` outer + `Mutex` inner) | **Corroborated** | [SRC data_map.rs:18-56,173-243] |
| M13 | `ObjectProps` packed, `#[repr(align(8))]` | **Corroborated** | [SRC core.rs:190-206] — field-exact |
| M14 | ETag raw 16-byte MD5 + parts; **panics** on other forms | **Corroborated** (panic exists) / **Unaddressed** (real weird object) | [SRC core.rs:256-264,428-437] |
| M15 | Diff match classification; Rhai filter at match/dump | **Corroborated** | [SRC core.rs:324-410, data_map.rs:104-163] |
| M16 | Output Parquet (…, GZIP6, plain encoding) + `.ks` CSV | **Corrected (F9)** | `.set_encoding(PLAIN)` is a hint — payloads carry PLAIN+RLE+RLE_DICTIONARY [OBS metadata]; rest corroborated [SRC utils.rs:20-93, data_map.rs:78-108] |
| M17 | Rhai AST allowlist; `max_variables(2)`, `max_map_size(2)` | **Corroborated** | [SRC core.rs:59-124,729-755] |

### Modes and tunables

| Inherited claim | Status | Evidence |
| --- | --- | --- |
| `-k` optional; test with and without | **Settled by smoke** (without-`-k`, serial) / **Unaddressed** (with-`-k`, blocked — no container file mount) | [SRC main.rs:36-38; RUN `receipts/smoke/list/*`] |
| `-c` default 100; no effect without `-k` | **Corroborated** | [SRC main.rs:32-34, tasks_s3.rs:32] |
| `-t` default 10 (tokio worker_threads) | **Corroborated** | [SRC main.rs:28-30] |
| `--filter` Rhai (`SOURCE`/`TARGET`; `size`/`last_modified`) | **Corroborated** (not exercised) | [SRC core.rs:49-50] |
| `--endpoint` auto-enables force-path-style, **no override** | **Corroborated (F3)** | Auto-enable + the "no override" half both hold: `--force-path-style` is enable-only, cannot select virtual-hosted style [SRC main.rs:52-54,146-147] |
| List 3 tasks / Diff 4 tasks | **Corroborated** | [SRC main.rs:120-141] |
| `ks-tool split`/`inventory` | **Corroborated** (exist) / **Unaddressed** (internals) | [SRC ks-tool/main.rs:15-39] |
| Parquet + `.ks` only; no alternate output flag | **Corroborated** / partly **Settled by smoke** | No stdout/text listing — smoke routed `--output-parquet-file /dev/stdout` [SRC utils.rs, data_map.rs] |

### Published numbers and estimates

| Inherited claim | Status | Evidence |
| --- | --- | --- |
| Ladder 8214/924/102/32 s at `-c` 1/10/100/1000, 100M-obj, `m6i.8xlarge` | **Corroborated as DOC self-report** / **Unaddressed as measurement** | Vendor self-report; not reproduced, not smokeable (parallel blocked). The **128 GB** figure is the instance-type spec, not in the cited README table (F11) [DOC README + AWS EC2 M6i spec] |
| `ObjectProps` "exactly 40 bytes" | **Corroborated** | [SRC core.rs:190-206] — 8+8+8+16; groundwork's own initial 48 was corrected |
| ~4 GB props / 100M; ETag-as-MD5 ~50% win | **Unaddressed** | [INFERRED] arithmetic; empirical/scale not tested |
| SDK retry 10/30 s; page timeout 5 s | **Corroborated** | [SRC core.rs:30-31,22; tasks_s3.rs:128-130] |
| Parquet GZIP6/100 MiB BufWriter; `.ks` 10 MiB; `ks-tool split` 100 MiB reader | **Corroborated** (first two) / **Unaddressed** (`ks-tool split` reader) | [SRC data_map.rs:104-108,78-101]; the `ks-tool split` reader itself not read |
| Rhai `max_variables(2)`, `max_map_size(2)` | **Corroborated** | [SRC core.rs:733-735] |
| Deps lag (`aws-sdk-s3 1.11`, `aws-config 1.1`, `hyper 0.14`, no upper pins) | **Corroborated** | [SRC Cargo.toml]; no committed `Cargo.lock` |

### Where the approach may fit

All **Corroborated as design descriptions** at the source level (throughput
delivery, packed `ObjectProps`, Parquet/Inventory integration, bidirectional
diff, Rhai sandbox, self-bootstrapping `.ks`, 5 s timeout + `next_start`
resume) [SRC as in Mechanism]. **None is promoted to a performance conclusion** —
the throughput/scaling strength rests on the `-k` parallel path, which was not
smoked; diff mode was not exercised.

### Tradeoffs and questions to test (hypotheses)

| # | Inherited hypothesis | Status | Evidence |
| --- | --- | --- | --- |
| W1 | Unbounded accumulation → OOM on a smaller box | **Corroborated** (mechanism) / **Unaddressed** (OOM at scale) | [SRC data_map.rs:104-163]; smoke 19-65 MB can't reach the cliff |
| W2 | No backpressure (unbounded mpsc) worsens memory at high `-c` | **Corroborated** (unbounded) / **Unaddressed** (scale) | [SRC main.rs:258] |
| W3 | No structured cancellation → sluggish Ctrl-C | **Corroborated** (mechanism) / **Unaddressed** (latency) | [SRC main.rs:242-246, core.rs:558-564] |
| W4 | `panic!` on unexpected branches — non-`hex32[-N]` ETag, out-of-range match-result enum, channel-send failure — crashes the run | **Corroborated** (ETag panic + `None`-code/`KeyCount` asserts located) / **Unaddressed** (real weird object; the match-result-enum and channel-send panic sites not independently located) | ETag panic [SRC core.rs:428-437]; `None`-code + `assert!(contents==KeyCount)` panics [SRC tasks_s3.rs:172-174,254]; `EDGE_BUCKET=none` |
| W5 | Two-layer retry hard to reason under throttling | **Corroborated** (both layers) / **Unaddressed** (throttling) | [SRC core.rs:649-659, tasks_s3.rs:91-106] |
| W6 | `u8` errno `<0x10`/`>=0x10` misclassifies unusual errors | **Corroborated** (threshold) / **Unaddressed** (misclassification) | [SRC error.rs:4-11,36-37] |
| W7 | `block_in_place`/`block_on` smells (no perf claim) | **Corroborated** (sites exist) | [SRC core.rs:666, data_map.rs:61, stats.rs:42,52] |
| W8 | Single unit test in the codebase | **Corroborated** (s3-fast-list crate) / **Contradicted** narrowly (whole codebase) | 1 test [SRC core.rs:821]; but `ks-tool/arn.rs` has 4 [SRC arn.rs:96,112,128,134] |
| W9 | Dependency lag → build failure vs current toolchains | **Partially Settled by build evidence** | `rust:1.86` fails at exit 101; evidence is a build-log **tail** without a full build-context binding (provenance header, F5) — settles the incompatibility, not the exact build context [OBS `_build/build-rust1.86-FAIL.txt`] |
| W10 | Force-path-style auto-on, **no override** | **Corroborated (F3)** | The hypothesis is right — flag is enable-only [SRC main.rs:52-54,147]; earlier groundwork wrongly marked this Contradicted |
| W11 | Ctrl-C mid-accumulation → possibly-inconsistent Parquet | **Corroborated** (mechanism) / **Unaddressed** (kill behavior) | [SRC data_map.rs:367-370] logs "*MAY INCONSISTENT*" |

**Additional weak points:** no client-side rate limiting → **Corroborated**
[SRC core.rs:649-659]; Express One Zone roadmap-only → **Corroborated** [DOC];
"worst regime = small prefix / high fixed overhead" → **Unaddressed** (no
comparative timing at smoke, by design); no crash-resume across runs →
**Corroborated** [SRC — `next_start` in-run only]; non-ASCII/control-char key
handling → **Unaddressed** (`EDGE_BUCKET=none`; the plain-ASCII NOAA keys
round-tripped exactly, [OBS]).

**Additive rows (not from the inherited tool page).** Two ledger entries above —
**M3/M1 corrected coverage (F1)** and **M9's exit-0-partial (F2)** — are new
findings that entered via the Round 2 cross-model review, not the inherited
hypothesis sheet. They are carried below as open hypotheses and marked as
review-entered. The `--force-path-style` correction (F3), the PLAIN-encoding
correction (F9), and the 128 GB attribution (F11) likewise refine inherited
rows per Round 2.

**Cross-cutting claims naming s3-fast-list** live in
`docs/open-questions.md` (out of this page's editing scope); what
groundwork found is recorded in
[`research/reconciliation.md`](reconciliation.md) § "Claims about OTHER
tools / S3 itself" for the orchestrator to route.

## Open hypotheses for the benchmark

Everything the groundwork left `VERIFIED: no`, carried in full with its
provenance. Source reading corroborated the *mechanism* of most of these; none
is a settled *result*.

- **`-k` hinted/parallel path** (inherited) — the tool's headline capability;
  needs an input-file mount into the container. Concurrency sweep `-c ∈
  {1,10,100,1000}` with a real hints file; how effective parallelism tracks
  `-c` and where it saturates; hint granularity (N segments vs speedup) on
  skewed keyspaces; `--threads` vs `--concurrency`.
- **End-to-end cold start** (inherited) — time a full
  end-to-end run on a never-listed bucket with no `-k` file, **including** the
  bootstrap step (a `ks-tool inventory` pull, or an initial no-`-k` pass) needed
  to reach a hinted parallel run, and compare it to the hinted-only number. The
  bootstrap cost belongs in any "time to first usable listing" figure; recording
  the gap between cold-start and hinted-only will make the tradeoff visible.
- **The concurrency ladder** (inherited) — reproduce 8214/924/102/32 s on our
  box, recording our box RAM; vendor self-report only, not smokeable while the
  parallel path is blocked.
- **Memory scaling / OOM** (inherited) — peak RSS vs object count for the
  accumulate-then-dump map; is there a cliff? Smoke peaks 19-65 MB cannot
  answer.
- **`diff` mode, `ks-tool split`/`inventory`** (inherited) — need a second
  bucket / real inputs; not exercised.
- **Cancellation latency, panic surface, throttling behavior, endpoint
  (virtual-hosted-only) behavior** (inherited) — all mechanism-corroborated,
  none run. The panic hypothesis has three inherited surfaces: a weird ETag
  (panic located), an out-of-range match-result enum, and a channel-send failure
  (the latter two not independently located); cancellation *latency* itself is an
  inference, not measured.
- **Edge-key fidelity** (inherited) — unicode/control-char/multipart keys;
  `EDGE_BUCKET=none`. `normalize.sh` must gain binary-safe framing first (the
  current `-list`/tab output cannot represent a Key with a TAB or newline).
- **Cut-point key omission (F1) — entered via Round 2 review, not
  inheritance.** `[SRC]`-hypothesis. Falsify: seed a bucket where an object key
  exactly equals a cut-point in the hints file, run `list -k`, count whether
  that key survives. Expected if it holds: the key is silently missing. Must be
  settled before any hinted correctness claim.
- **Silent incompleteness on a fatal slice error (F2) — entered via Round 2
  review, not inheritance.** `[SRC]`-hypothesis. Falsify: fault-inject a fatal
  error on one slice (deny a prefix, or point one slice at a missing bucket) and
  confirm exit 0 + a partial Parquet with no completeness signal.
- **Harness gaps to close first (both required before the parallel mode can be
  benchmarked):** a binary-safe output capture (bind-mount / `docker cp` /
  attach instead of `docker logs`) so Parquet survives, and an input-file mount
  so a hints file can reach the container. Routed to the orchestrator — see
  [`running.md`](../docs/running.md).
- **Architecture** — confirm amd64 as the common denominator with an actual
  amd64 build (only arm64 was built/run).

## Known caveats carried forward

- **HARNESS CAPTURE INCOMPATIBILITY (correctness is `VERIFIED: no`).** The
  wrapper captures container stdout via `docker logs`, which is not binary-safe;
  this tool emits binary Parquet, so the standard `verify-listing.sh` returned
  **BLOCKED** for every run. Correctness rests on a labelled **[OBS]
  manifest-diff** against faithful direct captures, not a standard verifier
  result.
  Run-facts (exit, wall, RSS, anonymous access) remain valid. Detail +
  provenance: [`running.md`](../docs/running.md) and
  `receipts/smoke/_capability/{HARNESS-INCOMPATIBILITY.txt,direct-capture.provenance.md}`.
- **`normalize.sh` is not binary-safe for exotic keys.** Unquoted DuckDB
  `-list` / tab output cannot represent a Key containing a TAB or newline (F6).
  Unaffected on the plain-ASCII NOAA corpus; blocks the deferred edge-key test.
- **Fork vs upstream.** Everything smoked is the fork build; the benchmark phase
  measures only what upstream ships. Revisit when the `--no-sign-request` patch
  merges.

## Provenance

**Mixed provenance.** This page's summary, metadata, things we tried, and
notes table are **firsthand**: a source read at pinned commit
`6c72f59`, a container build, a serial-mode smoke campaign (four committed
receipts), and a two-round critical cross-check, the second a full source-anchored
cross-model pass. The **inherited seed** was a single secondhand extraction of
two private review documents (an architecture-review note on `s3-fast-list` at
v1.1.0 and a companion source-code note) — themselves produced by reading the
tool's public source and README, **not by running it**. That describes the
inherited material only, not this page's current state; which inherited
sentences came from that seed is preserved in
[`research/reconciliation.md`](reconciliation.md). The `s3-fast-list`
project itself is public AWS-Samples OSS (MIT-0); only the seed extraction was
private.

## Receipts

Committed under [`receipts/smoke/`](../receipts/smoke/): four `list` mode/scope
receipts (run-facts, all exit 0; verifier **BLOCKED**, correctness **[OBS]**),
plus `_build/` (image + the `rust:1.86` build-failure log with a provenance
header) and `_capability/` (request-shape debug, the harness-incompatibility
record, the direct-capture sha256, and the direct-capture provenance record).
Anonymous, `noaa-normals-pds`, manifest snapshot 2026-07-17 (sha256
`c78a…2adb`), image `s3-fast-list@sha256:6246ee51…` (arm64, built from the
pinned Dockerfile). Full invocation table, the direct-capture procedure, and the
architecture matrix: [`running.md`](../docs/running.md). Large payloads (>100 KB) are
stored outside the repo at `<data>/receipts/s3-fast-list/` with
sha256 recorded (no-data-in-repo rule).
