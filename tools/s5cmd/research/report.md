# s5cmd — independent listing report

Groundwork report for the s3-listing-study. Derived independently from primary
sources (the tool's own docs, its source at a pinned tag, and my own smoke
runs) before reading the inherited dossier. Every claim carries an evidence
label: `[DOC]` docs, `[SRC file:line @ sha]` pinned source, `[RUN receipt]`
my own smoke run, `[3P]` third-party, `[OBS]` observed-but-not-wrapper-recorded,
`[INFERRED]`. Pinned short SHA for all `[SRC]` anchors: **991c9fb**.

---

## 1. Metadata

| | |
| --- | --- |
| Tool | `s5cmd` — "very fast S3 and local filesystem execution tool" [DOC README.md] |
| Repository | https://github.com/peak/s5cmd (canonical upstream; `go.mod` module `github.com/peak/s5cmd/v2` [SRC go.mod:1 @ 991c9fb]) |
| Pinned tag | `v2.3.0` (latest stable release; released 16 Dec 2024 [DOC CHANGELOG.md]) |
| Pinned commit | `991c9fbc16709341b4bac04513232a1445941f63` |
| Language | Go 1.20 (`go.mod`); the release Dockerfile builds with golang:1.22 [SRC go.mod:3, Dockerfile:1 @ 991c9fb] |
| License | MIT (Copyright 2020 Peak) [SRC LICENSE:1 @ 991c9fb] |
| AWS SDK | aws-sdk-go **v1** v1.44.298 (not the v2 SDK) [SRC go.mod:6 @ 991c9fb] |
| Upstream health | Active, not archived. `master` default branch, last push 2025-06-13; **189 open issues+PRs** (`open_issues_count`, which GitHub counts inclusive of PRs); 4,120 stars. v2.3.0 is the latest cut release; development continues on `master` (HEAD `54d6a8a`, 2025-06-13) [3P GitHub API, accessed 2026-07-17] |
| Image | `peakcom/s5cmd@sha256:2ff939e2ee3c76adcadd78dbfc3e2569b18a3743ed9dcfccb1ec589af7fb9903` (tag `v2.3.0`, Docker Hub) [RUN receipts/smoke/recursive] |
| Tool version (in image) | `v2.3.0-991c9fb` — self-reported by `s5cmd version` in the pinned image; the version string embeds the pinned commit `991c9fb`, linking the image bytes to the checkout [RUN receipts/smoke/_capability/observability/version.stdout.txt] |
| Report date | 2026-07-17 (UTC) |

---

## 2. How it works

**Listing is a single, serial, paginated stream — s5cmd does *not* parallelize
LIST.** This is the load-bearing finding and it holds against source:

- `ls` issues one `ListObjectsV2PagesWithContext` call per source URL and pushes
  each object onto an **unbuffered** channel `objCh := make(chan *Object)`, in a
  single goroutine [SRC storage/s3.go:309,317 @ 991c9fb]. The SDK paginator
  walks the continuation-token loop internally; s5cmd does not manage
  `ContinuationToken`/`NextMarker` itself.
- `ls` consumes that channel with a plain serial `for object := range
  client.List(...)` loop — no worker pool, no fan-out [SRC command/ls.go:197 @
  991c9fb]. `du` is identical [SRC command/du.go:153 @ 991c9fb].
- The `parallel` worker pool (`--numworkers`, default **256**) parallelizes only
  the *downstream operations* — the `cp`/`rm`/`mv` transfer or delete of each
  object — never the LIST that enumerates them [SRC command/app.go:18,
  parallel/parallel.go @ 991c9fb; INFERRED from the pool being consumed only in
  cp/rm/mv, not in ls/du]. `cp` still lists serially via one `client.List`, then
  dispatches each returned object as a `parallel.Task` [SRC command/cp.go, command/expand.go @ 991c9fb].
- Because the channel is unbuffered, listing is throttled to the consumer's
  print speed (back-pressure) [SRC storage/s3.go:309 @ 991c9fb; INFERRED].
