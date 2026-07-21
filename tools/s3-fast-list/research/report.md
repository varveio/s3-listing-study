# s3-fast-list — independent groundwork report

> Independent derivation from primary sources (docs, pinned source, third-party
> accounts, own smoke runs). Written blind to the study's inherited dossier
> (reconciled in Stage D). Every behavioral claim carries an evidence label;
> `[SRC file:line @ 6c72f59]` anchors to the pinned checkout.

## 1. Metadata

| | |
| --- | --- |
| Tool | `s3-fast-list` (+ companion binary `ks-tool`) |
| Upstream | https://github.com/aws-samples/s3-fast-list — canonical (AWS Samples org); confirmed sole home, not a fork [DOC https://github.com/aws-samples/s3-fast-list] |
| Pinned checkout | `6c72f596e2ffe7311dec8cb7de29b114c0251207` (branch `feat/no-sign-request` of the `sagiba/s3-fast-list` fork) |
| Upstream base | `b11e385ec6e32122aa01b98a3465a99e96df8b09` ("Specify output file paths", 2025-04-20) — the current upstream `main` HEAD |
| Patch delta | base + 2 commits / 51 lines adding `--no-sign-request` (anonymous access). Not merged upstream; contribution pending. See §4. |
| Crate versions | `s3-fast-list` 1.1.0, `ks-tool` 1.2.0 [SRC s3-fast-list/Cargo.toml, ks-tool/Cargo.toml @ 6c72f59] |
| Language | Rust (99%+); Cargo workspace, resolver 2 [SRC Cargo.toml @ 6c72f59] |
| License | MIT-0 (MIT No Attribution) [SRC LICENSE @ 6c72f59] |
| Upstream health | 40 stars, **0 open issues, 0 open PRs**, last commit 2025-04-20 — a quiescent sample project (~15 months stale at the 2026-07 study date) [DOC repo + commits API] |
| Image | built locally from the pinned checkout, `s3-fast-list@sha256:6246ee511116608864fab260aec1198c2761e42203316178a89ac1031664f2cc` (arm64) — see §7 |
| Report date | 2026-07-17 (UTC) |

## 2. How it works

s3-fast-list is a Rust/Tokio program that lists an S3 bucket with the
**ListObjectsV2** API and exports object metadata to a **Parquet** file. Its one
idea is to turn a single serial pagination into **many concurrent
paginations**, each bounded to a byte-range slice of the keyspace, with the
slices supplied by a **keyspace-hints file**.

### Task architecture
[SRC s3-fast-list/src/main.rs:254-345 @ 6c72f59] A Tokio multi-thread runtime
(`--threads`, default 10 worker threads) runs four task kinds in a `JoinSet`:
one (List) or two (Diff) **flat-list tasks** (the listers); one **data-map task**
(the in-memory accumulator + Parquet/ks writer); one **mon task** (stats/heartbeat).
Listers push page results into an **unbounded mpsc channel**; the data-map task
drains it and, when all list tasks report complete and the channel is empty,
dumps Parquet + ks and quits [SRC s3-fast-list/src/data_map.rs:337-386 @ 6c72f59].

### The listing algorithm — concurrency comes only from hints
[SRC s3-fast-list/src/tasks_s3.rs:18-89 @ 6c72f59] `flat_reactor_task` keeps up
to `--concurrency` (`-c`, default **100**) `tokio::spawn`ed range-list tasks in
flight, refilling as they finish. The pairs come from the hints file
[SRC data_map.rs:279-301, main.rs:191-218 @ 6c72f59]: N sorted, de-duplicated
hint lines become **N+1 pairs** *intended* to tile the keyspace as
`["",h1),[h1,h2),…,[hN,"")`. **Runtime coverage is narrower than these half-open
pairs suggest**: because `start_after` is exclusive and the boundary break fires
*before* insertion, an object whose key exactly equals a cut-point is fetched by
neither adjacent slice — a silent-drop hypothesis registered in §6/§10.
**With no hints file, N=0, so there is exactly ONE pair `["",∞)` and the listing
is a single serial pagination** — `--concurrency` then has no parallelising
effect. This is the pivotal fact: **parallelism is not automatic; it is bought
with a pre-computed hints file.** No auto-bisection, no adaptive splitting, no
delimiter descent. [CONFIRMED by smoke: a plain run issues exactly 1 flat-list
task / 1 keyspace pair — [OBS] _capability/debug-requestshape.stderr.txt]

