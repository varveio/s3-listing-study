# minio-mc — mechanism

Source-anchored architecture of `mc`'s listing path, drawn from the groundwork
report ([`../research/report.md`](../research/report.md)) and its critical
cross-check ([`../research/codex-review.md`](../research/codex-review.md), which
preserves the review history). Evidence labels are: `[DOC]`
docs, `[SRC file:line @ sha]` pinned source, `[RUN receipt]` a committed smoke
run, `[OBS]` observed but not wrapper-recorded (a capability probe), `[INFERRED]`.
Canonical tested identity is in [`../data/tool.json`](../data/tool.json), and
references of the form claim `some-id` resolve in the canonical ledger,
[`../data/claims.json`](../data/claims.json). Study state uses the canonical
status vocabulary (`supported`, `confirmed`, `unverified`, `unverifiable`).

Two pinned checkouts are cited. mc anchors are the CLI/formatting layer:
**`[SRC mc … @ 7394ce0]`** (release `RELEASE.2025-08-13T08-35-41Z`). The listing
engine is the SDK: **`[SRC minio-go … @ 68fb5ee]`** (module `v7.0.90`). Every
minio-go anchor below is pinned to the SDK at
`68fb5ee339f2e3a798c14d12ca0e04c51f304d58`.

This page reorganizes the existing groundwork; it adds no new findings.

## Listing is one serial, single-goroutine paginated stream

**Key observation: `mc ls` does not parallelize LIST, and exposes no
knob that could.**

`mc ls` is a thin CLI front-end over the minio-go SDK's `ListObjects` iterator.
Listing is **strictly serial** — a single goroutine issuing one `ListObjectsV2`
request at a time, advancing by `continuation-token`, streaming each page's
entries down a Go channel to the printer. There is **no keyspace division, no
parallelism, and no page-size or concurrency tuning** on the listing path.

**Call path (mc @ 7394ce0):**

```
mainList -> doList -> S3Client.List -> unversionedList
  -> listRecursiveInRoutine / listInRoutine -> listObjectWrapper
  -> minio-go ListObjects
```

- `S3Client.List` launches **one** goroutine and returns a `<-chan
  *ClientContent`; it dispatches to `versionedList` when `--versions`/`--rewind`
  is set, else `unversionedList` [SRC mc `cmd/client-s3.go:1897-1912` @ 7394ce0].
- `unversionedList` picks the routine by flags: recursive → `listRecursiveInRoutine`,
  non-recursive → `listInRoutine`, `--incomplete` → the multipart-uploads routines
  [SRC mc `cmd/client-s3.go:2000-2014` @ 7394ce0].
- Both object routines call `listObjectWrapper(..., maxKeys=-1, ...)` [SRC mc
  `cmd/client-s3.go:2351,2404,2420` @ 7394ce0].
- `listObjectWrapper` builds `minio.ListObjectsOptions{Prefix, Recursive,
  WithMetadata, MaxKeys}` and calls `api.ListObjects`. It forces legacy V1 **only
  for Google Cloud** (`isGoogle` → `UseV1:true`); for AWS/MinIO it uses V2.
  `--zip` adds the `x-minio-extract` header (MinIO-only) [SRC mc
  `cmd/client-s3.go:1564-1584` @ 7394ce0].

**Inside the SDK (minio-go @ 68fb5ee / v7.0.90):**

- `ListObjects` routes: `WithVersions` → `listObjectVersions`; `UseV1` → legacy
  `listObjects`; `snowball` region → legacy `listObjects`; otherwise (AWS/MinIO)
  → `listObjectsV2` [SRC minio-go `api-list.go:771-789` @ 68fb5ee].
