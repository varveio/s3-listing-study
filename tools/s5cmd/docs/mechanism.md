# s5cmd — mechanism

Architecture and behavior, consolidated from the groundwork report
([`../research/report.md`](../research/report.md)) and its critical cross-check
([`../research/codex-review.md`](../research/codex-review.md), all 19 review items
addressed). Evidence labels are carried through as they stand post-review:
`[DOC]` docs, `[SRC file:line @ sha]` pinned source, `[RUN receipt]` a committed
smoke run, `[OBS]` observed but not wrapper-recorded (a capability probe),
`[3P]` third-party, `[INFERRED]`. Pinned commit for every `[SRC]` anchor:
**`991c9fb`** (tag `v2.3.0`). References of the form claim `some-id` resolve in
the canonical ledger, [`../data/claims.json`](../data/claims.json); the current
tested identity is in [`../data/tool.json`](../data/tool.json).

This page reorganizes the existing groundwork; it adds no new findings.

## Listing is one serial, paginated stream

**Key observation: `ls` does not parallelize LIST** — claim
`ls-is-one-serial-list-chain`.

- `ls` issues one `ListObjectsV2PagesWithContext` call per source URL and
  pushes each object onto an **unbuffered** channel `objCh := make(chan
  *Object)`, in a single goroutine [SRC `storage/s3.go:309,317` @ 991c9fb]. The
  SDK paginator walks the continuation-token loop internally; s5cmd does not
  manage `ContinuationToken`/`NextMarker` itself.
- `ls` consumes that channel with a plain serial `for object := range
  client.List(...)` loop — no worker pool, no fan-out [SRC `command/ls.go:197`
  @ 991c9fb]. `du` is identical [SRC `command/du.go:153` @ 991c9fb].
- The `parallel` worker pool (`--numworkers`, default **256**) parallelizes
  only the *downstream operations* — the `cp`/`rm`/`mv` transfer or delete of
  each object — never the LIST that enumerates them [SRC `command/app.go:18`,
  `parallel/parallel.go` @ 991c9fb; INFERRED from the pool being consumed only
  in cp/rm/mv, not in ls/du]. `cp` still lists serially via one `client.List`,
  then dispatches each returned object as a `parallel.Task` [SRC
  `command/cp.go`, `command/expand.go` @ 991c9fb]. This is the source basis for
  claim `parallelism-is-transfer-side-for-lone-ls`.
- Because the channel is unbuffered, listing throughput is throttled to the
  consumer's print speed (back-pressure) [SRC `storage/s3.go:309` @ 991c9fb;
  INFERRED].
- **`command/ls.go` is the consumer, not the issuer** — claim
  `list-issued-in-storage-not-ls-consumer`. The inherited page's code anchor
  named `command/ls.go` as where the serial LIST lived; groundwork corrected
  this — `command/ls.go:197` only consumes the object channel, while the LIST
  request and its pagination are issued in `storage/s3.go` (`List` at
  `:299-341`, `ListObjectsV2PagesWithContext` at `:317`).

**Runtime corroboration.** `--log trace` writes the AWS SDK's full
request/response records to **stdout**. A 3-page trace of `normals-hourly/`
shows **one** `HeadBucket` region probe then **sequential** `ListObjectsV2`
pages, each carrying the previous page's continuation-token — a single serial
chain, no concurrent LIST in flight for one `ls` [OBS
`../receipts/smoke/_capability/observability`]. The cross-check corrected an
earlier probe that had inspected stderr, where nothing appears; stating which
stream to read strengthened the serial-listing evidence rather than weakening it
(claim `log-trace-exposes-per-request-pages`; correction history in
[`../research/codex-review.md`](../research/codex-review.md)). This is runtime
evidence for *one* `ls`; it cannot by itself exclude parallelism a hypothetical
sharding layer might add, but source shows there is none. **Serial-versus-parallel
at scale stays `unverified`** — claim `serial-listing-at-scale-unverified`: smoke
settles completeness, not the mechanism at 10^6–10^8 keys; a receipt-worthy claim
needs a run, and source reading is not a receipt (per `AGENTS.md`).

**`--numworkers` and `run` are the one exception.** For a single `ls`,
`--numworkers` has no effect on the LIST chain. But `s5cmd run <file>`
dispatches its command lines — `ls` included — through that same worker pool
[SRC `command/run.go:76` @ 991c9fb], so a batch of per-prefix `ls` invocations
run concurrently under `run` (claim `numworkers-sizes-run-fanout-concurrency`).
"All parallelism is transfer-side" is accurate for a lone `ls`; it does not hold
for the `run` fan-out.

## The `run`/`--numworkers` fan-out dispatch