Each pair is listed by `flat_list` [SRC tasks_s3.rs:108-305 @ 6c72f59]:
- Request = `ListObjectsV2(bucket, prefix=<-p start prefix>, start_after=<pair.start>)`
  via the SDK `.into_paginator().send()` stream. **No `Delimiter`, no `MaxKeys`**
  → fully recursive listing at the SDK default page size of **1000**.
- The pair upper bound is enforced **client-side, and the break fires *before*
  the key is inserted**: `end<=key` (lexicographic) stops the task at the first
  key that reaches the cut-point [SRC tasks_s3.rs:261-269 @ 6c72f59]. Combined
  with the **exclusive** `start_after` [SRC tasks_s3.rs:111-114 @ 6c72f59; DOC
  ListObjectsV2 — StartAfter returns keys strictly greater], a non-initial slice
  covers the **open** range `(h_i,h_{i+1})`, not `[h_i,h_{i+1})` — the cut-point
  key itself is fetched by neither adjacent slice (§6 boundary-omission
  hypothesis). Adjacent slices are therefore **strictly disjoint**; the in-map
  **de-dup** (`bulk_insert` inserts by name, ignoring already-present)
  [SRC data_map.rs:210-239 @ 6c72f59] guards against retry / `next_start`-resume
  re-delivery, **not** slice overlap.
- Division is therefore **range/StartAfter-based**, not prefix/delimiter, resting
  on S3's UTF-8 binary key order [DOC ListingKeysUsingAPIs].

### Where hints come from (two-pass / inventory)
[DOC README @ 6c72f59] Hints come from a **prior run** (every list/diff emits a
`.ks` prefix-distribution CSV, which `ks-tool split -c N` turns into an N-segment
hints file) or an **S3 Inventory** report (`ks-tool inventory`). Hand-written
hints are also accepted — `-k` reads arbitrary lines, so a prior source is not
required by the code. The intended "fast" path is therefore **typically**
two-pass or inventory-seeded, not inherently so.

### Pagination, retries, timeouts
- Page size: SDK default (1000), no flag [SRC tasks_s3.rs:111-121 @ 6c72f59].
- Retry: `RetryConfig::standard().with_max_attempts(10)`, **30 s** initial backoff
  [SRC core.rs:30-31,649-659 @ 6c72f59]; connect timeout 60 s; a per-`next()`
  page-stream timeout of **5 s** resumes the slice from a `next_start` failsafe
  cursor [SRC core.rs:22,32, tasks_s3.rs:91-136 @ 6c72f59].
- Errors: retriable/timeout resume from last key; `AccessDenied`/`NoSuchBucket`/
  `PermanentRedirect`/unknown-code are **fatal to the slice** — but "fatal" here
  means the lister calls `ctx.complete()` and returns *normally*, so the run then
  dumps whatever it accumulated and exits 0 (the silent-incompleteness hypothesis
  in §6) [SRC tasks_s3.rs:95-104,144-249,172-174, error.rs @ 6c72f59]. A
  **missing** service error code triggers `panic!` [SRC tasks_s3.rs:172-174].
- A per-page `assert!(contents.len()==key_count)` will `panic!` on a `KeyCount`
  that disagrees with contents length [SRC tasks_s3.rs:254 @ 6c72f59]. [INFERRED]
  robustness footgun; not hit at smoke scale.