- `listObjectsV2` runs a **single goroutine** with a `for` loop: issue one
  `listObjectsV2Query`, stream `result.Contents` then `result.CommonPrefixes` to
  the channel, save `NextContinuationToken`, repeat while `IsTruncated` [SRC
  minio-go `api-list.go:100-165` @ 68fb5ee]. Serial **by construction** — each
  request needs the previous response's token. **Observed serial at the wire**
  (not receipt-promoted) by
  `mc --debug` [OBS `../receipts/smoke/_capability/debug-trace`]: the curated excerpt
  shows sequential `list-type=2` request lines, each after the first carrying the
  prior response's `continuation-token`, and no `Authorization` header. The excerpt
  is a redacted, unhashed probe (not a wrapper receipt): it evidences the serial
  continuation-token chain and the absent signing header; the per-page split
  (1000/1000/549 on the 2,549-key hourly prefix) is **[INFERRED]** from the key
  count and observed page size, not read off the trace. The serial, no-concurrency
  finding is `supported` (source plus the `[OBS]` probe) — claim
  `listing-is-serial-single-goroutine`.

**Client-only scoping (the softened "server-internal parallelism" claim).** The
source and trace establish only that the **client** issues no concurrent LIST
requests and does no keyspace fan-out. They say **nothing** about how AWS or a
MinIO server serves each request internally — that is neither confirmed nor
refuted here. The inherited tool page's "whatever parallelism it benefits from is
server-internal" claim is therefore not adopted as fact; only the client-side
no-fan-out shape is source-supported, and a wire-level concurrency check stays
`unverified` benchmark/replay-phase work (per `AGENTS.md`, an `[OBS]` probe is not
a run receipt) — claim `server-internal-parallelism-unverified`.

## The listing engine is minio-go, not mc

Every meaningful LIST decision — V1/V2 selection, delimiter, pagination, retry,
encoding, and the truncated-without-token guard — lives in the pinned
`minio-go v7.0.90` SDK; mc contributes only the CLI, the alias/credential model,
and the output formatting [SRC mc `cmd/client-s3.go:1564-1584` @ 7394ce0; SRC
minio-go `api-list.go:100-165` @ 68fb5ee]. Auditing mc's listing therefore means
auditing minio-go — claim `listing-logic-in-minio-go`.

## Keyspace division, page size, and concurrency — MaxKeys hard-wired, no knobs

- **Keyspace division: none.** Recursion is server-side via the *absence* of a
  delimiter, not a client-side prefix split. Delimiter defaults to `/`; when
  `Recursive` it is set to `""` [SRC minio-go `api-list.go:64-69` @ 68fb5ee].
- **Page size: server default (≤1000), not overridable.** mc **hard-wires**
  `MaxKeys=-1`; the SDK sets `max-keys` on the query **only if `maxkeys > 0`** [SRC
  minio-go `api-list.go:224-227` @ 68fb5ee], so mc **never sends `max-keys`** and S3
  applies its own default page size (≤1000; 1000 is also the S3 ceiling — the
  observed page size at smoke). What is hard-wired is `MaxKeys=-1`, not a literal
  1000, and the server may return fewer. **No mc flag or env changes this** [SRC mc
  `cmd/client-s3.go:2351` @ 7394ce0].
- **Concurrency: 1, not tunable.** Single-goroutine paginator; no fan-out flag
  exists in `mc ls` or `mc find` [SRC minio-go `api-list.go:100-165` @ 68fb5ee;
  live `--help`].

The absent page-size and concurrency knobs are `supported` — claims
`maxkeys-hardwired-no-page-knob`, `concurrency-is-one-not-tunable`, and
`no-client-keyspace-sharding`.
- **Query parameters** (every `ListObjectsV2` request — the default object-listing
  path; `--versions`/V1 use different query builders): `list-type=2`,
  `encoding-type=url`, `fetch-owner=true`, `prefix=<p>`, `delimiter=<d>`, and
  `continuation-token` after page 1 [SRC minio-go `api-list.go:191-222` @ 68fb5ee],
  confirmed [OBS].
  `encoding-type=url` means wire keys are URL-encoded and the SDK decodes them;
  `fetch-owner=true` requests owner metadata on every entry.
- **Ordering.** Relies on S3 returning keys in lexicographic (UTF-8 byte) order;
  mc does not re-sort object output (it sorts only bucket names, for site-wide
  listings) [SRC mc `cmd/client-s3.go:2379-2383` @ 7394ce0]. Smoke output matched
  the byte-ordered manifest with 0 reordering [RUN `recursive-json`].

