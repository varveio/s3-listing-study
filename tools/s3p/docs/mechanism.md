# s3p — mechanism

Source-anchored architecture of `s3p`'s listing engine, consolidated from the
groundwork report ([`../research/report.md`](../research/report.md)) and its
critical cross-check ([`../research/codex-review.md`](../research/codex-review.md),
15 review items addressed). Evidence labels are carried through exactly as they
stand post-review: `[DOC]` docs, `[SRC file:line @ sha]` pinned source,
`[RUN receipt]` a committed wrapper smoke run, `[OBS]` observed but not
wrapper-recorded, `[3P]` third-party, `[INFERRED]`. Pinned commit for every
`[SRC]` anchor: **`5a23b22e3f551a12de278491eebea5eb6d952eff`** (git tag
**v3.6.0**); the smoked artifact is **s3p@3.7.2**, whose listing architecture and
absence of any anonymous path are identical to v3.6.0 (verified against the
installed 3.7.2 build). Canonical tested identity lives in
[`../data/tool.json`](../data/tool.json). References of the form claim `some-id`
resolve in the canonical ledger, [`../data/claims.json`](../data/claims.json);
run coverage and blocked state are owned by [`running.md`](running.md).

This page reorganizes the existing groundwork; it adds no new findings.

## The core loop — `S3Comprehensions.each` → `eachRecursive`

All read commands (`ls`, `summarize`, `compare`, `each`/`map`) funnel into
`S3Comprehensions.each`, whose engine is a recursive
`eachRecursive(startAfter, stopAt, usePrefixBisect)`
[SRC S3Comprehensions.caf:361 @ 5a23b22e]. This is **parallel listing by
design** — the concurrency is over LIST requests themselves, not just over
transfers (claim `bisects-unknown-keyspace`). For each range node:

1. Compute a **synthetic midpoint key** `middleKey = getBisectKey(startAfter,
   stopAt)` — a key lexicographically between the bounds, derived arithmetically
   from a fixed alphabet **without knowing any real keys** (zero sampling of
   actual key data) [SRC S3Keys.caf:46 @ 5a23b22e] (claim
   `bisect-key-is-arithmetic-no-sampling`).
2. Issue **two `ListObjectsV2` calls concurrently**: one with
   `StartAfter = startAfter` (left), one with `StartAfter = middleKey` (right),
   each `MaxKeys = 1000` [SRC S3Comprehensions.caf:381-384 @ 5a23b22e].
3. Trim: keep left items with `Key <= middleKey`, right items with
   `Key <= stopAt` [SRC S3Comprehensions.caf:389-390 @ 5a23b22e] (claim
   `per-node-kept-sets-disjoint`).
4. **Recurse where a page came back full**: `recurseLeft = leftCount >= limit`,
   `recurseRight = rightCount >= limit` (limit = 1000). A full 1000-key page
   means the range still holds more keys, so that half is bisected again; a
   non-full page means that half is exhausted
   [SRC S3Comprehensions.caf:440-441,491-499,244 @ 5a23b22e]. Recursion fans out
   under the worker pool, so many ranges are in flight at once (claim
   `two-lists-per-node`).

**Runtime corroboration — scheduling, not confirmed concurrency.** The
credential-starved `ls` probe produced **two** `listObjectsV2` attempts with
distinct `StartAfter` (`normals-hourly/` = left, `normals-hourly/O` = the
computed midpoint = right) before both failed at credential resolution [RUN
`../receipts/smoke/_capability/anon-ls/`]. This shows the bisection **scheduled**
two ranges; it does **not** show simultaneous on-the-wire execution — both
aborted before any HTTP LIST was sent, and the receipt carries no timestamps.
Actual wire-level concurrency is a benchmark-phase observation (claim
`probe-scheduled-two-lists`).

### The `(startAfter, stopAt]` interval