s5cmd ships no native keyspace-splitting or prefix-hinting listing mode. The
fan-out is a **user-built workaround**: partition the keyspace by prefix,
issue one `s5cmd ls` per prefix, and run the batch through `s5cmd run <file>`
(commands file **positional**, or piped on stdin — v2.3.0 has **no `-f`
flag**; `run -f <file>` fails with `Incorrect Usage: flag provided but not
defined: -f`, exit 1 [OBS `../receipts/smoke/_capability/run-fanout`]; claim
`run-takes-file-positionally-no-f-flag`). `run --help` states it executes the
declared commands "in parallel" — that worker-pool concurrency (`--numworkers`,
default 256) is the only way s5cmd lists multiple prefixes concurrently [SRC
`command/run.go:76` @ 991c9fb].

Smoked as the `fanout` mode: 4 top-level-prefix shards
(`normals-{monthly,daily,annualseasonal,hourly}/`) plus an unprefixed-remainder
shard (`index.html` lives under no prefix; a prefix-only fan-out would silently
drop it), verified complete via `--scope union`: **PASS, 148,917 keys, 0
cross-shard duplicates, 0 missing, 0 extra** [RUN
`../receipts/smoke/fanout/union/union-verify.md`], recorded as claim
`fanout-completeness-verified`. The in-process `s5cmd run <file>` orchestration
of the same partition also ran clean (148,917 distinct keys, exit 0) [OBS
`../receipts/smoke/_capability/run-fanout`]. This settles **completeness** of the
fan-out against the smoke bucket; the fan-out's *speed* relative to a native
parallel lister is `unverified` — claim `fanout-speed-vs-native-unverified`, a
benchmark-phase question.

## Retry model

A `customRetryer` wraps the SDK `DefaultRetryer`; `NumMaxRetries` comes from
the global `--retry-count`/`-r` flag, **default 10** [SRC `command/app.go:19`,
`storage/s3.go:1289,1376-1408` @ 991c9fb]. `ShouldRetry` additionally retries
`InternalError`, `RequestTimeTooSkewed`, `SlowDown`, and connection-reset/
timed-out substrings; it does **not** retry expired/invalid tokens [SRC
`storage/s3.go:1390-1408` @ 991c9fb]. **s5cmd overrides only `ShouldRetry`,
not the delay** — backoff itself is delegated to the embedded SDK
`DefaultRetryer` [SRC `storage/s3.go:1376-1408` @ 991c9fb], whose delay is
documented as exponential-with-jitter [3P aws-sdk-go `DefaultRetryer`]. **No
explicit per-request timeout or context deadline is set on List** [SRC
`storage/s3.go:299-317` @ 991c9fb; INFERRED from absence].

## Page size: 1,000 is S3's ceiling, not an s5cmd disadvantage

The SDK's built-in `*PagesWithContext` paginator drives pagination. **`MaxKeys`
is never set** — no `MaxKeys` reference anywhere in `command/` or `storage/`,
and no flag to set it [SRC `storage/s3.go:299-341` @ 991c9fb; INFERRED from
the absence of any `MaxKeys` assignment]. With the field omitted, S3 returns
its own maximum of **1,000 keys/page** — which is also S3's hard ceiling, so
this is **not an s5cmd deficit relative to other real-S3 clients**: no client
can exceed 1,000 [DOC AWS `ListObjectsV2` API]. It only matters relative to
tools that parallelize *pages* across sharded prefixes — the fan-out above is
exactly that workaround. This is claim `page-size-1000-is-s3-ceiling`, which
corrects the inherited framing of 1,000 as an s5cmd disadvantage. The full
bucket (148,917 keys) therefore spans approximately 149 pages [INFERRED from
148,917 ÷ 1,000; a 3-page `--log trace` of `normals-hourly/` confirms the
1,000-key page size, `../receipts/smoke/_capability/observability`].

## API dispatch and keyspace division

`S3.List` chooses the API by URL flags [SRC `storage/s3.go:158-167` @
991c9fb]: `--all-versions`/version-id → **ListObjectVersions** [SRC
`storage/s3.go:187`]; `--use-list-objects-v1` → **ListObjects (v1)** [SRC
`storage/s3.go:408`]; otherwise → **ListObjectsV2** (default) [SRC
`storage/s3.go:317`].

**Keyspace division: none** — claim `no-native-keyspace-division`. There is no
bisection, cut-point, or common-prefix recursion to parallelize listing. A
wildcard becomes **one recursive List plus a client-side regex filter**: the
prefix sent to S3 is only the literal portion before the first glob character,
and the wildcard tail is compiled to a regex applied per key as pages arrive
[SRC `storage/url/url.go:259-285`, `storage/s3.go:341` @ 991c9fb]. So
`s3://b/pref/*/*.gz` is one List at `Prefix=pref/` (no delimiter) then a
client-side filter, not multiple List calls — a broad glob is not cheaper
than a full prefix walk. Glob support itself is source-established and smoked
(claim `glob-wildcard-support`).

