# s3-fast-list — reconciliation with the inherited dossier

Walks every material claim in `tools/s3-fast-list/README.md` (the inherited
hypothesis sheet) against this groundwork's independent findings
(`research/report.md` + committed receipts). Verdicts:

| Verdict | Meaning |
| --- | --- |
| **Corroborated** | Independent work found the same (evidence labelled) |
| **Contradicted** | Found otherwise — both sides shown, positive evidence |
| **Unaddressed** | Not touched by this groundwork — stays an open hypothesis |
| **Settled by smoke run** | A committed smoke receipt genuinely decides it |

All `[SRC]` anchors are against the pinned checkout
`6c72f596e2ffe7311dec8cb7de29b114c0251207` (fork branch `feat/no-sign-request` =
upstream `b11e385` + the 51-line `--no-sign-request` patch). **Scope caveat that
governs every promotion below:** the smoked binary is the FORK, and the study's
benchmark phase measures only what upstream ships — so smoke facts are
fork-based groundwork (owner-approved), revisitable when the patch merges
upstream. Nothing is merged upstream yet.

**A second scope caveat, load-bearing:** the standard `verify-listing.sh` verdict
was **BLOCKED** for every run (the wrapper captures stdout via `docker logs`,
which is not binary-safe, and this tool emits binary Parquet — see report §7 and
`receipts/smoke/_capability/HARNESS-INCOMPATIBILITY.txt`). Listing *correctness*
therefore rests on a labelled **[OBS]** direct-capture procedure, not a certified
receipt. So completeness/correctness claims are **not** promoted out of
`VERIFIED: no`; only run-facts the wrapper genuinely recorded (build/run/anonymous
access) are promoted.

## Mechanism

