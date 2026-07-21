# s3kor — independent groundwork report

> Independent, primary-source report for the s3-listing-study. Derived without
> reading the inherited dossier (Stages A–C, workspace phase). Every behavioral
> claim carries an evidence label; unlabeled behavioral claims are defects.
>
> Evidence labels: `[DOC url]` tool docs · `[SRC file:line @ sha]` pinned
> checkout · `[RUN receipt]` my own smoke run · `[3P url]` third-party ·
> `[INFERRED]` reasoning over the above · `[OBS how]` real run the wrapper
> could not record.

## STATUS HEADLINE — capability finding

**s3kor has no anonymous / unsigned listing capability, and the study campaign
is `CREDS=none`.** Every listing mode is therefore **blocked, not skipped**:
demonstrated under the credential-starved wrapper, where s3kor v0.0.37 does not
merely fail to sign — it **panics during AWS session construction and exits
before issuing a single S3 request**
[RUN receipts/smoke/_capability/list/receipt.md].
This is a finding, not a run failure; per the brief this phase finalizes early.

---

## 1. Metadata

| | |
| --- | --- |
| Tool | s3kor |
| Repo (canonical) | https://github.com/sethkor/s3kor |
| Pinned tag | `v0.0.37` (latest release) |
| Pinned commit SHA | `844fe3d7931fcca415c8b8a4e22f048886e6b82b` |
| Language | Go (module `github.com/sethkor/s3kor`, `go 1.14`) [SRC go.mod:1-5 @ 844fe3d] |
| Key dependency | `github.com/aws/aws-sdk-go v1.30.16` (AWS SDK for Go **v1**) [SRC go.mod:9 @ 844fe3d] |
| CLI framework | `github.com/alecthomas/kingpin` v2.2.6 [SRC go.mod:6 @ 844fe3d] |
| License | GPL-3.0 [SRC LICENSE:1-2 @ 844fe3d] |
| Upstream health | **Dormant.** Last commit 2022-06-14 (default branch `master`, "custom endpoint documentation (#25)"); last release `v0.0.37` tagged 2021-10-02. 4 open issues, two from 2023–2024 with no maintainer response. [3P https://github.com/sethkor/s3kor/commits/master · https://github.com/sethkor/s3kor/releases · https://github.com/sethkor/s3kor/issues — accessed 2026-07-17] |
| Release lags default branch | The `v0.0.37` tag (2021-10-02) predates the last commit (2022-06-14, a docs-only change). I pin the **release** per the brief; the one post-release commit is documentation. [3P commits — 2026-07-17] |
| Image | Built from source (no upstream image exists — see § 7). Ref `s3kor@sha256:b021869dfa78b7af85506a5d566ec6c7e7ed49d940b20d9e110a04fa5006f37c` |
| Reported version string | `dev-local-version none unknown` — `go install` omits goreleaser's version ldflags; the identity is the pinned tag/commit above [OBS `docker run <image> --version`, captured `receipts/smoke/_build/first-exec.txt`; direct run, not wrapper-recorded] |
| Date | 2026-07-17 |

## 2. How it works

s3kor is a Go re-implementation of a subset of the `aws s3` CLI (cp / rm / ls /
sync), marketed as a "drop-in replacement… using multiparts and multiple
threads for fast parallel actions" [DOC README.md:1-2]. cp/sync expose
concurrency flags (defaults `--concurrent 30`/`20`) [SRC s3kor.go:48,76 @
844fe3d] and the README describes concurrent multipart transfers [DOC
README.md:126-133]; I did not read the transfer worker code (out of listing
scope), so I make no source claim about how those flags spawn workers. The
**listing** path is materially different and is what this report establishes.

**Listing architecture — a single serial paginator with concurrent output
post-processing, not parallel listing.** The `ls` command builds a
`BucketLister` and calls the AWS SDK's `ListObjectsV2Pages` auto-paginator with
just a `Bucket` and (if the URI has a path) a `Prefix`
[SRC list.go:177-194 @ 844fe3d]. `ListObjectsV2Pages` is a **sequential**
continuation-token loop: it fetches page N, invokes the callback, then fetches
page N+1. The callback does not itself issue requests — for each returned page
it spawns a goroutine (`bl.wg.Add(1); go processListObjectsOutputFunc(result.Contents)`)
that pushes the page's contents onto a buffered channel
[SRC list.go:187-194 @ 844fe3d]. So the concurrency is in **draining/formatting
pages**, not in issuing LIST calls. There is:

- **No keyspace division.** No prefix/delimiter recursion, no cut-points, no
  bisection, no worker pool over sub-prefixes. A single `Prefix` is passed
  through verbatim and the SDK walks the whole page chain serially
  [SRC list.go:177-194 @ 844fe3d].
- **No delimiter / shallow mode.** `Delimiter` is never set; `MaxKeys` is never
  set. `grep -niE 'MaxKeys|Delimiter'` over the source returns nothing
  [SRC — grep of *.go @ 844fe3d]. Every `ls` is a full recursive walk under the
  prefix; page size is the SDK/S3 default (≤1000). [INFERRED from the absence of
  any `MaxKeys` set on `ListObjectsV2Input`.]
- **Pagination:** s3kor calls the SDK's `*Pages` auto-paginator and sets no
  `ContinuationToken` itself [SRC list.go:187 @ 844fe3d]; that `*Pages` helper
  drives the continuation-token loop **serially** — an aws-sdk-go v1 property,
  not a line in s3kor. [INFERRED — aws-sdk-go v1 `ListObjectsV2Pages` semantics]
- **Retries:** s3kor sets `MaxRetries: 30` on the SDK config
  [SRC s3kor.go:153-157 @ 844fe3d] and adds no custom retryer; the actual
  backoff/jitter is then the aws-sdk-go v1 default retryer's behavior. [INFERRED
  — aws-sdk-go v1 default retryer, not anchored in s3kor source]
