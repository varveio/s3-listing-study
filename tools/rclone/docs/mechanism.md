# rclone — mechanism

How rclone lists an S3 bucket: the two request patterns and what selects them,
serial pagination, the pacer, `--checkers`' scope, the `--list-cutoff` external
sort, the list APIs, output contracts, and the memory and resume model. This
page reorganizes the groundwork report ([`../research/report.md`](../research/report.md))
and its cross-check rounds ([`../research/codex-review.md`](../research/codex-review.md));
it adds no new findings, and any correction it states is current truth whose
history lives in `../research/` and in each claim's `disposition`.

Evidence labels: `[SRC file:line @ 5bc93a2a7]` is the pinned checkout (tag
`v1.74.4`), `[RUN receipt]` a committed smoke run, `[OBS]` an observation not
recorded by the wrapper, `[DOC]` upstream docs, `[INFERRED]` a reasoned
inference. References of the form claim `some-id` resolve in the canonical
ledger, [`../data/claims.json`](../data/claims.json); statuses use the canonical
vocabulary (`confirmed`, `supported`, `unverified`, `unverifiable`).

## Two request patterns — and what selects them

rclone's S3 backend has two distinct listing shapes (claim
`two-distinct-request-patterns`). For the `ls*` listing commands they are **not**
selected by `--fast-list` (claim `mode-selector-is-not-fast-list`).

- **Flat `ListR` (undelimited).** A single recursive listing per bucket/prefix
  with the `delimiter` parameter omitted [SRC `backend/s3/s3.go:2428-2432,2745-2760`
  @ 5bc93a2a7], paging straight through the whole keyspace. "uses more memory but
  fewer transactions" [DOC `--fast-list`] (claim
  `fast-list-tradeoff-fewer-calls-more-memory`).
- **Hierarchical walk (per-directory, `Delimiter="/"`).** rclone lists
  directory-by-directory: each level is a `ListObjectsV2` with `Delimiter="/"`
  [SRC `backend/s3/s3.go:2430-2433` @ 5bc93a2a7], returning that level's objects
  plus `CommonPrefixes` (sub-directories). Discovered directories fan out across a
  bounded worker pool — `in := make(chan listJob, ci.Checkers)` [SRC
  `fs/walk/walk.go:380` @ 5bc93a2a7] — so concurrency here is **across directories,
  `--checkers`-wide (default 8)** [SRC `fs/config.go:60-61` @ 5bc93a2a7], not within
  a directory. Workers asynchronously enqueue child directories while siblings are
  still listing [SRC `fs/walk/walk.go:393` @ 5bc93a2a7], so levels overlap.

**The selector.** `lsjson`/`lsf`/`ls`/`lsl` call `walk.ListR` **directly** [SRC
`fs/operations/lsjson.go:248` @ 5bc93a2a7]. `walk.ListR` uses the backend `ListR`
(the flat path) whenever it can, and drops to the per-directory `Walk` only when
`maxLevel >= 0` (bounded recursion), or `--files-from`/`--exclude-file`/a directory
filter is set, or the backend has no `ListR` [SRC `fs/walk/walk.go:149-163` @
5bc93a2a7]. It **never consults `--fast-list`** (`ci.UseListR`). Consequences:

- **A plain `rclone lsjson -R` is ALREADY the flat `ListR`.** `--fast-list` changes
  neither its request shape nor `--checkers`' relevance. The `--fast-list` gate
  lives in the *other* entry point, `walk.Walk` [SRC `fs/walk/walk.go:65-77` @
  5bc93a2a7], used by sync/copy — not by the recursive `ls*` path.
- **To force the genuine per-directory walk from a listing command** you must
  remove the flat path: `--disable ListR` (nils the `ListR` feature, so
  `walk.ListR` falls back to `Walk`) [SRC `fs/features.go:216-249`,
  `fs/walk/walk.go:152-160` @ 5bc93a2a7], or bound recursion with `--max-depth N`
  (`maxLevel >= 0`) [SRC `fs/operations/operations.go:1034-1041` @ 5bc93a2a7]. Only
  then does `--checkers` bound anything on a pure listing (claim
  `checkers-bounds-walk-fanout`).

**Runtime corroboration — both patterns traced.** `-vv --dump headers` prints
each HTTP request line on stderr:

- Flat `ListR` (`_capability/debug`): a single `GET
  /?encoding-type=url&list-type=2&max-keys=1000&prefix=…` then `continuation-token=…`
  pages — no delimiter, one serial chain [RUN `../receipts/smoke/_capability/debug`].
- Genuine walk (`_capability/walk-debug`, `--disable ListR`): **13 page requests,
  every one carrying `delimiter=%2F`**, one continuation chain per directory across
  four directory chains —
  `normals-annualseasonal/1981-2010/` discovering `access/`+`archive/`+`doc/` in 1
  page, then `access/` in 10 serial `continuation-token` pages for its 9,839 keys,
  `archive/` and `doc/` one page each [RUN `../receipts/smoke/_capability/walk-debug`]
  (claim `forced-walk-under-disable-listr-traced`).
- Re-run on the same argv, the old mislabeled `recursive-hierarchical` invocation
  (`lsjson -R`, no `--fast-list`) emits **0 `delimiter=` requests, one undelimited
  chain** — an ad-hoc trace, consistent with its being the flat path with
  `--checkers 4` inert [OBS; see the receipt's correction block] (claim
  `plain-recursive-r-is-flat-obs`).

**Serial-vs-parallel at scale stays `unverified`.** Smoke settles completeness and
request shape, not behaviour at 10^6–10^8 keys; source reading is not a receipt.

## Pagination is a serial cursor-chained loop

Both patterns funnel into `Fs.list`, whose page loop is a plain `for {…}`: issue a
page, process `Contents` and `CommonPrefixes`, stop when `IsTruncated` is false [SRC
`backend/s3/s3.go:2472-2593` @ 5bc93a2a7]. Page size is `MaxKeys = f.opt.ListChunk`,
**default 1000** [SRC `:2454` + option Default 1000 @ 5bc93a2a7]. The next page is
requested with the previous response's `NextContinuationToken` (v2) or
`NextMarker`/last-key (v1) [SRC `:2204-2214,2147-2162` @ 5bc93a2a7] — so **paging
within a prefix is strictly sequential** (page N+1 needs page N's cursor; the v2
continuation chain is receipt-confirmed as claim `pagination-is-serial-within-prefix`,
the v1 marker chain is source-supported as claim `pagination-v1-serial-marker`). There is no bisection, cut-point, or
key-range sharding; the only parallelism is the walk splitting *distinct
directories* across `--checkers` [SRC `:2472`, `fs/walk/walk.go:380` @ 5bc93a2a7,
INFERRED from the absence of any concurrency in `Fs.list`] (claim
`no-intra-prefix-keyspace-sharding`). For a flat/undelimited listing the request
count is `ceil(keys / list_chunk)` pages issued serially; for a hierarchical walk it
is **one continuation chain per directory** (13 page requests across four chains in
the traced scope), set by tree shape, not the key total [RUN
`../receipts/smoke/_capability/debug`, `../receipts/smoke/_capability/walk-debug`].

## `--checkers`, `--transfers`: real scope

- **`--checkers` (default 8)** bounds the concurrent-directory fan-out of the
  **genuine hierarchical walk** — the `chan listJob, ci.Checkers` worker pool [SRC
  `fs/walk/walk.go:380,393`, `fs/config.go:60-61` @ 5bc93a2a7], run-traced as genuine
  per-directory fan-out [RUN `../receipts/smoke/recursive-walk`,
  `../receipts/smoke/_capability/walk-debug`]. It is **inert on the flat `ListR`** (a
  single serial chain — which is what a plain `lsjson -R` runs), so the inherited
  "shouldn't affect pure listing" is wrong for `--checkers` on a walk, right for the
  flat path (claim `checkers-bounds-walk-fanout`). Whether a *non-default* checker
  count moves wall-clock or request timing is unsmoked (claim
  `checkers-nondefault-timing-unsmoked`).
- **`--transfers` (default 4)** governs transfer concurrency only; no listing effect
  [SRC `fs/config.go:65-66` @ 5bc93a2a7] (claim `transfers-irrelevant-to-listing`).

## Retries and the pacer (error-driven, decays to zero)