| # | Dossier claim | Verdict | Evidence |
| --- | --- | --- | --- |
| M1 | Manual key-space partitioning turns serial ListObjectsV2 into a parallel scan; rests on UTF-8 binary key order; worker with `start_after=A`, stop at first key `>=B`, covers `[A,B)`; disjoint ranges never overlap | **Corroborated with a correction** | Partitioning + UTF-8-order basis Corroborated [SRC tasks_s3.rs:108-121,261-269]. **But the coverage is not `[A,B)`:** `start_after` is exclusive [SRC tasks_s3.rs:111-114] and the `end<=key` break fires *before* insertion [SRC tasks_s3.rs:261-269], so a non-initial slice covers the **open** `(A,B)`. Adjacent slices are strictly disjoint (no over-read); the in-map de-dup [SRC data_map.rs:210-239] guards retry/resume re-delivery, not overlap. See F1 below. |
| M2 | Cargo workspace, two crates `s3-fast-list` + `ks-tool` | **Corroborated** | [SRC Cargo.toml] |
| M3 | N ordered cut-points → N+1 half-open ranges; correctness regardless of balance | **Contradicted (source-derived, F1)** | The N+1-range partition exists [SRC data_map.rs:279-301], but the ranges are open at the cut-points, not half-open, so **an object whose key exactly equals a cut-point is dropped from every slice** — "correctness regardless of balance" fails whenever a cut-point coincides with a real key. [SRC tasks_s3.rs:111-114,261-269]. Registered as F1, a benchmark-phase `[SRC]`-hypothesis (report §6/§10). |
| M4 | `-k` optional; if omitted/missing, one `KeySpacePair{start:"",end:None}` → single serial full walk; `-c` has no effect; **accumulation still happens unconditionally**; "worst of both worlds" (serial throughput + O(bucket) RAM) | **Corroborated** (mechanism) / **Settled by smoke** (serial nature) | [SRC main.rs:191-218, data_map.rs:279-301]; [OBS] debug run: exactly 1 flat-list task, 1 keyspace pair `(start="",end=None)` — `_capability/debug-requestshape.stderr.txt`. Scale "O(bucket) RAM"/OOM = **Unaddressed** (smoke peaks 19–65 MB; not settleable) |
| M5 | Three cut-point sources: prior `.ks` → `ks-tool split`; `ks-tool inventory` from S3 Inventory; hand-written | **Corroborated** (existence) / **Unaddressed** (ks-tool internals) | [DOC README]; `ks-tool` has `Split` + `Inventory` subcommands [SRC ks-tool/main.rs:15-39]; their algorithms not independently read |
| M6 | Custom `Builder::new_multi_thread().worker_threads(-t, default 10)`; `JoinSet` of 3 tasks (List) / 4 (Diff): listing task(s) + data-map + monitor | **Corroborated** | [SRC main.rs:28-30,248-345] |
| M7 | `tokio::sync::Barrier` synchronizes start-of-work | **Corroborated** | [SRC core.rs:455-478 (`TaskRendezvous`), 509-511 (`wait_to_start`)] |
| M8 | Hand-rolled reactor (vector of in-flight pairs, not a Semaphore); `-c` default 100; drains finished handles; quit aborts in-flight; manual cancellation | **Corroborated** | [SRC tasks_s3.rs:18-89] |
| M9 | Per-pair loop: `list_objects_v2().prefix().start_after()` via SDK paginator; 5 s page timeout; typed error carries `next_start`; stop at `until`; results grouped by prefix to an **unbounded** mpsc; errno `u8`, `<0x10` continuable / `>=0x10` fatal | **Corroborated with a correction** | [SRC tasks_s3.rs:108-136,251-301, main.rs:258]; threshold is literally `errno < ERROR_S3_NO_BUCKET (0x10)` [SRC error.rs:8,36-37]. **"Fatal" does not fail the run:** the lister calls `ctx.complete()` and returns normally [SRC tasks_s3.rs:95-104], so the run dumps a partial listing and exits 0 — the silent-incompleteness hypothesis F2 (report §6/§10). |
| M10 | Two retry layers: SDK `RetryConfig::standard().with_max_attempts(10).with_initial_backoff(30s)` + app-level `next_start` resume | **Corroborated** | [SRC core.rs:30-31,649-659; tasks_s3.rs:91-106] |
| M11 | `GlobalState` bitmap (`Arc<AtomicUsize>`); quit `Arc<AtomicBool>` via Ctrl-C; **no** `CancellationToken` (cooperative polling); 3 atomic error counters, not surfaced prominently | **Corroborated** | [SRC core.rs:485-544, main.rs:242-246]; counters `task_next_stream_timeout/s3_client_timeout/s3_client_generic_error` [SRC core.rs:490-492] |
| M12 | Two-level accumulation map: outer `RwLock<HashMap<prefix,ObjectMap>>` (read-locked common path, write on new prefix) + inner `Arc<Mutex<HashMap<name,ObjectProps>>>` | **Corroborated** | [SRC data_map.rs:18-56,173-243] |
| M13 | `ObjectProps` = `flags:u8,status:u8,pad:u16,etag_parts:u32,last_modified:u64,size:u64,etag_md5:[u8;16]`, `#[repr(align(8))]` | **Corroborated** | [SRC core.rs:190-206] — field-for-field exact |
| M14 | ETag stored raw 16-byte MD5 + parts, not 34-char string; conversion parses inline, supports only `hex32`/`hex32-N`, **panics** on other forms | **Corroborated** | [SRC core.rs:256-264,413-453] — `panic!("unhandled etag format …")` at 428-437 |
| M15 | Diff match: `Dup` if both sides set; else size, then ETag; classify equal/mismatch/left-only/right-only; Rhai filter at match (Diff) or dump (List) | **Corroborated** | [SRC core.rs:324-410, data_map.rs:104-163] |
| M16 | Output: Parquet (Key,Size,LastModified,ETag,DiffFlag; GZIP6, plain encoding, 100 MiB `BufWriter`) + `.ks` CSV (BTreeMap-sorted, 10 MiB `BufWriter`) | **Corroborated with a correction** | [SRC utils.rs:20-93 (schema, `.set_encoding(PLAIN)`, GZIP(6)), data_map.rs:104-108 (100 MiB), 78-101 (BTreeMap, 10 MiB)]. **"Plain encoding" is not what ships:** `.set_encoding(PLAIN)` is a hint, not a disable — the produced Parquets carry **PLAIN, RLE and RLE_DICTIONARY** on every column [OBS parquet metadata of the direct-capture payloads] (F9). |
| M17 | Rhai filter: compiled once; AST allowlist walk (`SOURCE`/`TARGET`; `size`/`last_modified`); startup smoke-test against default `ObjectProps`; `fail_on_invalid_map_property(true)`, `max_variables(2)`, `max_map_size(2)` | **Corroborated** | [SRC core.rs:59-124,729-755] |

## Modes and tunables

