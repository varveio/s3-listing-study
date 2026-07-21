# pS3 — mechanism

Source-anchored architecture, consolidated from the groundwork report
(`../research/report.md`) and its critical cross-check (`../research/codex-review.md`,
16 review items addressed). Evidence labels are carried through exactly as they stand
post-review: `[SRC file:line @ sha]` pinned source, `[RUN receipt]` a committed
smoke run, `[OBS]` observed but not wrapper-recorded, `[3P]` third-party,
`[DOC]` docs, `[INFERRED]`. Pinned commit for every `[SRC]` anchor:
**`9428492`** (`9428492291ef3aa824dba0b495583279c3d33760`, default-branch HEAD;
no releases or tags). This page reorganizes the existing groundwork and adds no
new findings. **Every line derived from a
run of the amd64 binary inherits the qemu/amd64-emulation caveat**
(`running.md`) — the `_build` source-compile evidence is the exception, it ran
natively on arm64. References of the form claim `some-id` resolve in the canonical
ledger, [`../data/claims.json`](../data/claims.json); the current status vocabulary
(`confirmed`, `supported`, `unverified`, `unverifiable`) is defined there.

## Keyspace division — a brute-force character walk

pS3's premise [3P author blog]: the only way to parallelize S3 LIST is to issue
concurrent LISTs against different **prefixes**, but you usually don't know the
prefixes — so pS3 **discovers them by brute force**, then fans out. This is a
fundamentally different technique from S3P-style bisection: pS3 never computes a
key *between* two other keys.

Starting from prefix `""`, for each character `c` in a fixed **81-element**
alphabet — a package `var` slice (`space ! & ' ( ) + , - . /`, digits,
`: ; = ? @`, `A–Z _ a–z * $`) [SRC `cmd/root.go:36-39 @ 9428492`] — pS3 issues
`ListObjectsV2(Prefix = current+c, MaxKeys=1000)` and branches on the count
[SRC `cmd/listObjectsV2.go findPrefixes/discoverPrefixes:190-289 @ 9428492`;
corroborated 3P blog]:

- **> 999 keys** (a full 1000-key page): treat `current+c` as a "large" prefix,
  increment `processedCount`, and **recurse** `go discoverPrefixes(current+c)`
  in a new goroutine. The code tests `len(Contents) > 999` and **never**
  `IsTruncated` [SRC `:222-224`], so an exactly-1000-key *non-truncated* prefix
  is also treated as "large" and needlessly recursed. (Special case: if the
  first returned key equals the prefix exactly, that single key is emitted
  directly [SRC `:226-231`].)
- **1..999 keys**: a "small" prefix — **all** its keys are emitted straight from
  the discovery response [SRC `:252-254`] (they are complete: the page was not
  truncated).
- **0 keys**: skip.

`nextPrefix := currentPrefix + c` then `go discoverPrefixes(nextPrefix)` on
overflow is the exact recursion body [SRC `:213-241`]. A cutoff bounds
discovery: once `processedCount >= target` (the `--prefix-count` flag, default
500), any further `discoverPrefixes` call immediately appends its current prefix
to the "large" list and returns [SRC `:205-212`], stopping fine-grained descent.
A rebalancing loop shrinks `processedCount` by ¼ and re-runs, up to 10 times, if
too few large prefixes were found [SRC `:265-288`].

**Coverage limitation (correctness, [SRC]).** Because discovery only extends a
prefix with bytes from the 81-char alphabet, any key whose next distinguishing
byte is **outside** that set — e.g. `" # % < > [ \ ] ^ \` { | } ~`, or any
non-ASCII/UTF-8 lead byte — is **never discovered and silently dropped**. Keys
under an already-emitted "small" prefix are safe (the whole page returned); the
gap is for keyspaces broad enough to require descent through an out-of-alphabet
byte. Untestable here (`EDGE_BUCKET=none` → deferred). For `noaa-normals-pds`
all keys are ASCII within the alphabet, so coverage would be complete *if it
could authenticate*. Claims `alphabet-is-fixed-81-char-var`,
`out-of-alphabet-keys-dropped`, `out-of-alphabet-drop-runtime`.