LIST calls run through a pacer — `pacer.NewS3(pacer.MinSleep(10ms))` [SRC
`backend/s3/s3.go:1846,980` @ 5bc93a2a7] — wrapping each call in
`f.pacer.Call(shouldRetry)`. `shouldRetry` retries generic AWS-SDK retryables,
`RequestTimeout`, and HTTP 429/500/503 [SRC `backend/s3/s3.go:1267-1271` @
5bc93a2a7]; a 301 (wrong region) triggers a one-shot region re-resolve; and an
`xml.SyntaxError` on an un-encoded listing flips `EncodingType=url` and retries [SRC
`:2480-2488` @ 5bc93a2a7] (for AWS, `encoding-type=url` is sent from the start [RUN
`../receipts/smoke/_capability/debug`]).

**The pacer's control law.** The S3 backend uses the **`S3` calculator** — struct at
[SRC `lib/pacer/pacers.go:220` @ 5bc93a2a7], `NewS3` at [`:233`], `Calculate` at
[`:270-294`] — not the `Default` calculator at `:42-101`. `S3.Calculate` keys
entirely on **error/retry state** (`state.LastError` via `IsRetryAfter`, and
`state.ConsecutiveRetries`), never on latency (claim `s3-pacer-is-error-driven`):

- On a retry it **multiplicatively increases** sleep
  (`SleepTime<<attackConstant / (2^attackConstant−1)`, attackConstant=1, capped at
  `maxSleep`=2s).
- On success it decays sleep and **drops it to zero once the decayed value falls
  below `minSleep`** — `if sleepTime < c.minSleep { sleepTime = 0 }` [SRC
  `:289-293` @ 5bc93a2a7] — so successful calls run with no delay at all [SRC
  `:212-217`]. This is the S3-specific behaviour; it is **not** the `Default`
  calculator's "floor at `minSleep`".

So the inherited "AIMD on delay" characterization is wrong twice over: the pacer
reacts to **explicit error signals**, not latency, and it paces **inter-request
sleep**, not concurrency — listing calls are serial regardless (claim
`pacer-adapts-sleep-not-concurrency`). A genuine adaptive backoff pacer does exist
and is distinctive (claim `pacer-exists-adaptive-backoff`). `--tpslimit`/
`--tpslimit-burst` is a **separate** token-bucket TPS limiter, distinct from this
backoff pacer; `--low-level-retries` bounds retry attempts (claim
`tpslimit-is-separate-token-bucket`) [DOC].

## `--list-cutoff`: on-disk external sort (v1.70+)

v1.74.4 carries the v1.70 `--list-cutoff` fix: directory listings above the
threshold are sorted **on disk** (external sort) rather than in RAM. Default
**1,000,000** entries [SRC `fs/config.go:281` (`list_cutoff`) @ 5bc93a2a7]; the
sorter uses `github.com/lanrat/extsort` [SRC `fs/list/sorter.go:26` @ 5bc93a2a7; DOC
`docs/content/faq.md`] (claim `list-cutoff-external-sort`). This matters to the
exit-0/OOM allegations: they are about the **`sync`** path on **v1.67-era** code,
before this fix, so the pinned version postdates the alleged failure. The full
caveat is owned by [`running.md`](running.md#the-exit-0-on-oom-report-and-its-caveats)
(claim `oom-exit-zero-report`).

## List APIs: v1, v2, versions

`Fs.list` picks a `bucketLister` [SRC `backend/s3/s3.go:2462-2470` @ 5bc93a2a7]:
`newV2List` → **ListObjectsV2** (default), `newV1List` → legacy **ListObjects**
(`--s3-list-version 1`; claim `legacy-v1-list-api-distinct`), `newVersionsList` →
**ListObjectVersions** (`--s3-versions`/`--s3-version-at`). Auto-selection
(`list_version=0`) resolves to v2 for AWS [SRC `:1745-1749` @ 5bc93a2a7]. The default
`list-type=2` is observed on the wire [RUN `../receipts/smoke/_capability/debug`]; v1
was smoked PASS [RUN `../receipts/smoke/listv1`]; the versions API was **not smoked**
(bucket unversioned) [SRC `:2310-2334`].

**Completeness guard.** rclone converts a malformed truncated v2 response
(`IsTruncated` with no continuation token) into a hard error rather than a short
listing [SRC `:2209-2211` @ 5bc93a2a7] — a completeness property worth crediting;
the v1 path is more forgiving (falls back to last-key marker) [SRC `:2155-2161`].

## Memory model

**At the source level:** the S3 `ListR` streams entries to the caller through
`list.NewHelper`/tranche flush rather than accumulating all objects [SRC
`backend/s3/s3.go:2745-2764` @ 5bc93a2a7]; `--fast-list`'s extra memory is the
walker's `dirMap` of directory paths retained to synthesize parents [SRC
`fs/walk/walk.go:256-346` @ 5bc93a2a7], which by the code tracks directory paths,
not the object list (claim `s3-listr-streams-entries`). The genuine walk does **not**
bound memory to "one level at a time": workers enqueue child directories
asynchronously [SRC `fs/walk/walk.go:380,393` @ 5bc93a2a7], so multiple levels can be
in flight [SRC, INFERRED]. Whether the `dirMap` or any retained state blows up on a
deep or enormous keyspace — an OOM cliff — is a scale question smoke cannot answer
and stays `unverified` (claim `fast-list-memory-at-scale`). At smoke the full
148,917-key fast-list peaked at **69.6 MB RSS** [RUN
`../receipts/smoke/recursive-fastlist`] — a data point consistent with streaming,
not a scale claim (claim `fastlist-smoke-peak-rss`).

