# s3kor — mechanism

Source-anchored architecture of `s3kor ls`, consolidated from the groundwork
report ([`../research/report.md`](../research/report.md)); its derivation and
independent cross-check are preserved in
[`../research/codex-review.md`](../research/codex-review.md). This page
reorganizes the existing groundwork and adds no new findings; the current
wording on the memory model, race scope, and retries is canonical.

Evidence labels: `[SRC file:line @ sha]` pinned source · `[DOC]` upstream docs ·
`[RUN receipt]` committed smoke run · `[OBS]` observed directly, not
wrapper-recorded · `[3P]` third-party · `[INFERRED]`. Pinned commit for every
`[SRC]` anchor: **`844fe3d7931fcca415c8b8a4e22f048886e6b82b`** (tag `v0.0.37`).
Dependency: `aws-sdk-go v1.30.16` (AWS SDK for Go **v1**) [SRC `go.mod:9`].
Evidence strength uses the canonical status vocabulary — `confirmed`,
`supported`, `unverified`, `unverifiable`. References of the form claim
`some-id` resolve in the canonical ledger,
[`../data/claims.json`](../data/claims.json).

s3kor is a Go re-implementation of a subset of the `aws s3` CLI
(cp / rm / ls / sync), marketed as a "drop-in replacement … using multiparts
and multiple threads for fast parallel actions" [DOC `README.md:1-2`]. cp/sync
expose concurrency flags (defaults `--concurrent 30`/`20`) [SRC
`s3kor.go:48,76`] and the README describes concurrent multipart transfers [DOC
`README.md:126-133`]; the **transfer worker code was not read** (out of listing
scope), so no source claim is made about how those flags spawn workers. The
**listing** path is materially different and is what this page establishes.

## Listing is one serial paginator

**Key observation: `ls` does not parallelize LIST** (claim
`listing-is-serial-paginator`). The `ls` command builds a `BucketLister` and
calls the AWS SDK's `ListObjectsV2Pages` auto-paginator with just a `Bucket`
and (if the URI has a path) a `Prefix` [SRC `list.go:177-194`].

- `ListObjectsV2Pages` is a **sequential** continuation-token loop: fetch page
  N, invoke the callback, then fetch page N+1. s3kor sets no `ContinuationToken`
  itself [SRC `list.go:187`]; the `*Pages` helper drives the continuation loop
  serially — an aws-sdk-go v1 property, not a line in s3kor [INFERRED —
  aws-sdk-go v1 `ListObjectsV2Pages` semantics].
- The callback does **not** itself issue requests. For each returned page it
  spawns a goroutine (`bl.wg.Add(1); go
  processListObjectsOutputFunc(result.Contents)`) that pushes the page's
  contents onto a buffered channel [SRC `list.go:187-194`]. So the concurrency
  is in **draining/formatting pages**, not in issuing LIST calls — the "threads"
  in the README's "multiple threads … list" headline are page formatters here,
  not request issuers.
- Truncated (`IsTruncated`) responses are followed via the continuation token
  by the SDK's `*Pages` helper, not by s3kor itself [INFERRED — aws-sdk-go v1
  paginator semantics, not anchored in s3kor source].

Two listing modes (different API + different output contract):

| Mode | Invocation | API | Output contract | Evidence |
| --- | --- | --- | --- | --- |
| `list` | `ls s3://B[/prefix]` | `ListObjectsV2Pages` | one **key** per line (key only) | [SRC `list.go:172-213,163-167`] |
| `list-versions` | `ls --all-versions s3://B[/prefix]` | `ListObjectVersionsPages` | one line per version: `<versionId> <key>` | [SRC `list.go:109-151,156-160`] |

There is **no** delimiter/shallow mode, **no** JSON/table/output-format mode,
and **no** fan-out workaround — s3kor's parallelism is transfer-only [SRC — the
`ls` flag set is `--all-versions` alone; live `--help`
`../receipts/smoke/_build/first-exec.txt`]. The two-mode surface is claim
`list-versions-flag-is-all-versions`. **Serial-vs-parallel at scale stays
`unverified`** (claim `serial-listing-scale-cost-unverified`) — source settles
the mechanism, not the cost at 10^6–10^8 keys; a receipt-worthy claim needs a
run, and source reading is not a receipt (per `AGENTS.md`).

## No keyspace division

- **No prefix/delimiter recursion, no cut-points, no bisection, no worker pool
  over sub-prefixes.** A single `Prefix` is passed through verbatim and the SDK
  walks the whole page chain serially [SRC `list.go:177-194`].
- **No delimiter / shallow mode.** `Delimiter` is never set; every `ls` is a
  full recursive walk under the prefix.