**No delimiter, full-bucket only.** There is no `--prefix`, no delimiter, and no
max-keys flag [RUN `receipts/smoke/_capability/help`]; the tool cannot scope a
listing to a key prefix at all, which is why `run.sh` refuses a prefix argument
rather than silently listing the whole bucket under a "scoped" label.

## Concurrency: a 256 pager `var`, unbounded discovery

The accumulated "large" prefixes are each fully paginated by
`s3ListAllObjectsWithBackoff` [SRC `cmd/s3SDKfunctions.go:102-164 @ 9428492`] —
ListObjectsV2 with `ContinuationToken`, `MaxKeys=1000`, streaming each page's
objects into a channel — under a **semaphore of `maxSemaphore = 256`** (a
package `var`, never reassigned) [SRC `cmd/root.go:44`]. This semaphore bounds
**only the pager, not discovery**: the `go discoverPrefixes` recursion at [SRC
`:241`] fans out with **no semaphore**, so discovery goroutines are unbounded.
Objects flow through an unbuffered channel to a pool of **256 worker
goroutines** [SRC `readObjectsV2:155-188`, `numWorkers := maxSemaphore`] that
print them.

- **Parallelism** is genuine parallel *listing* — both discovery recursion and
  large-prefix pagination fan out via goroutines, not just parallel transfers
  [SRC; 3P].
- **No flag** exposes either the 256 pager count or the unbounded discovery, and
  page size (1000) is likewise a package `var` [SRC `root.go:42,44`;
  `listObjectsV2.go:241`; RUN `_capability/help`]. This is why the concurrency
  cannot be capped to `CONCURRENCY_CAP=8` (`../README.md` § Limitations and open questions); claims
`discovery-goroutines-unbounded` and `no-concurrency-flag-uncappable`.

## Pagination and page size

ListObjectsV2, `MaxKeys=1000` (a package `var`, never reassigned [SRC
`root.go:42`]); a continuation-token loop paginates each large prefix. 1000 is
S3's own page ceiling, so this is not a pS3 deficit relative to other real-S3
clients; the whole approach's leverage comes from paginating many prefixes
concurrently, not from larger pages.

## Retry / backoff models (as labeled)

- The pager helper `s3ListAllObjectsWithBackoff` has an exponential-backoff loop
  (`2^i` seconds, `maxRetries=10`) [SRC `s3SDKfunctions.go:146-148`].
- The **discovery-path** helper `s3ListObjectsWithBackOff` is **broken**:
  `if err != nil { return resp, nil }` [SRC `s3SDKfunctions.go:74-77`] returns
  immediately on any error with a **nil error and a nil/partial resp**, making
  the backoff below it dead code and handing callers a nil response they then
  dereference — an error-swallowing nil-deref risk (not triggered at smoke; the
  run died earlier at session creation).
- **Timeouts**: custom HTTP client — 5 s connect, 5 s TLS, 5 s response-header,
  HTTP/2 enabled, 100 idle conns/host [SRC `cmd/httpUtils.go:24-51`].

## Memory model

Streaming, not accumulate-then-dump — objects go producer → unbuffered channel →
printer, never a full in-memory slice of all keys [SRC]. The discovered
*prefix* list is the only accumulation, bounded by `--prefix-count` [SRC].
No OOM hypothesis at smoke scale; scale behavior is an open question
[SRC/INFERRED]. **Resume/checkpoint: none** [SRC — no such mechanism].

## Ordering / determinism

No ordering is maintained for correctness — keys are streamed as pages arrive
across many prefixes concurrently, so **output order is non-deterministic**
[SRC `readObjectsV2` + concurrent producers; INFERRED]. The verifier must treat
pS3 output as an unordered set. (Distinct from the *exit-code* determinism
observed at smoke — 5/5 non-trace + 3/3 `--trace` identical failures — which is
[OBS], see `running.md`.)

## Modes and tunables

The shipped **binary** exposes four object subcommands [RUN
`receipts/smoke/_capability/help`]; only `list-objects-v2` has source in the
checkout — the other three source files are **absent** (a provenance finding,
`running.md`).

