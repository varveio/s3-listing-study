# s7cmd — independent research report

> Independent groundwork for the s3-listing-study, derived from primary
> sources: the tool's own docs, its pinned source, and my own smoke runs.
> Evidence labels per claim: `[DOC]` docs · `[SRC file:line @ sha]` pinned
> source · `[RUN receipt]` my smoke run · `[3P]` third-party · `[INFERRED]` ·
> `[OBS]` observed but unrecordable. Unlabeled behavioral claims are defects.
>
> **Provenance note (load-bearing).** `s7cmd` is a thin umbrella CLI; its `ls`
> listing engine lives in a *separate* crate, **`s3ls-rs`**, pinned at the
> exact version `s7cmd` depends on (`=1.0.3`). `[SRC]` anchors therefore cite
> two checkouts, each with its own recorded SHA (see Metadata): `s7cmd` for the
> CLI/dispatch wrapper, `s3ls-rs` for the algorithm. Both are pinned; both are
> cited explicitly.

## 1. Metadata

| | |
| --- | --- |
| Tool | `s7cmd` (subcommand under test: `ls`) |
| Upstream | https://github.com/nidor1998/s7cmd — canonical (crates.io `s7cmd`, author `nidor1998`) |
| Pinned release | **v1.5.0** (latest stable; project cuts release tags) |
| `s7cmd` commit | `d589df7ce691edbede05fc9a691ab1787cdb6b9e` |
| Listing engine | `s3ls-rs` **v1.0.3** (the crate `s7cmd =1.0.3` depends on for `ls`) |
| `s3ls-rs` commit | `bf42067537da476b157b5d289a3e72d049b60db2` |
| Language | Rust (edition 2024, `rust-version = 1.91.1`); built binary reports `rustc 1.97.1` |
| License | Apache-2.0 (both crates) [DOC LICENSE] |
| Upstream health | Created 2026-03-27, last push 2026-07-11; v1.5.0 tagged 2026-07-11; 221 commits; **0 open issues/PRs**, 8 stars, 0 forks; not archived. Author states the project is "functionally complete," maintained minimally with **monthly dependency bumps** [DOC README §Contributing][3P github.com/nidor1998/s7cmd API 2026-07-17]. |
| Image | Built from **upstream's own `Dockerfile` at the pinned SHA** (no image is published upstream). Digest (local, never pushed): `sha256:07091182512e74cde4bb897a97b1fc9a586757560c5008ae8c701d7fdb6974da`, arch **arm64** |
| Report date | 2026-07-17 (UTC) |

`s7cmd` composes four of the author's crates: `s3sync` (sync, human-written
reference architecture), `s3util-rs` (bucket admin + `cp`/`mv`/`rm`),
`s3rm-rs` (`clean`), and **`s3ls-rs` (`ls`)** [DOC README][SRC s7cmd Cargo.toml].
Only `ls` is in scope for this listing study. The project advertises that it is
**"Fully AI-generated (human-verified)"** — every line of `s7cmd`, `s3ls-rs`,
`s3util-rs`, `s3rm-rs` source, tests, and docs is Claude-Code-generated under
human review; `s3sync` alone is human-written [DOC README §"Fully AI-generated"].

## 2. How it works

### Architecture

`s3ls-rs` is a **three-stage streaming pipeline** connected by bounded async
`tokio::mpsc` channels (queue size `--object-listing-queue-size`, default
200000) [DOC README §How it works][SRC s3ls-rs src/pipeline.rs:57-65 @ bf42067]:

```
[Lister + Filter Chain] -> channel -> [Aggregator] -> channel -> [DisplayWriter] -> stdout
   parallel prefix discovery           sort (or stream)           format + write
```

- **Lister** issues the S3 API calls and applies filters inline; entries that
  fail a filter are dropped before the aggregator sees them
  [SRC s3ls-rs src/lister.rs:45-68 @ bf42067].
- **Aggregator** buffers-and-sorts by default, or (`--no-sort`) forwards each
  entry immediately [SRC s3ls-rs src/aggregate.rs:33-83 @ bf42067] (wired from
  `pipeline.rs:145-156`, which only passes `no_sort` into `AggregatorConfig`).
- **DisplayWriter** picks one of four formatters (aligned / TSV / one-line /
  JSON) and writes to a `BufWriter<stdout>`
  [SRC s3ls-rs src/pipeline.rs:170-186 @ bf42067].
- Errors surface in precedence display > aggregator > lister
  [SRC s3ls-rs src/pipeline.rs:100-107 @ bf42067]; the cancellation itself, on
  a display-writer or aggregator error, happens earlier, in the code that
  awaits each stage and in the lister
  [SRC s3ls-rs src/pipeline.rs:68-97 @ bf42067].

### The listing algorithm (the core)

The load-bearing code is `ListingEngine` in
`s3ls-rs src/storage/s3/mod.rs @ bf42067`.