- **Timeouts:** s3kor configures none beyond SDK/HTTP defaults — `getAwsConfig`
  sets only region/endpoint/retries [SRC s3kor.go:153-177 @ 844fe3d].
- **Ordering / concurrency correctness — retracted assurance.** S3 returns keys
  in UTF-8 byte order per page (an S3 property, [INFERRED]) and the serial
  paginator delivers pages in order, but each page is handed to a *separate
  goroutine* writing a shared channel [SRC list.go:187-194 @ 844fe3d], so output
  order across page boundaries is a race [INFERRED]. **I previously wrote
  "set-correctness is unaffected" — that is retracted.** The `List` method
  starts the printer goroutine *before* reassigning the very channel the printer
  ranges over, and manages the `WaitGroup` with `Add` calls sequenced after the
  work that `Done`s them [SRC list.go:216-234, 154-170 @ 844fe3d]. That is a
  data race on the channel field with two source-visible failure shapes — the
  printer binding the abandoned (pre-reassignment) channel and hanging, or a
  `Done`-before-`Add` panic. [INFERRED from the unsynchronized field write/read
  and wg ordering.] I **could not observe** which occurs at runtime: every
  listing run was credential-blocked (§ 8), so completeness/ordering behavior is
  unverified, not assured.

**Memory model — streaming but NOT back-pressured (correction).** Pages feed a
single `printAllObjects` consumer over a channel buffered to `threads`
(hardcoded 50 for `ls`) [SRC list.go:154-170, 216-234, s3kor.go:216 @ 844fe3d].
**I previously wrote "bounded by backpressure" — that is wrong.** The paginator
callback does not block on the channel: it spawns a goroutine and returns `true`
immediately, so `ListObjectsV2Pages` fetches the next page without waiting on the
consumer [SRC list.go:187-194 @ 844fe3d]. If the single printer lags, page
goroutines **accumulate** — each pinning its page's object slice — and the
50-slot buffer caps blocked *sends*, not the *count of blocked sender
goroutines*. So peak memory can grow with the number of in-flight pages rather
than staying bounded to ~50 pages. [INFERRED from the spawn-and-return callback
structure; consistent with the unbounded-goroutine caveat in § 6.] Source
structural read, **not** a measured memory claim — whether it OOMs at scale is a
benchmark question.