### Memory model — accumulate-then-dump
[SRC data_map.rs:104-163,337-386 @ 6c72f59] **Every object is held in memory**
(`HashMap<prefix, HashMap<name, ObjectProps>>`; `ObjectProps` a packed **40**-byte
struct — `u8+u8+u16+u32 + u64 + u64 + [u8;16]`, already 8-aligned [SRC
core.rs:190-206]) until listing finishes; only then is Parquet
written. Peak memory grows ~linearly with object count. [INFERRED] the most
scale-sensitive property for the benchmark phase; **not** settleable at smoke
scale (observed smoke peaks were small — §8).

### Resume / ordering
- **No resume/checkpoint.** Ctrl-C force-dumps a partial, logged "*MAY
  INCONSISTENT*" [SRC data_map.rs:367-370, main.rs:242-246 @ 6c72f59].
- The `.ks` CSV is sorted; the **Parquet is written by unordered HashMap
  iteration** (rows not globally sorted) [SRC data_map.rs:78-101 vs 104-163 @
  6c72f59]. Irrelevant to a set-based verifier.

## 3. Modes and tunables

**Modes** (change request pattern or output contract):

| Mode | What it is | Smoke status |
| --- | --- | --- |
| `list` (plain) | Single serial recursive ListObjectsV2 pagination (no hints) → Parquet | **Smoked** — full bucket + 3 scoped prefixes, all correct (§8). Correctness [OBS] (harness capture blocker, §7/§8). |
| `list` + `-k` hints | N+1 concurrent range-bounded paginations → same Parquet contract | **Blocked for smoke** — needs an input hints FILE in the container; the wrapper mounts nothing (argv + observability env only). Mechanism is source-established. |
| `diff` | Lists TWO buckets in parallel, emits only per-key differences (`DiffFlag`) | **Blocked** — needs a second bucket; against one bucket all keys are `Equal` and `Equal` rows are never exported (`include_eq=false`) → empty output. CREDS=none, no second bucket. |

**Tunables** (magnitude only — sweep in the benchmark phase):

| Flag | Default | Effect | Evidence |
| --- | --- | --- | --- |
| `-c, --concurrency` | 100 | Max in-flight range-list tasks. **No effect without hints** (1 pair). Sweep target. | [SRC main.rs:32-34, tasks_s3.rs:32 @ 6c72f59] |
| `-t, --threads` | 10 | Tokio worker threads. | [SRC main.rs:28-30 @ 6c72f59] |
| `-p, --prefix` | `/` (→ `""`, whole bucket) | Prefix filter on every request; used here for scoped checks. | [SRC main.rs:24-26,115 @ 6c72f59] |
| `-f, --filter` | none | rhai expression on `size`/`last_modified`; filtered objects dropped from output. Feature, not a completeness mode. | [SRC main.rs:40-42, core.rs:59-124 @ 6c72f59] |
| `-k, --ks-file` | `{region}_{bucket}_ks_hints.input` | Keyspace-hints input (the parallelism switch). | [SRC main.rs:36-38 @ 6c72f59] |
| `--endpoint-url` / `--force-path-style` | none | S3-compatible endpoints (path-style auto-on with endpoint). | [SRC main.rs:48-54,146-147 @ 6c72f59] |
| `--no-sign-request` | off | Anonymous access (the patch). | [SRC main.rs:56-58, core.rs:661-664 @ 6c72f59] |
| `--output-parquet-file`/`--output-ks-file`/`--output-log-file` | timestamped | Output paths (base b11e385). Load-bearing here: `/dev/stdout`. | [SRC main.rs:60-70 @ 6c72f59] |

Page size / MaxKeys is **not** a tunable (hardcoded SDK default). Retry count
(10), backoff (30 s), page timeout (5 s), connect timeout (60 s) are
**compile-time constants**, not flags [SRC core.rs:22,30-32 @ 6c72f59].

## 4. How to run it properly