Each range is the **half-open interval `(startAfter, stopAt]`**: S3's
`StartAfter` is **exclusive** (keys strictly after it), and the trim keeps
`Key <= stopAt` (**inclusive** upper bound) [SRC S3Comprehensions.caf:389-390 @
5a23b22e]. This half-open reading corrects an earlier `[startAfter, stopAt)`
write-up; that revision history lives in [`../research/`](../research/). This
matters for manual restart
(`--start-after`/`--stop-at`) and completeness boundaries.

## Keyspace division — arithmetic bisection over a fixed 95-char alphabet

`getBisectKey` compares `startAfter` and `stopAt` character by character to the
first differing position, then picks the middle character of the supported
alphabet between them (with special cases for adjacent characters and for
incrementing to the next key) [SRC S3Keys.caf:46-90 @ 5a23b22e]. The alphabet is
hard-coded: printable ASCII **space `0x20` through `~` `0x7E` — 95 characters**
(`0x7E − 0x20 + 1 = 95`; neither 94 nor 96) [SRC S3Keys.caf:5 @ 5a23b22e] (claim
`alphabet-is-95-chars`).

**The character-set boundary is a correctness boundary, not a performance note.**
A key containing any character **outside** this set makes `getKeyCharIndex`
return `-1` and `getBisectKey` **throw** `"Invalid character found in inputs"`
[SRC S3Keys.caf:11,54-58 @ 5a23b22e] (claim `non-ascii-key-throws`). The README
states the constraint plainly: "Key names must use a limited character set …
Since Aws-S3 doesn't support listing Keys in descending order, S3P uses a
character-range-based divide-and-conquer algorithm" [DOC README.md].

