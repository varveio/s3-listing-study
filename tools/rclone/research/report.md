# rclone — independent listing report

Groundwork report for the s3-listing-study. Derived independently from primary
sources (official docs, the pinned source tree, and own smoke runs) before any
contact with the inherited dossier. Every behavioural claim carries an evidence
label; an unlabeled behavioural claim is a defect.

Evidence labels: `[DOC url]` official docs · `[SRC file:line @ 5bc93a2a7]` pinned
checkout · `[RUN receipt]` own smoke run · `[3P url]` third-party · `[INFERRED]`
reasoning · `[OBS]` observed but unrecordable by the wrapper.

---

## 1. Metadata

| | |
| --- | --- |
| Tool | rclone |
| Upstream | https://github.com/rclone/rclone (canonical; the project's own repo, not a fork) |
| Pinned tag | `v1.74.4` (latest stable release; tagged 2026-07-08) |
| Pinned commit | `5bc93a2a7ab0ebd0a11352bc4968eabeffb18027` |
| Language | Go (`go 1.25.0` per `go.mod`; the upstream image builds with go1.26.5) |
| License | MIT — `COPYING`, "Copyright (C) 2012 by Nick Craig-Wood … Permission is hereby granted, free of charge …" [SRC COPYING @ 5bc93a2a7]; GitHub SPDX `MIT` |
| Upstream health | Very active: ~58k stars, `pushed_at` 2026-07-17 (day of research), not archived, ~960 open issues; frequent point releases (v1.74.0 → v1.74.4) [3P https://api.github.com/repos/rclone/rclone, accessed 2026-07-17] |
| Image | `rclone/rclone@sha256:c61954aaa32328a5486715dd063a81c7879f5195ad3505cd362deddd509dc4a1` (multi-arch manifest-list digest, tag `1.74.4`; resolves to `linux/arm64` on this runner) |
| Tool version in image | `rclone v1.74.4` (`os/version: alpine 3.24.1`, `go/version: go1.26.5`, static) [RUN receipts/smoke/_build] |
| Date | 2026-07-17 |

rclone is a general-purpose multi-cloud sync/transfer tool; S3 is one of ~70
backends. Listing is exposed through the `ls*` command family over a common
backend `List`/`ListR` interface; the S3-specific listing logic lives in
`backend/s3/s3.go`.

## 2. How it works

**Two request patterns on the S3 wire — but for the `ls*` listing commands they
are NOT selected by `--fast-list`.** This is the load-bearing correction of the
round-2 anchor audit (2026-07-17); the earlier draft had it wrong.

- **Flat recursive (`ListR`, no delimiter).** A single recursive listing per
  bucket/prefix with the `delimiter` parameter omitted
  [SRC backend/s3/s3.go:2428-2432, 2745-2760 @ 5bc93a2a7], paging straight through
  the whole keyspace. "uses more memory but fewer transactions"
  [DOC `rclone help flags` / global `--fast-list`].
- **Hierarchical walk (per-directory, `Delimiter="/"`).** rclone lists
  directory-by-directory: each level is a `ListObjectsV2` with `Delimiter="/"`
  [SRC backend/s3/s3.go:2430-2433 @ 5bc93a2a7], returning that level's objects
  plus `CommonPrefixes` (sub-"directories"). Discovered directories fan out across
  a bounded worker pool — `in := make(chan listJob, ci.Checkers)`
  [SRC fs/walk/walk.go:380 @ 5bc93a2a7] — so **concurrency here is across
  directories, `--checkers` wide (default 8)** [SRC fs/config.go:60-61 @ 5bc93a2a7],
  not within a directory. Workers asynchronously enqueue child directories while
  siblings are still listing [SRC fs/walk/walk.go:393 @ 5bc93a2a7], so levels
  overlap. On a broad tree this issues one LIST per directory node and is
  transaction-heavy.

**What actually selects the path for `lsjson`/`lsf`/`ls`/`lsl`.** These commands
call `walk.ListR` **directly** [SRC fs/operations/lsjson.go:248 @ 5bc93a2a7], and
`walk.ListR` uses the backend `ListR` (the flat, undelimited path) whenever it can
— which for a plain unbounded `-R` it always can, because it drops to the
per-directory `Walk` only when `maxLevel >= 0`, or `--files-from`/`--exclude-file`/
a directory filter is in play, or the backend has no `ListR`
[SRC fs/walk/walk.go:149-163 @ 5bc93a2a7]. It **never consults `--fast-list`**
(`ci.UseListR`) at all. So:

- **A plain `rclone lsjson -R` is ALREADY the flat `ListR`** — `--fast-list` adds
  nothing to it, and its presence or absence changes neither the request shape nor
  `--checkers`' relevance. (The `ci.UseListR`/`--fast-list` gate lives in the OTHER
  entry point, `walk.Walk` at [SRC fs/walk/walk.go:65-77 @ 5bc93a2a7], used by
  sync/copy — not by the recursive `ls*` listing path.)