**Resume / checkpoint:** none. No continuation-token persistence, no restart
flag. [SRC — no such mechanism in list.go / s3kor.go @ 844fe3d]

**Error / truncation handling:** on a paginator error the callback path logs
via `zap` `logger.Fatal`, which calls `os.Exit(1)`; partial output already
printed is kept, no resume [SRC list.go:198-212 @ 844fe3d]. Truncated (`IsTruncated`)
responses are followed via the continuation token by the SDK's `*Pages` helper,
not by s3kor itself. [INFERRED — aws-sdk-go v1 paginator semantics, not anchored
in s3kor source]

**Anonymous access — the load-bearing finding.** s3kor builds its session with
`session.Must(session.NewSessionWithOptions({SharedConfigState:
SharedConfigEnable, …}))` — the standard AWS SDK credential chain, wrapped in
`Must` (panic-on-error) [SRC s3kor.go:179-197 @ 844fe3d]. There is **no
`--no-sign-request` flag, no anonymous config, no env convention** exposed for
the **listing client**. `credentials.AnonymousCredentials` appears twice in the
codebase — in `checkBucket`'s region-detection fallback (an anonymous
`HeadBucket` to read `X-Amz-Bucket-Region`) [SRC common.go:49 @ 844fe3d] and in
the S3-to-S3 copy download path [SRC multicopy.go:513 @ 844fe3d] — **neither is
the listing client.** (Correction: an earlier draft said this was the *only*
occurrence; it is not, but neither occurrence gives `ls` an unsigned path.) The
region-detection `HeadBucket` path *is* anonymous, but it is not even reached
when `--region` is supplied, because `checkBucket` returns the **signing**
client immediately [SRC common.go:24-26 @ 844fe3d]; and it only fetches a region
header, never lists. The actual `ListObjectsV2`/`ListObjectVersions` call
therefore always uses the credential-chain (signing) client
[SRC list.go:264-273, common.go:15-84 @ 844fe3d]. So: **s3kor has no unsigned
path for listing** — a source finding, confirmed behaviorally by the blocked
smoke runs (§ 8).

## 3. Modes and tunables

s3kor's entire listing surface is the `ls` subcommand with a single listing
flag. Two modes (different S3 API + different output contract):

| Mode | Invocation | API | Output contract | Evidence |
| --- | --- | --- | --- | --- |
| `list` | `ls s3://B[/prefix]` | `ListObjectsV2Pages` | one **key** per line (key only) | [SRC list.go:172-213, 163-167 @ 844fe3d] |
| `list-versions` | `ls --all-versions s3://B[/prefix]` | `ListObjectVersionsPages` | one line per version: `<versionId> <key>` | [SRC list.go:109-151, 156-160 @ 844fe3d] |

There is **no** delimiter/shallow mode, **no** JSON/table/output-format mode,
and **no** fan-out workaround — s3kor's parallelism is transfer-only. [SRC —
`ls` flag set is `--all-versions` alone; live `--help` § 7]

Tunables affecting a listing run (all changing magnitude only, not request
pattern or output contract):

| Flag / knob | Default | Effect on listing | Sweep for benchmark? | Evidence |
| --- | --- | --- | --- | --- |
| `--region R` | unset | Region for the client; if set, skips region auto-detection | no (correctness, not perf) | [SRC common.go:24-26 @ 844fe3d] |
| `--detect-region` | false | Auto-detect bucket region (extra `GetBucketLocation`/`HeadBucket` round-trips) | maybe (adds pre-list RTTs) | [SRC s3kor.go:29, common.go:28-81 @ 844fe3d] |
| `--custom-endpoint-url U` | unset | Path-style custom endpoint (S3-compatible stores) | no | [SRC s3kor.go:26,159-176 @ 844fe3d] |
| `--profile P` | unset | Named credential profile (irrelevant under CREDS=none) | no | [SRC s3kor.go:27,181-188 @ 844fe3d] |
| `--verbose` | false | `zap` DevelopmentConfig logging **to a temp file inside the container** — not stdout/stderr | no | [SRC s3kor.go:30,110-151 @ 844fe3d] |
| SDK `MaxRetries` | 30 | Retry attempts per request; not user-exposed | no (hardcoded) | [SRC s3kor.go:156 @ 844fe3d] |
| listing `threads` | 50 | Channel buffer depth for `ls`; **hardcoded**, not a flag; sizes the page buffer, not request concurrency | no (not configurable) | [SRC s3kor.go:216, list.go:216-234 @ 844fe3d] |
| page size (`MaxKeys`) | S3 default ≤1000 | Not set by s3kor, not exposed | no (not configurable) | [SRC list.go:177-183 @ 844fe3d] |