| Subcommand | Kind | Request pattern / output contract | Evidence |
| --- | --- | --- | --- |
| `list-objects-v2` | **MODE** (primary) | Brute-force prefix fan-out over ListObjectsV2, recursive, **no delimiter**, full-bucket only | [SRC], [RUN help] |
| `list-object-versions` | **MODE** | ListObjectVersions API (`s3:ListBucketVersions`); different API + version-id output contract | [RUN help]; helpers exist [SRC `s3SDKfunctions.go:198-255`], command source absent |
| `head-objects` | mode-ish | "List Head information for all objects" — **[INFERRED]** likely lists then HEADs each key (from the name + an `s3headObject` helper [SRC `cmd/s3SDKfunctions.go:13-28`]; command source absent, so unconfirmed) | [RUN help] |
| `list-test` | not a real mode | Help body is the **unmodified cobra scaffold** ("A longer description that spans multiple lines…"); dev/placeholder | [RUN help] |

**Flags.** Each subcommand also declares a required local `--bucket string`
(the bucket to list) [RUN help]. In HEAD source `--prefix-count` is likewise a
**local** flag on `list-objects-v2` only [SRC `listObjectsV2.go:43-45`], not
persistent; the shipped binary's other subcommands each declare their own
`--prefix-count` in `--help` [RUN help]. The remaining flags below are
persistent root flags (plus root `--version`, which prints `pS3 version 0.1.16`
and exits [RUN `_capability/help/help.txt`]):

| Flag | Default | Effect | Evidence |
| --- | --- | --- | --- |
| `--bucket string` (per subcommand, required) | — | The bucket to list | [RUN help] |
| `--prefix-count int` | 500 | Discovery cutoff: point at which prefixes stop being subdivided and are handed to the parallel pager — **the primary benchmark knob** | [SRC `listObjectsV2.go:45`, `findPrefixes:205`] |
| `--output {json,text}` | text | **Inert in HEAD source** — `readObjectsV2` ignores `fOutput` and always `fmt.Printf`s one fixed line [SRC `:155-188`]. Binary may differ (untested — blocked) | [SRC] |
| `--region` | "" | Passed to SDK; interacts with a buggy `GetBucketLocation` fallback (below) | [SRC `:86-117`] |
| `--endpoint-url` | "" | Override S3 endpoint | [SRC `root.go:90`] |
| `--no-verify-ssl` | false | `InsecureSkipVerify` + `DisableSSL` | [SRC `listObjectsV2.go:63,71`] |
| `--profile` | "" | Named profile from shared credentials file | [SRC `:92,121`] |
| `--verbose` / `--debug`(hidden) / `--trace`(hidden) | off | Logging. **`--debug`/`--trace` SUPPRESS object output** and print only a count [SRC `readObjectsV2:164-169`] — you cannot get both objects and request-level logging | [SRC] |

**Not present**: no `--prefix` / delimiter / max-keys / concurrency flag; no
`--no-sign-request`. Pager concurrency (256) and page size (1000) are package
`var`s never exposed as flags, and discovery concurrency is unbounded.

## Output contract

`readObjectsV2` prints a single fixed format:
`Object: %v \t %d \t %s\n` = `Object: <LastModified> <TAB> <size> <TAB> <key>`,
where `<LastModified>` is a Go `time.Time` via `%v`
(`2006-01-02 15:04:05.999999999 -0700 MST`) [SRC `listObjectsV2.go:167`].
Fields exposed: **key, size, mtime**. **Not exposed: ETag, storage class.**

**Adapter fidelity — the embedded-newline gap is real; the leading-space loss
was retracted.** The `printf` inserts exactly **one** space after the final tab,
so `normalize.sh` strips that single delimiter space and **genuine leading
spaces survive** — the format is *not* lossy for leading-space keys (an earlier
draft wrongly claimed it was; **do not resurrect that claim**). The real adapter
gap is **embedded newlines**: S3 keys may legally contain `\n`
[DOC <https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-keys.html>],
which pS3 prints into a `\n`-terminated line, so the line-oriented adapter would
split/drop such a key. Untested (the fixture has no newline key).