**Delimiter vs recursive** is decided by whether the path contains a glob
character [SRC `storage/url/url.go:264-270` @ 991c9fb]: no glob →
`Delimiter="/"`, `Prefix=<full path>` (shallow/delimiter listing;
CommonPrefixes surfaced as `DIR` rows [SRC `storage/s3.go:305-307,318-332` @
991c9fb]); glob present → `Delimiter=""` (fully recursive) + client-side
filter.

## Memory model

Streaming. `ls` prints each object as it comes off the unbuffered channel — no
accumulate-then-dump, no in-memory key buffer, and no sort (output order is
exactly S3's returned page order) [SRC `command/ls.go:197-221` @ 991c9fb].
`du` accumulates only per-storage-class totals, not the key list [SRC
`command/du.go:142,168` @ 991c9fb]. `sync` uses `extsort` to buffer/sort,
unlike the streaming `ls`/`du` [SRC `command/sync.go` @ 991c9fb; INFERRED that
`ls`/`du` are the only in-scope listing consumers] — out of scope here (`sync`
is a transfer command). At smoke, `peak_rss` stayed ~40 MB for full recursive
and ~53 MB for all-versions on a 148,917-key bucket [RUN
`../receipts/smoke/recursive`, `../receipts/smoke/allversions`] — claim
`ls-streaming-memory-at-smoke`, consistent with streaming, but a smoke-scale
observation, not a scale claim (`unverified` above 148,917 keys — claim
`serial-listing-at-scale-unverified`).

## Output contracts per mode

| Mode | Request | Output contract |
| --- | --- | --- |
| `recursive` (`ls "s3://b/*"`) | ListObjectsV2, `Delimiter=""`, client-side filter | Text columns (with `-e -s`): `date time storage-class etag size relkey` — format string `"2006/01/02 15:04:05"` then storage-class/etag/size/relative-path [SRC `command/ls.go:248,258-311` @ 991c9fb]; paths are **relative to the query prefix** [SRC `command/ls.go:285,301` @ 991c9fb] |
| `delimiter` (`ls "s3://b/"`, no glob) | ListObjectsV2, `Delimiter="/"` | Same text columns for object rows; DIR rows print `DIR <prefix/>` with metadata columns blank [RUN `../receipts/smoke/delimiter`] |
| `json` (`--json ls "s3://b/*"`) | Same request as recursive | One object per line: `{"key":"s3://bucket/key","etag":…,"last_modified":"…Z","type":"file","size":N,"storage_class":…}` — `strutil.JSON(l.Object)` of the `storage.Object` struct [SRC `command/ls.go:316-318` @ 991c9fb]. Observed: `key` is the **absolute** `s3://bucket/key` URL, `last_modified` already RFC3339 `…Z` [RUN `../receipts/smoke/json`] |
| `listv1` (`--use-list-objects-v1`) | Legacy **ListObjects (v1)** | Same text contract as recursive |
| `allversions` (`--all-versions`) | **ListObjectVersions** | Same text contract plus a trailing versionID token (`null` on a non-versioned bucket) |
| `fullpath` (`--show-fullpath`) | Same ListObjectsV2 request as recursive | Absolute `s3://bucket/key` paths only — no size/etag/mtime/storage-class columns [SRC `ls.go:93,253`] |
| `fanout` (N per-prefix `ls`, unioned or via `run <file>`) | N independent serial ListObjectsV2 chains | Same text contract as `recursive`/`delimiter` per shard; the union's remainder adapter drops DIR common-prefix rows |

The output flags `-e`, `-H`, and `--json` change only the printed format, not
the request or listing mechanism (claim `output-flags-are-formatting-only`).
The timestamp has no offset marker [SRC `command/ls.go:248`]; because
containers run `TZ=UTC` and the SDK parses `LastModified` as UTC, it is UTC by
construction [INFERRED].

## Scoped caveats

Each caveat below is owned here or in the claim's qualification; `running.md`
carries the operator-facing view and does not re-derive them.

**KEY-BYTE FIDELITY.** The `normalize.sh` adapter's text branches split on
whitespace and rejoin fields with a single space, so a key containing runs of
spaces, tabs, or an embedded newline is **not** reproduced byte-for-byte; the
JSON branch's `jq @tsv` escapes tab/newline/backslash too (claim
`adapter-whitespace-key-fidelity-loss`). This is exact for the NOAA smoke
keyspace (keys are `[A-Za-z0-9._/-]`, no whitespace) and every committed PASS
exercises only such keys. The adapters are exact for the current corpus, not a
general weird-key parser — full weird-key/unicode fidelity is **deferred** with
the edge-case fixture (`EDGE_BUCKET=none`); when that bucket exists, `--json`
plus a byte-safe JSON path (not `@tsv`) is the route to assert space/tab/newline
keys.