**Nothing about listing is tunable from the CLI beyond region/endpoint/profile.**
The `-c/--concurrent` knob exists only on `cp`/`sync`, not `ls`
[SRC s3kor.go:48,76 vs 39-41 @ 844fe3d]. The benchmark phase has no listing
tunable to sweep here — the only "knob" that changes request count is
`--detect-region` (pre-list round-trips), and s3kor's list concurrency is fixed
at 1 (serial paginator).

## 4. How to run it properly

**Quickstart (per the tool's own model):**

```sh
# Recursive list of a whole bucket (requires resolvable AWS credentials):
s3kor --region us-east-1 ls s3://my-bucket
# List under a prefix:
s3kor --region us-east-1 ls s3://my-bucket/some/prefix/
# List all object versions + delete markers:
s3kor --region us-east-1 ls --all-versions s3://my-bucket
# Let s3kor discover the region itself (extra round-trips):
s3kor --detect-region ls s3://my-bucket
```

**Best-practice configuration for large listings:** the project publishes
**none** for `ls`. The README's performance guidance
(`--concurrent`, file-descriptor limits, VPC gateway endpoints) is entirely
about **transfers**, not listing [DOC README.md:100-133]. Because listing is a
fixed serial paginator, there is no concurrency, page-size, or hint knob to
tune — the only lever is supplying `--region` to avoid the pre-list
region-detection round-trips.

**No hinted / two-pass workflow exists.** s3kor has no prefix-sharding or
parallel-list mode to configure.

**Auth setup — and the unsigned gap.** s3kor uses the standard AWS SDK-for-Go
v1 credential chain (env vars → shared `~/.aws/credentials`/`config` →
container/instance role), selectable via `--profile` [SRC s3kor.go:179-197,
DOC README.md:68]. **There is no anonymous/unsigned mode** — no
`--no-sign-request` equivalent, no config, no env. A public bucket that the AWS
CLI can read with `--no-sign-request` is **unreadable by s3kor without
credentials.** [SRC s3kor.go:179-197, common.go:15-84 @ 844fe3d] [INFERRED from a 3P sweep — no `--no-sign-request`/anonymous mechanism found in README, GitHub issues, or docs; searched 2026-07-17, absence of a found result, not a cited document]

**Footguns:**
- **Panics on a session-construction error.** Session creation is wrapped in
  `session.Must` [SRC s3kor.go:183,190 @ 844fe3d], so any error *building the
  session* is a Go **panic** at startup, not a clean message. The harness's
  credential-starved environment triggers exactly such an error (it sets
  `AWS_WEB_IDENTITY_TOKEN_FILE` while emptying `AWS_ROLE_ARN`, so the SDK's
  session build fails), and s3kor panics before any request (§ 8) [RUN
  receipts/smoke/_capability/list/receipt.md]. **Scope:** this is the observed
  behavior under *this* environment; a bare no-credentials environment (empty
  chain, no web-identity vars) would instead build a session fine and fail at
  **request** time — either way s3kor cannot list unsigned, but the *panic* is
  specific to the session-build-error condition.
- **Doc/flag drift.** The README documents `--auto-region` for region
  auto-detection [DOC README.md:71]; the actual flag is **`--detect-region`**
  [SRC s3kor.go:29 @ 844fe3d; live `--help` § 7]. `--auto-region` does not
  exist.
- **`--verbose` logs are hidden.** They go to a temp file *inside the
  container/host tmpdir*, not to the terminal, so `--verbose` gives no visible
  request trace [SRC s3kor.go:110-151 @ 844fe3d].
- **Output order is not stable** across page boundaries (§ 2). Don't rely on
  `ls` output being sorted.

## 5. Output and observability

**Formats.** Plain text only. `list` prints the bare key per line; `list-versions`
prints `<versionId> <key>` per line. No size, ETag, mtime, or storage-class is
ever printed, and there is no JSON/table/format option
[SRC list.go:154-170 @ 844fe3d].

**`normalize.sh` contract (this repo, `tools/s3kor/normalize.sh`):**

| Mode | Raw line | Emitted `key/size/etag/mtime/storage_class` |
| --- | --- | --- |
| `list` | `<full key>` | `key TAB - TAB - TAB - TAB -` (only key exposed) |
| `list-versions` | `<versionId> <key>` | `key TAB - TAB - TAB - TAB -` (versionId dropped; only key exposed) |

s3kor prints **absolute** keys (not path-relative), so `normalize.sh` needs no
prefix to reconstruct keys; the `[prefix]` arg is accepted but unused. For
`list-versions`, only the first space-delimited token (the version id) is
stripped, so keys containing spaces survive intact. Since only the key column
is populated, the verifier asserts **keys only** for both modes (size/etag/
mtime/storage_class are `-` and skipped by policy). Self-tested on synthetic
fixtures including keys with spaces [OBS host self-test, captured `receipts/smoke/_adapter/self-test.txt`; adapters never run on the measurement clock].

**`list-versions` is only manifest-comparable on an *unversioned* bucket
(important caveat).** `ListObjectVersions` returns **every** version *and* every
delete marker [SRC list.go:109-151 @ 844fe3d], and `normalize.sh` deliberately
drops the version id and marker identity to emit a bare key. On an unversioned
bucket (each key has exactly one current version, no delete markers) the emitted
key set equals the current-object set the manifest records, so a keys-only
verify is valid. **The registry does not record the smoke bucket's versioning
state, and `normalize.sh` does not enforce it** — so `list-versions` verifies
validly only *if* the bucket is unversioned; that precondition is unestablished
here [INFERRED — not a registry/receipt fact] and the mode was in any case
credential-blocked, so it was never actually verified. On a **versioned**
bucket the same mode legitimately emits duplicate keys (multiple versions) and
keys that exist only as delete markers — both would read as duplicates/extras
against a current-object manifest. This is a property of the mode, not a tool
fault; version-level completeness is not checkable against the current-object
manifest at all. The registered edge fixture is unseeded (`EDGE_BUCKET=none`),
so this is flagged for the benchmark phase rather than exercised.

**Metrics / counters / logs the tool exposes: essentially none on
stdout/stderr.** No API-call counter, no progress heartbeat for `ls` (the
progress bar is a transfer feature), no request log to the terminal. The only
logging is `--verbose` → `zap` → a temp file inside the container
[SRC s3kor.go:110-151 @ 844fe3d]. Request-shape capture for this tool must
defer to the study's replay-server phase.

## 6. Failure surface

- **Session-build error → panic at startup** (not graceful). Under the harness's
  credential-starved env (web-identity token file set, role ARN emptied),
  `session.Must` converts the SDK's session-build error into a Go panic before
  any request. [RUN receipts/smoke/_capability/list/receipt.md] [SRC
  s3kor.go:190 @ 844fe3d] (A bare empty-credential env would instead fail at
  request time — see § 8 scope note.)
- **Paginator error mid-listing → `os.Exit(1)`** via `zap` `logger.Fatal`, after
  whatever pages already printed; no resume. [SRC list.go:198-212 @ 844fe3d]
- **Memory growth at scale: unknown, and the design is NOT bounded (see § 2).**
  The paginator callback spawns a goroutine per page and returns immediately, so
  the 50-slot channel does not back-pressure the fetch loop; page goroutines
  accumulate if the single printer lags [SRC list.go:187-194 @ 844fe3d]
  [INFERRED]. So peak memory can grow with in-flight pages — a benchmark
  question, not settleable at smoke scale, and explicitly **not** a "bounded"
  claim.
- **Endpoint quirks:** `--custom-endpoint-url` forces path-style addressing and
  a `customendpoint` pseudo-region if none given [SRC s3kor.go:159-176 @
  844fe3d]; not exercised here.
- **`ls` concurrency race (§ 2), not merely ordering.** Beyond non-deterministic
  output order, the printer-before-channel-reassignment data race and the
  `WaitGroup` mis-sequencing can hang or panic *before* complete output [SRC
  list.go:216-234 @ 844fe3d][INFERRED]. So I make **no** set-completeness
  assurance for `ls`; it is unverified (every listing was credential-blocked),
  not known-correct.

No third-party reports of listing behavior at scale were found; this is a
genuine coverage gap for s3kor, consistent with its low adoption. [INFERRED from a 3P sweep — no scale/listing report found across README, issues, and web search on 2026-07-17; absence of a found result, not a cited document]

## 7. Container

**Decision: build from source at the pinned commit.** Upstream ships **neither
a published image nor a Dockerfile** — distribution is a Homebrew tap and
goreleaser GitHub-release binaries [DOC README.md:4-27; SRC .goreleaser.yml,
Makefile @ 844fe3d; INFERRED from a Docker Hub + GHCR search returning no `sethkor/s3kor` image on 2026-07-17 — absence of a found result, not a cited document]. Per the brief's Stage B ladder, that puts s3kor in the
"neither image nor Dockerfile" case, so `tools/s3kor/Dockerfile` is a
best-effort recipe and the **built image digest is the run's identity**.

Build (multi-stage, `go install <module>@<pinned-commit>`, CGO disabled to
match upstream's `.goreleaser.yml`):

```sh
docker build -t s3kor:v0.0.37-study -f tools/s3kor/Dockerfile .
# base build image: golang@sha256:77f25981bd57e60a510165f3be89c901aec90453fd0f1c5a45691f6cb1528807 (golang:1.18-alpine)
# base runtime image: alpine@sha256:6baf43584bcb78f2e5847d1de515f23499913ac9f12bdf834811a3145eb11ca1 (alpine:3.19)
# tool: go install github.com/sethkor/s3kor@844fe3d7931fcca415c8b8a4e22f048886e6b82b
```

Built image digest: **`sha256:b021869dfa78b7af85506a5d566ec6c7e7ed49d940b20d9e110a04fa5006f37c`**.
The binary self-reports `dev-local-version none unknown` because `go install`
does not inject goreleaser's `-ldflags` version/commit/date; identity is the
pinned commit, and receipts carry `--tool-version v0.0.37` explicitly. [OBS `--version`, `receipts/smoke/_build/first-exec.txt`]

**Architecture matrix (per distribution channel):**

| Channel | amd64 | arm64 | Notes |
| --- | --- | --- | --- |
| Upstream published image | — | — | none exists |
| Prebuilt release binaries (goreleaser) | native | native | also 386, arm (v6/v7); darwin amd64+arm64. Windows arch is ambiguous: README lists amd64 only [DOC README.md:13-21] while `.goreleaser.yml` includes `arm` globally and ignores only windows arm64/386 [SRC .goreleaser.yml @ 844fe3d] — not settled here (not load-bearing; the benchmark denominator is linux amd64/arm64, both native) |
| Source build (Go) | native | native | pure-Go, CGO off — cross-compiles to any Go target |

**What smoke ran on:** natively on the runner's **arm64** (aarch64), image
arch arm64, **not emulated** [RUN run.meta: `image_arch=arm64 host_arch=arm64`].
For the benchmark's common-denominator arch, **amd64** is the expected choice
and s3kor supports it natively on every channel — flagged in Open questions.

**Live `--help` vs docs (Stage B first execution):** the container's `--help`
confirms the `ls` flag set is `--all-versions` only, and the global region flag
is **`--detect-region`** — the README's `--auto-region` is stale. `--version`
prints `dev-local-version none unknown` (see above). Nothing in live help
contradicts the source read beyond the already-noted README `--auto-region`
drift. [OBS live `--help`/`ls --help`, captured `receipts/smoke/_build/first-exec.txt`; direct run, not wrapper-recorded]

## 8. Smoke results

**Auth: anonymous, credential-starved (enforced by the wrapper), CREDS=none.**
Both listing modes require signed requests (§ 2, § 4), so both are
**blocked, not skipped** — recorded here with the failing invocation as the
receipt, per the brief's auth protocol. No verifier verdict is possible (the
tool produced no listing to verify).

| Mode | Invocation (argv appended to entrypoint `s3kor`) | Auth | Exit | Wall | Result | Receipt |
| --- | --- | --- | --- | --- | --- | --- |
| `list` | `ls --region us-east-1 s3://noaa-normals-pds` | anonymous | `2` | 0.058s | **BLOCKED** — panic at session build, 0 S3 requests | `receipts/smoke/_capability/list/receipt.md` |
| `list-versions` | `ls --all-versions --region us-east-1 s3://noaa-normals-pds` | anonymous | `2` | 0.059s | **BLOCKED** — identical panic | `receipts/smoke/_capability/list-versions/receipt.md` |

Observed panic (both modes, verbatim from stderr) [RUN]:

```
panic: WebIdentityErr: role ARN is not set
  github.com/aws/aws-sdk-go/aws/session.Must(...) session.go:326
  main.getAwsSession() s3kor.go:190
  main.switchCommand({...}) s3kor.go:204
  main.main() s3kor.go:269
```

**Why this exact error, precisely characterized:** the wrapper's
credential-starved environment empties the AWS key/secret/token/role variables
and points `AWS_WEB_IDENTITY_TOKEN_FILE` (and the shared-credentials/config
files) at a nonexistent in-container path [SRC harness/smoke-run.sh:300-306].
aws-sdk-go v1.30.16 with `SharedConfigEnable` sees a web-identity **token-file**
set while `AWS_ROLE_ARN` is empty and returns `WebIdentityErr: role ARN is not
set`; `session.Must` turns that into a panic [SRC s3kor.go:190 @ 844fe3d].
The **root** finding does not depend on this particular string: s3kor has no
unsigned code path, so under *any* credential-starved condition the tool cannot
list — this environment simply makes it panic at startup rather than fail at
request time. Occurs in `getAwsSession()`, *before* command dispatch, so it is
identical for both modes. The count of S3 LIST requests issued is **0**.

**Verifier:** not run — there is no output to verify (0-byte stdout). Recursive
full-bucket and the three registry scoped-prefix checks
(`normals-hourly/`, `normals-monthly/1991-2020/`,
`normals-annualseasonal/1981-2010/access/`) are all **blocked** for the same
reason and could not be executed.

**Adapter validation (non-mode evidence).** `normalize.sh` was self-tested on
synthetic fixtures for both modes, including keys containing spaces, confirming
correct key extraction and the 5-field `key/-/-/-/-` contract. `run.sh`
argv was verified NUL-delimited for both modes and parameterized on
bucket/region/prefix (no hardcoded bucket — passes the wrapper's scan gate).
[OBS host self-tests, `receipts/smoke/_adapter/self-test.txt`]

**Edge-case fidelity checks:** `EDGE_BUCKET=none` → **deferred** (unicode /
weird-key / size+ETag / multipart-ETag assertions), per registry.

**Method note.** Container first-execution (`--version`, `--help`, `ls --help`)
was run directly against the pinned image; smoke modes ran through
`harness/smoke-run.sh` (arm64 native, `--network host`, 120s timeout, auth
anonymous). Adapter self-tests ran on the host after the clock stopped
(adapters are never on the measurement clock).

## 9. Notable findings

- **"Multiple threads for fast parallel actions … list" is misleading for
  listing.** The headline README claim [DOC README.md:1-2] is true for
  transfers but **not** for `ls`: listing is a single serial `ListObjectsV2Pages`
  loop. The "threads" in `ls` only buffer/format already-fetched pages; they
  issue no requests. [SRC list.go:172-234 @ 844fe3d]
- **No anonymous access for listing** — unusual for an S3 listing tool, and a
  hard blocker for reading AWS Open Data buckets that the AWS CLI reads with
  `--no-sign-request`. The tool *contains* `credentials.AnonymousCredentials`
  (region detection [SRC common.go:49] and the S3-to-S3 copy download path [SRC
  multicopy.go:513 @ 844fe3d]) but wires neither into the listing client.
- **`session.Must` turns a session-build error into a panic.** Under the
  harness's credential-starved env a tool intended as an `aws s3` drop-in
  crashes with a Go stack trace where the AWS CLI would print `Unable to locate
  credentials`. [RUN][SRC s3kor.go:190] (Scope: the panic is specific to the
  session-build-error condition; see § 8.)
- **`ls` has a source-visible concurrency race**, not just an ordering one: one
  goroutine per page racing a shared channel gives non-deterministic output
  order, and `List` reassigns the channel *after* starting the printer and
  mis-sequences the `WaitGroup`, so a printer can bind the abandoned channel and
  hang or hit a `Done`-before-`Add` panic [SRC list.go:187-194, 216-234 @
  844fe3d][INFERRED]. Unobserved at runtime — every listing was credential-blocked.
- **`--verbose` is a black hole for observability** — logs land in a temp file
  inside the container, invisible to the operator. [SRC s3kor.go:110-151]
- **Doc rot:** README's `--auto-region` flag does not exist (it's
  `--detect-region`); the project is dormant (~4 years) so this is unlikely to
  be fixed. [SRC s3kor.go:29 vs DOC README.md:71]

## 10. Open questions for the benchmark phase

- **s3kor cannot participate in an anonymous-only benchmark.** If the benchmark
  keeps `CREDS=none`, s3kor is untestable for listing; it needs a scoped,
  list-only credential to run at all. **Decision for the owner.**
- **Common-denominator arch:** amd64 is supported natively on every channel;
  smoke ran arm64 native. Confirm amd64 for the comparative phase (no emulation
  needed either way). [flagged per brief]
- **Memory at scale (unsettled):** is the streaming buffered-channel design
  actually bounded on million-object listings, or does the one-goroutine-per-page
  spawn (no pool) grow goroutine/heap unboundedly if the single printer lags?
  Proposed: measure RSS + goroutine count across a size sweep. [INFERRED
  hypothesis, § 2/§ 6]
- **Serial-list latency:** with list concurrency fixed at 1 and `MaxRetries 30`,
  how does wall-clock scale with page count vs tools that shard prefixes?
  Proposed sweep: bucket/prefix sizes spanning ~3 to ~149+ pages. No internal
  tunable to sweep — the only lever is `--detect-region` on/off (pre-list RTTs).
- **Output-order non-determinism** could matter if any downstream consumer
  assumes sorted output; worth a note in comparative fairness controls.

## 11. Sources

**Primary — pinned checkout** (`github.com/sethkor/s3kor` @
`844fe3d7931fcca415c8b8a4e22f048886e6b82b`, tag `v0.0.37`):
`s3kor.go`, `list.go`, `common.go`, `constants.go`, `go.mod`, `.goreleaser.yml`,
`Makefile`, `README.md`, `LICENSE`.

**Docs:**
- README (in-repo) — https://github.com/sethkor/s3kor — accessed 2026-07-17.

**Third-party** (context only, accessed 2026-07-17):
- Commit history — https://github.com/sethkor/s3kor/commits/master
- Releases — https://github.com/sethkor/s3kor/releases
- Issues — https://github.com/sethkor/s3kor/issues
- Docker Hub / GHCR searches (no s3kor image found)
- Raw source on `master` (README/list.go/s3kor.go) — corroborated the
  pinned-tag read.

**Pinned SHA:** `844fe3d7931fcca415c8b8a4e22f048886e6b82b`

**Receipt index:**
- `receipts/smoke/_capability/list/receipt.md` (+ `run.meta`, `stdout.txt`,
  `stderr.txt`)
- `receipts/smoke/_capability/list-versions/receipt.md` (+ `run.meta`,
  `stdout.txt`, `stderr.txt`)

**Container:** built image
`s3kor@sha256:b021869dfa78b7af85506a5d566ec6c7e7ed49d940b20d9e110a04fa5006f37c`;
build bases golang:1.18-alpine
`@sha256:77f25981bd57e60a510165f3be89c901aec90453fd0f1c5a45691f6cb1528807`,
alpine:3.19
`@sha256:6baf43584bcb78f2e5847d1de515f23499913ac9f12bdf834811a3145eb11ca1`.