- **No `MaxKeys` control.** `MaxKeys` is never set — `grep -niE
  'MaxKeys|Delimiter'` over the source returns nothing [SRC — grep of `*.go` @
  844fe3d]. With the field omitted, page size is the S3 default of ≤1000 keys —
  also S3's own ceiling — and there is no flag to change it [SRC
  `list.go:177-183`; INFERRED from the absence of any `MaxKeys` assignment].

## No listing tunables

Nothing about listing is tunable from the CLI beyond region/endpoint/profile.
The `-c/--concurrent` knob exists only on `cp`/`sync`, not `ls` [SRC
`s3kor.go:48,76` vs `39-41`]. Flags that touch a listing run change magnitude
only, not request pattern or output contract:

| Flag / knob | Default | Effect on listing | Evidence |
| --- | --- | --- | --- |
| `--region R` | unset | Region for the client; if set, skips region auto-detection | [SRC `common.go:24-26`] |
| `--detect-region` | false | Auto-detect bucket region (extra `GetBucketLocation`/`HeadBucket` round-trips) | [SRC `s3kor.go:29`, `common.go:28-81`] |
| `--custom-endpoint-url U` | unset | Path-style custom endpoint (S3-compatible stores); forces a `customendpoint` pseudo-region if none given | [SRC `s3kor.go:26,159-176`] |
| `--profile P` | unset | Named credential profile (irrelevant under `CREDS=none`) | [SRC `s3kor.go:27,181-188`] |
| `--verbose` | false | `zap` DevelopmentConfig logging **to a temp file inside the container** — not stdout/stderr | [SRC `s3kor.go:30,110-151`] |
| SDK `MaxRetries` | 30 | Retry attempts per request; not user-exposed | [SRC `s3kor.go:156`] |
| listing `threads` | 50 | Channel buffer depth for `ls`; **hardcoded**, not a flag; sizes the page buffer, not request concurrency | [SRC `s3kor.go:216`, `list.go:216-234`] |
| page size (`MaxKeys`) | S3 default ≤1000 | Not set by s3kor, not exposed | [SRC `list.go:177-183`] |

The benchmark phase has no listing tunable to sweep — the only lever that
changes request count is `--detect-region` (pre-list round-trips), and s3kor's
list concurrency is fixed at 1 (serial paginator).

## The page-vs-format goroutine race

Beyond non-deterministic output order, `ls` carries a **source-visible
concurrency race** (claim `ls-concurrency-race-source`):

- S3 returns keys in UTF-8 byte order per page (an S3 property [INFERRED]) and
  the serial paginator delivers pages in order, but each page is handed to a
  **separate goroutine** writing a shared channel [SRC `list.go:187-194`], so
  output order across page boundaries is a race [INFERRED].
- **The earlier "set-correctness is unaffected" assurance was removed.** The
  `List` method starts the printer goroutine (`go bl.printAllObjects`,
  `list.go:218`) *before* reassigning the very channel the printer ranges over
  (`bl.objects`, `list.go:225`), and manages the `WaitGroup` with `Add` calls
  sequenced *after* the work that `Done`s them [SRC `list.go:216-234,154-170`].
  That is a data race on the channel field with **two source-visible failure
  shapes**: the printer binding the abandoned (pre-reassignment) channel and
  hanging, or a `Done`-before-`Add` panic [SRC][INFERRED].
- **Unobserved at runtime** (claim `ls-race-runtime-behavior-unverified`).
  Every listing run was credential-blocked (see `running.md`), so
  completeness/ordering behavior is **unverified, not assured** — no
  set-completeness assurance is made for `ls`.

## Retry model

s3kor sets `MaxRetries: 30` on the SDK config [SRC `s3kor.go:153-157`] and
**adds no custom retryer or `ShouldRetry` override**. Because there is no
override, it does **not** special-case 503/`SlowDown` the way s5cmd's
`customRetryer` does — retry policy is entirely the aws-sdk-go v1 **default
retryer**: connection-reset/timeout (socket/timeout) retries with exponential
backoff [INFERRED — aws-sdk-go v1 default retryer, not anchored in s3kor
source]. **Timeouts:** s3kor configures none beyond SDK/HTTP defaults —
`getAwsConfig` sets only region/endpoint/retries [SRC `s3kor.go:153-177`].

**Error / truncation handling.** On a paginator error the callback path logs
via `zap` `logger.Fatal`, which calls `os.Exit(1)`; partial output already
printed is kept, and there is no resume [SRC `list.go:198-212`].
**Resume / checkpoint:** none — no continuation-token persistence, no restart
flag [SRC — no such mechanism in `list.go` / `s3kor.go`].

## Memory model