### Anonymous access (the smoke path)
`--no-sign-request` is a **global** flag calling `aws_config … .no_credentials()`
[SRC main.rs:56-58, core.rs:661-664 @ 6c72f59; patch commit 8ccae03]. Docs
recommend also passing `--region` (the flag does not change region resolution,
and an unsigned run usually has no profile/env fallback) [DOC README @ 6c72f59].
Example: `s3-fast-list --no-sign-request list --region us-east-1 --bucket <b>`.
[CONFIRMED anonymous: smoke runs returned public data under the wrapper's
credential-starved env, §8.]

### Large-listing best practice (project's own guidance)
[DOC README "List GIANT bucket"/"Prepare your ks hints" @ 6c72f59] Two-pass /
hinted: (1) get a prefix distribution (a prior `.ks` dump, or S3 Inventory via
`ks-tool inventory`); (2) `ks-tool split -c N`; (3) `s3-fast-list list -k <hints>
-c N`. Its own benchmark claims 100M objects from 8214 s (c=1) to 32 s (c=1000)
on m6i.8xlarge [DOC README "Performance test"] — vendor self-report, context only.

### Footguns
- **Parallelism is a silent no-op without a hints file** [SRC main.rs:191-218, data_map.rs:279-301, tasks_s3.rs:18-89 @ 6c72f59].
- **Output is a Parquet file, never stdout** — script it by parsing Parquet or
  routing `--output-parquet-file /dev/stdout` (this study). [SRC main.rs:316-334].
- **Unbounded in-memory accumulation** before any output is written [SRC data_map.rs:104-163,337-386 @ 6c72f59].
- A `.ks` file is always written (to CWD by default); redirect/clean it
  [SRC main.rs:310-313; here `--output-ks-file /dev/null`].

## 5. Output and observability

### Output formats
- **Parquet** (object metadata), Arrow schema `Key(Utf8) · Size(UInt64) ·
  LastModified(UInt64 epoch **seconds**) · ETag(Utf8, **lowercase hex,
  unquoted**; multipart `<hex>-<parts>`) · DiffFlag(UInt8)`, GZIP(6), PARQUET_1_0
  [SRC utils.rs:20-93, core.rs:256-264,444-446 @ 6c72f59]. The writer calls
  `.set_encoding(PLAIN)`, but that is a *hint*, not a disable: the produced
  Parquets actually carry **PLAIN, RLE and RLE_DICTIONARY** on every column
  [OBS parquet metadata of the direct-capture payloads] — dictionary encoding is
  not turned off.
  In `list` mode every row has `DiffFlag=1`. **No StorageClass captured.**
- **`.ks` CSV** — `"prefix","count"` prefix distribution, sorted; a byproduct/feed
  for `ks-tool`, not an object listing [SRC data_map.rs:78-101 @ 6c72f59].

### `normalize.sh` contract (this tool, `list` mode)
Reads raw Parquet on stdin (spooled to a temp file — Parquet needs random
access), emits `key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class`:
`key`=Parquet `Key` (already absolute — encode/decode round-trips it, verified in
smoke); `size`=`Size`; `etag`=`ETag` (unquoted lowercase hex, matches manifest);
`mtime`=`LastModified` epoch seconds → `YYYY-MM-DDTHH:MM:SSZ` UTC (duckdb
`make_timestamp` is tz-naive over a UTC epoch); `storage_class`=`-` (unexposed).
**Caveat (adapter, not tool):** the adapter emits DuckDB `-list` output with a
tab separator and **no quoting**, so a valid `Key` containing a literal TAB or
newline would break the five-column / one-record-per-line contract (an extra
field or an extra record). The smoke bucket's keys are plain ASCII with neither
byte, so no run was affected; deferring the edge-key bucket does **not** make the
adapter safe for that later test — it needs binary-safe framing first. Flagged in
`normalize.sh` and in Open questions.