**Where the native `<` runs relative to the throw.** s3p compares keys with
JavaScript's native `<`/`<=`, which orders by UTF-16 code unit — and those
comparisons run **before** the alphabet lookup and its throw: `getBisectKey`
opens `if startAfter < stopAt` [SRC S3Keys.caf:46 @ 5a23b22e] and `eachRecursive`
guards on `startAfter >= stopAt` [SRC S3Comprehensions.caf:362 @ 5a23b22e], both
**before** the `charIndex < 0` throw at [SRC S3Keys.caf:57-58 @ 5a23b22e]. So a
boundary pair like BMP `U+E000` then astral `U+10000` (whose order reverses
between UTF-16 and S3's unsigned-byte/UTF-8 order) can make the comparison
mis-decide a range empty and **return before** the throw. The UTF-16 divergence
is therefore reachable and **not** subsumed by the non-ASCII throw (claim
`utf16-ordering-runs-before-throw`, whose corrected disposition records this
reading; its runtime effect is claim `utf16-ordering-runtime-behavior`). Both
effects are source-read only — the edge checks were deferred; see
[`running.md`](running.md).

**The author's self-flagged bug.** A code comment reads verbatim: *"it occurs to
me that there might be a bug because bisectKey could be after stopAt"*
[SRC S3Keys.caf:36 @ 5a23b22e]. It is guarded by a runtime post-condition
assertion that **throws** `"Whoops! …"` unless `startAfter <= bisectKey <=
stopAt` [SRC S3Keys.caf:87-88 @ 5a23b22e] (claim
`bisect-key-postcondition-assertion`). What observable failure this produces, if
any, is claim `bisect-key-postcondition-runtime`.

**`usePrefixBisect` is library-internal, not a CLI flag.** An optional mode
bisects by directory prefix instead of character range; it is **auto-triggered
internally when the right page returns 0 items** (a sparse region), **not**
user-opt-in — there is no CLI flag, and it is absent from all `--help` @ 3.7.2
[SRC S3Comprehensions.caf:448 @ 5a23b22e] (claim
`prefix-bisect-is-library-internal`). The author documents it as
tested-but-not-a-default: "It did not [enhance performance]. However, other
directory structures may perform better" [SRC S3Keys.caf:29-35 @ 5a23b22e] — that
"no faster" finding is the author's own and is claim `prefix-bisect-no-faster`. A
separate `aggressive` mode (skip a recursion step for more parallelism) is
likewise library-only, no CLI flag [SRC S3Comprehensions.caf:456-461 @ 5a23b22e].

## Pagination model — StartAfter re-listing, no continuation tokens

s3p does **not** use `NextContinuationToken`. Each "page" is an independent
`ListObjectsV2` with `StartAfter` set to the last key seen; completeness comes
from recursing until pages stop being full [SRC S3Comprehensions.caf:382-383,443
& S3.caf:46-54 @ 5a23b22e]. This is deliberate: continuation tokens are
inherently serial (page N's token comes from page N-1), so abandoning them is
what *enables* the parallel bisection. Page size is fixed at `limit = 1000` (the
AWS max) and is **not** exposed as a CLI flag [SRC S3Comprehensions.caf:244 &
S3PCli.caf:93-95 @ 5a23b22e]. A missing/undefined `Contents` is treated as an
empty page; a non-array `Contents` throws [SRC S3.caf:60-63 @ 5a23b22e].

## Concurrency — a LIFO worker pool, default 100

A `PromiseWorkerPool` caps simultaneous list requests at `listConcurrency`
(**default 100**), and the pool is **LIFO**: `queue`→`push` at
`PromiseWorkerPool.caf:30`, `_work`→`pop` at `:40` (pool at
`PromiseWorkerPool.caf:26-48`); default at S3Comprehensions.caf:246
[SRC @ 5a23b22e] (claim `lifo-pool-default-100`). Exposed as `--list-concurrency`
[SRC S3PCli.caf:94 @ 5a23b22e]. In 3.7.x, `--max-sockets` bounds the underlying
HTTP connection pool and "defaults to match … list-concurrency for list-only
commands" [OBS live `ls --help` @ 3.7.2] — so `--list-concurrency` transitively
bounds sockets too.

## Modes and output contracts

| Mode (subcommand/flag) | Request pattern | Output contract | Evidence |
| --- | --- | --- | --- |
| `ls` (default) | bisection LIST | one **Key** per line | [SRC S3PCliCommands.caf:21-26; DOC] |
| `ls --long` | bisection LIST | `"<yyyy-mm-dd HH:MM:ss> <human-size> <Key>"` per line — human-rounded size (**lossy**; not a verification mode) | [SRC S3PCliCommands.caf:22] |
| `ls --raw` | bisection LIST | one **JSON** object per line = a `listObjectsV2` Contents element (`Key,LastModified,ETag,Size,StorageClass`) | [SRC S3PCliCommands.caf:23] |
| `summarize` | bisection LIST | aggregate report (counts, size histogram, min/max, optional per-folder) — **no per-object records** | [SRC S3P.caf:41-112] |
| `compare` | bisection LIST on **two** buckets in lockstep | diff summary — needs a second bucket; N/A for a single smoke bucket | [SRC S3P.caf:114-197] |
| `each` / `map` | bisection LIST | user JS `--map`/`--map-list`/`--reduce` — the library primitives underlying `ls` | [SRC S3PCliCommands.caf:43-44] |

The `ls --long` line is lossy and cannot serve verification (claim
`ls-long-is-lossy`). `list-buckets` lists buckets (not objects) and needs
credentials — out of scope. `cp`/`sync`/`delete` are **mutating** and excluded
by the study guardrails. Object data goes to **stdout**; progress/heartbeat and
final-stats lines go to **stderr** [SRC S3Comprehensions.caf:285-307,
S3PCliCommands.caf:21 @ 5a23b22e] — so stdout is clean object data on a
successful run. (On a *failed* run s3p also prints the fatal error to stdout —
observed in the probe [RUN `../receipts/smoke/_capability/anon-ls/stdout.txt`].)

### Tunables

| Flag | Default | Effect | Evidence |
| --- | --- | --- | --- |
| `--list-concurrency N` | **100** | max simultaneous LIST requests — primary parallelism knob | [SRC S3PCli.caf:94; S3Comprehensions.caf:246] |
| `--max-sockets N` | = list-concurrency (list-only) | HTTP connection pool size | [OBS `ls --help` @ 3.7.2] |
| `--max-list-requests N` | unset | **soft** cap on total LIST requests (see Observability) | [SRC S3PCli.caf:95; S3Comprehensions.caf:362,378] |
| `--fetch-owner` | off | also fetch `Owner`; "~10% overhead" | [DOC `ls --help`; SRC S3.caf:52] |
| `--prefix` / `--start-after` / `--stop-at` | — | scope selection (set-intersection); can skip the rest of the bucket cheaply | [SRC S3PCli.caf:21-23; DOC] |
| page size (`limit`) | 1000 | items per LIST; **not CLI-exposed** | [SRC S3Comprehensions.caf:244] |
| `--pattern` / `--filter` | — | post-list filtering in JS; "won't speed up listing" | [DOC `ls --help`] |

## Retry model

s3p adds **no retry of its own**: `s3.list` only wraps the call with a
`.tapCatch` that logs and re-throws, plus a warning if a single list exceeds
60 s [SRC S3.caf:55-67 @ 5a23b22e]. But it does **not disable** the AWS SDK v3
client's default retries either — the `S3Client` is built with no retry override
[SRC S3.caf:26-29 @ 5a23b22e], so retries are **not absent** as the inherited
"no LIST retry/backoff" weakness assumes (claim
`no-s3p-level-retry-sdk-defaults-remain`). [INFERRED: effective policy = SDK v3
default standard mode, ~3 attempts with backoff; unverified at runtime.] Whether
a transient 503 nonetheless crashes or corrupts a run under sustained throttling
is the unrun hypothesis in claim `transient-503-crashes-run`.

## Memory model

- **CLI `ls` streams** — each item is printed via `onItem` as it arrives; page
  arrays (`leftItems`/`rightItems`) are released as the map/apply step consumes
  them [SRC S3PCliCommands.caf:20-25 & S3Comprehensions.caf:418-428 @ 5a23b22e]
  (claim `cli-ls-streams`). This is a **source-structure** observation (the CLI
  path holds no in-memory key buffer); it does **not** show that the CLI cannot
  run out of memory at scale, and it does **not** locate the inherited OOM report
  (claim `oom-at-100m-objects`) — issue #23 is unread and unattributed.
- **The library `ls`/`list` API accumulates** all keys into one array [SRC
  S3PCliCommands.caf:25 & S3P.caf:36-38 @ 5a23b22e] — a memory footgun at scale
  for API users (claim `library-api-accumulates`). Issue #23 was **not read**
  this phase; the source establishes only *that* the library API accumulates,
  **not** that it is #23's cause.
- **`summarize` is fixed-size only by default.** With `--summarize-folders` it
  builds a nested per-folder object whose size grows with the number of distinct
  folder paths [SRC S3P.caf:75-83 @ 5a23b22e] — that variant is not fixed-size.

## Observability — two counter caveats

s3p exposes a once-per-second stderr heartbeat with `items`, `items/s`,
`listRequests`, and (with `--verbose`) `efficiency`, `outstanding`,
`listWorkers`, `listQueue`; and a final stats object (`items`, `requests`,
`itemsPerSecond`, `averageItemsPerRequest`, …) [SRC S3Comprehensions.caf:296
(heartbeat), :524 (final stats) @ 5a23b22e]. Two caveats govern how these read as
cost signals:

- **Logical, not HTTP.** `requests`/`listRequests` counts the **logical**
  `s3.list` operations s3p issues (incremented `+= 2` per node **before** the
  call [SRC S3Comprehensions.caf:378 @ 5a23b22e]) — **not** underlying HTTP
  attempts, so SDK-level retries are invisible to it (claim
  `request-counter-is-logical-not-http`). Useful as a first-order
  request-efficiency signal (logical LISTs vs unique keys), but for true
  wire-cost the benchmark must count HTTP requests independently.
- **`--max-list-requests` is a soft cap.** It is checked (`>=`) *before* each
  `+= 2` [SRC S3Comprehensions.caf:362,378 @ 5a23b22e], so an odd limit can be
  exceeded by up to one pair; on hitting the budget it aborts "nicely" (finishes
  in-flight calls, logs, throws with stats) [SRC S3Comprehensions.caf:202-203,
  529-532 @ 5a23b22e] (claim `max-list-requests-is-soft-cap`).

## Failure surface (summary)

- **Character-set correctness boundary** — keys outside the 95-char ASCII
  alphabet throw or risk mis-partition; the single biggest fidelity risk, and
  exactly what an edge-case bucket would exercise. The edge checks were deferred;
  see [`running.md`](running.md). [SRC S3Keys.caf:57-58 @ 5a23b22e; DOC]
  (claims `non-ascii-key-throws`, `non-ascii-runtime-behavior`)
- **UTF-16 vs unsigned-byte ordering** — reachable before the throw; see above.
  [SRC S3Keys.caf:46; S3Comprehensions.caf:362 @ 5a23b22e]
- **No anonymous access** — hard blocker for credential-free listing; see
  [`running.md`](running.md). [SRC S3.caf:26-29 @ 5a23b22e; RUN] (claims
  `no-anonymous-access-path`, `anonymous-listing-blocked-at-auth`)
- **Packaging fragility** — the published v3.6.0 could not `require` `colors`;
  non-functional until 3.6.1. [OBS `../receipts/smoke/_build/build-notes.md`]
  (claim `v3-6-0-cannot-start`)
- **Memory growth, library API** — the library `list()` API accumulates
  (unbounded for huge buckets by source structure); CLI `ls` streams. Neither is
  a runtime scale measurement, and issue #23 is unread/unattributed. [SRC
  S3P.caf:36-38, S3PCliCommands.caf:20-25 @ 5a23b22e]
- **Retry/interruption** — no s3p-level retry or checkpoint; an interrupted long
  listing restarts from scratch or from a manual `--start-after`; throttling
  behaviour is whatever the AWS SDK does. [SRC S3.caf:55-67 @ 5a23b22e; INFERRED]
- **AWS SDK node-version deprecation** — SDK v3 warns it will require node ≥22
  after Jan 2027; the smoke image runs node 20 (works today). [OBS stderr @ 3.7.2]

## Source anchors (re-verified against the pinned checkout @ 5a23b22e)

- `source/S3Parallel/Lib/S3Keys.caf` `getBisectKey` **46–90** — the bisection
  algorithm (first-divergent-character walk + ASCII-midpoint selection).
  Corroborated.
- `S3Keys.caf:5` — `supportedKeyChars`, the fixed **95-char** printable-ASCII
  alphabet. Corroborated (alphabet = 95).
- `S3Keys.caf:57-58` — the `charIndex < 0` throw `"Invalid character found in
  inputs"` (lookup `getKeyCharIndex` at `:11`). Re-pinned from the inherited
  ":56-57".
- `S3Keys.caf:46,87` — native `<` at `:46`, `<=` at `:87` (UTF-16 code-unit
  order). Corroborated.
- `S3Keys.caf:36` — the author's own bug comment, verbatim; guarded by the
  post-condition assertion at `:87-88`. Corroborated.
- `eachRecursive` — re-pinned to **S3Comprehensions.caf:361-503** (was
  "believed `S3Comprehensions.caf`, line range not captured"). Two `pwp.queue ->
  s3.list` per node at `:381-384`; stop condition `recurseLeft = leftCount >=
  limit` at `:440-441`.
- LIFO worker pool + default `listConcurrency = 100` — re-pinned to pool
  `PromiseWorkerPool.caf:26-48` (LIFO `push` `:30` / `pop` `:40`); default 100 at
  `S3Comprehensions.caf:246` (was "no file:line").

These seven re-verified anchors are conserved as origin `ANCHORS` in
[`../research/claims-migration.md`](../research/claims-migration.md). Full
source-file inventory: [`../research/report.md`](../research/report.md) § 11.