## The `--zip`/`--x-minio-extract` header and other endpoint quirks

- Forces legacy `ListObjectsV1` for Google Cloud Storage [SRC mc
  `cmd/client-s3.go:1570-1574` @ 7394ce0]; the SDK also falls back to V1 for
  "snowball" regions [SRC minio-go `api-list.go:781-785` @ 68fb5ee]. Neither
  applies to AWS S3.
- `--zip` lists entries inside a zip object via the `x-minio-extract` header —
  **MinIO servers only**, N/A on AWS S3 [SRC mc `cmd/client-s3.go:1576-1582` @
  7394ce0].

## Credential and alias model

mc has **no `--no-sign-request` flag.** It resolves credentials from a named
*alias*. **Anonymous access = an alias with empty access/secret keys**, which
minio-go resolves to `SignatureAnonymous` and then skips signing entirely
(claims `no-aws-env-signing`, `anonymous-is-empty-cred-alias`):

- mc's credential chain is `MC_STS_ENDPOINT_*` (optional STS) then the alias's
  static keys only [SRC mc `cmd/client.go:269-311` @ 7394ce0]. **mc does not read
  `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` for signing** — unlike aws-cli and
  rclone. This is a genuine capability distinction, not a cosmetic one: it changes
  what "anonymous" means for mc (it means *no alias credentials and no mounted
  config*).
- An alias with an empty access **or** secret key resolves to
  `Value{SignerType: SignatureAnonymous}` — anonymous is explicitly "not an error"
  [SRC minio-go `pkg/credentials/static.go:55-58` @ 68fb5ee].
- For an anonymous signer the SDK returns the request unsigned, adding no
  `Authorization` header [SRC minio-go `api.go:904-912` @ 68fb5ee], confirmed by the
  absence of that header in the `--debug` trace [OBS `_capability/debug-trace`].