### Metrics / logs
- `env_logger` at `info` → **stderr** by default (file only with `--log`):
  startup banner, per-task heartbeats (5 s), final `prefix/object count`
  [SRC main.rs:157-185, data_map.rs:327-332, tasks_s3.rs:73-76 @ 6c72f59].
- `RUST_LOG=…=debug` → per-slice `Sending S3 request` and per-page `Waiting for
  S3 response` lines — the route to observing serial-vs-parallel behavior
  [SRC main.rs:160-162, tasks_s3.rs:117-129 @ 6c72f59]; passed via the wrapper's
  `--env` allowlist. **No total-API-call counter is exposed** (§8).

## 6. Failure surface

- **Memory**: accumulate-then-dump; peak ~linear in object count. Benchmark OOM
  hypothesis; not smoke-settleable. [SRC data_map.rs:104-163,337-386 @ 6c72f59] [INFERRED]
- **Panics on unexpected S3 shapes**: `assert!(contents==KeyCount)` per page;
  `panic!` on a `None`-code service error or an ETag neither 32-hex-quoted nor
  `hex-<n>` [SRC tasks_s3.rs:254,172-174, core.rs:428-437 @ 6c72f59]. [INFERRED]
  brittle vs nonstandard/compat stores.
- **Cut-point key omission ([SRC]-hypothesis, benchmark-phase).** `start_after`
  is exclusive and the boundary check `end<=key` breaks *before* inserting, so an
  object whose key exactly equals a hint/cut-point is fetched by neither adjacent
  slice and dropped from the output — silently, with a correct-looking count for
  every key that is *not* a cut-point. This affects only the hinted/parallel path
  (N≥1 cut-points); the no-hints serial path has no cut-point and is unaffected
  (which is why the serial smoke runs matched the manifest exactly). It
  invalidates the inherited "correctness regardless of balance" claim for hinted
  runs. **Source-derived, not run-proven** [SRC tasks_s3.rs:111-114,261-269 @
  6c72f59; DOC ListObjectsV2 StartAfter exclusive]. Falsification (§10): seed a
  bucket where an object key exactly equals a cut-point in the hints file, run
  `list -k`, and check whether that key appears in the output.
- **Silent incompleteness on a fatal slice error ([SRC]-hypothesis,
  benchmark-phase).** A fatal S3 error (`AccessDenied`/`NoSuchBucket`/
  `PermanentRedirect`/unknown code) does not fail the run: the lister calls
  `ctx.complete()` and returns normally [SRC tasks_s3.rs:95-104 @ 6c72f59], the
  data-map then dumps whatever it accumulated and signals normal shutdown [SRC
  data_map.rs:372-376 @ 6c72f59], and `main` returns → **exit 0** [SRC
  main.rs:346 @ 6c72f59]. So a partial listing can ship with a success exit code
  and no error marker in the Parquet. **Source-derived, not run-proven**;
  falsification (§10): fault-inject a fatal error on one slice (e.g. deny one
  prefix, or point one slice at a missing bucket) and confirm exit 0 + a partial
  output with no completeness signal.
- **Interruption**: no resume; Ctrl-C dumps a possibly-inconsistent partial.
- **Endpoint quirks**: a custom endpoint **forces** path-style and there is no way
  to select virtual-hosted style — `--force-path-style` is enable-only and
  redundant when an endpoint is set [SRC main.rs:52-54,146-147 @ 6c72f59];
  directory buckets (S3 Express) on the roadmap, unsupported [DOC README @ 6c72f59].
- **Hint-quality dependence**: parallel speedup needs cut-points that divide the
  populated keyspace; keys clustered under few prefixes parallelize poorly. The
  cited third-party account discusses fixed-`Prefix` requests, not arbitrary
  `StartAfter` range cuts — balanced range cut-points *can* subdivide a clustered
  prefix, so this is a tool-specific [INFERRED] caveat, with the `Prefix`-request
  observation as adjacent context [3P blog.rasc.ch].