- **Observability corroborates it at runtime.** `--log trace` writes the AWS SDK
  request/response records to stdout; a 3-page trace of `normals-hourly/` shows
  **one** HeadBucket region probe then **sequential** ListObjectsV2 pages, each
  carrying the previous page's `continuation-token` — a single serial chain, no
  concurrent LIST in flight for one `ls` [OBS receipts/smoke/_capability/observability].
  (Runtime evidence for *one* `ls`; it cannot by itself exclude parallelism a
  hypothetical sharding layer might add — but source shows there is none.)

**API dispatch.** `S3.List` chooses the API by URL flags [SRC storage/s3.go:158-167 @ 991c9fb]:
`--all-versions`/version-id -> **ListObjectVersions** [SRC storage/s3.go:187];
`--use-list-objects-v1` -> **ListObjects (v1)** [SRC storage/s3.go:408]; otherwise
-> **ListObjectsV2** (default) [SRC storage/s3.go:317].

**Pagination & page size.** The SDK's built-in `*PagesWithContext` paginator
drives pagination. **`MaxKeys` is never set** — no `MaxKeys` reference anywhere
in `command/` or `storage/`, and no flag to set it [SRC storage/s3.go:299-341 @
991c9fb; INFERRED from the absence of any MaxKeys assignment]. With the field
omitted, S3 returns its own maximum of **1,000 keys/page** — which is also S3's
hard ceiling, so this is *not* a s5cmd deficit relative to other real-S3
clients: no client can exceed 1,000 [DOC AWS ListObjectsV2 API]. The full bucket
(148,917 keys) therefore spans ~149 pages [INFERRED from 148,917 ÷ 1,000; a
3-page `--log trace` of `normals-hourly/` confirms the 1,000-key page size,
receipts/smoke/_capability/observability].

**Keyspace division.** None. There is no bisection, cut-point, or common-prefix
recursion to parallelize listing. A wildcard becomes **one recursive List plus a
client-side regex filter**: the prefix sent to S3 is only the literal portion
before the first glob character, and the wildcard tail is compiled to a regex
applied per key as pages arrive [SRC storage/url/url.go:259-285, storage/s3.go:341 @ 991c9fb].
So `s3://b/pref/*/*.gz` = one List at `Prefix=pref/` (no delimiter) then filter,
not multiple List calls.

**Delimiter vs recursive.** Decided by whether the path contains a glob
character [SRC storage/url/url.go:264-270 @ 991c9fb]:
- **No glob** -> `Delimiter="/"`, `Prefix=<full path>` -> shallow/delimiter listing;
  CommonPrefixes are surfaced as `DIR` rows [SRC storage/s3.go:305-307,318-332 @ 991c9fb].
- **Glob present** -> `Delimiter=""` (fully recursive) + client-side filter.

`ls`/`du` also support a repeatable client-side `--exclude` wildcard, applied
per object after the listing returns [SRC command/ls.go:208, command/wildcard.go:30 @ 991c9fb].
There is **no `--include`** on `ls`/`du` (include exists only on cp/sync).

**Retry / backoff / timeout.** A `customRetryer` wraps the SDK `DefaultRetryer`;
`NumMaxRetries` comes from the global `--retry-count`/`-r` flag, **default 10**
[SRC command/app.go:19, storage/s3.go:1289,1376-1408 @ 991c9fb]. `ShouldRetry`
additionally retries `InternalError`, `RequestTimeTooSkewed`, `SlowDown`, and
connection-reset/timed-out substrings; it does **not** retry expired/invalid
tokens [SRC storage/s3.go:1390-1408 @ 991c9fb]. Backoff is delegated to the
embedded SDK `DefaultRetryer` (s5cmd overrides only `ShouldRetry`, not the delay)
[SRC storage/s3.go:1376-1408 @ 991c9fb]; that retryer's delay is documented as
exponential-with-jitter [3P aws-sdk-go DefaultRetryer]. **No explicit
per-request timeout or context deadline is set on List** [SRC storage/s3.go:299-317 @ 991c9fb; INFERRED from absence].

