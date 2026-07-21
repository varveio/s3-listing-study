# s3-fast-list — mechanism

Source-anchored architecture of `s3-fast-list` (+ its companion binary
`ks-tool`). Every anchor cites the pinned checkout
`6c72f596e2ffe7311dec8cb7de29b114c0251207`; the fork-and-patch story behind that
SHA is owned by [`running.md`](running.md#build) and canonically by
[`data/tool.json`](../data/tool.json). Anchors were re-verified against the
pinned source in a 2026-07-17 cross-model review; the corrections it produced are
stated here as current truth. Nothing below was re-derived for this page — full
derivation and context: [`research/report.md`](../research/report.md) §2-§6.

Every behavioral claim carries an evidence label; `[SRC file:line @ 6c72f59]`
anchors to the pinned checkout, `[OBS]` is a direct observation from a run,
`[INFERRED]` is a reasoned inference, `[DOC]` is upstream docs. References of
the form claim `some-id` resolve in the canonical ledger,
[`../data/claims.json`](../data/claims.json).

## What it is

`s3-fast-list` is a Rust/Tokio program that lists an S3 bucket with
**ListObjectsV2** and exports object metadata to a **Parquet** file. Its one
idea is to turn a single serial pagination into **many concurrent
paginations**, each bounded to a byte-range slice of the keyspace, with the
slices supplied by a **keyspace-hints file**. It is a Cargo workspace of two
crates — `s3-fast-list` (the lister) and `ks-tool` (the hints generator)
[SRC Cargo.toml @ 6c72f59].

## Task architecture

A custom Tokio multi-thread runtime (`--threads`, default 10) runs four task
kinds in a `JoinSet`: one (List) or two (Diff) **flat-list tasks** (the
listers); one **data-map task** (the in-memory accumulator + Parquet/ks
writer); one **mon task** (heartbeat/stats) [SRC main.rs:254-345 @ 6c72f59].
A `tokio::sync::Barrier` (`TaskRendezvous`) synchronizes start-of-work
[SRC core.rs:455-461,509-511 @ 6c72f59]. Listers push page results into an
**unbounded** mpsc channel [SRC main.rs:258 @ 6c72f59]; the data-map task
drains it and, when all list tasks report complete and the channel is empty,
dumps Parquet + ks and quits [SRC data_map.rs:337-386 @ 6c72f59].

Task-completion is tracked by a `GlobalState` bitmap (`Arc<AtomicUsize>`);
quit is a **polled** `Arc<AtomicBool>` flipped by a Ctrl-C handler — there is
**no** `CancellationToken`, so a worker inside a long SDK retry does not notice
quit until the SDK call returns [SRC core.rs:485-544, main.rs:242-246 @ 6c72f59].
Three atomic error counters (`task_next_stream_timeout`, `s3_client_timeout`,
`s3_client_generic_error`) exist for observability but are not surfaced
prominently [SRC core.rs:490-492 @ 6c72f59]. That this makes cancellation *feel
sluggish* is an [INFERRED] latency consequence; the signal-to-exit latency itself
is `unverified` (no run) — claim `ctrl-c-shutdown-is-sluggish`.

## The listing algorithm — concurrency comes only from hints

`flat_reactor_task` is a hand-rolled reactor (a vector of in-flight pairs, not
a `Semaphore`): it keeps up to `--concurrency` (`-c`, default **100**)
`tokio::spawn`ed range-list tasks in flight, refilling as they finish
[SRC tasks_s3.rs:18-89 @ 6c72f59]. The pairs come from the hints file: N
sorted, de-duplicated hint lines become **N+1 pairs** [SRC data_map.rs:279-301,
main.rs:191-218 @ 6c72f59].

**With no hints file, N=0, so there is exactly one pair `["",∞)` and the
listing is a single serial pagination** — `--concurrency` then has no
parallelising effect. **Parallelism is not automatic;
it is bought with a pre-computed hints file.** No auto-bisection, no adaptive
splitting, no delimiter descent [SRC main.rs:191-218 @ 6c72f59]. A separate
debug capture records 1 flat-list task / 1 keyspace pair [OBS
`../receipts/smoke/_capability/debug-requestshape.stderr.txt`] — corroboration,
not run confirmation; its full provenance caveat is owned by
[`running.md`](running.md#smoked-and-blocked-modes).

Each pair is listed by `flat_list` [SRC tasks_s3.rs:108-305 @ 6c72f59]:

- Request = `ListObjectsV2(bucket, prefix=<-p>, start_after=<pair.start>)` via
  the SDK `.into_paginator().send()` stream. **No `Delimiter`, no `MaxKeys`** →
  fully recursive listing at the SDK default page size of **1000**.
- Division is **range/StartAfter-based**, not prefix/delimiter, resting on S3's
  UTF-8 binary key order [DOC ListingKeysUsingAPIs].

### Boundary semantics

The pair upper bound is enforced **client-side, and the break fires *before* the
key is inserted**: `end<=key` (lexicographic) stops the task at the first key
that reaches the cut-point [SRC tasks_s3.rs:261-269 @ 6c72f59]. Combined with
the **exclusive** `start_after` — ListObjectsV2's `StartAfter` returns keys
strictly greater than the marker — a non-initial slice covers the **open** range
`(h_i, h_{i+1})`, **not** the half-open `[h_i, h_{i+1})` [SRC tasks_s3.rs:111-114
@ 6c72f59; DOC ListObjectsV2].

Two consequences:

1. **Adjacent slices are strictly disjoint** — the boundary key is not
   over-read into the lower slice. The in-map de-dup (`bulk_insert` inserts by
   name, ignoring already-present) [SRC data_map.rs:210-239 @ 6c72f59]
   therefore guards against retry / `next_start`-resume re-delivery, **not**
   slice overlap.
2. **A key exactly equal to a cut-point is fetched by neither adjacent slice —
   it is silently dropped.** The map cannot de-duplicate an object that was
   never fetched. This invalidates the inherited "correctness regardless of
   balance" claim for hinted runs. It is a **source-derived hypothesis** —
   claim `hint-boundary-key-can-be-omitted` — it affects only the
   hinted/parallel path (N≥1 cut-points); the no-hints serial path has no
   cut-point and is unaffected, which is consistent with the serial-scope
   direct captures matching the manifest exactly. Falsify by seeding a bucket
   where a real key equals a
   cut-point and counting whether it survives a `list -k` run (benchmark phase).

## Where hints come from

Hints come from three sources, **none required by the code**: a **prior run**
(every list/diff emits a `.ks` prefix-distribution CSV, which `ks-tool split -c
N` turns into an N-segment hints file); an **S3 Inventory** report (`ks-tool
inventory`); or **hand-written** lines (`-k` reads arbitrary lines) [DOC README
@ 6c72f59; SRC main.rs:207-218 @ 6c72f59]. The intended "fast" path is therefore
**typically** two-pass or inventory-seeded, not inherently so — claim
`hints-have-three-input-sources`.

## Pagination, retries, timeouts

- Page size: SDK default (1000), no flag [SRC tasks_s3.rs:111-121 @ 6c72f59].
- Retry, two layers: SDK `RetryConfig::standard().with_max_attempts(10)`,
  **30 s** initial backoff [SRC core.rs:30-31,649-659 @ 6c72f59]; plus an
  app-level `next_start` resume — a per-`next()` page-stream timeout of **5 s**
  resumes the slice from the last-seen key [SRC core.rs:22,32,
  tasks_s3.rs:91-136 @ 6c72f59]. Connect timeout 60 s.
- There is **no client-side rate limiting** — throttling handling rests
  entirely on the SDK `RetryConfig` [SRC core.rs:649-659 @ 6c72f59].

### Error handling

Errno is a `u8`: `< 0x10` continuable (retried from `next_start`), `>= 0x10`
fatal [SRC error.rs:4-11,36-37 @ 6c72f59]. `AccessDenied`/`NoSuchBucket`/
`PermanentRedirect`/unknown-code are fatal; a **missing** service error code
triggers `panic!` [SRC tasks_s3.rs:144-249,172-174 @ 6c72f59].

**"Fatal" does not fail the run.** On a fatal slice error the lister calls
`ctx.complete()` and returns *normally* [SRC tasks_s3.rs:95-104 @ 6c72f59]; the
data-map then dumps whatever it accumulated and signals normal shutdown [SRC
data_map.rs:372-376 @ 6c72f59]; `main` returns → **exit 0** [SRC main.rs:346 @
6c72f59]. So a fatal error on one slice can ship a **partial listing with a
success exit code and no error marker in the Parquet** — a source-derived
silent-incompleteness hypothesis (claim
`fatal-slice-error-can-exit-zero`), needing a fault-injection run before it ships
as a finding (benchmark phase).

A per-page `assert!(contents.len()==key_count)` will `panic!` on a `KeyCount`
that disagrees with contents length [SRC tasks_s3.rs:254 @ 6c72f59] [INFERRED
robustness footgun; not hit at smoke scale].

## Memory model — accumulate-then-dump

**Every object is held in memory** in a two-level map (outer
`RwLock<HashMap<prefix, ObjectMap>>`, inner `Arc<Mutex<HashMap<name,
ObjectProps>>>`) until listing finishes; only then is Parquet written [SRC
data_map.rs:18-56,104-163,337-386 @ 6c72f59]. `ObjectProps` is a packed **40**-
byte struct (`u8+u8+u16+u32 + u64 + u64 + [u8;16]`, `#[repr(align(8))]`) [SRC
core.rs:190-206 @ 6c72f59]. Peak memory grows ~linearly with object count
[INFERRED — the most scale-sensitive property for the benchmark phase; not
settleable at smoke, whose peaks were 19-65 MB]. There is no backpressure (the
mpsc is unbounded [SRC main.rs:258 @ 6c72f59]).

The ETag is stored as a raw 16-byte MD5 + a parts count, not a 34-char string;
conversion parses inline and supports only `hex32` / `hex32-N`, **panicking on
any other form** [SRC core.rs:256-264,413-453 @ 6c72f59] — brittle against
compat stores that emit non-standard ETags [INFERRED].

## Output

- **Parquet** (object metadata), Arrow schema `Key(Utf8) · Size(UInt64) ·
  LastModified(UInt64 epoch **seconds**) · ETag(Utf8, lowercase hex, unquoted;
  multipart `<hex>-<parts>`) · DiffFlag(UInt8)`, GZIP(6), PARQUET_1_0, 100 MiB
  `BufWriter` [SRC utils.rs:20-93, data_map.rs:104-108, core.rs:256-264,444-446
  @ 6c72f59]. In `list` mode every row has `DiffFlag=1`. **No StorageClass
  captured.** The Parquet is written by unordered HashMap iteration (rows not
  globally sorted) — irrelevant to a set-based verifier.
  - **Encoding:** the writer calls
    `.set_encoding(PLAIN)`, but that is a *hint*, not a disable — the produced
    Parquets actually carry **PLAIN, RLE and RLE_DICTIONARY** on every column
    [OBS parquet metadata of the direct-capture payloads]; dictionary encoding
    is not turned off (claim `parquet-uses-dictionary-encoding`).
- **`.ks` CSV** — `"prefix","count"` prefix distribution, BTreeMap-sorted, 10
  MiB `BufWriter`; a byproduct/feed for `ks-tool`, not an object listing [SRC
  data_map.rs:78-101 @ 6c72f59].

Output is **file-only** — the tool never writes a listing to stdout. The study
routes `--output-parquet-file /dev/stdout` to capture it; this is the direct
cause of the harness capture incompatibility (see
[`running.md`](running.md)).

## Endpoints and path-style

A custom endpoint (`--endpoint-url`) **forces path-style addressing**:
`opt_force_path_style = cli.force_path_style || opt_endpoint.is_some()` [SRC
main.rs:146-147 @ 6c72f59] (claim `custom-endpoint-forces-path-style`).
The `--force-path-style` flag exists [SRC main.rs:52-54 @ 6c72f59] but is
**enable-only** — it can only turn path-style *on*, so once an endpoint is set
there is **no way to select virtual-hosted style** (claim
`custom-endpoint-has-no-virtual-hosted-override`). The inherited "no override"
weakness therefore stands; whether a virtual-hosted-only endpoint misbehaves is
untested (no endpoint receipt).

## `ks-tool` companion

Two subcommands **exist** [SRC ks-tool/main.rs:15-39 @ 6c72f59]; neither was
exercised, so their behavior stays `unverified` (claim
`ks-tool-input-generation-internals`). The algorithm descriptions below are the
**inherited claim** [DOC README + inherited tool page] — the subcommands'
internals were **not** separately read, so treat them as claimed, not
source-verified:

- `split -c N` — *claimed* to read a `.ks` CSV via a 100 MiB-buffered reader
  (that reader was itself not read — claim
  `ks-tool-split-reader-buffer`, `unverified`), accumulate into a
  `BTreeMap<prefix, count>`, and emit a cut-point each time cumulative count
  crosses `total/N` → N-1 cut-points / N segments.
- `inventory -m … -c N` — *claimed* to read an S3 Inventory `manifest.json`,
  parallel-download the referenced gzipped CSVs (via AWS Labs
  `s3-transfer-manager` / `s3-manifest`), accumulate a prefix→count map, and emit
  a `.ks` file.

## Modes

| Mode | What it is |
| --- | --- |
| `list` (plain) | Single serial recursive ListObjectsV2 pagination (no hints) → Parquet |
| `list` + `-k` hints | N+1 concurrent range-bounded paginations → same Parquet contract |
| `diff` | Lists two buckets in parallel, emits only per-key differences (`DiffFlag`) |

Study coverage of these modes is summarised in the root README and detailed in
[`running.md`](running.md#smoked-and-blocked-modes).