**Parallel vs sequential decision** [SRC s3ls-rs src/storage/s3/mod.rs:409-413]:
```
use_parallel = max_parallel_listings > 1
            && delimiter.is_none()                 // i.e. recursive (-r) mode
            && (!express_one_zone || allow_flag)
```
- **Non-recursive** always sets `delimiter = "/"` [SRC ...:783-787] -> a single
  **sequential** paginated `ListObjectsV2` at one level, returning objects +
  `CommonPrefixes` (the `PRE` rows).
- **Recursive** (`-r`) sets no delimiter -> **parallel** listing (unless
  `--max-parallel-listings 1` or a flat/Express-One-Zone bucket forces
  sequential).

**Parallel algorithm** — recursive prefix discovery via `tokio::task::JoinSet`
bounded by an `Arc<Semaphore>` of size `max_parallel_listings`
[SRC ...:558-746, :789]:
1. **Discovery phase** (recursion depth <= `max_parallel_listing_max_depth`,
   default 2): paginate the current prefix **with `delimiter="/"`**, emit
   objects at that level immediately, collect sub-prefixes [SRC ...:596-674].
2. Each sub-prefix is spawned as a task that acquires its own semaphore permit
   and recurses [SRC ...:701-727].
3. **Listing phase** (recursion depth > `max_parallel_listing_max_depth`): drop
   the delimiter and switch to `list_sequential` — a full no-delimiter
   pagination of that leaf prefix [SRC ...:581-586].

So the keyspace is divided by **delimiter-based common-prefix recursion to a
fixed depth, then a flat sequential drain of each leaf** — not bisection, not
cut-points. **Consequence** (documented and confirmed by source): a bucket with
**no `/` hierarchy** discovers zero sub-prefixes and the whole listing collapses
to sequential pagination [DOC README §High performance][SRC ...:590-674 (the
delimiter-discovery loop that would populate `all_sub_prefixes`, and finds none
on a flat keyspace)].

**Pagination.** One `ListObjectsV2` per page; `ListObjectVersions` for
`--all-versions`. Continuation via `next_continuation_token` (objects) or
`next_key_marker`/`next_version_id_marker` (versions)
[SRC ...:146-153, :238-245]. Two robustness guards: a truncated page with **no**
continuation token, or the **same token twice**, both `bail!` rather than loop
forever [SRC ...:506-522, :637-666]. `max_keys` default 1000 (= S3 max) [DOC].

**Parallelism is listing-only.** The parallel machinery is entirely inside the
lister; there is no per-object work fan-out. Metadata comes only from the list
response — `s3ls` never issues `HeadObject`/`GetObject`; the only AWS SDK calls
in the storage layer are `.list_objects_v2()` and `.list_object_versions()`
[DOC README §API request calculation][SRC s3ls-rs src/storage/s3/mod.rs:93
(list_objects_v2), :175 (list_object_versions) @ bf42067].

**Retries / timeouts.** SDK **standard** retry mode, `max_attempts` default 10,
initial backoff 100 ms [SRC s3ls-rs src/storage/s3/client_builder.rs:154-160 @
bf42067][DOC help]. No operation/connect/read timeout is set unless the user
passes one (`build_timeout_config` returns `None` when all are unset)
[SRC ...:164-191]. **Stalled-stream protection is ON by default**
(`--disable-stalled-stream-protection` to turn off) [SRC ...:36-46].

**Ordering.** Default output is **sorted by key** in the aggregator; parallel
tasks emit interleaved, so order is only guaranteed after the sort. `--no-sort`
emits in arrival order (lexicographic within a listing op, interleaved across
parallel ops) [DOC README §Streaming mode][SRC s3ls-rs src/aggregate.rs:33-83
(streaming vs buffering), :144-168 (sort implementation) @ bf42067].
The verifier is order-insensitive, so both were accepted.

**Memory model.** Default sorted mode **buffers every entry** (~700-860 B/object
+ ~15 MB baseline) [DOC README §Low memory usage]. `--no-sort` streams at a
near-constant ~84 MB regardless of count [DOC]. Parallel sort (rayon) kicks in
only past `--parallel-sort-threshold` (default 1,000,000) [DOC][SRC
s3ls-rs src/aggregate.rs:163-168 @ bf42067] (`pipeline.rs:150` only copies the
configured threshold through to `AggregatorConfig`).

**Resume/checkpoint.** None. `s7cmd` is a one-shot CLI; interruption (SIGINT)
cancels via a `PipelineCancellationToken` and exits 0, with no persisted state
[SRC s7cmd src/ls_bin/mod.rs:52-88 @ d589df7][DOC README §Non-Goals: no daemon].

## 3. Modes and tunables