**Memory model.** Streaming. `ls` prints each object as it comes off the
unbuffered channel — no accumulate-then-dump, no in-memory key buffer, and **no
sort** (output order is exactly S3's returned page order) [SRC command/ls.go:197-221 @ 991c9fb].
`du` accumulates only per-storage-class totals, not the key list [SRC command/du.go:142,168 @ 991c9fb].
(`sync` uses `extsort` to buffer/sort, unlike the streaming `ls`/`du`; it is out
of scope here [SRC command/sync.go @ 991c9fb; INFERRED that `ls`/`du` are the
only in-scope consumers].) At smoke, `peak_rss` stayed ~40 MB for full recursive and
~53 MB for all-versions on a 148,917-key bucket [RUN receipts/smoke/recursive, receipts/smoke/allversions] —
consistent with streaming, but a smoke-scale observation, not a scale claim.

**Ordering assumptions.** Relies on S3's returned lexical order; does not re-sort
in the listing path [SRC command/ls.go:197 @ 991c9fb; INFERRED].

**Resume / checkpoint.** None. Each `ls` starts a fresh List from the prefix;
there is no marker persistence or checkpoint file in the listing path [SRC command/ls.go, storage/s3.go @ 991c9fb; INFERRED from absence].

---

## 3. Modes and tunables

A **mode** changes the request pattern or output contract; a **tunable** only
changes magnitude. All flags below verified in the live `--help` and source.

### Modes (each smoked — see section 8)

| Mode | Invocation shape | Request pattern | Evidence |
| --- | --- | --- | --- |
| recursive | `ls "s3://b/*"` | ListObjectsV2, Delimiter="" , client-side filter | [SRC s3.go:317, url.go:268][RUN recursive] |
| delimiter | `ls "s3://b/"` | ListObjectsV2, Delimiter="/", CommonPrefixes as DIR | [SRC s3.go:305-307,318][RUN delimiter] |
| json | `--json ls "s3://b/*"` | Same request as recursive; JSON output contract | [SRC ls.go:317][RUN json] |
| listv1 | `--use-list-objects-v1 ls "s3://b/*"` | Legacy **ListObjects v1** API | [SRC s3.go:408][RUN listv1] |
| allversions | `ls --all-versions "s3://b/*"` | **ListObjectVersions** API | [SRC s3.go:187][RUN allversions] |
| fullpath | `ls --show-fullpath "s3://b/*"` | Same ListObjectsV2 request; output contract = absolute paths only (no metadata columns) | [SRC ls.go:93,253][RUN fullpath] |
| fanout | N per-prefix `ls` invocations, unioned (or one `s5cmd run <file>`) | Manual keyspace partition — N independent serial ListObjectsV2 chains | [RUN fanout/union][OBS _capability/run-fanout] |

> The **fanout** mode was added in Stage D because the inherited dossier names it
> mandatory (honest mixed provenance). It is not a distinct s5cmd algorithm — it
> is the user-built workaround for s5cmd's lack of native listing parallelism:
> partition the keyspace by prefix and list each prefix separately, either as N
> separate `s5cmd ls` invocations (verified here via `--scope union`) or in one
> `s5cmd run <file>` process whose worker pool runs the lines in parallel.

### Tunables (record; benchmark sweeps flagged)

| Flag | Default | Effect on listing | Sweep? | Evidence |
| --- | --- | --- | --- | --- |
| `--retry-count`/`-r` | 10 | Max retries per request (List included) | **Yes** — retry pressure under throttling | [SRC app.go:19] |
| `--numworkers` | 256 | No effect on a **single** `ls`'s LIST chain; but it **sizes the `run` fan-out** — how many `ls` lines execute concurrently, i.e. how many prefix listings run in parallel | **Yes, for the fan-out mode** — this is the fan-out's concurrency knob (and the campaign concurrency cap binds it × shard count) | [SRC command/app.go:18, command/run.go:76] |
| page size (`MaxKeys`) | 1000 (S3 ceiling) | Not settable — no flag; S3 caps at 1000 regardless of client | N/A (fixed at the service ceiling) | [SRC storage/s3.go @ 991c9fb; DOC AWS API] |
| `-e`/`--etag` | off | Adds ETag column (text) | No | [SRC ls.go:71][RUN] |
| `-s`/`--storage-class` | off | Adds storage-class column (text) | No | [SRC ls.go:80][RUN] |
| `-H`/`--humanize` | off | Human-readable sizes (breaks byte parsing) | No | [SRC ls.go:75] |
| `--show-fullpath` | off | Prints only full `s3://…` paths, drops all metadata columns | No | [SRC ls.go:93,253] |
| `--exclude <pat>` | none | Client-side post-filter (repeatable); shrinks result set | No | [SRC ls.go:85,208] |
| `--request-payer` | none | Adds requester-pays header to ListObjectsV2/V1; **not** wired into `listObjectVersions` (allversions mode omits it) | Situational | [SRC storage/s3.go:302; not set at s3.go:169] |
| `--endpoint-url` | none | Non-AWS S3-compatible endpoint (env `S3_ENDPOINT_URL`) | N/A | [SRC app.go:44, s3.go:1273] |

> **Note.** `-e`/`-s` do not change the request (ListObjectsV2 already returns
> ETag and StorageClass); they only widen the text output. Every smoked text
> mode ran with `-e -s` so the verifier could assert all five contract fields.

---

## 4. How to run it properly

**Quickstart (anonymous public bucket, recursive):**
```sh
s5cmd --no-sign-request ls "s3://noaa-normals-pds/*"
```

**Recommended configuration for a large listing.** s5cmd publishes no
listing-specific tuning guidance — its benchmarks and "run it properly" advice
are all about *transfer* throughput (`--numworkers`, `--concurrency`,
`--part-size`), none of which touch listing [DOC README.md § Benchmarks; INFERRED].
For listing there is essentially nothing to tune: page size is fixed at 1000,
listing is single-threaded regardless of `--numworkers`, and the only knob that
affects a List is `--retry-count`. The practical levers are:
- Scope tightly. A literal prefix in the URL (`ls "s3://b/pref/*"`) is sent as
  the S3 `Prefix`; a leading `*` forces a full-bucket walk [SRC url.go:268].
- Prefer a **delimiter** listing (`ls "s3://b/pref/"`, no glob) when you only
  need one level — it returns CommonPrefixes instead of every key [SRC url.go:265].
- For non-AWS/older gateways lacking ListObjectsV2, `--use-list-objects-v1`
  [DOC --help]. (At smoke it was markedly slower even against real S3 — see section 8.)

**Two-pass / hinted workflow.** No native keyspace-splitting or prefix-hinting
mode exists. Parallel enumeration is a **user-built workaround**: issue N
`s5cmd ls` invocations over disjoint prefixes. s5cmd's own batch runner executes
such a list in parallel — `s5cmd run <file>` (commands file **positional**, or
piped on stdin; there is **no `-f` flag** in v2.3.0 — see § 9) [OBS
_capability/run-fanout]. Smoked as the `fanout` mode: 4 top-level-prefix shards
+ an unprefixed-remainder shard, verified complete via `--scope union` (PASS,
148917 keys, 0 duplicates) [RUN receipts/smoke/fanout/union]. The plan must
include the **root-level remainder** (`index.html` lives under no prefix); a
prefix-only fan-out silently drops it.

**Auth setup.** Unsigned access is the global **`--no-sign-request`** flag,
which wires `credentials.AnonymousCredentials` into the client [SRC storage/s3.go:1242-1244 @ 991c9fb];
it is mutually exclusive with `--profile`/`--credentials-file` [SRC command/app.go:110-117 @ 991c9fb].
Signed access uses the standard AWS chain, or `--profile`/`--credentials-file`.
**Region:** `ls`/`du` have **no region flag** — region is auto-detected via
`s3manager.GetBucketRegion`, defaulting to `us-east-1` when undetectable [SRC storage/s3.go:1320,1344 @ 991c9fb].
This worked unsigned against the us-east-1 smoke bucket [RUN receipts/smoke/*].

**Footguns.**
- A leading `*` (`ls "s3://b/*"`) walks the whole bucket serially at 1000
  keys/page — there is no parallel fast-path to fall back on.
- `-H`/`--humanize` turns sizes into `4.1K` and will break any byte-exact
  downstream parse.
- `--show-fullpath` drops size/mtime/etag entirely — full paths only.
- v2.3.0 changed `ls` on an **empty** bucket to exit **0** (was 1); a
  non-existent bucket still exits 1 [DOC CHANGELOG.md v2.3.0].

---

## 5. Output and observability

**Text (default).** Column layout with `-e -s`:
`date time storage-class etag size relkey` — the format string is
`"2006/01/02 15:04:05"` then storage-class/etag/size/relative-path [SRC
command/ls.go:248,258-311 @ 991c9fb], and paths are **relative to the query
prefix** [SRC command/ls.go:285,301 @ 991c9fb][RUN]. DIR rows (delimiter mode)
print `DIR <prefix/>` with the metadata columns blank [RUN receipts/smoke/delimiter].
The timestamp has no offset marker [SRC command/ls.go:248]; because containers
run `TZ=UTC` and the SDK parses `LastModified` as UTC, it is UTC by construction
[INFERRED].

**JSON (`--json`).** One object per line: `{"key":"s3://bucket/key","etag":…,
"last_modified":"…Z","type":"file","size":N,"storage_class":…}` — the JSON is
`strutil.JSON(l.Object)` of the `storage.Object` struct [SRC command/ls.go:316-318 @ 991c9fb].
Observed: `key` is the **absolute** `s3://bucket/key` URL and `last_modified` is
already RFC3339 `…Z` [RUN receipts/smoke/json].

**`normalize.sh` contract (this tool's adapter):**
- `recursive`/`listv1`: text columns; reconstruct `full_key = <prefix> + relkey`
  (relative-path reconstruction, section 2); emit all five fields.
- `allversions`: same as recursive plus a trailing versionID token (`null` on a
  non-versioned bucket) which is dropped. **Caveat:** dropping the versionID is
  only sound because the smoke bucket is **non-versioned** (each key appears
  once). On a versioned bucket multiple versions of one key would collapse into
  duplicate key records; the verifier would then (correctly) flag duplicates. The
  `allversions` PASS therefore validates the ListObjectVersions *request/output
  contract*, not multi-version/delete-marker fidelity — that needs a versioned
  fixture (deferred with the edge cases).
- `delimiter`: DIR rows -> `key` only, other fields `-`; object rows -> five fields.
- `rootkeys`: the fan-out union's remainder adapter — same raw input as
  `delimiter` (root listing) but DIR common-prefix rows are **dropped**, leaving
  only the unprefixed object keys the union's remainder is checked against.
- `json`: `.key` stripped of `s3://bucket/`, `.last_modified` used verbatim;
  no prefix prepend (key is absolute).

**Metrics/counters.** `--stat` prints an **operation** tally (`ls 1 0 1`) —
high-level, not S3 API calls. `--log debug` emits no per-request records. **But
`--log trace` writes the full AWS SDK request/response records to stdout**, so
the API-call/page count *is* obtainable by counting them: a 3-page trace of
`normals-hourly/` shows 1 HeadBucket (region probe) + 3 sequential ListObjectsV2
pages [OBS receipts/smoke/_capability/observability]. s5cmd surfaces no built-in
numeric counter, and the wrapper runs did not enable trace (it would bloat every
payload with the request dump). Scale request-shape capture defers to the
study's replay-server phase.

---

## 6. Failure surface

- **Memory growth:** `ls` streams and does not buffer keys [SRC command/ls.go:197 @ 991c9fb],
  so unbounded listing-memory growth is not expected. Smoke stayed ~40–53 MB
  [RUN recursive, allversions]. Whether that holds at 10^7–10^8 keys is a
  scale question smoke cannot answer — hypothesis, not verified.
- **Interruption / resume:** no checkpoint; an interrupted `ls` restarts from
  the prefix [SRC @ 991c9fb; INFERRED].
- **Truncated responses / retries:** the SDK paginator handles continuation
  tokens; s5cmd's `customRetryer` retries `SlowDown`/`InternalError`/skew/
  connection errors up to `--retry-count` (10) [SRC storage/s3.go:1390-1408 @ 991c9fb].
  A run emitting gigabytes of retry noise is possible in principle; not observed
  at smoke (0 bytes stderr on every run) [RUN].
- **No per-request timeout** on List [SRC @ 991c9fb; INFERRED] — a hung
  connection relies on the SDK/OS defaults, not an s5cmd deadline. Hypothesis
  for the failure-injection phase.
- **Endpoint quirks:** `--use-list-objects-v1` exists precisely for gateways
  without ListObjectsV2 [DOC --help]; path-style vs virtual-host is auto-chosen
  [SRC storage/s3.go:1258,1428 @ 991c9fb].

---

## 7. Container

**Image chosen:** upstream `peakcom/s5cmd:v2.3.0`, pinned by digest
`sha256:2ff939e2ee3c76adcadd78dbfc3e2569b18a3743ed9dcfccb1ec589af7fb9903`. This
is upstream's own published image (README § Docker -> `docker pull peakcom/s5cmd`)
[DOC README.md:116-124] — the most defensible "what users actually run", so no
self-built Dockerfile is staged. Entrypoint is `["/s5cmd"]`, so `run.sh` argv
starts at the global flags/subcommand [RUN receipts/smoke/recursive].

**Architecture matrix (upstream multi-arch manifest list):**

| Channel | amd64 | arm64 | other |
| --- | --- | --- | --- |
| Upstream image `peakcom/s5cmd:v2.3.0` | native | native | ppc64le, arm/v6, arm/v7, 386 [OBS `docker manifest inspect peakcom/s5cmd:v2.3.0`] |
| Prebuilt binaries (Releases) | yes | yes | goreleaser targets amd64/arm64 for linux/macOS/windows [SRC .goreleaser.yml @ 991c9fb] |
| Source build (Go) | yes | yes | any Go cross-compile target [DOC README.md § Build from source; INFERRED — pure-Go, CGO_ENABLED=0 in the Dockerfile] |

Native on **both** amd64 and arm64 — no common-denominator problem for the
benchmark phase (amd64 is the expected choice; s5cmd meets it natively).

**Smoke ran on:** arm64 (image arm64 on host aarch64), **not emulated** [RUN receipts/smoke/*].

---

## 8. Smoke results

Bucket `noaa-normals-pds` (us-east-1, 148,917 keys, manifest sha256
`c78a827…992adb`). **Pre-flight:** re-listed anonymously with the pinned harness
client; sorted-set byte-identical to the manifest (both hash to
`8b5b584…6983aac`) — **no drift** [RUN receipts/smoke/_capability/preflight]. All modes ran
**unsigned** (`auth=anonymous`, credential-starved). `CREDS=none`, so no
credentialed pass. `EDGE_BUCKET=none`, so unicode/weird-key/multipart-ETag
fidelity checks are **deferred**.

| Mode | Invocation (argv after entrypoint) | Scope | Exit | Wall | Verdict | Receipt |
| --- | --- | --- | --- | --- | --- | --- |
| recursive | `--no-sign-request ls -e -s s3://b/*` | full | 0 | 16.96s | **PASS** 148917 | `receipts/smoke/recursive` |
| recursive | `… ls -e -s s3://b/normals-hourly/*` | prefix | 0 | 0.54s | **PASS** 2549 | `receipts/smoke/recursive-hourly` |
| recursive | `… ls -e -s s3://b/normals-monthly/1991-2020/*` | prefix | 0 | 2.25s | **PASS** 15625 | `receipts/smoke/recursive-monthly` |
| recursive | `… ls -e -s s3://b/normals-annualseasonal/1981-2010/access/*` | prefix | 0 | 1.51s | **PASS** 9839 | `receipts/smoke/recursive-annual` |
| delimiter | `--no-sign-request ls -e -s s3://b/` | delimiter `/` | 0 | 0.16s | **PASS** 5 | `receipts/smoke/delimiter` |
| json | `--json --no-sign-request ls s3://b/*` | full | 0 | 15.77s | **PASS** 148917 | `receipts/smoke/json` |
| listv1 | `--no-sign-request --use-list-objects-v1 ls -e -s s3://b/*` | full | 0 | 70.27s | **PASS** 148917 | `receipts/smoke/listv1` |
| allversions | `--no-sign-request ls --all-versions -e -s s3://b/*` | full | 0 | 86.99s | **PASS** 148917 | `receipts/smoke/allversions` |
| fullpath | `--no-sign-request ls --show-fullpath s3://b/*` | full | 0 | 17.10s | **PASS** 148917 (keys only) | `receipts/smoke/fullpath` |
| fanout | 4 prefix shards (`ls s3://b/normals-{monthly,daily,annualseasonal,hourly}/*`) + `rootkeys` remainder | union | 0 | 0.16–5.58s each | **PASS** 148917 | `receipts/smoke/fanout/` (`union/union-verify.md`) |

**Fan-out (`fanout`)** — added in Stage D at the dossier's prompt. Each of the 4
top-level-prefix shards PASSed its own prefix scope (48796 / 48787 / 48784 /
2549); the unprefixed remainder (`rootkeys` adapter: root delimiter listing with
DIR common-prefixes dropped) covered `index.html`; `--scope union` over all five
returned **PASS: 148917 keys, 0 cross-shard duplicates, 0 missing, 0 extra,
structurally complete** [RUN receipts/smoke/fanout/union/union-verify.md]. The
in-process `s5cmd run <file>` orchestration of the same partition also ran clean
(148917 distinct keys, exit 0) [OBS receipts/smoke/_capability/run-fanout].

Every verifier verdict: `dups=0 missing=0 extra=0 fields=0` (all five contract
fields asserted where the mode exposed them). Delimiter returned exactly the 4
CommonPrefixes + `index.html` (the root-level key) expected at `/`.

**Request-behavior observations.** No API-call counter or per-request log at any
level [OBS _capability/observability/]. Durations are facts about each run, not
comparative numbers — but note the *within-tool* shape: ListObjectsV2 recursive
(~17s) vs the same walk via **v1** (~70s) vs **ListObjectVersions** (~87s, and
peak_rss ~53 MB vs ~40 MB). These are single-run smoke observations flagged for
the benchmark phase, not established results.

**Deferred:** edge-case fidelity checks (unicode, URL-special keys, directory
marker, multipart ETag) — `EDGE_BUCKET=none`.

---

## 9. Notable findings

- **"Blazing fast" is about transfers, not listing.** s5cmd's reputation and its
  own benchmarks are upload/download throughput via its transfer worker pool
  (`--numworkers`, default 256 today; the cited 2019 Robinson benchmark tuned
  concurrency separately, not at this default) [DOC README.md § Overview/Benchmarks; 3P Robinson].
  The **listing** path is an ordinary serial SDK paginator — one goroutine, one
  continuation-token stream, 1000 keys/page, no fan-out [SRC storage/s3.go:309,317 @ 991c9fb].
  For a listing benchmark, s5cmd is expected to behave like any single-threaded
  ListObjectsV2 client; its transfer parallelism does not carry over. [INFERRED
  from source; the central hypothesis for the benchmark phase.]
- **Back-pressure by unbuffered channel.** The List producer blocks until the
  printer consumes each object [SRC storage/s3.go:309 @ 991c9fb] — listing speed
  is coupled to stdout consumption, an unusual coupling worth watching when
  output is piped vs discarded.
- **Wildcards are client-side.** `s3://b/pref/*/*.gz` fetches *every* key under
  `pref/` and regex-filters locally [SRC storage/url/url.go:259-285 @ 991c9fb] —
  a broad glob is not cheaper than a full prefix walk.
- **Relative-path output.** `ls` prints paths relative to the query prefix, not
  full keys [SRC command/ls.go:285 @ 991c9fb] — a real footgun for anyone
  post-processing scoped listings (and the reason `normalize.sh` takes the
  prefix). `--json` and `--show-fullpath` both give absolute keys instead.
- **v1 and ListObjectVersions were far slower at smoke** (~4x–5x the V2 wall
  time for the same key set) — plausibly smaller effective page handling or
  per-page cost; flagged as a hypothesis, not a result [RUN listv1, allversions].
- **Empty-bucket exit code flipped** to 0 in v2.3.0 [DOC CHANGELOG.md] — a
  listing-semantics change that matters to scripts treating exit code as "found
  anything".
- **`--log trace` is the only request-level window.** It dumps the full AWS SDK
  request/response records to stdout — so the API/page count and the sequential
  continuation-token chain are visible [OBS _capability/observability]. `--log
  debug` shows nothing per-request, and `--stat` counts operations, not calls.
  No built-in numeric API counter exists. Also visible in the trace: a
  **HeadBucket** region probe precedes every listing (the `GetBucketRegion`
  auto-detect), an extra round trip worth noting for latency accounting.
- **`s5cmd run` batch flag is positional, not `-f`.** The fan-out workaround runs
  through `s5cmd run <file>` (or stdin); `run -f <file>` fails with
  `Incorrect Usage: flag provided but not defined: -f` in v2.3.0 [OBS
  _capability/run-fanout]. `run --help` says it executes the listed commands
  "in parallel" — that worker-pool concurrency is the only way s5cmd lists
  multiple prefixes concurrently. The fan-out covers the bucket exactly (union
  PASS) but its *speed* vs a native parallel lister is a benchmark question.

---

## 10. Open questions for the benchmark phase

1. **Serial listing at scale.** Confirm the serial single-stream model dominates
   wall-clock at 10^6–10^8 keys, and quantify vs parallel-capable tools. The
   ratio should be ~the other tool's effective LIST concurrency (RTT-independent).
2. **Fixed 1000-key page size.** Not tunable in s5cmd — but 1,000 is **S3's own
   ceiling**, so this is not a disadvantage vs other real-S3 clients (none can
   exceed it); it only matters vs tools that **parallelize pages** across sharded
   prefixes. Quantify the serial-page-count cost against such tools.
3. **v1 vs v2 vs versions cost.** Smoke showed ~17s / ~70s / ~87s for the same
   bucket. Pin down whether this is per-page overhead, page-size, or response
   size, under the fair-timing harness. **Sweep:** all three APIs.
4. **`--retry-count` under throttling.** The only listing knob. **Sweep:** e.g.
   {1, 10, 50} against a throttling-prone target.
5. **Memory at scale.** Streaming suggests flat memory; verify no hidden
   accumulation (and that `--all-versions`' higher smoke RSS doesn't grow
   super-linearly). Memory is a headline benchmark claim.
6. **Fan-out workaround performance.** Completeness is settled (union PASS,
   `s5cmd run <file>` covers the bucket exactly). What smoke cannot answer is
   whether the fan-out is *competitive* with a native parallel lister when tuned
   — the dossier's claim #5. **Sweep** `--numworkers` and shard count/prefix
   granularity on `s5cmd run`; this is likely the most-quoted number for this
   tool, so it must be measured under the fair-timing harness, not assumed.
7. **Architecture.** Native amd64 + arm64; run on amd64 (common denominator).

---

## 11. Sources

**Primary — source (pinned `991c9fb`, tag v2.3.0):** `go.mod`, `LICENSE`,
`Dockerfile`, `CHANGELOG.md`, `command/ls.go`, `command/du.go`,
`command/app.go`, `command/cp.go`, `command/expand.go`, `command/wildcard.go`,
`storage/s3.go`, `storage/url/url.go`, `parallel/parallel.go`.

**Primary — docs (accessed 2026-07-17):**
- README: https://github.com/peak/s5cmd/blob/v2.3.0/README.md
- CHANGELOG: https://github.com/peak/s5cmd/blob/v2.3.0/CHANGELOG.md
- Live `--help` / `version` from the pinned image [RUN].

**Third-party (accessed 2026-07-17):**
- GitHub repo metadata API (health/stars/issues): https://api.github.com/repos/peak/s5cmd
- Joshua Robinson, "s5cmd for high performance object storage" (transfer
  benchmark cited in README) — https://medium.com/@joshua_robinson/s5cmd-for-high-performance-object-storage-7071352cc09d [3P, context only]

**Pinned commit:** `991c9fbc16709341b4bac04513232a1445941f63`

**Receipt index (all `auth=anonymous`, image digest
`sha256:2ff939e2ee3c76adcadd78dbfc3e2569b18a3743ed9dcfccb1ec589af7fb9903`):**
`receipts/smoke/recursive`, `recursive-hourly`, `recursive-monthly`,
`recursive-annual`, `delimiter`, `json`, `listv1`, `allversions`,
`fanout/{monthly,daily,annual,hourly,remainder,union}`, and
`_capability/{observability,run-fanout}/` (probe transcripts). Manifest sha256
`c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb`.