**`allversions` validates the request/output contract only, on a
non-versioned bucket** (claim `allversions-validates-request-contract-only`).
The `normalize.sh` adapter discards the trailing versionID token entirely. On
the smoke bucket (non-versioned, one version per key) that is sound. On a
**versioned** bucket, multiple versions of one key would collapse into duplicate
key records, and the verifier would (correctly) flag them as duplicates. The
`allversions` **PASS** therefore validates that s5cmd issues the
ListObjectVersions request and produces the expected output shape — it does
**not** validate multi-version or delete-marker fidelity, which stays
`unverified` (claim `allversions-multiversion-fidelity-unverified`) and is
deferred with the other edge cases (`EDGE_BUCKET=none`).

## Observability

`--stat` prints an **operation** tally (`ls 1 0 1`) — high-level, not S3 API
calls. `--log debug` emits no per-request records. **`--log trace` writes the
full AWS SDK request/response records to stdout** (claim
`log-trace-exposes-per-request-pages`), so the API-call/page count *is*
obtainable by counting `DEBUG: Request s3/<Op>` lines: a 3-page trace of
`normals-hourly/` shows 1 HeadBucket (region probe) + 3 sequential
ListObjectsV2 pages [OBS `../receipts/smoke/_capability/observability`]. s5cmd
surfaces no built-in numeric counter, and the wrapper runs did not enable
trace (it would bloat every payload with the request dump). Scale
request-shape capture defers to the study's replay-server phase.

## Container and architecture

Image chosen: upstream `peakcom/s5cmd:v2.3.0`, pinned by digest
`sha256:2ff939e2ee3c76adcadd78dbfc3e2569b18a3743ed9dcfccb1ec589af7fb9903` —
upstream's own published image [DOC README.md § Docker], matching the normal
packaged setup. Entrypoint is `["/s5cmd"]`. Native on **both**
amd64 and arm64 [OBS `docker manifest inspect peakcom/s5cmd:v2.3.0`;
`.goreleaser.yml` for prebuilt binaries, SRC]; smoke ran on arm64 (image arm64
on host aarch64), not emulated [RUN `../receipts/smoke/*`]. Upstream also
publishes prebuilt binaries per release (claim
`prebuilt-binaries-published-per-release`). See `running.md` for the full
reproduction procedure.

## Source anchors

- `command/ls.go:197` — the serial channel *consumer* (not the LIST issuer;
  see correction above).
- `storage/s3.go:299-341,317` — `List` and `ListObjectsV2PagesWithContext`,
  where the LIST request and its pagination are actually issued.
- `storage/s3.go:158-167,187,408` — API dispatch (ListObjectsV2 / v1 /
  ListObjectVersions).
- `storage/url/url.go:259-285,264-270` — glob-to-regex compilation and the
  delimiter-vs-recursive decision.
- `command/app.go:18-19` — `--numworkers` (default 256) and `--retry-count`
  (default 10) global flags.
- `command/run.go:76` — `run` dispatching its command lines through the
  worker pool.
- `storage/s3.go:1289,1376-1408,1390-1408` — `customRetryer` /
  `NumMaxRetries` / `ShouldRetry`.
- `command/ls.go:248,258-311,285,301,316-318` — text/JSON output formatting
  and relative-path reconstruction.
- `command/du.go:142,153,168` — `du`'s serial consume and accumulate-only
  memory model.
- `command/cp.go`, `command/expand.go` — `cp`'s serial list + `parallel.Task`
  dispatch per object.
- `command/sync.go` — `extsort`-based buffering (out of scope; not `ls`/`du`).

## Deferred / open questions

Carried forward from [`../research/report.md`](../research/report.md) § 10,
unresolved by smoke and out of this consolidation's scope to resolve:

1. Serial listing at 10^6–10^8 keys, quantified against parallel-capable
   tools (claim `serial-listing-at-scale-unverified`).
2. The 1,000-key page-size cost, quantified against page-parallel tools.
3. v1 vs v2 vs ListObjectVersions cost — smoke saw ~17s / ~70s / ~87s on the
   same 148,917-key bucket; pin down the cause under the fair-timing harness.
4. `--retry-count` under throttling.
5. Memory at scale — confirm no hidden accumulation, including whether
   `--all-versions`'s higher smoke RSS grows super-linearly.
6. Fan-out workaround performance vs a native parallel lister, sweeping
   `--numworkers` and shard granularity (claims `numworkers-sweep-unverified`,
   `fanout-speed-vs-native-unverified`).