## 7. Container

- **No upstream image is published**; upstream ships only a **Dockerfile**
  [SRC Dockerfile @ 6c72f59]. Per the brief's middle case, the image is built
  from **upstream's own Dockerfile at the pinned SHA** — with one documented
  deviation (below), staged as `tools/s3-fast-list/Dockerfile`.
- **Reproducibility defect (finding).** Upstream pins `FROM rust:1.86-slim`, and
  it **does not build at the pinned SHA today**: `Cargo.lock` is `.gitignored`
  (no committed lockfile) and `Cargo.toml` uses loose semver, so `cargo build`
  resolves the newest compatible transitive deps, which now demand **rustc ≥
  1.94.1** (`aws-smithy-*`, `aws-types@1.4.0`); under rust:1.86 the cargo
  dependency resolution fails with exit 101. The evidence is a **build-log tail**
  [OBS receipts/smoke/_build/build-rust1.86-FAIL.txt] — it captures the failing
  dep-resolution and `BUILD_EXIT=1` but not a full build-context binding; the
  reconstructed context (upstream Dockerfile at `6c72f59`, its `FROM
  rust:1.86-slim`, arm64 box) is in that file's header, and what is not recorded
  is stated there. Minimal faithful fix: `FROM rust:1.94-slim-bookworm` — same
  Debian release (glibc 2.36) so the binary still loads on `distroless/cc-debian12`.
  A trixie `rust:1.94-slim` would build a `GLIBC_2.39` binary the debian12 runtime
  cannot load — this is the reason for the explicit `-bookworm` pin, [INFERRED]
  from the Debian release glibc versions (bookworm 2.36, trixie 2.39; distroless/
  cc-debian12 provides 2.36), **not** a committed run. Nothing else changed.
  Built image id/digest is the run identity.
- **Architecture matrix**:

  | Channel | amd64 | arm64 | Note |
  | --- | --- | --- | --- |
  | Upstream published image | — | — | none exists |
  | Prebuilt binaries | — | — | none released |
  | Source build (this Dockerfile) | not built | **built + ran [OBS]** | arm64 build/run receipted; amd64 [INFERRED] buildable (pure-Rust deps, multi-arch base) but **not run** |

  Only **arm64** has build+run evidence (the runner box). amd64 is **inferred**
  buildable — the base images are multi-arch and the deps are pure-Rust — but a
  multi-arch base does not prove the whole project builds and runs on amd64; no
  amd64 build or run was performed. The benchmark's single common arch is expected
  to be **amd64**, which must be confirmed by an actual amd64 build before it is
  relied on. Flag in Open questions.
- **Smoke ran on**: native **arm64** (host `aarch64`), **not emulated** (every
  receipt: `Emulated: no`). Runtime image `gcr.io/distroless/cc-debian12`;
  entrypoint **null**, so run.sh argv starts with `/usr/bin/s3-fast-list`. Build
  command + logs under `receipts/smoke/_build/`.
- **HARNESS CAPTURE INCOMPATIBILITY (major finding).** The wrapper captures
  container stdout via `docker logs` (json-file driver), which is **not
  binary-safe**. This tool emits **binary Parquet**; routed to `/dev/stdout` it is
  corrupted in capture (non-UTF-8 bytes → U+FFFD), so the stored payload is an
  unparseable Parquet and the standard `verify-listing.sh` path **never returned
  a verdict** (BLOCKED). Universal across payload sizes (127 KB and 8 MB both
  corrupted). Run facts in the receipts remain valid; correctness rests on an
  **[OBS] manifest-diff against faithful direct captures** — not a certified
  verifier PASS. The direct captures' provenance (what is bound by evidence and
  what is *not recorded*) is stated in
  `receipts/smoke/_capability/direct-capture.provenance.md`. Full detail:
  `receipts/smoke/_capability/HARNESS-INCOMPATIBILITY.txt`.