- **To force the genuine per-directory hierarchical walk from a listing command**
  you must remove the flat path: `--disable ListR` (nils the `ListR` feature, so
  `walk.ListR` falls back to `Walk`) [SRC fs/features.go:216-249, fs/walk/walk.go:152-160 @ 5bc93a2a7],
  or bound the recursion with `--max-depth N` (`maxLevel >= 0`)
  [SRC fs/operations/operations.go:1034-1041 @ 5bc93a2a7]. Only then does
  `--checkers` bound anything on a pure listing.
- Both patterns are traced on the wire: the flat `ListR` as a single undelimited
  `continuation-token` chain [RUN receipts/smoke/_capability/debug]; the genuine
  walk (`--disable ListR`) as **13 requests, every one `delimiter=%2F`, one per
  directory** [RUN receipts/smoke/_capability/walk-debug].

**Pagination is serial, in one loop.** Both patterns funnel into `Fs.list`,
whose page loop is a plain `for {…}`: issue a page, process `Contents` and
`CommonPrefixes`, stop when `IsTruncated` is false [SRC backend/s3/s3.go:2472-2593 @ 5bc93a2a7].
Page size is `MaxKeys = f.opt.ListChunk`, **default 1000**
[SRC backend/s3/s3.go:2454 + option Default 1000 @ 5bc93a2a7]. The next page is
requested with the previous response's `NextContinuationToken` (v2) or `NextMarker`/last-key
(v1) [SRC backend/s3/s3.go:2204-2214, 2147-2162 @ 5bc93a2a7] — so **paging within
a prefix is strictly sequential** (page N+1 needs page N's cursor). Confirmed on
the wire: `normals-hourly/` (2,549 keys) produced 3 chained requests,
`GET /?…list-type=2&max-keys=1000&prefix=…` then two `continuation-token=…`
requests [RUN receipts/smoke/_capability/debug].

**Three list APIs (bucketLister implementations).** `Fs.list` picks one
[SRC backend/s3/s3.go:2462-2470 @ 5bc93a2a7]: `newV2List` → `ListObjectsV2`
(default), `newV1List` → legacy `ListObjects` (`--s3-list-version 1`),
`newVersionsList` → `ListObjectVersions` (`--s3-versions`/`--s3-version-at`).
Auto-selection (`list_version=0`) resolves to v2 for AWS
[SRC backend/s3/s3.go:1745-1749 @ 5bc93a2a7]. Observed request for the default is
`list-type=2` [RUN receipts/smoke/_capability/debug].

**Keyspace division: none within a prefix.** There is no bisection, cut-point,
or key-range sharding. Parallelism only ever comes from the walker splitting
distinct directories across `--checkers`; the fast-list path is single-threaded
pagination [SRC backend/s3/s3.go:2472 @ 5bc93a2a7, INFERRED from the absence of any
concurrency in `Fs.list`].

**Retries / backoff.** LIST calls run through a pacer
(`pacer.NewS3(pacer.MinSleep(10ms))`) [SRC backend/s3/s3.go:1846, 980 @ 5bc93a2a7]
wrapping each call in `f.pacer.Call(shouldRetry)`. `shouldRetry` retries
generic AWS-SDK retryables, `RequestTimeout`, and HTTP 429/500/503
[SRC backend/s3/s3.go:1267-1271, shouldRetry @ 5bc93a2a7]. A 301 (wrong region)
triggers a one-shot region re-resolve and retry when a bucket is specified. There
is also a **self-healing XML-encoding retry**: if a listing without URL-encoding
hits an `xml.SyntaxError`, rclone flips `EncodingType=url` and retries
[SRC backend/s3/s3.go:2480-2488 @ 5bc93a2a7]; for AWS it URL-encodes from the
start (`encoding-type=url` seen on every request [RUN _capability/debug]).

**Ordering.** rclone makes no promise of sorted output and does not sort listing
results; entries stream in S3 return order (byte order for v1/v2). Directory
synthesis in the walker means order across a fast-list is not lexicographic
overall. [INFERRED from streaming callback design, SRC fs/walk/walk.go:287-346 @ 5bc93a2a7]

**Memory model.** The S3 `ListR` streams entries to the caller through
`list.NewHelper`/tranche flush rather than accumulating all objects
[SRC backend/s3/s3.go:2745-2764 @ 5bc93a2a7]; `--fast-list`'s extra memory is the
walker's `dirMap` of directory paths it must retain to synthesize parents
[SRC fs/walk/walk.go:256-346 @ 5bc93a2a7], which scales with the number of
directories, not objects. On this broad-shallow bucket that stayed small: full
148,917-key fast-list peaked at **69.6 MB RSS** [RUN receipts/smoke/recursive-fastlist].
Whether memory stays bounded on a deep or enormous keyspace is a scale question
this smoke cannot answer — deferred to the benchmark.

**Resume/checkpoint.** None for listing. A listing is a transient enumeration
with no on-disk cursor; an interrupted `lsjson` starts over. (rclone's resume
machinery is for transfers, not enumeration.) [INFERRED, SRC: no checkpoint state
in `Fs.list`]

## 3. Modes and tunables

"Mode" = a change in request pattern or output contract. "Tunable" = magnitude
only.

### Modes (request pattern / output contract)

| Mode | How invoked | What changes | Evidence |
| --- | --- | --- | --- |
| recursive-fastlist | `lsjson --fast-list -R` | Flat recursive `ListObjectsV2`, no delimiter, serial paging. NB `--fast-list` is inert for `lsjson -R` (already `ListR`); the flag is kept only to name intent | [SRC :2428-2432,:2745; fs/walk/walk.go:149-163 @ 5bc93a2a7] [RUN recursive-fastlist] |
| recursive-walk (genuine hierarchical) | `lsjson --disable ListR -R` | Per-directory `ListObjectsV2` w/ `Delimiter=/`, children from `CommonPrefixes` fanned across `--checkers`. `--disable ListR` forces the per-directory `Walk` fallback | [SRC :2430-2433; fs/walk/walk.go:152-160,380,393 @ 5bc93a2a7] [RUN recursive-walk, _capability/walk-debug] |
| ~~recursive-hierarchical~~ (`lsjson -R`, no `--fast-list`) | — | **MISLABELED — withdrawn.** `lsjson -R` calls `walk.ListR` directly, which selects the flat backend `ListR` regardless of `--fast-list` [SRC fs/operations/lsjson.go:248, fs/walk/walk.go:149-163 @ 5bc93a2a7]. The receipt is a third flat listing, not a walk; `--checkers` was inert. Kept as an annotated receipt only | [RUN receipts/smoke/recursive-hierarchical — see its correction block] |
| delimiter-shallow | `lsjson` / `lsf` / `lsd` (no `-R`) | Single delimiter level; returns objects + `CommonPrefixes` (dirs) | [SRC :2506-2542 @ 5bc93a2a7] [RUN delimiter-shallow] |
| listv1 (legacy API) | `--s3-list-version 1` | `ListObjects` (v1) instead of v2; `Marker` paging | [SRC :2147-2162 @ 5bc93a2a7] [RUN listv1] |
| versions | `--s3-versions` / `--s3-version-at T` | `ListObjectVersions` API (different request + output) | [SRC :2310-2334 @ 5bc93a2a7] — **not smoked** (bucket unversioned; see §8) |
| Output formats: `lsjson`/`lsf`/`ls`/`lsl`/`lsd` | subcommand | Distinct output contracts over the same request pattern | [DOC] [RUN lsf, delimiter-shallow] |

### Tunables (magnitude only — flag for the benchmark sweep)

| Flag | Default | Effect | Sweep? | Evidence |
| --- | --- | --- | --- | --- |
| `--s3-list-chunk` (`MaxKeys`) | 1000 | Objects per LIST page; AWS caps at 1000 | **Yes** (values ≤1000; AWS ignores >1000) | [SRC :426-434 @ 5bc93a2a7] [RUN _capability/debug shows `max-keys=1000`] |
| `--checkers` | 8 | Concurrent directory listings in the genuine hierarchical walk. **No effect on the flat `ListR`** (a single serial chain — which is what a plain `lsjson -R` runs) | **Yes** (walk mode only, i.e. `--disable ListR`/`--max-depth`) | [SRC fs/walk/walk.go:380,393, fs/config.go:60-61 @ 5bc93a2a7] [RUN recursive-walk, _capability/walk-debug — walk fans out per-directory] |
| `--s3-list-version` | 0 (auto→2) | Which List API | mode, not tunable | [SRC :437-462,:1745-1749 @ 5bc93a2a7] |
| `--transfers` | 4 | Transfer concurrency — **irrelevant to listing** | No | [SRC fs/config.go:65-66 @ 5bc93a2a7] |
| `--s3-list-url-encode` | unset (auto) | URL-encode listings (control-char safety) | No (correctness, not speed) | [SRC :453-465 @ 5bc93a2a7] |

Smoked one non-default tunable value: `--checkers 4` in **recursive-walk** — the
genuine hierarchical walk, where `--checkers` is actually operative (it bounds the
per-directory worker pool). The earlier `recursive-hierarchical` run also passed
`--checkers 4`, but that run was a flat `ListR` (a single serial chain), so the
flag was **inert** there — see that receipt's correction block. `--s3-list-chunk`
left at 1000 (AWS max); smaller values only add pages for the same keyspace — a
pure sweep item [INFERRED from the single serial page loop, SRC backend/s3/s3.go:2472 @ 5bc93a2a7].

## 4. How to run it properly

**Quickstart (anonymous, no config file), on-the-fly backend string:**

```
rclone lsjson --fast-list --files-only --use-server-modtime --no-mimetype -R \
  :s3,provider=AWS,region=us-east-1:noaa-normals-pds
```

**Anonymous / unsigned access.** rclone signs nothing when `access_key_id` and
`secret_access_key` are empty and `env_auth` is false (its default): it installs
`aws.AnonymousCredentials{}` [SRC backend/s3/s3.go:1508-1511 @ 5bc93a2a7]. There
is no `--no-sign-request` flag — **absence of credentials *is* the anonymous
mode.** The `:s3,provider=AWS,region=R:bucket` connection-string remote supplies
provider+region with no config file. Confirmed unsigned on the wire: no
`Authorization` header on any request [RUN _capability/debug], and every smoke
run passed under the wrapper's credential-starved `auth=anonymous`
[RUN all receipts].

**The load-bearing footgun — do not let a listing HEAD every object.** rclone's
S3 `Object.ModTime` returns the listing's `LastModified` **only if
`--use-server-modtime` is set**; otherwise it calls `readMetaData` → **a HEAD per
object** to read `x-amz-meta-mtime` [SRC backend/s3/s3.go ModTime @ 5bc93a2a7].
`Object.MimeType` does the same [SRC backend/s3/s3.go MimeType @ 5bc93a2a7], and
`lsjson` computes both by default [SRC fs/operations/lsjson.go:181-185 @ 5bc93a2a7].
So a plain `rclone lsjson -R` (or `lsl`) on this bucket would fire **148,917
HEADs** on top of the LIST requests — ≈149 pages *if* `--fast-list` is used, and
*more* under the default hierarchical walk (one LIST per directory); the HEAD
storm dominates either way [SRC ModTime/MimeType/lsjson.go @ 5bc93a2a7; INFERRED
for the LIST-count comparison]. Proper listing therefore **must** pass
`--use-server-modtime --no-mimetype` (or `--no-modtime --no-mimetype`, or use
`lsf` without the `t`/`h` format codes). This is the single most important
"run it properly" fact for rclone-on-S3 listing, and it is easy to miss because
the extra HEADs are invisible without `-vv`.

**Large-listing best practice (per the project's own guidance).**
`--fast-list` "uses more memory but fewer transactions" [DOC `--fast-list` help];
the S3 backend docs recommend it for buckets where the transaction count matters
and warn it costs memory proportional to the listing
[DOC https://rclone.org/s3/, accessed 2026-07-17]. So: `--fast-list` (i.e. the
flat `ListR`, which a plain `lsjson -R` already is) for a whole-bucket enumeration
(few requests); the genuine hierarchical walk (`--disable ListR`/`--max-depth`)
with `--checkers` when you want to parallelise across many directories and trade
fewer transactions for a lower peak footprint. **The walk does NOT bound memory to
"one level at a time":** workers asynchronously enqueue child directories while
siblings are still listing [SRC fs/walk/walk.go:380,393 @ 5bc93a2a7], so multiple
levels can be in flight at once — the footprint is bounded by the `--checkers`-deep
job channel and whatever `dirMap`/sort state is retained, not by tree depth
[INFERRED, SRC fs/walk/walk.go:380,393 @ 5bc93a2a7]. No hinted/two-pass listing
workflow exists [DOC — none documented; INFERRED].

**Prerequisites.** Region matters for signed access but AWS anonymous listing
tolerates it; rclone defaults region to `us-east-1` when unset
[SRC backend/s3/s3.go:1521-1522 @ 5bc93a2a7]. Endpoint override via
`--s3-endpoint`; path-style via `--s3-force-path-style` (not needed for AWS).

**Footguns.** (1) The HEAD-per-object modtime/mimetype trap above. (2) The
hierarchical walk's `--checkers`-wide fan-out on a broad bucket can dwarf a
fast-list in request count. (3) `lsjson`/`ls`/`lsl` synthesize directory entries
in recursive mode; use `--files-only` to compare against an object manifest.

## 5. Output and observability

**Formats (output-contract modes).** `lsjson` (JSON array; per item `Path`,
`Name`, `Size`, `ModTime`, `IsDir`, `Tier`, optional `Hashes`/`Metadata`);
`lsf` (delimited, `--format` codes `p`ath/`s`ize/`t`ime/`h`ash/`T`ier/…, chosen
`--separator`); `ls` (size + path); `lsl` (size, mtime, path); `lsd`
(directories only). [DOC `rclone lsjson/lsf --help`; RUN delimiter-shallow, lsf]

**`normalize.sh` contract, per mode** (adapter runs off the measurement clock):

| Mode | key | size | etag | mtime | storage_class |
| --- | --- | --- | --- | --- | --- |
| recursive-fastlist / recursive-walk / -hierarchical / listv1 | `prefix + .Path` | `.Size` | `-` | `.ModTime`→sec+`Z` | `.Tier` |
| delimiter-shallow | file `.Path`; dir `.Path + "/"` | file `.Size`, dir `-` | `-` | file `.ModTime`, dir `-` | file `.Tier`, dir `-` |
| lsf | `prefix + path` | size | `-` | `-` | `-` |

- **etag is `-` everywhere by design.** rclone's S3 listing path surfaces no raw
  ETag (no `lsf` format code, no `lsjson` field). `lsjson --hash md5` returns an
  MD5 that equals the ETag only for single-part objects, so claiming it as the
  ETag would be wrong for multipart uploads — the adapter declines rather than
  risk a false field. [SRC fs/operations/lsjson.go:221-227 @ 5bc93a2a7, INFERRED]
- **storage_class is real**, taken from `lsjson`'s `.Tier`, which comes straight
  from the `ListObjectsV2` response (`"Tier":"STANDARD"`) — no HEAD
  [RUN recursive-fastlist raw output]. Verified equal to the manifest's
  `StorageClass` column on every non-dir row [RUN all lsjson receipts, fields=0].
- **mtime is UTC by construction.** Containers run `TZ=UTC` (wrapper-pinned) and
  `--use-server-modtime` returns S3 `LastModified` (whole seconds); `lsjson`
  prints RFC3339 with `Z` and `.000000000` fractional, which the adapter
  truncates to seconds — exact, not a rounding [RUN, SRC].

**Observability / metrics.** rclone exposes **no API-call counter** in listing
output; `--stats` counts transfers, not LISTs. The only route to request-level
visibility is `-vv --dump headers` (or `--dump bodies`), which prints each HTTP
request line on stderr — used here to trace both request patterns. For a
**flat/undelimited** listing (a plain `lsjson -R`, with or without `--fast-list`)
the request count is `ceil(keys / list_chunk)` pages issued serially — traced: the
undelimited `continuation-token` chain [RUN _capability/debug]. A **hierarchical
(delimited) walk** issues one LIST **per directory** instead, so its count is set
by the tree shape, not the key total — traced: 13 `delimiter=%2F` requests for the
3-directory `normals-annualseasonal/1981-2010/` subtree (parent + `access/` in 10
serial pages + `archive/` + `doc/`) [RUN _capability/walk-debug]. rclone is
verbosity-flag driven, not `RUST_LOG`-driven.

## 6. Failure surface

- **Memory under `--fast-list`.** Docs explicitly warn it "uses more memory"
  [DOC `--fast-list`]. Smoke stayed at ~70 MB for 148,917 keys
  [RUN recursive-fastlist], but that is a broad-shallow bucket; growth on a huge
  or deep keyspace is unmeasured — a benchmark question. [INFERRED]
- **Interruption.** No listing checkpoint; interrupted enumeration restarts.
  [INFERRED, §2]
- **Truncated/short responses.** rclone treats an `IsTruncated` response with no
  continuation token as a hard protocol error rather than silently stopping
  ("s3 protocol error: received listing v2 with IsTruncated set and no
  NextContinuationToken") [SRC backend/s3/s3.go:2209-2211 @ 5bc93a2a7] — a good
  completeness guard; the v1 path is more forgiving (falls back to last-key
  marker) [SRC :2155-2161 @ 5bc93a2a7].
- **Endpoint/provider quirks.** Extensive provider-specific handling
  (Ceph/DigitalOcean/IBM COS URL-encoding caveats) is documented in-code
  [SRC :2437-2448 @ 5bc93a2a7]; irrelevant to AWS but a caution for S3-compatibles.
- **`exit 0` on error?** Not observed here — every run exited 0 legitimately.
  The benchmark's memory-exhaustion / exit-code allegations are scale claims this
  smoke neither reproduces nor refutes. [INFERRED]

## 7. Container

**Image.** Upstream publishes an official multi-arch image on Docker Hub. Pinned
by **manifest-list digest** `rclone/rclone@sha256:c619…dc4a1` (tag `1.74.4`).
Entrypoint is `["rclone"]` [RUN `docker inspect`], so `run.sh` argv starts at the
subcommand. No self-built Dockerfile was needed; the upstream image is what users
run and the most defensible subject.

**Architecture matrix.**

| Channel | amd64 | arm64 | Notes |
| --- | --- | --- | --- |
| Upstream Docker image (manifest list `c619…`) | yes `sha256:cdbecba0…` | yes `sha256:7d8906d4…` | also `386`, `arm/v6`, `arm/v7` [RUN `docker buildx imagetools inspect`] |
| Prebuilt release binaries | yes | yes | rclone ships binaries for both natively [DOC https://rclone.org/downloads/] |
| Source build (Go) | yes | yes | pure-Go, `CGO_ENABLED=0` static [SRC Dockerfile @ 5bc93a2a7] |

**amd64 and arm64 are both natively supported on every channel** — no
common-denominator problem for the benchmark (amd64 is the expected choice).

**What smoke ran on.** Native `linux/arm64` (host is aarch64); **no emulation**
(`EMULATED=no — image arm64 on host arm64`) [RUN all receipts]. First in-container
execution recorded `rclone v1.74.4` on alpine 3.24.1, go1.26.5, static
[RUN receipts]. Live `--help`/`help flags` matched the Stage-A doc reading;
nothing surfaced that the docs omitted.

## 8. Smoke results

Bucket `noaa-normals-pds` (us-east-1), manifest snapshot 2026-07-17, sha256
`c78a827…92adb`, 148,917 keys. **Pre-flight: no drift** — an anonymous full
re-list with the pinned harness client matched the manifest exactly (148,917
records, byte-identical after canonicalization). All runs anonymous
(credential-starved), native arm64, non-benchmark.

| Mode | Scope | Invocation (argv after `rclone`) | Exit | Wall | Verdict | Receipt |
| --- | --- | --- | --- | --- | --- | --- |
| recursive-fastlist | full bucket | `lsjson --fast-list --files-only --use-server-modtime --no-mimetype -R :s3,…:BUCKET` | 0 | 16.95s | **PASS** 148917/148917 | receipts/smoke/recursive-fastlist |
| recursive-fastlist | `normals-monthly/1991-2020/` | …`-R :s3,…:BUCKET/PREFIX` | 0 | 1.74s | **PASS** 15625 | receipts/smoke/recursive-fastlist-monthly1991 |
| recursive-fastlist | `normals-annualseasonal/1981-2010/access/` | …scoped | 0 | 1.26s | **PASS** 9839 | receipts/smoke/recursive-fastlist-access |
| recursive-walk (genuine hierarchical) | `normals-annualseasonal/1981-2010/` | `lsjson --files-only --use-server-modtime --no-mimetype --disable ListR --checkers 4 -R …` | 0 | 1.39s | **PASS** 9841/9841 | receipts/smoke/recursive-walk |
| ~~recursive-hierarchical~~ | `normals-hourly/` | `lsjson … --checkers 4 -R …` | 0 | 0.53s | PASS 2549 — **but MISLABELED: a flat `ListR`, not a walk; `--checkers` inert** (see receipt correction block) | receipts/smoke/recursive-hierarchical |
| delimiter-shallow | root (`/`) | `lsjson --use-server-modtime --no-mimetype :s3,…:BUCKET` | 0 | 0.17s | **PASS** 5 (4 CommonPrefixes + index.html) | receipts/smoke/delimiter-shallow |
| listv1 | `normals-hourly/` | `…,list_version=1:BUCKET/PREFIX` (v1 `ListObjects`) | 0 | 1.36s | **PASS** 2549 | receipts/smoke/listv1 |
| lsf | `normals-hourly/` | `lsf --fast-list --files-only --format ps --separator ";" -R …` | 0 | 0.52s | **PASS** 2549 | receipts/smoke/lsf |

**Every verifier-checked mode PASS**, 0 duplicates / 0 missing / 0 extra / 0 field
mismatches. For lsjson modes the verifier checked **key + size + mtime +
storage_class** (etag exempt by adapter policy); lsf checked key + size only.
Designated registry prefixes covered: `normals-hourly/` (listv1, lsf; and the
mislabeled hierarchical run), `normals-monthly/1991-2020/` (fastlist),
`normals-annualseasonal/1981-2010/access/` (fastlist), `normals-annualseasonal/1981-2010/`
(genuine walk).

**Live bucket drift observed mid-session (2026-07-17, ~13:2x UTC).** While
producing the genuine-walk receipt, NOAA began re-uploading objects under
`normals-hourly/` and `normals-monthly/` — their `LastModified` advanced to
today's date (independently confirmed by the harness aws-cli re-list, which
returned `DRIFT`, not a tool finding [SRC harness/verify-listing.sh drift path]).
`normals-annualseasonal/1981-2010/` was still un-drifted (0/9,841 keys re-uploaded),
so the genuine walk was verified there and PASSES byte-exact. The earlier
`normals-hourly/`-scoped receipts (run ~12:0x, before the re-upload) predate the
drift and remain valid as recorded. This drift touches only mtime; keys, size and
storage_class are unchanged. Flagged to the manifest owner for a re-baseline
decision — **not** attributed to any tool.

**Request behaviour — both patterns traced.** `receipts/smoke/_capability/debug`
(`-vv --dump headers`) shows the **flat** `ListR` issuing a serial
`GET /?encoding-type=url&list-type=2&max-keys=1000&prefix=…` then
`continuation-token=…` chain with **no delimiter**.
`receipts/smoke/_capability/walk-debug` shows the **genuine hierarchical walk**
(`--disable ListR`) issuing 13 requests, **every one with `delimiter=%2F`**, one
per directory. Both confirm ListObjectsV2, page size 1000, serial paging within a
prefix, URL-encoded, unsigned (no `Authorization`). rclone exposes no built-in API
counter (§5).

**Fan-out.** N/A as a *keyspace-sharding* workaround — rclone's only parallelism
is internal (`--checkers` across discovered directories in the genuine walk), not
"generate N invocations", so there is no fan-out workaround to union-verify
[SRC fs/walk/walk.go:380,393 @ 5bc93a2a7]. The genuine walk *is* internally
concurrent across directories, but a single `lsjson` invocation still returns one
complete listing, verified whole.

**Deferred edge checks.** `EDGE_BUCKET=none`: unicode/weird-key/multipart-ETag
fidelity checks are **deferred**, not run.

**Concurrency discipline.** fast-list / listv1 / lsf / delimiter / and the
mislabeled `recursive-hierarchical` are all serial (internal concurrency 1 — a
single page loop with no goroutine fan-out [SRC backend/s3/s3.go:2472 @ 5bc93a2a7]).
Only **recursive-walk** (the genuine hierarchical walk) is internally concurrent:
capped at `--checkers 4`, one invocation at a time → product ≤ 4, within the
`CONCURRENCY_CAP=8` share. The default `--checkers 8` (product 8) sits exactly at
the cap and is flagged for the benchmark to schedule accordingly.

## 9. Notable findings

- **The HEAD-per-object modtime/mimetype trap (§4) is the headline.** A naive
  `rclone lsjson -R` on a public bucket silently adds a HEAD per object — turning
  a listing (≈149 pages under `--fast-list`; more under the default walk) into
  ~149 k requests. It is a correctness-of-methodology landmine for any
  benchmark: measuring rclone "listing" without `--use-server-modtime
  --no-mimetype` measures a HEAD storm, not a listing. Verified in source across
  three functions [SRC ModTime, MimeType, lsjson.go @ 5bc93a2a7].
- **Flat `ListR` vs the hierarchical walk is a genuine request-pattern fork — but
  `--fast-list` does NOT select it for `ls*` commands.** A plain `lsjson -R` is
  already the flat `ListR`; the fork to the `--checkers`-wide tree of delimiter
  listings is reached only via `--disable ListR` (or bounded `--max-depth`)
  [SRC fs/operations/lsjson.go:248, fs/walk/walk.go:149-163 @ 5bc93a2a7]. Radically
  different S3 traffic (one flat continuation chain vs one LIST per directory,
  both traced §8); the benchmark should treat them as distinct modes (this report
  does — recursive-fastlist vs recursive-walk).
- **Storage class rides along free.** rclone surfaces `.Tier` from the list
  response, so storage_class is verifiable with zero extra requests — unusual
  among listing tools, and it verified clean on all 148,917 keys.
- **Strict truncation guard on v2.** rclone converts a malformed truncated v2
  response into a hard error rather than a short listing [SRC :2209-2211] — a
  completeness property worth crediting.
- **No `--no-sign-request`; anonymity is the absence of credentials.** A clean
  design point, but a footgun for users who expect an explicit flag.
- **URL-encoding auto-retry.** The XML-syntax-error → retry-with-encoding path
  [SRC :2480-2488] is a nice robustness touch for control-character keys.

## 10. Open questions for the benchmark phase

1. **`--fast-list` memory at scale.** Smoke saw ~70 MB for 148,917 broad-shallow
   keys. Does the walker `dirMap` blow up on a deep or very large keyspace, and
   is there an OOM cliff (the memory allegation the study cares about)? Capture
   `peak_rss` + `cgroup_peak_mem` across a size/depth sweep.
2. **Hierarchical `--checkers` sweep.** For a broad bucket, at what `--checkers`
   does the genuine walk (`--disable ListR`) beat/lose to the flat `ListR` in
   wall-clock and in request count? Sweep `--checkers ∈ {1,2,4,8,16,32}`
   (respecting the campaign cap). NB the walk must be forced with `--disable ListR`
   or `--max-depth`; a plain `lsjson -R` is the flat path and ignores `--checkers`.
3. **`--s3-list-chunk` sweep** `∈ {100, 500, 1000}` (AWS caps at 1000): pages
   traded against per-request overhead. Cross-internet RTT may dominate.
4. **v1 vs v2 API** wall-clock and request-count parity at scale (smoke saw both
   complete identically at 2,549 keys).
5. **CPU vs network.** Per the harness note, cross-internet RTT can mask per-page
   CPU cost. Capture CPU time alongside wall-clock to test any language-bottleneck
   hypothesis for rclone's Go JSON/marshal path.
6. **Benchmark architecture:** amd64 (native on all channels) — no blocker.

## 11. Sources

**Pinned checkout:** `github.com/rclone/rclone` @ `v1.74.4`,
`5bc93a2a7ab0ebd0a11352bc4968eabeffb18027`. All `[SRC]` anchors are against this
commit.

**Docs (accessed 2026-07-17):**
- https://rclone.org/s3/ — S3 backend, `--fast-list`, list options, anonymous access
- https://rclone.org/commands/rclone_lsjson/ , `/rclone_lsf/`, `/rclone_lsd/` — output contracts
- https://rclone.org/downloads/ — release binary architectures
- `rclone help flags` / `rclone lsjson --help` (in-container) — live flag text [RUN _build]

**Third-party (accessed 2026-07-17):**
- https://api.github.com/repos/rclone/rclone — upstream health (stars, issues, activity, MIT)
- https://hub.docker.com/r/rclone/rclone — official image

**Image:** `rclone/rclone@sha256:c61954aaa32328a5486715dd063a81c7879f5195ad3505cd362deddd509dc4a1`
(arm64 `7d8906d4…`, amd64 `cdbecba0…`).

**Receipt index** (`tools/rclone/receipts/smoke/`): `recursive-fastlist`,
`recursive-fastlist-monthly1991`, `recursive-fastlist-access`, `recursive-walk`
(genuine hierarchical walk, PASS), `recursive-hierarchical` (mislabeled flat run,
annotated), `delimiter-shallow`, `listv1`, `lsf`, `_capability/debug` (flat trace),
`_capability/walk-debug` (walk trace). Large stdout payloads live under
`<data>/receipts/rclone/` with sha256 recorded in each `run.meta`.
Manifest sha256 `c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb`.