**Mode** = a change to the request pattern *or* the output contract.
**Tunable** = magnitude only. Flags are on `s7cmd ls`, which builds the same
`s3ls-rs =1.0.3` engine as standalone `s3ls` — same listing engine, defaults,
and output formatters, by construction [SRC s7cmd Cargo.toml @ d589df7]. The
CLI surface is **not** byte-identical, though: `s7cmd` deliberately
hides/strips each subcommand's inherited `--auto-complete-shell` from its
help and completion output in favor of a single top-level flag
[SRC s7cmd src/cli.rs:400-449 @ d589df7]; standalone `s3ls` still exposes its
own `--auto-complete-shell` [DOC s3ls-rs README]. Full runtime equivalence
beyond the shared crate and this one known divergence is [INFERRED] from the
dependency pin, not a comparative run (see §9).

### Modes (each smoked — see §8)

| Mode | Flags | Request pattern / output contract | Evidence |
| --- | --- | --- | --- |
| recursive (parallel) | `-r` | Parallel prefix-recursion; full keyspace; `ListObjectsV2` | [SRC mod.rs:409-746][RUN recursive-tsv/*] |
| shallow (delimiter) | *(no `-r`)* | Single sequential `ListObjectsV2` with `delimiter="/"`; objects + `PRE` | [SRC mod.rs:783-787][RUN shallow-tsv/*] |
| all-versions | `-r --all-versions` | **Different API** `ListObjectVersions`; adds VersionId, possible delete-marker rows; IsLatest column only with `--show-is-latest` | [SRC mod.rs:156-246; config/args/mod.rs:303-312 (`--show-is-latest` requires `--all-versions`); display/columns.rs:163-171 (IsLatest gated on the flag)][RUN all-versions/*] |
| depth-limited | `-r --max-depth N` | Parallel recursion bounded to N; emits `PRE` at boundary (delimiter semantics via a distinct synthesis path) | [SRC mod.rs:682-699][RUN max-depth/root] |
| bucket-listing | *(no target)* | **`ListBuckets`** — **blocked anonymously** (see §8) | [SRC s7cmd ls_bin/mod.rs:34-49][RUN _capability/bucket-list] |
| Output: aligned (default) | *(default)* | Whitespace-aligned columns (DATE SIZE ...KEY) | [SRC display/aligned_formatter.rs][RUN recursive-aligned] |
| Output: TSV | `--tsv` | Tab-separated columns | [SRC display/tsv.rs][RUN recursive-tsv] |
| Output: one-line | `-1`/`--one` | Key (or prefix) per line, no columns | [SRC display/one_line_formatter.rs][RUN recursive-one] |
| Output: NDJSON | `--json` | One JSON object per line, S3-API field names | [SRC display/json.rs][RUN recursive-json] |

### Tunables (record; sweep in benchmark phase)

| Tunable | Default | Effect | Benchmark? | Evidence |
| --- | --- | --- | --- | --- |
| `--max-parallel-listings` | **64** | Semaphore cap on concurrent listing ops (1-65535) | **Yes — headline** | [DOC][SRC mod.rs:409-427 (acquisition at dispatch), :701-727 (per-task acquisition), :825 (semaphore construction, sized at :789)] |
| `--max-parallel-listing-max-depth` | 2 | Recursion depth that uses the delimiter discovery phase | **Yes** | [DOC][SRC mod.rs:581] |
| `--max-keys` | 1000 | Objects per page (1-1000) | Yes | [DOC] |
| `--no-sort` | off | Stream vs buffer-and-sort (memory down, order lost) | **Yes (memory)** | [DOC][RUN recursive-tsv-nosort] |
| `--parallel-sort-threshold` | 1,000,000 | Entry count that switches to rayon parallel sort | Yes (>1M only) | [DOC] |
| `--object-listing-queue-size` | 200000 | Bounded channel depth | Maybe | [DOC][SRC pipeline.rs:57] |
| `--rate-limit-api` | unset | Cap S3 API req/s (leaky-bucket) | Maybe | [DOC][SRC mod.rs:791-806] |
| `--aws-max-attempts` / `--initial-backoff-milliseconds` | 10 / 100 ms | SDK standard retry | For fault tests | [DOC][SRC client_builder.rs:154-160] |
| `--*-timeout-milliseconds` | unset | operation/attempt/connect/read timeouts | For fault tests | [SRC client_builder.rs:164-191] |
| `--disable-stalled-stream-protection` | off (protection ON) | Toggle SDK stalled-stream guard | Maybe | [SRC client_builder.rs:36-46] |
| `--show-*`, `--human-readable`, `--header`, `--raw-output`, `--reverse`, `--sort`, filters | — | Column/format/filter selection (client-side; **filters do not reduce API calls** [DOC]) | No (output only) | [DOC][SRC display/columns.rs] |

**Concurrency-cap note.** The default `--max-parallel-listings 64` **exceeds
this subject's `CONCURRENCY_CAP=16`.** It is configurable, so every recursive
smoke run pinned it to **16** (<= cap; one invocation at a time -> aggregate <= 16).
The default 64 is flagged for the benchmark phase (Open questions).

## 4. How to run it properly

**Quickstart (anonymous public bucket, the smoke path):**
```sh
s7cmd ls -r --tsv --show-storage-class --show-etag \
  --target-no-sign-request --target-region us-east-1 \
  s3://noaa-normals-pds/
```

**Large listings — the project's own guidance** [DOC README §Low memory usage,
§--no-sort, §Sorting detail]:
- Default (sorted) buffers everything; fine up to ~hundreds of thousands.
- For very large buckets, **`--no-sort`** streams at ~84 MB constant; if sorted
  output is needed, stream to a file and `sort(1)` externally (spills to disk):
  `s7cmd ls -r --no-sort --tsv s3://b/ > out.tsv; sort -t$'\t' -k3 out.tsv`.
- Parallelism benefits **prefix-hierarchical** buckets; a flat keyspace falls
  back to sequential (no speedup) [DOC §High performance].
- To cut **cost**, narrow the prefix (filters are client-side and do not reduce
  API calls) [DOC §Filters do not reduce API requests]; for recurring
  full-bucket enumeration the docs point to **S3 Inventory** instead [DOC].
- No hinted/two-pass workflow was found in the pinned source or docs —
  checked by searching both for "two-pass"/"hint" (no hits)
  [SRC sweep of s3ls-rs src/, README.md @ bf42067]; the only "two-pass" is the
  internal discovery->listing phase split.

**Auth setup.** Standard AWS chain (env, `~/.aws`, profiles, IMDS, SSO) [DOC
§Requirements]. **Anonymous:** `--target-no-sign-request` -> `s3ls-rs`
`S3Credentials::NoSign` -> `config_loader.no_credentials()`, which disables both
credential loading **and** request signing [SRC client_builder.rs:120-123 @
bf42067]. Passing `--target-region` explicitly remains appropriate for this
credential-starved harness — but not for the reason the upstream README gives.
`build_region_provider` uses the explicit `--target-region` first and then
falls back to `.or_default_provider()` for **both** `FromEnvironment` and
`NoSign` [SRC client_builder.rs:138-148 @ bf42067], and that default chain may
itself consult environment/profile/IMDS; the upstream claim that "no profile is
consulted" for a default region under anonymous access [DOC §Anonymous access]
does not hold against this code path. Supplying the region explicitly simply
keeps the run deterministic regardless of ambient config, rather than being
required by an absence of a fallback. Anonymous conflicts with
`--target-profile`/`--target-access-key` etc.

**Footguns.**
- **Trailing slash matters**: `s3://b/data` matches `data`, `data-backup/`,
  `database.txt`; `s3://b/data/` scopes to the folder [DOC §Trailing slash].
- A non-existent prefix returns empty and **exits 0** (a prefix is a filter, not
  a resource) [DOC].
- Default `--max-parallel-listings 64` can trip IMDS throttling on EC2 and load
  the target [DOC §--max-parallel-listings, §batch-run caveats].
- `s7cmd` targets **Amazon S3 only**; S3-compatible endpoints are as-is,
  unsupported [DOC §Scope].
- **Proxy:** `s7cmd`/`s3ls` honor `HTTP_PROXY`/`HTTPS_PROXY` automatically [DOC
  §Proxy support]. The study wrapper's `--env` guard **refuses** proxy variables
  (they would falsify `auth=anonymous`), so proxy behavior was not and should
  not be exercised here.

## 5. Output and observability

**Formats** (all share "one record per line, stable field order"):
- **aligned** (default): `DATE  SIZE  [STORAGE_CLASS] [ETAG] ... KEY`, columns
  padded; `PRE` marker + empty date for common prefixes [SRC display/columns.rs:29-141].
- **TSV** (`--tsv`): same columns, tab-joined; **ETag quote-trimmed**
  [SRC display/tsv.rs:15-21, columns.rs:137].
- **one-line** (`-1`): key/prefix only [SRC display/one_line_formatter.rs:27-30].
- **NDJSON** (`--json`): `{"Key","LastModified","ETag"(quoted),"Size",
  "StorageClass",...}`; common prefix -> `{"Prefix":...}` [SRC display/json.rs:15-66].
- mtime: `chrono` RFC3339 **seconds precision with `Z`** -> `YYYY-MM-DDTHH:MM:SSZ`
  in UTC (default; `--show-local-time` for local) [SRC display/mod.rs:169-176].
  Containers run `TZ=UTC`, and this mode prints an explicit `Z`, so timestamps
  are unambiguously UTC by construction.
- Keys: control chars escaped `\xNN` by default (`--raw-output` disables); every
  key in the smoke bucket is plain ASCII, so escaping is an **identity** here
  [SRC display/mod.rs:78-108].

**`normalize.sh` contract (per mode)** -> `key\tsize\tetag\tmtime\tstorage_class`:

| Mode(s) | Parse | Fields exposed |
| --- | --- | --- |
| `recursive-tsv`, `recursive-tsv-nosort`, `all-versions`, `max-depth`, `shallow-tsv` | TSV: `key=$NF`, `size=$2`, `etag=$4`, `mtime=$1`, `sc=$3`; `$2=="PRE"`-> prefix (all `-`) | all 5 (all-versions/shallow/max-depth: `PRE` rows key-only) |
| `recursive-aligned` | whitespace: `mtime=$1 size=$2 key=$3` (keys are space-free here) | key, size, mtime |
| `recursive-json` | `python3 json`: Key/Size/ETag(strip quotes)/LastModified/StorageClass; `{"Prefix"}`-> key-only | all 5 |
| `recursive-one` | whole line = key | key only |

**Metrics/observability the tool exposes:**
- **API-call counter** — an internal `AtomicU64` incremented once *before*
  each page fetch — i.e. a count of logical page-fetch operations, not a
  direct count of wire-level S3 HTTP requests — logged at completion as
  `Listing pipeline completed api_calls=N` under `-vv` (debug)
  [SRC s3ls-rs src/storage/s3/mod.rs:466,604; src/pipeline.rs:109-112 @
  bf42067]. The client is built with the AWS SDK's standard retry mode, up to
  10 attempts by default [SRC s3ls-rs src/storage/s3/client_builder.rs:153-160
  @ bf42067], so a single counted page fetch can correspond to more than one
  chargeable HTTP request under retries; establishing an exact wire-level
  request count needs SDK tracing or an external counter — a natural
  hand-off to the study's Phase 2 replay-server instrument. Captured in every
  receipt (see §8).
- `-vvv` (trace) logs each `ListObjectsV2 request bucket=... prefix=... delimiter=...
  max_keys=... continuation_token=...` — full request shape [DOC §Estimating API
  requests][SRC mod.rs:83-90]. Not needed at smoke scale; useful for the
  replay-server phase.
- `--json-tracing`, `--aws-sdk-tracing`, `--span-events-tracing`,
  `--disable-color-tracing` for structured/expanded logs [DOC §Observability].
- `--summarize` appends a total count/size line (not used in smoke).

Tracing goes to **stderr**; listing output to **stdout** — the two never mix,
so `-vv` was safe to enable on every run without polluting the verified stdout.

## 6. Failure surface

- **Memory growth** (default sorted): grows linearly with object count
  (~700-860 B/object); ~785 MB at 1.1 M objects per the vendor table [DOC
  §Low memory usage] — a **hypothesis** at scale, unmeasured here (smoke peaked
  at 120.8 MB for 148,917 keys, consistent with the model) [RUN recursive-tsv/full].
- **OOM behavior at scale**: not settleable at smoke scale; flagged for benchmark.
  `--no-sort` is the documented mitigation [DOC].
- **Interruption**: SIGINT cancels via the cancellation token and exits 0; no
  partial-state persistence [SRC ls_bin/mod.rs:52-88][3P not independently tested].
- **Truncation / bad pagination**: explicit `bail!` on a truncated page with no
  token, or a repeated token (anti-infinite-loop) [SRC mod.rs:506-522,637-666] —
  a genuine robustness feature, source-confirmed, not runtime-exercised.
- **Error handling**: any stage error cancels the pipeline [SRC
  s3ls-rs pipeline.rs:68-97]; the exit-code mapping itself is not in the
  pipeline but in `s7cmd`'s own dispatch/wrapper — arg errors from
  `Config::try_from` map to exit 2, and `ls_bin::run` maps pipeline/SDK errors
  through `exit_code_from_error` (default 1) [DOC §CLI exit codes][SRC s7cmd
  src/dispatch.rs:61-76 @ d589df7; s7cmd src/ls_bin/mod.rs:66-77 @ d589df7;
  s3ls-rs src/types/error.rs:33-37 @ bf42067].
- **Endpoint quirks**: Express One Zone buckets disable parallel listing by
  default (CommonPrefixes pollution during multipart uploads); opt in with
  `--allow-parallel-listings-in-express-one-zone` [DOC §S3 Express One Zone][SRC
  mod.rs:302-303,409-413]. Not testable on the smoke bucket.

## 7. Container

**Image.** No container-image channel was found in the README, release
assets, or on Docker Hub (404) [DOC README §Installation]. **GitHub
Packages/GHCR could not be enumerated** — the API returned 403 for missing
`read:packages`, and anonymous GHCR token acquisition was denied — so this
channel check is **incomplete**, not a confirmed "no image published
anywhere." It **does** ship a `Dockerfile`, so per Stage B I built from
**upstream's own Dockerfile at the pinned SHA** — the closest defensible
artifact to "what the project intends users to run."

- Build: multi-stage `rust:1-trixie` builder -> `cargo build --release` (fat LTO,
  `codegen-units=1`, `strip=symbols`) -> `debian:trixie-slim` runtime with
  `ca-certificates`; non-root `s7cmd` user; **`ENTRYPOINT ["/usr/local/bin/s7cmd"]`**
  [SRC s7cmd Dockerfile @ d589df7]. Build command:
  `docker build -t s7cmd:groundwork-v1.5.0 .` in the pinned checkout.
- Built image digest (local, never pushed — so no registry repo digest):
  `sha256:07091182512e74cde4bb897a97b1fc9a586757560c5008ae8c701d7fdb6974da`.
  Referenced to the wrapper as
  `s7cmd@sha256:0709...da` (a valid, resolvable digest-pinned ref).
- Version in-image: `s7cmd 1.5.0 (aarch64-unknown-linux-gnu), rustc 1.97.1`.
  The `--version` git-hash field is blank because the upstream `.dockerignore`
  excludes `.git/`, so `shadow-rs` has no repo to read — cosmetic only [OBS
  from the build; version number itself is correct].
- Live `ls --help` matches the `s3ls-rs` documented listing/tunable option
  set, **except** `--auto-complete-shell`, which `s7cmd` deliberately hides on
  every subcommand in favor of one top-level flag
  [SRC s7cmd src/cli.rs:400-449 @ d589df7] — the docs otherwise hid nothing
  [RUN _build/help-and-version.txt].

**Architecture matrix** (first-class deliverable):

| Distribution channel | amd64 | arm64 | Source |
| --- | --- | --- | --- |
| Upstream container image | — | — | none found in README/releases/Docker Hub; GitHub Packages/GHCR unverifiable (403 / anonymous token denied) [DOC] |
| Prebuilt binaries (GitHub Releases) | yes (glibc + musl) | yes (glibc + musl) | [DOC §Pre-built binaries] |
| Source build (this image) | yes (native on amd64 host) | yes (**built & smoked here, arm64**) | [RUN] |

The runner is **arm64 (aarch64)**, so the image was built and smoked **natively
(emulated=no)**. Both arches are supported natively across the channels that
exist (prebuilt binaries and source build — no container channel was found
across the registries checked, though GHCR could not be fully ruled out), so
the benchmark phase can pick either; **amd64 is the expected cross-tool common
denominator** — flagged in Open questions. Smoke produces no comparative
numbers, so the arch choice here is immaterial.

## 8. Smoke results

Bucket `noaa-normals-pds` (us-east-1), manifest snapshot 2026-07-17, sha256
`c78a...2adb`, 148,917 keys. **Every run anonymous** (`--target-no-sign-request`,
under the wrapper's credential-starved mode; `auth=anonymous` enforced). Image
`s7cmd@sha256:0709...da`, arch arm64 native. Concurrency pinned to
`--max-parallel-listings 16` (<= CONCURRENCY_CAP) on recursive modes; sequential
modes are 1 by construction. All verifier verdicts **PASS**.

| Mode | Scope | Exit | Wall (s) | peak_rss | api_calls | Verifier | Receipt |
| --- | --- | --- | --- | --- | --- | --- | --- |
| recursive-tsv | full (148,917) | 0 | 2.627 | 120.8 MB | **204** | PASS (all 5 fields) | receipts/smoke/recursive-tsv/full |
| recursive-tsv | normals-monthly/1991-2020/ (15,625) | 0 | 1.928 | 30.5 MB | 19 | PASS (all 5) | recursive-tsv/normals-monthly-1991-2020 |
| recursive-tsv | normals-annualseasonal/1981-2010/access/ (9,839) | 0 | 1.138 | 26.4 MB | 10 | PASS (all 5) | recursive-tsv/normals-annualseasonal-1981-2010-access |
| recursive-tsv | normals-hourly/ (2,549) | 0 | 0.495 | 23.1 MB | 17 | PASS (all 5) | recursive-tsv/normals-hourly |
| shallow-tsv | root, delimiter `/` (5 = 4 PRE + index.html) | 0 | 0.174 | 18.8 MB | 1 | PASS | shallow-tsv/root |
| shallow-tsv | normals-hourly/, delimiter `/` (6 PRE) | 0 | 0.177 | 19.1 MB | 1 | PASS | shallow-tsv/normals-hourly |
| max-depth | root `--max-depth 1`, delimiter `/` (5) | 0 | 0.182 | 19.1 MB | 1 | PASS | max-depth/root |
| all-versions | normals-hourly/ (2,549) | 0 | 0.990 | 23.9 MB | 17 | PASS (all 5) | all-versions/normals-hourly |
| recursive-aligned | normals-hourly/ (2,549) | 0 | 0.458 | 23.8 MB | 17 | PASS (key+size+mtime) | recursive-aligned/normals-hourly |
| recursive-json | normals-hourly/ (2,549) | 0 | 0.518 | 23.4 MB | 17 | PASS (all 5) | recursive-json/normals-hourly |
| recursive-one | normals-hourly/ (2,549) | 0 | 0.489 | 23.3 MB | 17 | PASS (keys only) | recursive-one/normals-hourly |
| recursive-tsv-nosort | normals-hourly/ (2,549) *(tunable)* | 0 | 0.579 | 22.4 MB | 17 | PASS (all 5) | recursive-tsv-nosort/normals-hourly |

**Request-behavior observations (from the tool's own `-vv` counter):**
- `Using parallel listing max_parallel=16 max_depth=2` confirms the parallel
  path engaged for recursive modes [RUN recursive-tsv/normals-hourly stderr].
- **Parallel issues more logical page fetches than a pure sequential
  minimum**: the full bucket took **204** counted page fetches vs the ~149-page
  sequential floor — exactly the documented effect of delimiter pages mixing
  objects and prefixes [DOC §API request calculation][RUN recursive-tsv/full].
  Corroborated as a page-fetch count; it is not a wire-level HTTP request
  count (see §5) — under the SDK's retry policy a single counted fetch can
  cost more than one actual request.
- Leaf-vs-branch prefix shape shows in the counts: the depth-3 leaf
  `.../1981-2010/access/` (9,839 keys) took only **10** calls (~ pure sequential
  drain), while the shallower `.../1991-2020/` (15,625) took **19**.
- shallow and `--max-depth 1` at root each make exactly **1** API call (single
  delimiter listing), confirming the boundary-synthesis path emits `PRE` without
  recursing [RUN max-depth/root, shallow-tsv/root].
- `--no-sort` peaked marginally lower than sorted at 2,549 keys (22.4 vs 23.1 MB);
  the documented 9x memory gap appears only at ~1 M keys — a scale hypothesis.

**Capability finding — bucket listing is blocked anonymously.** `s7cmd ls`
with no target calls `ListBuckets`; anonymously S3 returns a **307 redirect**
and the tool exits **1** (`Failed to list buckets`) [RUN
_capability/bucket-list; exit_code=1]. With `CREDS=none` this mode is
**blocked, not skipped** — untested-for-this-reason, receipt attached. The
same run confirms `no-sign-request: disabling credential loading and request
signing` in the tool's own debug output [OBS/RUN same receipt].

**Auth protocol.** Anonymous succeeded for every object-listing mode, so no
credentialed fallback was needed. `CREDS=none`, so no credentialed pass was run.

**Edge-case fidelity checks** (unicode / URL-special keys / directory marker /
multipart ETag): **deferred** — `EDGE_BUCKET=none`. The primary bucket is plain
ASCII, so escaping and byte-fidelity paths were not stressed.

## 9. Notable findings

- **The listing engine is a separate, independently-releasable crate — but
  the CLI surface it's wrapped in is not byte-identical to it.** `s7cmd`'s
  `ls` is a ~90-line wrapper that builds `s3ls_rs::ListingPipeline` from the
  `s3ls-rs` crate pinned at `=1.0.3` [SRC s7cmd Cargo.toml,
  src/ls_bin/mod.rs:15-88 @ d589df7]. Because it compiles the *same crate
  version*, the listing engine, defaults, and output formatters are the
  standalone `s3ls` tool's by construction [SRC]. The **flag set is not
  identical**, though: `s7cmd` deliberately hides/strips each subcommand's
  inherited `--auto-complete-shell` in favor of a single top-level flag
  [SRC s7cmd src/cli.rs:400-449 @ d589df7], and standalone `s3ls` still
  exposes its own `--auto-complete-shell` [DOC s3ls-rs README]; the committed
  `s7cmd ls --help` receipt correspondingly lacks it
  [RUN receipts/smoke/_build/help-and-version.txt]. `s7cmd` also runs a
  modified process-level wrapper: `src/ls_bin/mod.rs` documents dropping
  upstream's `load_config_exit_if_err` helper (which called
  `std::process::exit`) so a bad `Ls` config in a `batch-run` script doesn't
  kill the whole batch [SRC s7cmd src/ls_bin/mod.rs:1-12 @ d589df7]. I did
  **not** run standalone `s3ls` to compare behaviorally, so full **runtime**
  equivalence beyond the shared crate and this known CLI/wrapper divergence is
  [INFERRED] from the dependency, not a measured behavioral-identity result.
  Anyone benchmarking "s7cmd listing" is benchmarking `s3ls-rs 1.0.3`'s
  listing engine, wrapped. The `s7cmd src/ls_bin/mod.rs` header notes it was
  vendored from `s3ls-rs@0.4.1` (a stale comment — the actual dependency is
  `=1.0.3`) [SRC s7cmd Cargo.toml, src/ls_bin/mod.rs:1 @ d589df7].
- **"Fully AI-generated (human-verified)."** The project prominently states all
  `s7cmd`/`s3ls-rs` code, tests, and docs are Claude-Code-generated under human
  review, with a stated 96%+ test-coverage policy; `s3sync` is the lone
  human-written reference [DOC README]. Unusual provenance worth flagging to the
  study owner given the study's own "don't trust unverified prose" premise —
  though the source I read is coherent and well-tested.
- **Explicit anti-benchmark stance.** The README's Non-Goals reject
  "tool X is faster/uses less RAM" issues outright and disclaim any
  compatibility with `s3cmd`/`s4cmd`/`s5cmd`/`s6cmd` — "7 was simply the next
  number." A listing-speed study is measuring something the author declines to
  optimize for [DOC §Non-Goals].
- **Infinite-loop guards.** Bailing on a repeated continuation token is a
  deliberate defense against buggy S3-compatible endpoints [SRC mod.rs:515-522]
  — thoughtful for a tool that officially supports only real S3.
- **Directory-marker depth handling.** `key_depth` strips one trailing `/` so a
  `foo/bar/` marker object counts at the same depth as `foo/bar.txt`
  [SRC mod.rs:366-380] — the kind of edge the seeded fixture's `weird/marker/`
  key would exercise (deferred here).
- **Low adoption, active upkeep.** 8 stars, 0 forks, 0 open issues, but
  four-months-young with monthly dependency maintenance and a recent RUSTSEC
  fix (crossbeam-epoch) in v1.5.0 [3P git log; github API].

## 10. Open questions for the benchmark phase

1. **`--max-parallel-listings` sweep.** Default 64 exceeds the study's per-agent
   cap; smoke ran at 16. Sweep e.g. {1, 4, 16, 32, 64, 128, 256} against the
   campaign's <=32 aggregate cap to find the throughput knee and the point of
   diminishing/negative returns (more parallel = more delimiter overhead).
2. **`--max-parallel-listing-max-depth` sweep** {1,2,3,4}: trades discovery
   overhead against leaf-drain parallelism; interacts with bucket prefix shape.
3. **Flat-keyspace regression.** Parallelism collapses to sequential on buckets
   with no `/` hierarchy — a benchmark should include a flat bucket to expose
   this documented cliff (the seeded fixture's `flat/` prefix is ideal).
4. **Memory at scale & OOM behavior.** Default sorted mode's ~785 MB @ 1.1 M
   claim, and whether it exits-0-on-OOM, are unmeasured; compare sorted vs
   `--no-sort` on a >=1 M-key bucket, watching the tail window.
5. **`--no-sort` vs sorted throughput and memory** at scale (the 9x memory gap).
6. **`--max-keys` sensitivity** and `--rate-limit-api` under throttling.
7. **Architecture**: pick **amd64** as the cross-tool denominator (all channels
   support it natively); re-confirm no emulation. This report's smoke was arm64.
8. **API-cost accounting**: the built-in `api_calls` counter establishes
   logical page-fetches-per-listing and is a useful first-class metric
   alongside wall-clock, but under the SDK's retry policy it is not a
   wire-level HTTP request count (see §5) — accounting for chargeable
   requests needs SDK tracing or an external counter, a natural hand-off to
   the study's Phase 2 replay-server instrument.

## 11. Sources

**Docs** (accessed 2026-07-17):
- s7cmd README — https://github.com/nidor1998/s7cmd/blob/v1.5.0/README.md
- s3ls-rs README — https://github.com/nidor1998/s3ls-rs/blob/v1.0.3/README.md
- s7cmd repo metadata — https://api.github.com/repos/nidor1998/s7cmd

**Pinned source:**
- `s7cmd` @ `d589df7ce691edbede05fc9a691ab1787cdb6b9e` (tag v1.5.0) —
  `<sources>/s7cmd`
- `s3ls-rs` @ `bf42067537da476b157b5d289a3e72d049b60db2` (tag v1.0.3) —
  `<sources>/s3ls-rs`
  Key files: `src/pipeline.rs`, `src/lister.rs`, `src/storage/s3/mod.rs`,
  `src/storage/s3/client_builder.rs`, `src/display/{columns,tsv,json,one_line_formatter,mod}.rs`

**Third-party:** none of substance found — `s7cmd` returns no distinct web
results beyond its own GitHub; the author's sibling tools (`s3sync`, `s3rm-rs`)
are the only related material. Low external coverage is itself a finding.

**Receipts** (this report): `tools/s7cmd/receipts/smoke/` — 12 mode/scope
receipts + `_build/help-and-version.txt` + `_capability/bucket-list`. Large
payloads (>100 KB) at `<data>/receipts/s7cmd/` with sha256 in each
`run.meta` (redacted + secret-scanned before hashing by the wrapper).