## 8. Smoke results

Image `s3-fast-list@sha256:6246ee51…` (arm64, native), auth **anonymous**
(`--no-sign-request` under the wrapper's credential-starved env), bucket
`noaa-normals-pds` / `us-east-1`, manifest sha256 `c78a8273…` (148,917 keys),
2026-07-17. All runs are the plain `list` mode (serial, 1 keyspace pair —
[OBS] confirmed via `RUST_LOG` debug: 1 flat-list task, 1 `Sending S3 request`).

| Scope | Invocation (argv, via run.sh) | Exit | Wall | peak_rss | Verifier | [OBS] correctness vs manifest | Receipt |
| --- | --- | --- | --- | --- | --- | --- | --- |
| full bucket | `s3-fast-list --no-sign-request --output-parquet-file /dev/stdout --output-ks-file /dev/null list --region us-east-1 --bucket noaa-normals-pds` | 0 | 20.06 s | 65.1 MB | BLOCKED (harness capture; §7) | 148,917 = 148,917 — 0 missing/extra/field-mismatch/dup | `receipts/smoke/list/full/` |
| `normals-hourly/` | …`--prefix normals-hourly/`… | 0 | 5.06 s | 19.1 MB | BLOCKED | 2,549 = 2,549 — clean | `receipts/smoke/list/hourly/` |
| `normals-monthly/1991-2020/` | …`--prefix …`… | 0 | 5.06 s | 28.0 MB | BLOCKED | 15,625 = 15,625 — clean | `receipts/smoke/list/monthly-1991-2020/` |
| `normals-annualseasonal/1981-2010/access/` | …`--prefix …`… | 0 | 5.06 s | 23.8 MB | BLOCKED | 9,839 = 9,839 — clean | `receipts/smoke/list/annualseasonal-1981-2010-access/` |

- **Verifier verdict = BLOCKED via the standard path** for all runs, because the
  wrapper's `docker logs` capture corrupts the binary Parquet (§7). Not a tool
  FAIL and not a normalize.sh defect. Each receipt's verdict cell documents this.
- **[OBS] correctness** = `docker run > file` direct capture, normalized by
  `normalize.sh`, diffed against the manifest with the verifier's canonicalization
  (`LC_ALL=C`, `tolower(etag)`, mtime tz-canonical). This is a manifest-diff, not
  a certified verifier PASS. The direct-capture command was intended to replicate
  the wrapper argv but was **not independently logged** (the shell history
  predates the capture); what *is* bound is the payload sha256, row count, byte
  size and valid-parse — see `direct-capture.provenance.md`. Direct-capture
  Parquets + sha256: `receipts/smoke/_capability/direct-capture.sha256`.
- **Request behavior [OBS]**: `RUST_LOG=s3_fast_list=debug` shows exactly 1
  keyspace pair `(start="",end=None)` and 1 flat-list task — serial, confirming
  §2 (`_capability/debug-requestshape.stderr.txt`).
- **`list -k` hints mode and `diff` mode: NOT smoked** — blocked (needs container
  file injection / a second bucket; §3). Mechanisms are source-established.
- **Edge-case checks deferred**: `EDGE_BUCKET=none` — unicode/weird-key/multipart
  fidelity recorded **deferred**, not run.

## 9. Notable findings

- **The tool's whole value proposition — parallel listing — is inert by
  default.** Concurrency engages only when fed a keyspace-hints file, which is
  typically produced by a prior full listing or an S3 Inventory (hand-written
  hints are also accepted). A naive single invocation is an ordinary serial
  paginator. [SRC main.rs:191-218, tasks_s3.rs:18-89 @ 6c72f59; CONFIRMED §8]
- **Client-side range slicing over StartAfter**, not server-side delimiter
  descent — an inventory-friendly design leaning entirely on S3 UTF-8 key order.
  Because `start_after` is exclusive and the boundary breaks before insertion,
  adjacent slices are strictly disjoint but leave a **gap at every cut-point
  key** (§6 boundary-omission hypothesis); the in-map de-dup guards against retry
  re-delivery, not slice overlap. [SRC tasks_s3.rs:111-114,261-269,
  data_map.rs:210-239 @ 6c72f59]
- **Parquet-only, file-only output** makes it awkward to compose with
  stdout-oriented tooling — and it is the direct cause of the harness capture
  incompatibility (§7): binary output does not survive `docker logs`.
- **Several `panic!`/`assert!` paths** on unexpected S3 responses suggest it was
  built against well-behaved AWS S3, not defensively against compat stores.
- **No committed lockfile on a pinned "sample"** → the documented build is already
  broken (rustc-version drift). Maintenance-rot finding independent of runtime
  behavior. [SRC .gitignore, Cargo.toml @ 6c72f59]

## 10. Open questions for the benchmark phase

- **Memory scaling / OOM**: peak RSS vs object count for the accumulate-then-dump
  map — is there a cliff? (smoke peaks were 19–65 MB and cannot answer). Sweep
  bucket sizes.
- **Concurrency sweep**: `-c` ∈ {1,10,100,1000} **with a real hints file**; how
  effective request parallelism tracks `-c` and where it saturates.
- **Hint granularity**: N segments vs speedup; sensitivity to cut-point quality
  on skewed keyspaces.
- **`--threads` vs `--concurrency`** interaction on multi-core boxes.
- **Harness gaps to close first** (both required to benchmark the parallel mode):
  (a) a **binary-safe output capture** (bind-mount / `docker cp` / attach instead
  of `docker logs`) so Parquet survives; (b) an **input-file mount** so a hints
  file can reach the container.
- **Architecture**: confirm amd64 as the common denominator with an **actual
  amd64 build** (only arm64 was built/run; amd64 is inferred, §7).
- **Cut-point key omission ([SRC]-hypothesis, §6).** Seed a bucket where an object
  key exactly equals a cut-point in the hints file, run `list -k`, and count
  whether that key survives to the output. Expected if the hypothesis holds: the
  key is silently missing from every slice. This must be settled before any
  hinted/parallel correctness claim is made.
- **Silent incompleteness on a fatal slice error ([SRC]-hypothesis, §6).**
  Fault-inject a fatal S3 error on one slice (deny a prefix, or point one slice at
  a missing bucket) and confirm whether the run exits 0 with a partial Parquet and
  no completeness signal. Needed before this ships as a finding.
- **Adapter binary-safety**: `normalize.sh` must gain binary-safe framing before
  the edge-key bucket (tab/newline keys) is tested — the current `-list`/tab
  output cannot represent such keys (§5).

## 11. Sources

- [DOC] Upstream repo + README — https://github.com/aws-samples/s3-fast-list (2026-07-17)
- [DOC] Commits API (last-commit date) — https://api.github.com/repos/aws-samples/s3-fast-list/commits (2026-07-17)
- [DOC] S3 ListObjectsV2 / UTF-8 key ordering — https://docs.aws.amazon.com/AmazonS3/latest/userguide/ListingKeysUsingAPIs.html (2026-07-17)
- [3P] "Faster S3 Object Listing", blog.rasc.ch, 2025-07 — https://blog.rasc.ch/2025/07/s3-fast-list.html (2026-07-17) — discusses fixed-`Prefix` request splitting; used here as adjacent context for the clustered-keyspace caveat, which is applied to `StartAfter` range cuts as a tool-specific [INFERRED] extension (§6).
- [SRC] Pinned checkout `6c72f596e2ffe7311dec8cb7de29b114c0251207` (upstream base `b11e385ec6e32122aa01b98a3465a99e96df8b09`).
- [RUN]/[OBS] Receipts under `tools/s3-fast-list/receipts/smoke/` (index in §8); direct-capture evidence under `<data>/receipts/s3-fast-list/`.