| Dossier claim | Verdict | Evidence |
| --- | --- | --- |
| `-k` optional; test with and without | **Corroborated**; without-`-k` **Settled by smoke** (ran, serial); with-`-k` **Unaddressed** (blocked — needs a container file mount; report §3/§8) | [SRC main.rs:36-38, tasks_s3.rs:12-89 @ 6c72f59], receipts/smoke/list/* |
| `-c` default 100; no effect without `-k` | **Corroborated** | [SRC main.rs:32-34, tasks_s3.rs:32] |
| `-t` default 10 (tokio worker_threads) | **Corroborated** | [SRC main.rs:28-30] |
| `--filter` Rhai (`SOURCE`/`TARGET`; `size`/`last_modified`) | **Corroborated** (not exercised) | [SRC core.rs:49-50] |
| `--endpoint` auto-enables force-path-style with **no override flag** | **Corroborated** | Auto-enable Corroborated [SRC main.rs:146-147]. The "no override" half is **also correct**: `--force-path-style` exists [SRC main.rs:52-54] but is an **enable-only boolean** — `opt_force_path_style = cli.force_path_style \|\| opt_endpoint.is_some()` [SRC main.rs:147], so with an endpoint set it is always true and there is **no way to select virtual-hosted style**. An earlier groundwork revision mis-read this flag as an override; corrected here (F3). No endpoint was tested (no receipt). |
| List mode 3 tasks / Diff mode 4 tasks | **Corroborated** | [SRC main.rs:120-141] |
| `ks-tool split -c N` / `inventory -m … -c N` | **Corroborated** (exist) / **Unaddressed** (internals) | [SRC ks-tool/main.rs:15-39] |
| Parquet + `.ks` only; no alternate output flag | **Corroborated** / partially **Settled by smoke** | [SRC utils.rs, data_map.rs]; smoke had to route `--output-parquet-file /dev/stdout` because there is no stdout/text listing — report §5/§7 |

## Claimed numbers

| Dossier claim | Verdict | Evidence |
| --- | --- | --- |
| Concurrency ladder 8214/924/102/32 s at `-c` 1/10/100/1000 on 100M-obj, m6i.8xlarge/128 GB | **Corroborated as a DOC self-report; Unaddressed as measurement** | [DOC README "Performance test"] — vendor self-report; **not reproduced** and **not smokeable** (parallel `-k` mode blocked). The cited README table names only the instance type **`m6i.8xlarge`**; the **128 GB** figure is not in that table — it is the published spec of that instance type [DOC AWS EC2 M6i instance spec], not a README claim. Stays a hypothesis for the benchmark phase. |
| `ObjectProps` "exactly 40 bytes" | **Corroborated** | [SRC core.rs:190-206] — 8+8+8+16 = 40, 8-aligned. (This groundwork's own report initially miscomputed 48; corrected.) |
| ~4 GB props for 100M objects; ETag-as-MD5 ~50% win | **Unaddressed** | [INFERRED] plausible arithmetic; empirical/scale claim not tested |
| SDK retry 10 attempts / 30 s backoff; next-page timeout 5 s | **Corroborated** | [SRC core.rs:30-31; core.rs:22, tasks_s3.rs:128-130] |
| Parquet GZIP6 / 100 MiB BufWriter; `.ks` 10 MiB; `ks-tool split` 100 MiB reader | **Corroborated** (first two) / **Unaddressed** (`ks-tool split` reader) | [SRC data_map.rs:104-108,78-101]; main.rs:207-210 uses a 50 MiB reader for ks-hints INPUT — the `ks-tool split` reader itself not read |
| Rhai `max_variables(2)`, `max_map_size(2)` | **Corroborated** | [SRC core.rs:733-735] |
| Deps lag: `aws-sdk-s3 1.11`, `aws-config 1.1`, `hyper 0.14`, no upper pins | **Corroborated** | [SRC Cargo.toml: `aws-sdk-s3="1.11.0"`, `aws-config="1.1.1"`, `connector-hyper-0-14-x`]; no `Cargo.lock` committed (`.gitignore`) |

## Claimed strengths

All are **Corroborated as design descriptions** at the source level (throughput
delivery, packed `ObjectProps`, Parquet/Inventory integration, bidirectional
diff, Rhai sandbox, self-bootstrapping `.ks`, 5 s timeout + `next_start` resume)
[SRC as in Mechanism]. **None is promoted to a performance verdict**: the
throughput/scaling strength rests on the `-k` parallel path, which was not
smoked. Diff mode was not exercised (needs a second bucket).

## Claimed weaknesses (hypotheses)

| # | Dossier hypothesis | Verdict | Evidence |
| --- | --- | --- | --- |
| 1 | Unbounded in-memory accumulation → OOM on smaller box | **Corroborated** (mechanism) / **Unaddressed** (OOM at scale) | [SRC data_map.rs:104-163]; smoke peaks 19–65 MB — cannot reach the cliff |
| 2 | No backpressure (unbounded mpsc) worsens memory at high `-c` | **Corroborated** (unbounded) / **Unaddressed** (scale effect) | [SRC main.rs:258] |
| 3 | No structured cancellation (polled `AtomicBool`) → sluggish Ctrl-C | **Corroborated** (mechanism) / **Unaddressed** (latency) | [SRC main.rs:242-246, core.rs:558-564] |
| 4 | `panic!` on non-`hex32[-N]` ETag crashes the run | **Corroborated** (panic exists) / **Unaddressed** (behavior on a real weird object) | [SRC core.rs:428-437]; EDGE_BUCKET=none → deferred |
| 5 | Two-layer retry hard to reason under throttling | **Corroborated** (both layers exist) / **Unaddressed** (throttling behavior) | [SRC core.rs:649-659, tasks_s3.rs:91-106] |
| 6 | `u8` errno `<0x10`/`>=0x10` magic threshold misclassifies unusual errors | **Corroborated** (threshold exists) / **Unaddressed** (misclassification in practice) | [SRC error.rs:4-11,36-37] |
| 7 | `block_in_place`/`block_on` smells (no perf claim) | **Corroborated** (sites exist) | [SRC core.rs:666, data_map.rs:61, stats.rs:42,52] |
| 8 | Single unit test in the codebase (`test_object_key`, core.rs) | **Corroborated** for the `s3-fast-list` crate / **Contradicted** narrowly for "whole codebase" | s3-fast-list crate has exactly 1 test [SRC core.rs:821]; but `ks-tool/src/arn.rs` carries **4** tests [SRC ks-tool/arn.rs:96,112,128,134] |
| 9 | Dependency lag causes a real problem (build failure vs current toolchains) | **Corroborated / partially Settled by build evidence** | The pinned Dockerfile (`rust:1.86-slim`) **fails to build at the pinned SHA** — no committed `Cargo.lock` + loose semver → transitive deps float forward and demand rustc ≥ 1.94.1 (exit 101). [OBS receipts/smoke/_build/build-rust1.86-FAIL.txt]. Evidence class = **build-log tail**: it captures the failing dep-resolution + `BUILD_EXIT=1` but not a full build-context binding; the reconstructed context is in the file's provenance header, and what is not recorded is stated there (F5). Nuance: the break is caused by deps floating FORWARD, not by the pinned versions lagging — same root cause (no lockfile), opposite direction. Negative claim about third-party software, exact-as-available evidence attached. |
| 10 | Force-path-style auto-on with `--endpoint`, **no override** | **Corroborated** (see Modes table) | The hypothesis is **right**: `--force-path-style` exists [SRC main.rs:52-54] but is enable-only and cannot select virtual-hosted style with a custom endpoint [SRC main.rs:147]. An earlier groundwork revision wrongly marked this Contradicted (F3). |
| 11 | Ctrl-C mid-accumulation → possibly-inconsistent Parquet, no completeness indicator | **Corroborated** (mechanism) / **Unaddressed** (kill behavior) | [SRC data_map.rs:367-370] logs "*MAY INCONSISTENT*"; not exercised |

Additional weak points: no client-side rate limiting → **Corroborated** [SRC
core.rs:649-659 @ 6c72f59 — only the SDK `RetryConfig`; no client-side limiter];
Express One Zone roadmap-only → **Corroborated**
[DOC README roadmap]; "worst regime = small prefix / high fixed overhead" →
**Unaddressed** (no comparative timing at smoke, by design); no crash-resume
across runs → **Corroborated** [SRC — `next_start` is in-run only]; non-ASCII /
control-char key handling undiscussed → **Unaddressed** (EDGE_BUCKET=none →
deferred; but the plain-ASCII NOAA keys round-tripped exactly, [OBS]).

## Code anchors — accuracy audit (editorial)

The dossier's anchors are impressively close (its source was read against a
near-identical commit) but each is off by a few lines at `6c72f59`. Corrected
anchors (editorial corrections, [SRC @ 6c72f59]):

| Dossier | Actual @ 6c72f59 |
| --- | --- |
| main.rs:200 (empty-hints fallback) | main.rs:207-210 (load if exists) + 218 (`KeySpaceHints::new_from`) |
| main.rs:237 (ctrlc) | main.rs:244 |
| main.rs:247 (JoinSet spawn) | main.rs:256 (`JoinSet::new`), 271 (`spawn_blocking`) |
| main.rs:251 (unbounded_channel) | main.rs:258 |
| main.rs:143 (force-path-style auto) | main.rs:147 |
| data_map.rs:280 (`new_from`) | data_map.rs:280 ✓ |
| data_map.rs:324 (`do_dump`) | data_map.rs:324 ✓ |
| data_map.rs:368 ("MAY INCONSISTENT") | data_map.rs:368 ✓ |
| data_map.rs:60 (block_in_place) | data_map.rs:61 |
| tasks_s3.rs:18 (reactor) | tasks_s3.rs:18 (`flat_reactor_task`) ✓; entry `flat_list_main_task` at 12 |
| tasks_s3.rs:108 (`flat_list`) | tasks_s3.rs:108 ✓ |
| tasks_s3.rs:130 (5 s timeout_at) | tasks_s3.rs:128-130 ✓ |
| tasks_s3.rs:264 (boundary check) | tasks_s3.rs:261-269 |
| core.rs:456 (`TaskRendezvous`) | core.rs:455-461 |
| core.rs:486 (`GlobalState`) | core.rs:485 |
| core.rs:413 (Object→ObjectProps) | core.rs:413 ✓ |
| core.rs:324 (`match`) | core.rs:324 ✓ |
| core.rs:60 (`check_expr`) | core.rs:61 |
| core.rs:649 (RetryConfig) | core.rs:649 ✓ |
| core.rs:660 (block_in_place) | core.rs:666 |
| stats.rs:42 (block_in_place) | stats.rs:42 ✓ (also 52) |
| error.rs (errno + `continue_on_error`) | error.rs:4-11 (consts), 36-37 (`< 0x10`) |

## Library inventory

**Corroborated** at the source level: arrow+parquet output; hand-rolled tokio
reactor + `RwLock`/`Mutex` two-level map (no dashmap); Rhai with AST allowlist;
`indicatif::MultiProgress` + `HttpStatusCodeTracker` + 3 `AtomicUsize` counters;
AWS `aws-sdk-s3 1.11`/`aws-config 1.1`/hyper-0.14 feature; `ks-tool` pulls
`s3-transfer-manager`/`s3-manifest`. Verified in `s3-fast-list/Cargo.toml`,
`ks-tool/Cargo.toml`, and the module sources [SRC core.rs, data_map.rs,
tasks_s3.rs, utils.rs @ 6c72f59]. `ks-tool`'s `blocking_lock()`
inside `spawn_blocking` **Unaddressed** (ks-tool internals not read).

## Dossier changes made in this branch

- **Header**: status banner updated to note groundwork ran the serial `list` mode
  on the fork build (receipts committed), while the parallel `-k`, `diff`, and
  all scale/throughput claims remain unverified, and correctness rests on the
  labelled [OBS] direct-capture procedure (verifier BLOCKED by the harness capture
  incompatibility).
- **Correction (reverted, F3)** on weakness #10 / the `--endpoint` row: an earlier
  groundwork revision promoted a `[CORRECTED]` claim that `--force-path-style` is an
  override contradicting the weakness. That was wrong — the flag is enable-only and
  cannot select virtual-hosted style [SRC main.rs:52-54,147]. The "no override"
  hypothesis stands (untested — no endpoint receipt); the promotion is reverted.
- **Receipts** section populated with the committed smoke receipts, honestly
  scoped (run-facts; verifier BLOCKED; correctness [OBS]).
- **Provenance** updated to record mixed lineage (inherited secondhand notes +
  this firsthand groundwork).
- No claim promoted out of `VERIFIED: no` on correctness grounds — the harness
  blocker means no certified verifier verdict exists (see the second scope caveat).

## Claims about OTHER tools / S3 itself — routed to the orchestrator

`docs/open-questions.md` (NOT edited here — spans tools) carries claims
naming s3-fast-list; reconciled for the orchestrator to route:
- L20-25 "StartAfter accepts any key → arbitrary cut-points" — **Corroborated** [SRC tasks_s3.rs:111-114].
- L112 Rust classification — **Corroborated** (trivially).
- L141 "`next_start=last_seen_key` in-run only, not persisted" — **Corroborated** [SRC tasks_s3.rs:91-106,271-273].
- L179 "Express support roadmap TODO only" — **Corroborated** [DOC README roadmap].
- L315 blog.rasc.ch external account — a source reference; used as [3P] context in report §6.

## Harness findings routed to the orchestrator (NOT tool findings)

1. **`docker logs` capture is not binary-safe** → this tool's binary Parquet is
   corrupted in capture, blocking the standard `verify-listing.sh` verdict.
   Needs a binary-safe output channel (bind-mount / `docker cp` / attach).
2. **No input-file mount** → the `-k` hinted/parallel mode (the tool's whole
   point, and the dossier's headline claim) cannot be exercised through the
   wrapper. Needs a read-only input mount before the benchmark phase.
Both detailed in `receipts/smoke/_capability/HARNESS-INCOMPATIBILITY.txt`.