`normalize.sh` (this repo, `tools/ps3/adapter/normalize.sh`) parses `^Object: ` lines →
`key<TAB>size<TAB>-<TAB>mtime<TAB>-` (etag/storage_class `-`; mtime canonicalized
to `YYYY-MM-DDTHH:MM:SSZ`, UTC by construction under `TZ=UTC`). Validated only
against a synthetic fixture (`receipts/smoke/_adapter/list-sample.txt`) because
**no live pS3 listing could be produced** (blocked).
`list-object-versions`/`head-objects` line formats are unverified (source
absent); the adapter assumes the `list-objects-v2` format.

## Observability

An object count under `--debug`/`--trace` (`debug: item count`), but that mode
suppresses the listing itself. Per-request visibility only via `--trace`
(SDK-level errors, prefix trace lines). No built-in API-call counter for the
LIST path. Request-shape capture defers to the study's replay-server phase.

## Failure surface

- **Auth failure is silent/false-success in the common case** [OBS
  `receipts/smoke/_capability/silent-empty-obs.md`]: with no credentials and no
  config-file redirection, pS3 exits **0 with zero objects and no error** — a
  caller cannot distinguish this from an empty bucket. Under the harness's
  stricter starvation (config/creds files → nonexistent path) it instead fails
  at session creation, exit 1 [RUN `_capability/list-anon`]. `[OBS]` is never a
  receipt: only the exit-1 half is receipt-backed.
- **GetBucketLocation region bug** [SRC `listObjectsV2.go:107-117`]: every run
  first calls `GetBucketLocation` (needs creds/permission). On error or a
  us-east-1 bucket, `getBucketLocation` returns `""`; the fallback then does
  `if location != fRegion { region = "" } else { region = "us-west-1" }` — so
  passing `--region us-east-1` sets the region to **empty**, and passing nothing
  yields **us-west-1** for a us-east-1 bucket (claim `get-bucket-location-region-bug`).
  The bug is source-level [SRC]; the
  trace merely shows the fallback firing (`GetBucketLocation` →
  `NoCredentialProviders`, then `trace: bucket region is:` empty) [OBS
  `silent-empty-obs.md`] — an [OBS] illustration, not a settling receipt.
- **Error-swallowing nil-deref risk** [SRC `s3SDKfunctions.go:74-77`] — the
  discovery helper returns `(resp, nil)` on error and callers dereference
  `resp.Contents`. Not triggered here (the run died at session creation).
- **Inconsistent error handling** [SRC]: the *parallel pager* aborts the whole
  process on error (`log.Fatalln`, exit 1 [SRC `listObjectsV2.go:316`]); the
  *discovery* helper instead **swallows** errors (`return resp, nil` [SRC
  `s3SDKfunctions.go:74-77`]). Neither path checkpoints partial progress.
- **Memory growth**: streaming producer→channel→printer bounds object memory;
  the prefix list (bounded by `--prefix-count`) is the only accumulation. No OOM
  hypothesis at smoke scale; scale behavior is an open question [SRC/INFERRED].

All scale-dependent items are **hypotheses** carried to the benchmark phase
(`../README.md` § Limitations and open questions); nothing here settles them.

## Source anchors

- `cmd/listObjectsV2.go:190-289` — `findPrefixes`/`discoverPrefixes`, the
  brute-force character-walk (`discoverPrefixes` closure at `:196`, recursion
  body `:213-241`, `>999` branch `:222-224`, small-prefix emit `:252-254`,
  cutoff `:205-212`, rebalance `:265-288`).
- `cmd/root.go:36-39` — the 81-element alphabet package `var`; `:42` page size
  `maxKeys=1000`; `:44` `maxSemaphore = 256`; `:16` `pS3Version = "0.1.16"`.
- `cmd/listObjectsV2.go:155-188` — `readObjectsV2`: 256 printer workers, fixed
  output format `:167`, `--debug`/`--trace` output suppression `:164-169`;
  `:291-327` `listObjectsInParallel`; `:316` pager `log.Fatalln`;
  `:107-117` GetBucketLocation region fallback; `:90-99,119-128` session build.
- `cmd/s3SDKfunctions.go:102-164` — `s3ListAllObjectsWithBackoff` (pager,
  backoff `:146-148`); `:74-77` broken discovery helper; `:198-255`
  version-listing helpers; `:13-28` `s3headObject`.
- `cmd/httpUtils.go:24-51` — custom HTTP client timeouts.