- Two ways to get an empty-cred alias: the ad-hoc `MC_HOST_<alias>` env value
  carrying no `user:pass@` (used by this study's harness — see `running.md`), or
  the persistent `mc alias set s3 https://s3.amazonaws.com "" ""`.
- Region is auto-resolved via an anonymous `GET ?location` before the first LIST
  [OBS `_capability/debug-trace`], after which minio-go targets the
  region-specific dualstack virtual-host endpoint.

## Retry model

SDK default `MaxRetry = 10` [SRC minio-go `retry.go:31`], base `DefaultRetryUnit =
200ms` [SRC minio-go `retry.go:41`], exponential-with-jitter implemented in
`newRetryTimer` [SRC minio-go `retry.go:49-92`] and invoked at [SRC minio-go
`api.go:663` @ 68fb5ee]. `newRetryTimer` loops `for i := range maxRetry`, so each
HTTP request in `executeMethod` is **attempted up to 10 times total** (the
retry-timer yields 10 iterations at `MaxRetry=10`, i.e. the initial attempt plus up
to 9 retries). A mid-listing page error that survives all attempts is sent down the
channel and listing aborts [SRC minio-go `api-list.go:116-121` @ 68fb5ee] (claim
`sdk-retry-policy`; behavior under 503 throttling stays `unverified`, claim
`retry-throttle-behavior-untested`). mc sets
no per-request HTTP client timeout; the transport uses `IdleConnTimeout=90s`,
`TLSHandshakeTimeout=10s`, `ExpectContinueTimeout=10s` [SRC mc
`cmd/client.go:342-357` @ 7394ce0], plus hidden
`--conn-read-deadline`/`--conn-write-deadline` global flags defaulting to 10
minutes [SRC mc `cmd/flags.go:87-92` @ 7394ce0].

**Truncated-without-token guard.** If S3 returns `IsTruncated` but an empty
continuation token, the SDK bails rather than looping forever. The **reachable**
guard is inside `listObjectsV2Query`: it detects `IsTruncated &&
NextContinuationToken == ""` on the decoded response and returns a `NotImplemented`
"Truncated response should have continuation token set" error [SRC minio-go
`api-list.go:253-257` @ 68fb5ee], which the outer loop surfaces down the channel
via the page-error path [SRC minio-go `api-list.go:116-121`]. The outer loop also
carries a defensive backstop that emits a "…S3 server is incompatible with S3 API"
error if the local continuation token is still empty on a truncated page [SRC
minio-go `api-list.go:157-163`] — but the inner query check pre-empts it on the V2
path, so it is a secondary guard. The no-infinite-loop guarantee holds either way.
This is a minio-go behaviour, shared by *every* minio-go-based tool —
not an mc-specific or s3ls-rs-exclusive guard (claim
`truncated-without-token-guard`; see
[`../research/reconciliation.md`](../research/reconciliation.md) § "Items routed").

## Memory model

**Streaming, bounded.** Objects flow object-by-object through channels from the
SDK goroutine to `doList` — mc's `contentCh` is unbuffered/cap-0 [SRC mc
`cmd/client-s3.go:1901` @ 7394ce0]; minio-go's `objectStatCh` is cap-1 [SRC
minio-go `api-list.go:63` @ 68fb5ee] — which prints each entry as it arrives.
Nothing accumulates the full key set in memory for a plain listing [SRC mc
`cmd/client-s3.go:1901-1911` @ 7394ce0; SRC minio-go `api-list.go:63,124-133` @
68fb5ee]. Consistent with smoke, where RSS did not grow with key count:
`peak_rss` **28.1 MB** / `cgroup_peak` **9.0 MB** on the 5-entry shallow run vs
**35.4 MB** / **16.1 MB** on the 148,917-key full run [RUN
`shallow/receipt.md` vs `recursive-json/receipt.md`] — a small, roughly constant
footprint at smoke scale, **not** a scale sweep and not evidence of O(1) at
millions of keys (claims `memory-streaming-bounded`, `full-bucket-smoke-peak-rss`;
scaling stays `unverified`, claim `memory-scaling-unconfirmed`). Exception:
`mc ls --versions` sorts *per-object* version groups
in memory (`sortObjectVersions`) but only within one key's versions, not globally
[SRC mc `cmd/ls.go:167-178` @ 7394ce0] [INFERRED bounded].

## Resume / checkpoint

**None.** No resume token is exposed to the user; an interrupted `mc ls` restarts
from the beginning. The `continuation-token` lives entirely inside the SDK loop
and is never surfaced [SRC minio-go `api-list.go:100-165` @ 68fb5ee] [INFERRED]
(claim `no-user-facing-resume`; an actual interrupt/resume run stays `unverified`,
claim `interrupt-resume-behavior-untested`).

## Footguns (corrected forms)

- **Text output size is humanized and lossy** (`humanize.IBytes` → `1006KiB`); the
  exact byte size is available **only** via `--json` [SRC mc `cmd/ls.go:62` @
  7394ce0] [RUN].
- **Text output prints no ETag at all**; `--json` does [SRC mc `cmd/ls.go:60-86`
  vs `88-95` @ 7394ce0].
- **Pager applies to help text, not listing output.** On a TTY mc sends only
  `app.HelpWriter` through the internal pager [SRC mc `cmd/main.go:525-526` @
  7394ce0]; listing messages go straight through `console.Println`/`printMsg` [SRC
  mc `cmd/print.go:35` @ 7394ce0], so `mc ls` output is not paged.
  `--disable-pager`/`MC_DISABLE_PAGER` exists [SRC mc `cmd/flags.go:46-51`] but
  does not affect listing.
- **Keys are printed relative to the listed target**, not as absolute keys — a
  scoped `mc ls s3/b/prefix/` prints keys without the `prefix/` [SRC mc
  `cmd/ls.go:114-128` @ 7394ce0] [RUN]. `normalize.sh` re-prepends the scope
  prefix.