**Streaming but NOT back-pressured** (claim
`memory-streaming-not-backpressured`). Pages feed a single `printAllObjects`
consumer over a channel buffered to `threads` (hardcoded 50 for `ls`) [SRC
`list.go:154-170,216-234`, `s3kor.go:216`]. An earlier **"bounded by
backpressure"** reading does not hold (claim
`memory-streaming-not-backpressured`): the
paginator callback does not block on the channel — it spawns a goroutine and
returns `true` immediately, so `ListObjectsV2Pages` fetches the next page
without waiting on the consumer [SRC `list.go:187-194`]. If the single printer
lags, page goroutines **accumulate** — each pinning its page's object slice —
and the 50-slot buffer caps blocked *sends*, not the *count of blocked sender
goroutines*. So peak memory can grow with the number of in-flight pages rather
than staying bounded to ~50 pages [INFERRED from the spawn-and-return callback
structure]. This is a **source structural read, not a measured memory claim** —
whether it OOMs at scale is a benchmark question (claim
`memory-oom-at-scale-unverified`), and the capability smoke recorded no memory
number (`peak_rss` unavailable, process panicked at startup; see `running.md`).

## No unsigned path for listing

s3kor builds its session with `session.Must(session.NewSessionWithOptions({
SharedConfigState: SharedConfigEnable, …}))` — the standard AWS SDK credential
chain wrapped in `Must` (panic-on-error) [SRC `s3kor.go:179-197`]. There is
**no `--no-sign-request` flag, no anonymous config, no env convention** exposed
for the **listing client**.

`credentials.AnonymousCredentials` appears **twice** in the codebase, and
**neither is the listing client**:

- `checkBucket`'s region-detection fallback — an anonymous `HeadBucket` to read
  `X-Amz-Bucket-Region` [SRC `common.go:49`]. This path *is* anonymous, but it
  only fetches a region header, never lists, and it is not even reached when
  `--region` is supplied, because `checkBucket` returns the **signing** client
  immediately [SRC `common.go:24-26`].
- the S3-to-S3 copy download path [SRC `multicopy.go:513`] — a transfer path,
  not listing.

The actual `ListObjectsV2`/`ListObjectVersions` call therefore always uses the
credential-chain (signing) client [SRC `list.go:264-273`, `common.go:15-84`].
So **s3kor has no unsigned path for listing** (claim `no-unsigned-listing-path`)
— a source finding, confirmed behaviorally by the blocked smoke runs
(claim `credential-starved-listing-blocked`; see `running.md`).

**The `session.Must` panic path.** Under the harness's credential-starved env
(which sets `AWS_WEB_IDENTITY_TOKEN_FILE` while emptying `AWS_ROLE_ARN`),
aws-sdk-go v1.30.16 with `SharedConfigEnable` sees a web-identity token-file set
with an empty role ARN and returns `WebIdentityErr: role ARN is not set`;
`session.Must` turns that **session-build** error into a Go panic at
**`s3kor.go:190`**, in `getAwsSession()`, *before* command dispatch — so it is
identical for both modes and issues **0** S3 requests (claim
`session-build-panic`) [SRC `s3kor.go:190`][RUN
`../receipts/smoke/_capability/list/receipt.md`]. **Scope:** the *panic* is
specific to this session-build-error condition; a bare empty-credential env
(empty chain, no web-identity vars) would build a session fine and fail at
**request** time instead — either way `ls` cannot run unsigned. The root
capability finding does not depend on the particular error string.

## Doc-drift: `--detect-region` vs the README's `--auto-region`

The README documents `--auto-region` for region auto-detection [DOC
`README.md:71`]; the actual flag is **`--detect-region`** (claim
`region-flag-doc-drift`) [SRC `s3kor.go:29`; live `--help`
`../receipts/smoke/_build/first-exec.txt`]. `--auto-region` does not exist. The
project is ~4 years dormant [3P — 2026-07-17], so this is unlikely to be fixed.

## Source anchors

- `list.go:177-194` — the serial `ListObjectsV2Pages` call and the
  spawn-and-return per-page callback.
- `list.go:216-234,154-170` — `List`: printer started before channel
  reassignment; `WaitGroup` mis-sequencing (the race).
- `list.go:109-151,156-167` — `list` vs `list-versions` output contracts.
- `list.go:198-212` — paginator-error `logger.Fatal` → `os.Exit(1)`.
- `list.go:264-273` — the listing call uses the signing client.
- `s3kor.go:179-197,190` — session build wrapped in `session.Must` (the panic
  path).
- `s3kor.go:153-177,156,216` — `getAwsConfig` (region/endpoint/`MaxRetries: 30`),
  hardcoded `threads` 50.
- `s3kor.go:29,48,76,39-41` — `--detect-region`; cp/sync `--concurrent` vs `ls`
  (no concurrency flag).
- `s3kor.go:110-151` — `--verbose` → `zap` → temp file inside the container.
- `common.go:24-26,49,15-84` — region-detection short-circuit and the anonymous
  `HeadBucket`.
- `multicopy.go:513` — `AnonymousCredentials` in the S3-to-S3 copy download
  path (not listing).