**Resume/checkpoint.** Source shows **no checkpoint or cursor state** in `Fs.list`
[SRC `backend/s3/s3.go:2419-2609` @ 5bc93a2a7 — observation], so an interrupted
`lsjson` would restart from scratch; rclone's resume machinery is for transfers, not
enumeration (claim `no-list-checkpoint-state-in-source`). That is a source
observation, not a runtime test: whether a killed listing really restarts from zero
stays `unverified` (claim `list-crash-resume-run`) — a direct answer needs the
SIGKILL-and-resume protocol [INFERRED].

## The HEAD-per-object footgun

rclone's S3 `Object.ModTime` returns the listing's `LastModified` **only if
`--use-server-modtime` is set**; otherwise it calls `readMetaData` → **a HEAD per
object** to read `x-amz-meta-mtime` [SRC `backend/s3/s3.go` ModTime @ 5bc93a2a7].
`Object.MimeType` does the same, and `lsjson` computes both by default [SRC
`fs/operations/lsjson.go:181-185` @ 5bc93a2a7]. So a plain `rclone lsjson -R` (or
`lsl`) on this bucket would fire **148,917 HEADs** on top of the LIST requests —
turning a listing into a HEAD storm that dominates the request count, invisible
without `-vv`. Proper listing **must** pass `--use-server-modtime --no-mimetype` (or
`lsf` without the `t`/`h` format codes). The mechanism is source-supported across
three functions and never run the wrong way (claim `head-per-object-storm-mechanism`);
the suppressed correct path is receipt-backed on every lsjson receipt (`fields=0`;
claim `head-per-object-suppressed-at-smoke`).

## Output contracts per mode

| Mode | Request | Output contract |
| --- | --- | --- |
| `recursive-fastlist` (`lsjson --fast-list -R`) | Flat undelimited `ListObjectsV2`, serial paging (`--fast-list` inert — already `ListR`) | JSON array; per item `Path`, `Name`, `Size`, `ModTime`, `IsDir`, `Tier` [SRC `fs/operations/lsjson.go` @ 5bc93a2a7] |
| `recursive-walk` (`lsjson --disable ListR -R`) | Per-directory `Delimiter=/` `ListObjectsV2`, children fanned across `--checkers` | Same JSON contract |
| `delimiter-shallow` (`lsjson`/`lsf`/`lsd`, no `-R`) | Single delimiter level; objects + `CommonPrefixes` (dir rows, `IsDir=true`) | JSON files carry size/mtime/tier; dir rows carry none |
| `listv1` (`--s3-list-version 1`) | Legacy `ListObjects` (v1), `Marker` paging | Same JSON contract |
| `versions` (`--s3-versions`/`--s3-version-at`) | `ListObjectVersions` | Distinct request + output — **not smoked** |
| Output formats `lsjson`/`lsf`/`ls`/`lsl`/`lsd` | subcommand | Distinct output contracts over the same request pattern |