- **Trailing slash only saves a probe (not a stat-vs-list switch):** `mc ls s3/b`
  stats the target, recognises it is a directory, appends `/`, and lists it
  anyway; `mc ls s3/b/` skips the extra `Stat` probe [SRC mc
  `cmd/ls-main.go:220-228` @ 7394ce0]. Both list the bucket.

## Modes and tunables

A *mode* changes the request pattern or output contract; a *tunable* only changes
magnitude. mc's listing tunables are almost all **absent** — the notable finding.

| Flag / mode | Default | Kind | Effect | Evidence |
| --- | --- | --- | --- | --- |
| (plain) `mc ls s3/b/` | delimiter `/` | **mode** | One directory level: CommonPrefixes as folders + keys at that level | [SRC minio-go `api-list.go:64-65` @ 68fb5ee], [RUN `shallow`] |
| `--recursive, -r` | off | **mode** | Drops the delimiter → full recursive key listing | [SRC mc `cmd/client-s3.go:2385` @ 7394ce0], [RUN `recursive`] |
| `--json` (global) | off | **mode** (output contract) | One compact JSON object per line (JSONL): exact `size`,`etag`,`lastModified`,`storageClass`,`key` | [SRC mc `cmd/flags.go:57-61; cmd/ls.go:88-95` @ 7394ce0], [RUN `recursive-json`] |
| `--versions` | off | **mode** | `GET ?versions` (list-object-versions API) instead of list-type=2 | [SRC mc `cmd/client-s3.go:1566` @ 7394ce0; minio-go routing `api-list.go:771-773`, versions query `api-list.go:543-588` @ 68fb5ee], [RUN `versions-json-hourly`] |
| `--rewind <t>` | off | **mode** | Point-in-time listing via the versions API at a timestamp | [SRC mc `cmd/ls-main.go:127-158`] parses the timestamp; the versions-API dispatch is [SRC mc `cmd/client-s3.go:1904-1905,1566`] (not smoked: bucket unversioned) |
| `--incomplete, -I` | off | **mode** | Lists in-progress multipart uploads (`ListMultipartUploads`), not objects | [SRC mc `cmd/client-s3.go:2016-2102`] (not smoked: object-listing study; returns none here) |
| `--zip` | off | mode | Lists entries inside a zip object — **MinIO servers only**, N/A on AWS S3 | [SRC mc `cmd/client-s3.go:1576-1582`] |
| `mc find` (subcommand) | — | **mode** | Alternate traversal; drives the **same serial `List()` path** as `ls --recursive`; distinct output contract (alias-prefixed absolute keys, **no ETag even in `--json`**). **Completeness caveat: unconditionally SKIPS `GLACIER` objects** [SRC mc `cmd/find.go:304`], so it is not a faithful full lister on buckets with archived objects — the all-`STANDARD` smoke bucket cannot exercise this hole (claims `find-shares-serial-list-path`, `find-emits-no-etag`, `find-skips-glacier`, `find-lists-standard-objects`) | [SRC mc `cmd/find.go:275-284,304`], [RUN `find-json-hourly`] |
| `--summarize` | off | tunable | Appends total object count + size; same request pattern | [SRC mc `cmd/ls-main.go:52-54`] |
| `--storage-class, --sc <SC>` | "" | tunable | **Client-side** filter of the listed set to a storage class (skips non-matching entries after listing) | [SRC mc `cmd/ls.go:244`] |
| page size | 1000 (server) | **not tunable** | mc passes `MaxKeys=-1`; no flag/env to change it | [SRC mc `cmd/client-s3.go:2351`; minio-go `api-list.go:224-227` @ 68fb5ee] |
| listing concurrency | 1 (serial) | **not tunable** | Single-goroutine paginator; no fan-out flag exists | [SRC minio-go `api-list.go:100-165` @ 68fb5ee] |

**Nothing here for the benchmark to sweep on the listing path** — page size and
concurrency, the usual knobs, do not exist as mc options. mc's benchmark story is
"serial baseline, unconfigurable." The only per-mode variation worth running is the
API/output-contract axis (recursive vs delimiter vs versions; text vs JSON), all
smoked (see `running.md`).

