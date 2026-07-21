# minio-mc — independent listing-tool report

Independent groundwork report for the s3-listing-study. Derived from primary
sources only (upstream docs, the pinned source, the minio-go SDK at its pinned
module version, and this agent's own smoke runs). Every behavioral claim carries
an evidence label; an unlabeled behavioral claim is a defect.

## 1. Metadata

| | |
| --- | --- |
| Tool | `mc` (MinIO Client) — the `mc ls` subcommand is the listing surface |
| Repo | https://github.com/minio/mc (canonical upstream; confirmed AGPLv3 MinIO project, not a fork) |
| Pinned release | `RELEASE.2025-08-13T08-35-41Z` (latest release tag reachable from `master`) |
| Pinned commit SHA | `7394ce0dd2a80935aded936b09fa12cbb3cb8096` |
| Language | Go (`go 1.23.0` in `go.mod`; binary built with go1.24.6 per `mc --version` [RUN]) |
| License | GNU AGPL v3 — [SRC LICENSE:1 @ 7394ce0], [DOC https://github.com/minio/mc] |
| Listing SDK | `github.com/minio/minio-go/v7 v7.0.90` (commit `68fb5ee339f2e3a798c14d12ca0e04c51f304d58`) — all LIST HTTP behavior lives here |
| Upstream health | **Repository is ARCHIVED (read-only) on GitHub** as of the API snapshot (`archived:true`, `pushed_at 2025-11-20`, `open_issues_count=47` — GitHub counts PRs as issues, so ~35 issues + ~12 PRs; 3522 stars) [DOC GitHub API repos/minio/mc]. The pinned release is effectively terminal — no further releases or issue triage expected. |
| Image | `minio/mc@sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727` (multi-arch index; arm64 sub-manifest `sha256:37d109dddbbb2c95873f5fc81ac93f37023264770fc580a7564148892087b1b7`) |
| Report date | 2026-07-17 (UTC) |

## 2. How it works

**One-line summary:** `mc ls` is a thin CLI front-end over the minio-go SDK's
`ListObjects` iterator. Listing is **strictly serial** — a single goroutine
issuing one `ListObjectsV2` request at a time, advancing by `continuation-token`,
streaming each page's entries down a Go channel to the printer. There is **no
keyspace division, no parallelism, no page-size or concurrency tuning** on the
listing path.

### Call path (mc, pinned @ 7394ce0)

`mainList` -> `doList` -> `S3Client.List` -> `unversionedList` ->
`listRecursiveInRoutine` / `listInRoutine` -> `listObjectWrapper` ->
`minio-go ListObjects`.

- `S3Client.List` launches **one** goroutine and returns a `<-chan *ClientContent`;
  it dispatches to `versionedList` when `--versions`/`--rewind` is set, else
  `unversionedList` [SRC cmd/client-s3.go:1897-1912 @ 7394ce0].
- `unversionedList` picks the routine by flags: recursive->`listRecursiveInRoutine`,
  non-recursive->`listInRoutine`, `--incomplete`->the multipart-uploads routines
  [SRC cmd/client-s3.go:2000-2014 @ 7394ce0].
- Both object routines call `listObjectWrapper(..., maxKeys=-1, ...)`
  [SRC cmd/client-s3.go:2351,2404,2420 @ 7394ce0].
- `listObjectWrapper` builds `minio.ListObjectsOptions{Prefix, Recursive,
  WithMetadata, MaxKeys}` and calls `api.ListObjects`. It forces the **legacy V1
  API only for Google Cloud** (`isGoogle` -> `UseV1:true`); for AWS/MinIO it uses
  V2. `--zip` adds the `x-minio-extract` header (MinIO-only)
  [SRC cmd/client-s3.go:1564-1584 @ 7394ce0].

### Request-level behavior (minio-go @ 68fb5ee / v7.0.90)

- **API + parallelism:** `ListObjects` routes to `listObjectsV2` for AWS
  [SRC api-list.go:771-789 @ v7.0.90]. `listObjectsV2` runs a **single
  goroutine** with a `for` loop: issue one `listObjectsV2Query`, stream
  `result.Contents` then `result.CommonPrefixes` to the channel, save
  `NextContinuationToken`, repeat while `IsTruncated`
  [SRC api-list.go:100-165 @ v7.0.90]. Serial by construction — each request
  needs the previous response's token. **Confirmed at the wire** by `mc --debug`
  [OBS receipts/smoke/_capability/debug-trace/]: the curated excerpt shows 3
  sequential `list-type=2` request lines, each after the first carrying the prior
  response's `continuation-token`. The excerpt is a redacted, unhashed probe (not
  a wrapper receipt): it evidences the serial continuation-token chain and the
  absence of an `Authorization` header; the per-page split (1000/1000/549) is
  [INFERRED] from the key count and observed page size, not read off the trace.
- **Keyspace division:** none. Recursion is server-side via the absence of a
  delimiter, not a client-side prefix split. Delimiter defaults to `/`; when
  `Recursive` it is set to `""` [SRC api-list.go:64-69 @ v7.0.90].
- **Page size:** server default (<=1000). mc passes `MaxKeys=-1`; the SDK sets
  `max-keys` on the query **only if `maxkeys > 0`** [SRC api-list.go:224-227 @
  v7.0.90], so mc never sends `max-keys` — it takes the S3 default of 1000.
  **No mc flag or env changes this.**
- **Query parameters** (every LIST): `list-type=2`, `encoding-type=url`,
  `fetch-owner=true`, `prefix=<p>`, `delimiter=<d>`, and `continuation-token`
  after page 1 [SRC api-list.go:191-222 @ v7.0.90], confirmed [OBS].
  `encoding-type=url` means the wire keys are URL-encoded and the SDK decodes
  them; `fetch-owner=true` requests owner metadata on every entry.
- **Retries/backoff:** SDK default `MaxRetry = 10`, base `DefaultRetryUnit =
  200ms`, exponential-with-jitter via `newRetryTimer` [SRC retry.go:31-42,
  api.go:663 @ v7.0.90]. Retries wrap the whole HTTP request in `executeMethod`;
  a mid-listing page error is retried up to 10 times, else the error is sent
  down the channel and listing aborts [SRC api-list.go:116-121 @ v7.0.90].
- **Timeouts:** no per-request HTTP client timeout is set; transport uses
  `IdleConnTimeout=90s`, `TLSHandshakeTimeout=10s`, `ExpectContinueTimeout=10s`
  [SRC cmd/client.go:342-357 @ 7394ce0]. mc additionally has hidden
  `--conn-read-deadline`/`--conn-write-deadline` global flags defaulting to
  10 minutes [SRC cmd/flags.go:87-92 @ 7394ce0].
- **Ordering:** relies on S3 returning keys in lexicographic (UTF-8 byte) order;
  mc does not re-sort object output (it sorts only bucket names, for site-wide
  listings, [SRC cmd/client-s3.go:2379-2383 @ 7394ce0]). Smoke output matched
  the byte-ordered manifest with 0 reordering [RUN recursive-json].
- **Truncation guard:** if S3 returns `IsTruncated` but an empty
  `continuation-token`, the SDK emits an explicit "S3 server is incompatible"
  error rather than looping [SRC api-list.go:157-163 @ v7.0.90].

### Memory model

**Streaming, bounded.** Objects flow object-by-object through channels from the SDK goroutine to `doList` (mc's `contentCh` is
unbuffered/cap-0 [SRC cmd/client-s3.go:1901]; minio-go's `objectStatCh` is cap-1
[SRC api-list.go:63 @ v7.0.90]), which prints each entry as it
arrives; nothing accumulates the full key set in memory for a plain listing
[SRC cmd/client-s3.go:1901-1911 @ 7394ce0], [SRC api-list.go:63,124-133 @
v7.0.90]. Consistent with smoke, where RSS did not grow with key count: `peak_rss`
28.1 MB / `cgroup_peak` 9.0 MB on the 5-entry shallow run vs 35.4 MB / 16.1 MB
on the 148,917-key full run [RUN shallow/receipt.md:87 vs recursive-json/receipt.md:87]
— a small, roughly constant footprint at smoke scale, not a scale sweep.
Exception: `mc ls --versions` sorts *per-object* version groups in memory
(`sortObjectVersions`) but only within one key's versions, not globally
[SRC cmd/ls.go:167-178 @ 7394ce0] [INFERRED bounded].

### Resume / checkpoint

**None.** No resume token is exposed to the user; an interrupted `mc ls` restarts
from the beginning. The `continuation-token` lives entirely inside the SDK loop
and is never surfaced [SRC api-list.go:110-150 @ v7.0.90] [INFERRED].

## 3. Modes and tunables

A *mode* changes the request pattern or output contract; a *tunable* only changes
magnitude. mc's listing tunables are almost all **absent** — the notable finding.

| Flag / mode | Default | Kind | Effect | Evidence |
| --- | --- | --- | --- | --- |
| (plain) `mc ls s3/b/` | delimiter `/` | **mode** | One directory level: CommonPrefixes as folders + keys at that level | [SRC api-list.go:64-65], [RUN shallow] |
| `--recursive, -r` | off | **mode** | Drops the delimiter -> full recursive key listing | [SRC cmd/client-s3.go:2385], [RUN recursive] |
| `--json` (global) | off | **mode** (output contract) | One JSON object per line (JSONL): exact `size`,`etag`,`lastModified`,`storageClass`,`key` | [SRC cmd/flags.go:57-61; cmd/ls.go:88-95], [RUN recursive-json] |
| `--versions` | off | **mode** | `GET ?versions` (list-object-versions API) instead of list-type=2 | [SRC cmd/client-s3.go:1566, api-list.go:772], [RUN versions-json-hourly] |
| `--rewind <t>` | off | **mode** | Point-in-time listing via the versions API at a timestamp | [SRC cmd/ls-main.go:127-158] parses the timestamp; the versions-API dispatch is [SRC cmd/client-s3.go:1904-1905, 1566] (not smoked: bucket unversioned) |
| `--incomplete, -I` | off | **mode** | Lists in-progress multipart uploads (`ListMultipartUploads`), not objects | [SRC cmd/client-s3.go:2016-2102] (not smoked: object-listing study; returns none here) |
| `--zip` | off | mode | Lists entries inside a zip object — **MinIO servers only**, N/A on AWS S3 | [SRC cmd/client-s3.go:1576-1582] |
| `mc find` (subcommand) | — | **mode** | Alternate traversal; drives the same serial `List()` path as `ls --recursive`; distinct output contract (alias-prefixed absolute keys, **no ETag even in `--json`**). **Completeness caveat: `mc find` unconditionally SKIPS `GLACIER` objects** [SRC cmd/find.go:304], so it is not a faithful full lister on buckets with archived objects — the smoke bucket is all `STANDARD`, so the PASS receipts cannot exercise this hole. | [SRC cmd/find.go:275-284,304], [RUN find-json-hourly] |
| `--summarize` | off | tunable | Appends total object count + size; same request pattern | [SRC cmd/ls-main.go:52-54] |
| `--storage-class, --sc <SC>` | "" | tunable | **Client-side** filter of the listed set to a storage class (skips non-matching entries after listing) | [SRC cmd/ls.go:244] (flag decl [SRC cmd/ls-main.go:55-58]) |
| page size | 1000 (server) | **not tunable** | mc passes `MaxKeys=-1`; no flag/env to change it | [SRC cmd/client-s3.go:2351; api-list.go:224-227] |
| listing concurrency | 1 (serial) | **not tunable** | Single-goroutine paginator; no fan-out flag exists | [SRC api-list.go:100-165] |

**Nothing here for the benchmark to sweep on the listing path** — page size and
concurrency, the usual knobs, do not exist as mc options. The benchmark's mc
story is "serial baseline, unconfigurable." The only per-mode variation worth
running is the API/output-contract axis (recursive vs delimiter vs versions;
text vs JSON), all smoked below.

## 4. How to run it properly

### Quickstart (anonymous, public AWS bucket)

mc has **no `--no-sign-request` flag.** It resolves credentials from a named
*alias*. Anonymous access = an alias whose access/secret keys are empty, which
minio-go resolves to `SignatureAnonymous` and then skips signing
[SRC cmd/client.go:301-311 @ 7394ce0], [SRC pkg/credentials/static.go:55-58
@ v7.0.90], [SRC api.go:905 @ v7.0.90]. Two ways to get an empty-cred alias:

```sh
# (a) ad-hoc via env — no config file needed (used by this study's harness):
docker run --rm --network host -e MC_HOST_s3=https://s3.amazonaws.com \
  minio/mc ls --recursive s3/noaa-normals-pds/          # [RUN] anonymous, works

# (b) persistent alias (writes ~/.mc/config.json):
mc alias set s3 https://s3.amazonaws.com "" ""          # empty access/secret
mc ls --recursive s3/noaa-normals-pds/
```

The `MC_HOST_<alias>` value carries `user:pass@` only when credentials are
wanted; omitting them **is** the anonymous mechanism [DOC min.io mc docs;
confirmed RUN]. `mc` **does not read `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`
for signing** — its credential chain is `MC_STS_ENDPOINT_*` (optional) then the
alias's static keys only [SRC cmd/client.go:269-311 @ 7394ce0]. So for mc,
"credential starvation" means *no alias credentials and no mounted config*, which
the harness satisfies (see section 7). Region is auto-resolved via an anonymous
`GET ?location` before the first LIST [OBS debug-trace].

### Best-practice config for large listings

Upstream ships **no published capacity/large-listing guidance and no benchmark**
for `mc ls`; the docs treat it as a simple directory lister [DOC
https://github.com/minio/mc, README.md]. There is no hinted/two-pass workflow and
no parallel-listing option to recommend — the "proper" large listing is simply
`mc ls --recursive` (optionally `--json` for machine parsing), which the tool
streams. For scripts, prefer `--json` (stable contract, exact bytes) over the
human text format (see section 5 footguns).

### Footguns

- **Text output size is humanized and lossy** (`humanize.IBytes` -> `1006KiB`);
  the exact byte size is available **only** via `--json`
  [SRC cmd/ls.go:62 @ 7394ce0] [RUN].
- **Text output prints no ETag at all**; `--json` does [SRC cmd/ls.go:60-86 vs
  88-95 @ 7394ce0].
- **Pager applies to help text, not listing output.** On a TTY mc sends only
  `app.HelpWriter` through the internal pager [SRC cmd/main.go:525-526]; listing
  messages go straight through `console.Println`/`printMsg` [SRC cmd/print.go:35],
  so `mc ls` output is not paged. `--disable-pager`/`MC_DISABLE_PAGER` exists
  [SRC cmd/flags.go:46-51] but does not affect listing. (An earlier draft wrongly
  claimed listing output is paged; corrected after Stage E.)
- **Keys are printed relative to the listed target**, not as absolute keys — a
  scoped `mc ls s3/b/prefix/` prints keys without the `prefix/`
  [SRC cmd/ls.go:114-128 @ 7394ce0] [RUN]. `normalize.sh` re-prepends the
  scope prefix.
- **Trailing slash only saves a probe (not a stat-vs-list switch):** `mc ls s3/b`
  stats the target, recognises it is a directory, appends `/`, and lists it
  anyway; `mc ls s3/b/` skips the extra `Stat` probe [SRC cmd/ls-main.go:220-228].
  Both list the bucket. (Corrected after Stage E — an earlier draft implied the
  no-slash form only stats.)

## 5. Output and observability

### Formats

- **Text (default):** `[<mtime> UTC]<%7s size>[ <SC>] <key>` per line, colorized
  only on a TTY (plain under the harness). Size humanized; no ETag; folders
  (common prefixes) show size `0B` and a **synthetic `time.Now()` mtime**
  [SRC cmd/ls.go:60-86; cmd/client-s3.go:2284-2288 @ 7394ce0], `printDate =
  "2006-01-02 15:04:05 MST"` [SRC cmd/ls.go:36]. With `TZ=UTC` the stamp reads
  `... UTC` and is genuinely UTC.
- **JSON (`--json`):** **single-line JSONL** — one compact JSON object per line
  with `status,type,lastModified(RFC3339 Z),size(exact int64),key,etag,url,
  storageClass` [RUN shallow-json]. (Source uses `json.MarshalIndent`
  [SRC cmd/ls.go:89-95 @ 7394ce0], but the observed stream is compacted by the
  console layer to one line per object — [RUN] over [SRC] here.) Folders
  carry `type:"folder"`, empty `etag`, and a nanosecond synthetic `lastModified`.

### `normalize.sh` contract (this tool's adapter)

Emits `key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class`, `-` where a mode does
not expose a field. Prepends the run's scope prefix to reconstruct full keys.

| Mode(s) | key | size | etag | mtime | storage_class |
| --- | --- | --- | --- | --- | --- |
| `*-json` (recursive/shallow/versions) | yes | yes exact | yes | yes (folders `-`) | yes (folders `-`) |
| `recursive`, `shallow` (text) | yes | `-` (humanized/lossy) | `-` (absent) | yes (folders `-`) | yes from SC token |

JSON parsing uses `jq`; text parsing uses a `python3` regex. Both run **after**
the wrapper's clock stops (adapters are never on the measurement clock).

### Metrics / counters / logs mc exposes

- **No built-in API-call counter or progress metric for `ls`** [SRC cmd/ls.go;
  live `--help` shows no counter flag]. Request count is inferable only from key
  volume (ceil(keys/1000) LIST pages) [INFERRED] or observed via `--debug` [OBS].
- **`--debug`** dumps full HTTP request/response headers and per-request
  `Response Time` — the route to confirm serial pagination, page size, unsigned
  requests, and endpoint/region resolution [OBS debug-trace]. This is the
  richest observability surface and what the study's replay-server phase can key
  off.

## 6. Failure surface

- **Memory growth:** bounded by design — streaming, no full-keyset accumulation
  [SRC section 2], and smoke RSS did not grow with key count (28-35 MB across 5 to
  148,917 keys [RUN]). This is a smoke-scale observation, **not** proof of O(1) at
  millions of keys; scale-dependent OOM behaviour is **not** settleable here.
  `--versions` holds only one key's version group at a time [INFERRED].
- **Interruption:** no resume; an aborted listing restarts from scratch
  [INFERRED section 2].
- **Error handling:** per-page errors retried <=10x with 200ms exponential-jitter
  backoff, then the error is streamed and listing stops [SRC api-list.go:116-121;
  retry.go:31-42 @ v7.0.90]. A truncated response with no continuation token is
  turned into an explicit incompatibility error [SRC api-list.go:157-163].
- **Endpoint quirks:** forces legacy `ListObjectsV1` for Google Cloud Storage
  [SRC cmd/client-s3.go:1570-1574 @ 7394ce0]; falls back to V1 for "snowball"
  regions [SRC api-list.go:781-785 @ v7.0.90]. Neither applies to AWS S3.
- **Upstream is archived** — an archived repo is read-only, so no fixes are
  *expected* to land [DOC GitHub API, accessed 2026-07-17]; [INFERRED] a failure
  in this release line is unlikely to be fixed upstream. (Archived repos *can* be
  unarchived, so this is a maintenance-posture inference, not a permanence proof.)

All of the above are labeled hypotheses except where a smoke receipt is cited;
none are generalized past smoke scale.

## 7. Container

**Image:** upstream official `minio/mc:RELEASE.2025-08-13T08-35-41Z`, pinned by
digest `sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727`
(multi-arch manifest index). Chosen per the brief's "prefer the tool's own
upstream image" rule — it is what users actually run, and the most defensible
answer to "you configured it wrong." Entrypoint is `["mc"]` [RUN docker inspect],
so `run.sh` argv starts at the subcommand/global-flag, not `mc`.

**Architecture matrix** (`docker manifest inspect` [RUN]):

| Channel | amd64 | arm64 | other |
| --- | --- | --- | --- |
| Upstream image `minio/mc` (this tag) | yes native (`sha256:eb4ea988...`) | yes native (`sha256:37d109dd...`) | yes ppc64le |
| Prebuilt binaries `dl.min.io/client/mc/...` | yes | yes | per-arch archives; `Dockerfile.release` fetches `linux-${TARGETARCH}` |
| Source build (Go) | yes | yes | any Go target |

amd64 (the campaign's expected common denominator) is natively supported — no
emulation needed for either candidate arch.

**Smoke ran natively on arm64** (runner is aarch64; image arm64) — `emulated=no`
in every receipt. No Dockerfile was authored (upstream publishes the image).

**Anonymous wiring:** the harness passes `--env MC_HOST_s3=https://s3.amazonaws.com`,
an endpoint-naming alias with **no embedded credentials**. This is endpoint
configuration mc structurally requires (it has no default endpoint like aws-cli),
**not** a traffic redirect or credential injection: it names real AWS S3, and
`auth=anonymous` remains enforced because the alias carries no keys and no config
is mounted. The wrapper's `--env` guard accepts the name (not credential-shaped);
the value has no credential shape [RUN receipts].

## 8. Smoke results

Bucket `noaa-normals-pds` (us-east-1), manifest `noaa-normals-pds.2026-07-17`
(sha256 `c78a827...2adb`, 148,917 keys). All runs **anonymous**, native arm64,
via `harness/smoke-run.sh`; all verdicts via `harness/verify-listing.sh`.
**Pre-flight:** an independent anonymous re-list with the pinned harness client
was **byte-identical** to the manifest (decompressed sha256 `8b5b584...`) — no
drift; recorded at [OBS `receipts/smoke/_capability/preflight/preflight.md`]. Edge-case fidelity checks (unicode/weird-key/multipart-ETag) are
**deferred** (`EDGE_BUCKET=none`).

| Mode | Invocation (argv after `mc`) | Scope | Exit | Wall | Verdict | Receipt |
| --- | --- | --- | --- | --- | --- | --- |
| recursive-json | `--json ls --recursive s3/b/` | full (148,917) | 0 | 91.8s | **PASS** 0 miss/extra/dup, fields ok | `receipts/smoke/recursive-json/` |
| recursive (text) | `ls --recursive s3/b/` | full (148,917) | 0 | 75.4s | **PASS** key+mtime ok | `receipts/smoke/recursive/` |
| shallow (text) | `ls s3/b/` | delimiter `/` (5) | 0 | 0.27s | **PASS** | `receipts/smoke/shallow/` |
| shallow-json | `--json ls s3/b/` | delimiter `/` (5) | 0 | 0.21s | **PASS** | `receipts/smoke/shallow-json/` |
| recursive-json | `--json ls --recursive s3/b/normals-hourly/` | prefix (2,549) | 0 | 1.6s | **PASS** | `receipts/smoke/recursive-json-hourly/` |
| recursive-json | `--json ls --recursive s3/b/normals-monthly/1991-2020/` | prefix (15,625) | 0 | 9.7s | **PASS** | `receipts/smoke/recursive-json-monthly1991/` |
| recursive-json | `--json ls --recursive s3/b/normals-annualseasonal/1981-2010/access/` | prefix (9,839) | 0 | 4.7s | **PASS** | `receipts/smoke/recursive-json-annualaccess/` |
| versions-json | `--json ls --versions --recursive s3/b/normals-hourly/` | prefix (2,549) | 0 | 1.7s | **PASS** | `receipts/smoke/versions-json-hourly/` |
| find-json | `--json find s3/b/normals-hourly/` | prefix (2,549) | 0 | 1.6s | **PASS** (key+size+mtime; no etag) | `receipts/smoke/find-json-hourly/` |
| find (text) | `find s3/b/normals-hourly/` | prefix (2,549) | 0 | 1.5s | **PASS** (key only) | `receipts/smoke/find-hourly/` |

Notes:
- `recursive-json` full-bucket is the **fidelity anchor**: 148,917 distinct keys,
  `size`+`etag`+`mtime`+`storage_class` all matched the manifest exactly.
- The `shallow` delimiter modes returned exactly the expected 4 CommonPrefixes +
  `index.html` (verifier's derived delimiter-scope set) [RUN].
- `versions-json` on the unversioned bucket returned each object once with the
  same key/size/etag/mtime as list-type=2 — the versions API is a distinct
  request pattern that produces the same object set here [RUN].
- Durations are recorded facts about each run, **not** comparative numbers.
- Request-behavior [OBS]: the curated `mc --debug` excerpt on the hourly prefix
  shows one `GET ?location` then serial `list-type=2` request lines, each after
  the first carrying the prior `continuation-token`, and no `Authorization`
  header (`receipts/smoke/_capability/debug-trace/`). The excerpt is redacted and
  unhashed (a probe, not a wrapper receipt); the 1000/1000/549 page split is
  [INFERRED] from the 2,549-key count, not read off the trace.

- `mc find` (added at Stage D because the inherited dossier named it — honest
  mixed provenance) prints keys as **alias-prefixed absolute paths**
  (`s3/<bucket>/<key>`) and emits **no ETag even under `--json`**; `normalize.sh`
  strips the `<alias>/<bucket>/` prefix and marks etag `-`. It traverses via the
  same serial `List()` path as `ls --recursive` [SRC cmd/find.go:275-284] [RUN].

### Adapter note (honest provenance)

The first `recursive` (text) verify returned **FAIL missing=2** — an adapter bug,
not a tool defect: two keys had a size string exactly 7 chars wide (`1006KiB`),
which `%7s` prints with **no** separating space after `]`, and the text regex
required whitespace there. Fixed (`\s+`->`\s*`) [normalize.sh]; the mode was
re-run (fresh receipt) and PASSES. The tool listed all 148,917 keys throughout —
proven independently by the `recursive-json` PASS on the same key set.

## 9. Notable findings

_Each bullet restates a finding established with its evidence label in §2–§8;
the load-bearing labels ([SRC]/[RUN]/[OBS]/[DOC]/[INFERRED]) live there and are
not re-attached per sentence here._

- **mc `ls` is a serial paginator with zero listing knobs.** The two dimensions a
  large-scale lister usually exposes — page size and concurrency — simply do not
  exist as mc options; `MaxKeys=-1` is hard-wired and the SDK loop is
  single-goroutine. For the benchmark, mc is the honest serial baseline.
- **The listing brain is minio-go, not mc.** Every meaningful LIST decision
  (V1/V2, delimiter, pagination, retry, encoding) is in the SDK; mc contributes
  the CLI, the alias/credential model, and the output formatting. A reader
  auditing mc's listing must audit `minio-go v7.0.90`.
- **Text output is quietly lossy** — humanized sizes and no ETag mean the default
  human format cannot round-trip object metadata; `--json` is the only faithful
  contract. A subtle trap for anyone scripting against `mc ls` text.
- **No AWS-env signing.** Unlike aws-cli/rclone, mc ignores `AWS_ACCESS_KEY_ID`
  etc.; credentials come only from its own alias/`MC_HOST_*`/STS chain. This
  changes what "anonymous" even means for mc and is a real capability distinction.
- **Folders are fabricated client-side** with `time.Now()` timestamps and zero
  size — a delimiter listing's "directories" are synthetic CommonPrefixes, not
  objects. `normalize.sh` marks their mtime/size as `-`.
- **Upstream archived.** The canonical repo is read-only; the community `mc` line
  is effectively frozen at this release. A material fact for a study that will be
  published and for anyone choosing tooling.
- **An anonymous `GET ?location` precedes listing** — minio-go resolves the
  bucket region first (works unsigned on this public bucket), then targets the
  region-specific dualstack virtual-host endpoint `noaa-normals-pds.s3.dualstack.
  us-east-1.amazonaws.com` [OBS].

## 10. Open questions for the benchmark phase

1. **Serial throughput ceiling.** With page size fixed at 1000 and strict serial
   pagination, mc's full-bucket time is approx `ceil(N/1000) x (RTT + page-parse)`.
   At 148,917 keys this was ~76-92s cross-internet. In-region it will be RTT-bound
   and much faster, but still serial — the benchmark should measure the serial
   wall-clock and per-page CPU, not sweep knobs that don't exist.
2. **Text vs JSON CPU/output cost.** `--json` marshals every entry; text
   humanizes sizes. At scale the formatting cost may be measurable — worth
   capturing CPU time for both output contracts (same request pattern).
3. **Memory at scale.** Smoke showed flat ~35 MB RSS, but this is
   scale-independent by design (streaming). Confirm no accumulation at
   millions of keys and under `--versions` (per-key version grouping).
4. **Common-denominator arch:** amd64 is natively supported by the upstream
   image (and arm64/ppc64le); the benchmark's single-arch choice is unconstrained
   by mc. Flagged per the brief.
5. **Retry/throttle behavior under 503s** — the SDK's 10x/200ms-jitter policy is
   untestable politely at smoke scale; a replay-server fault-injection run would
   settle it.
6. **`--versions` / `--rewind` / `--incomplete`** on a *versioned* and a
   *multipart-heavy* bucket — distinct request patterns not exercisable on this
   tame, unversioned bucket. Proposed for an edge/versioned fixture.

## 11. Sources

**Pinned source:** mc `RELEASE.2025-08-13T08-35-41Z` @
`7394ce0dd2a80935aded936b09fa12cbb3cb8096`; minio-go `v7.0.90` @
`68fb5ee339f2e3a798c14d12ca0e04c51f304d58`. All [SRC] anchors are against these.

**Docs / third-party (accessed 2026-07-17):**
- https://github.com/minio/mc — README, help text, Dockerfiles ([DOC]).
- GitHub REST API `repos/minio/mc` — archived status, health metrics ([DOC]).
- minio-go SDK source `github.com/minio/minio-go/v7@v7.0.90` ([SRC]).
- `mc --version`, `mc ls --help`, `mc --debug` observed in-container ([RUN]/[OBS]).

**Receipt index** (`tools/minio-mc/receipts/smoke/`): `recursive-json/`,
`recursive/`, `shallow/`, `shallow-json/`, `recursive-json-hourly/`,
`recursive-json-monthly1991/`, `recursive-json-annualaccess/`,
`versions-json-hourly/`, `find-hourly/`, `find-json-hourly/`,
`_capability/debug-trace/` (request-shape probe), and
`_capability/preflight/` (drift pre-flight).
Full-bucket stdout payloads are externalized under
`<data>/receipts/minio-mc/` with sha256 recorded in each `run.meta`.