**Fields exposed without a per-object HEAD:** key (always), size (`.Size`), mtime
(`.ModTime`, only with `--use-server-modtime`; `lsf` omits it by construction),
storage_class (`.Tier`, straight from `ListObjectsV2`; claim
`storage-class-from-list-response`). **etag is `-` everywhere by design:** rclone's
S3 listing path surfaces no raw ETag; `lsjson --hash md5` equals the ETag only for
single-part objects, so the adapter declines it [SRC `fs/operations/lsjson.go:221-227`
@ 5bc93a2a7]. mtime is UTC by construction (containers run `TZ=UTC`; S3
`LastModified` is whole-second; the adapter truncates RFC3339 to seconds).
storage_class verified equal to the manifest on all 148,917 keys [RUN
`../receipts/smoke/recursive-fastlist`, `fields=0`]. The structured `lsf`/`lsjson`
formats were run (claim `output-formats-lsf-lsjson-run`); no list command emits
Parquet (claim `no-parquet-output`).

## Observability

rclone exposes **no API-call counter** in listing output; `--stats` counts
transfers, not LISTs. The only route to request-level visibility is `-vv --dump
headers` (or `--dump bodies`), which prints each HTTP request line on stderr — used
here to trace both request patterns (`../receipts/smoke/_capability/debug`,
`../receipts/smoke/_capability/walk-debug`). rclone is verbosity-flag driven, not
`RUST_LOG`-driven. Scale request-shape capture defers to the study's replay-server
phase.

## Container and architecture

Image: upstream `rclone/rclone@sha256:c619…dc4a1` (tag `1.74.4`), a multi-arch
manifest-list digest resolving to `linux/arm64` on this runner [DOC
hub.docker.com/r/rclone/rclone]. Entrypoint is `["rclone"]`, so `../adapter/run.sh`
argv starts at the subcommand. **amd64 and arm64 are both natively supported** on
every channel (upstream image, prebuilt binaries, source build — pure-Go
`CGO_ENABLED=0` static) [RUN `docker buildx imagetools inspect`; DOC
rclone.org/downloads; SRC Dockerfile @ 5bc93a2a7]. Smoke ran native arm64, no
emulation [RUN `../receipts/smoke/*`]. See [`running.md`](running.md).

## Source anchors

- `fs/operations/lsjson.go:248` — `ls*` recursion calls `walk.ListR` directly
  (bypassing the `--fast-list` gate); `:181-185` HEAD-triggering ModTime/MimeType;
  `:221-227` the declined ETag.
- `fs/walk/walk.go:149-163` — `walk.ListR` flat-vs-walk selection (`maxLevel>=0` →
  fallback); `:65-77` the `Walk` entry point (where `ci.UseListR`/`--fast-list`
  actually gates); `:380,393` the `--checkers`-deep worker pool and async child
  enqueue; `:256-346` the `dirMap`.
- `fs/features.go:216-249` — `Disable` (how `--disable ListR` nils the feature).
- `fs/operations/operations.go:1034-1041` — `ConfigMaxDepth` (`-R` → maxLevel −1;
  `--max-depth` → ≥0).
- `backend/s3/s3.go:2428-2432,2472-2593,2745-2764` — delimiter omission, the serial
  page loop, the streaming `ListR`; `:2462-2470,1745-1749` API dispatch;
  `:2209-2211` the truncation guard; `:1508-1511` anonymous credentials;
  `:1846,980,1267-1271,2480-2488` pacer wiring and retry.
- `lib/pacer/pacers.go:220,233,270-294` — the `S3` calculator struct/`NewS3`/
  `Calculate` (error-driven, decay-to-zero-below-`minSleep`).
- `fs/config.go:60-66,281` — `--checkers`/`--transfers` defaults; `list_cutoff`.
- `fs/list/sorter.go:26` — `extsort`-based external sort above `--list-cutoff`.

## Deferred / open questions

Carried forward from [`../research/report.md`](../research/report.md) §10,
unresolved by smoke: `--fast-list` memory/OOM at scale (claim
`fast-list-memory-at-scale`, with the streaming-vs-sync and version-delta caveats),
exit-0-on-OOM (claim `oom-exit-zero-report`), the >3h stall (claim
`three-hour-stall-before-transfer`), 7 GB RSS (claim `seven-gb-rss-large-listings`),
crash-resume via SIGKILL (claim `list-crash-resume-run`), the constrained-memory run
(claim `constrained-memory-fastlist-run`), the `--checkers` and `--s3-list-chunk`
sweeps (claims `checkers-nondefault-timing-unsmoked`, `list-chunk-effect-unsmoked`),
and v1-vs-v2 timing (claim `v1-vs-v2-parity-unsmoked`). Full corrected specs are in
the root README.