## Output contracts per mode

- **Text (default):** `[<mtime> UTC]<%7s size>[ <SC>] <key>` per line, colorized
  only on a TTY (plain under the harness). Size humanized; **no ETag**; folders
  (common prefixes) show size `0B` and a **synthetic `time.Now()` mtime** [SRC mc
  `cmd/ls.go:60-86; cmd/client-s3.go:2284-2288` @ 7394ce0]. With `TZ=UTC` the
  stamp is genuinely UTC.
- **JSON (`--json`):** **single-line JSONL** — one compact JSON object per line
  with `status,type,lastModified(RFC3339 Z),size(exact int64),key,etag,url,
  storageClass` [RUN `shallow-json`]. (Source uses `json.MarshalIndent` [SRC mc
  `cmd/ls.go:89-95` @ 7394ce0], but the observed stream is compacted by the console
  layer to one line per object — [RUN] over [SRC] here.) Folders carry
  `type:"folder"`, empty `etag`, and a nanosecond synthetic `lastModified`.

[`../adapter/normalize.sh`](../adapter/normalize.sh) (this tool's adapter) emits
`key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class`, `-` where a mode does not
expose a field, and re-prepends the run's scope prefix. `*-json` modes expose all
five fields; text modes expose `-` for size (humanized/lossy) and etag (absent).
Text-mode key parsing is inherently best-effort — a key literally beginning
`"<SC> …"` is indistinguishable from the SC column — so `*-json` modes are
authoritative (claim `text-key-parse-best-effort`).

## Source anchors

**mc @ 7394ce0** (`RELEASE.2025-08-13T08-35-41Z`) — CLI/formatting layer:

- `cmd/client-s3.go:1564-1584,1897-1912,2000-2014,2351,2404,2420` — the call path,
  the single listing goroutine, `MaxKeys=-1`, Google-V1 forcing, `--zip` header.
- `cmd/client.go:269-311,342-357` — credential chain (no AWS_* signing), transport
  timeouts.
- `cmd/find.go:275-284,304` — `mc find`'s shared serial `List()` path and the
  unconditional `GLACIER` skip.
- `cmd/ls.go:60-95,114-128,167-178,244` — text/JSON formatters, relative-key
  printing, per-version in-memory sort, client-side `--storage-class` filter.
- `cmd/ls-main.go:52-54,127-158,220-228` — `--summarize`, `--rewind` timestamp
  parse, trailing-slash probe.
- `cmd/main.go:525-526`, `cmd/print.go:35`, `cmd/flags.go:46-51,57-61,87-92` —
  pager scope, listing print path, global flags.

**minio-go @ 68fb5ee** (`v7.0.90`) — listing engine (all anchors pinned to the
SDK at that revision):

- `api-list.go:63,64-69` — cap-1 `objectStatCh`, delimiter default `/` → `""`
  when recursive.
- `api-list.go:100-165` — the single-goroutine `listObjectsV2` continuation loop
  (serial by construction); `:116-121` the per-page error path.
- `api-list.go:253-257` — the reachable truncated-without-token guard
  (`NotImplemented` "Truncated response should have continuation token set"); the
  outer loop's `:157-163` is a defensive backstop pre-empted on the V2 path.
- `api-list.go:191-222` (V2 query parameters); `:224-227` — `max-keys` set only if
  `maxkeys > 0` (so never, for mc); `:543-588` — the `?versions` query builder.
- `api-list.go:771-789` — `ListObjects` API routing (versions / V1 / snowball /
  V2).
- `retry.go:31` (`MaxRetry=10`), `retry.go:41` (`DefaultRetryUnit=200ms`),
  `retry.go:49-92` (`newRetryTimer` exponential-with-jitter, `for i := range
  maxRetry` → up to 10 attempts total), invoked at `api.go:663`.
- `pkg/credentials/static.go:55-58`, `api.go:904-912` — empty-cred →
  `SignatureAnonymous`; anonymous signer returns the request unsigned.
