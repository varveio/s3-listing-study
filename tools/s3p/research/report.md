# s3p — independent groundwork report

> Scope: workspace phase (Stages A–C) of the s3-listing-study. Independent
> derivation from primary sources; the inherited dossier is deliberately unread
> until Stage D. Every behavioral claim carries an evidence label
> (`[DOC]`/`[SRC]`/`[RUN]`/`[3P]`/`[INFERRED]`/`[OBS]`).

## 1. Metadata

| | |
| --- | --- |
| Tool | `s3p` ("S3 Parallel") |
| Repo (canonical) | https://github.com/generalui/s3p |
| Source pin (for `[SRC]`) | git tag **v3.6.0**, SHA **`5a23b22e3f551a12de278491eebea5eb6d952eff`** (latest GitHub *release tag*) |
| Smoked version | **`s3p@3.7.2`** — npm `latest` dist-tag; what `npm i -g s3p` / `npx s3p` installs today (see §7 for why this ≠ the source pin) |
| Language | CaffeineScript (a CoffeeScript-family language) compiled to JavaScript; runs on Node.js. Uses `@aws-sdk/client-s3` v3 (`^3.436.0`) [SRC package.json @ 5a23b22e] |
| License | ISC [SRC LICENSE.md @ 5a23b22e] |
| Image | study-authored `s3p@sha256:622d7ec0e110f49e8cddf1b65b8bae98f641690b0d6db317df6f21e573894b91` (node:20-bookworm-slim base, `s3p@3.7.2`) |
| Date | 2026-07-17 |

**Upstream health** [3P GitHub API, accessed 2026-07-17]: 323 stars, 36 forks,
33 open issues, ISC, not archived. Created 2020-04. Original authors GenUI LLC
(with Resolution Bioscience). **Release/tag lag is itself a finding**: the latest
git *tag* is v3.6.0 (commit dated 2024-08-14), but `master` HEAD
(`d8d6dcac`, pushed 2026-04-17) is **16 untagged commits ahead**, and its
`package.json` version is **3.6.1** [SRC `git show d8d6dcac:package.json` →
`"version":"3.6.1"` @ clone]. npm goes further still: `latest` is **3.7.2**,
whose version has **no corresponding commit in the cloned git history at all**
(git tops out at 3.6.1 on master). So the tool users install (3.7.2) has no git
provenance in the canonical repo. [SRC `git rev-list v3.6.0..master --count` = 16
@ clone] [3P `npm view s3p dist-tags` → `{latest: '3.7.2'}`; `npm view s3p
versions` lists 3.6.1, 3.7.0–3.7.2]

---

## 2. How it works — the listing architecture

s3p's headline is that it lists S3 **in parallel** rather than serially, and
everything else (summarize, compare, cp, sync) is built on that listing core
[DOC README.md]. The mechanism is a **recursive keyspace bisection**, not the
usual `ContinuationToken` page loop.

### The core loop — `S3Comprehensions.each` → `eachRecursive`

All read commands funnel into `S3Comprehensions.each`, whose engine is a
recursive function `eachRecursive(startAfter, stopAt, usePrefixBisect)`
[SRC S3Comprehensions.caf:361 @ 5a23b22e]. For each range — **`(startAfter,
stopAt]`**: S3's `StartAfter` is *exclusive* (keys strictly after it) and the
trim keeps `Key <= stopAt` (inclusive upper) [SRC S3Comprehensions.caf:389-390]:

1. Compute a **synthetic midpoint key** `middleKey = getBisectKey(startAfter,
   stopAt)` — a key lexicographically between the bounds, derived arithmetically
   from a fixed 95-character alphabet, **without knowing any real keys**
   [SRC S3Keys.caf:46 @ 5a23b22e].
2. Issue **two `ListObjectsV2` calls concurrently**: one with
   `StartAfter = startAfter` (left), one with `StartAfter = middleKey` (right),
   each `MaxKeys = 1000` [SRC S3Comprehensions.caf:381-384 @ 5a23b22e].
3. Trim: keep left items with `Key <= middleKey`, right items with
   `Key <= stopAt` [SRC :389-390].
4. **Recurse where a page came back full**: `recurseLeft = leftCount >= limit`,
   `recurseRight = rightCount >= limit`. A full 1000-key page means the range
   still holds more keys, so that half is bisected again; a non-full page means
   that half is exhausted [SRC :440-441, :491-499]. Recursion fans out under a
   worker pool, so many ranges are in flight at once.

This is parallel **listing** by design (not just parallel transfers): the
concurrency is over LIST requests themselves [SRC — the `Promise.all` of two
`pwp.queue -> s3.list` per node, S3Comprehensions.caf:381-384]. The
credential-starved probe corroborates that the fan-out *logic* executes: a single
`ls` produced **two** `listObjectsV2` attempts with distinct StartAfter
(`normals-hourly/` = left, `normals-hourly/O` = the computed midpoint = right)
before both failed at credential resolution [RUN receipts/smoke/_capability/anon-ls/].
This is **not** proof of simultaneous on-the-wire execution — both aborted before
any HTTP LIST was sent, and the receipt carries no timestamps — only that the
bisection scheduled two ranges. Actual wire-level concurrency is a benchmark-phase
observation (report §10).

### Keyspace division — arithmetic bisection over a fixed alphabet

`getBisectKey` compares `startAfter` and `stopAt` character by character to the
first differing position, then picks the middle character of the supported
alphabet between them (with special cases for adjacent characters and for
incrementing to the next key) [SRC S3Keys.caf:46-90 @ 5a23b22e]. The alphabet is
hard-coded: space `0x20` through `~` `0x7E` (95 chars) [SRC S3Keys.caf:5 @
5a23b22e]. A key containing any character **outside** this set makes
`getKeyCharIndex` return `-1` and `getBisectKey` **throws** `"Invalid character
found in inputs"` [SRC S3Keys.caf:57-58 @ 5a23b22e]. The README states the
constraint plainly: "Key names must use a limited character set … Since Aws-S3
doesn't support listing Keys in descending order, S3P uses a character-range-based
divide-and-conquer algorithm" [DOC README.md]. **This is a correctness boundary,
not a performance note** — see §6.

An optional `usePrefixBisect` mode bisects by directory prefix instead of
character range; it is triggered internally when the right page returns 0 items
(a sparse region) [SRC S3Comprehensions.caf:448 @ 5a23b22e]. The author documents
it as tested-but-not-a-default: "It did not [enhance performance]. However, other
directory structures may perform better" [SRC S3Keys.caf:29-35 @ 5a23b22e].

### Pagination model — StartAfter re-listing, no continuation tokens

s3p does **not** use `NextContinuationToken`. Each "page" is an independent
`ListObjectsV2` with `StartAfter` set to the last key seen; completeness comes
from recursing until pages stop being full [SRC S3Comprehensions.caf:382-383,
:443 & S3.caf:46-54 @ 5a23b22e]. This is deliberate: continuation tokens are
inherently serial (page N's token comes from page N-1), so abandoning them is
what *enables* the parallel bisection. Page size is fixed at `limit = 1000`
(AWS max) and is **not** exposed as a CLI flag [SRC S3Comprehensions.caf:244 &
S3PCli.caf:93-95 @ 5a23b22e].

### Concurrency

A `PromiseWorkerPool` caps simultaneous list requests at `listConcurrency`
(**default 100**) [SRC S3Comprehensions.caf:246, :266 & PromiseWorkerPool.caf:11
@ 5a23b22e]. Exposed as `--list-concurrency` [SRC S3PCli.caf:94 @ 5a23b22e]. In
3.7.x, `--max-sockets` bounds the underlying HTTP connection pool and "defaults
to match … list-concurrency for list-only commands" [OBS live `ls --help` @
3.7.2] — so `--list-concurrency` transitively bounds sockets too.

### Retries / timeouts / ordering / memory / resume

- **Retries/backoff**: none in s3p itself; it delegates entirely to AWS SDK v3
  defaults (standard retry mode). `s3.list` only wraps the call with a
  `.tapCatch` that logs and re-throws, plus a warning if a single list takes
  >60 s [SRC S3.caf:55-67 @ 5a23b22e]. [INFERRED: effective retry policy = SDK
  default 3 attempts; unverified at runtime.]
- **Ordering assumption**: relies on S3 returning keys in strict lexicographic
  (UTF-8 byte) order and on the fixed alphabet's collation. [SRC S3Keys.caf
  @ 5a23b22e]
- **Memory model**: streaming for CLI `ls` — each item is printed via `onItem`
  as it arrives; page arrays (`leftItems`/`rightItems`) are released as soon as
  the map/apply step consumes them [SRC S3PCliCommands.caf:20-25 &
  S3Comprehensions.caf:418-428 @ 5a23b22e]. The library `ls`/`list` API instead
  accumulates all keys into one array [SRC S3PCliCommands.caf:25 & S3P.caf:36-38
  @ 5a23b22e] — a memory footgun at scale for API users, not for the CLI.
  `summarize` holds a small fixed summary object by default — **but** with
  `--summarize-folders` it builds a nested per-folder object whose size grows
  with the number of distinct folder paths [SRC S3P.caf:75-83 @ 5a23b22e], so
  that variant is not fixed-size.
- **Resume/checkpoint**: none automatic. `--start-after`/`--stop-at` allow
  manual range restart; `--max-list-requests` aborts "nicely" (finishes
  in-flight calls, logs, throws with stats) when a request budget is hit
  [SRC S3Comprehensions.caf:202-203, :529-532 @ 5a23b22e].
- **Truncation handling**: a `Contents` that is missing/undefined is treated as
  an empty page; a non-array `Contents` throws [SRC S3.caf:60-63 @ 5a23b22e].

---

## 3. Modes and tunables

"Mode" = a distinct subcommand or output contract; "tunable" = magnitude only.
Every listing command shares the one bisection request pattern above.

### Listing modes (list-only, non-mutating)

| Mode (subcommand/flag) | Request pattern | Output contract | Evidence |
| --- | --- | --- | --- |
| `ls` (default) | bisection LIST | one **Key** per line | [SRC S3PCliCommands.caf:21-26; DOC] |
| `ls --long` | bisection LIST | `"<yyyy-mm-dd HH:MM:ss> <human-size> <Key>"` per line (human-rounded size — lossy) | [SRC S3PCliCommands.caf:22] |
| `ls --raw` | bisection LIST | one **JSON** object per line = a `listObjectsV2` Contents element (`Key,LastModified,ETag,Size,StorageClass`) | [SRC S3PCliCommands.caf:23] |
| `summarize` | bisection LIST | aggregate report (counts, size histogram, min/max, optional per-folder) — **no per-object records** | [SRC S3P.caf:41-112] |
| `compare` | bisection LIST on **two** buckets in lockstep | diff summary | [SRC S3P.caf:114-197] — needs a second bucket; N/A for a single smoke bucket |
| `each` / `map` | bisection LIST | user JS `--map`/`--map-list`/`--reduce` | library primitives underlying `ls` [SRC S3PCliCommands.caf:43-44] |

`list-buckets` lists buckets (not objects) and needs credentials — out of scope.
`cp`/`sync`/`delete` are **mutating** and excluded by the study guardrails.

### Tunables (magnitude — sweep in the benchmark phase)

| Flag | Default | Effect | Evidence | Sweep? |
| --- | --- | --- | --- | --- |
| `--list-concurrency N` | **100** | max simultaneous LIST requests | [SRC S3PCli.caf:94; S3Comprehensions.caf:246] | **yes — primary parallelism knob** |
| `--max-sockets N` | = list-concurrency (list-only) | HTTP connection pool size | [OBS `ls --help` @ 3.7.2] | yes (pair with list-concurrency) |
| `--max-list-requests N` | unset | soft cap on total LIST requests; checked (`>=`) *before* each `+= 2` [SRC S3Comprehensions.caf:362,378], so it can overshoot by up to one pair, then aborts nicely | [SRC S3PCli.caf:95] | as a budget guard, not perf |
| `--fetch-owner` | off | also fetch `Owner`; "~10% overhead" | [DOC `ls --help`; SRC S3.caf:52] | optional |
| `--prefix` / `--start-after` / `--stop-at` | — | scope selection (set-intersection); can skip the rest of the bucket cheaply | [SRC S3PCli.caf:21-23; DOC "Features"] | scope, not magnitude |
| page size (`limit`) | 1000 | items per LIST; **not CLI-exposed** | [SRC S3Comprehensions.caf:244] | fixed |
| `aggressive` | off | skip a recursion step for more parallelism; **library-only, no CLI flag** | [SRC S3Comprehensions.caf:456-461] | note only |
| `--pattern` / `--filter` | — | post-list filtering in JS; "won't speed up listing" | [DOC `ls --help`] | no |

> **Concurrency + this study**: the default `--list-concurrency 100` exceeds this
> subject's `CONCURRENCY_CAP=8`. It is configurable, so the mode is *not* blocked
> on concurrency — `run.sh` pins `--list-concurrency 8` for smoke. (Auth blocks
> it for a different reason; see §8.)

---

## 4. How to run it properly

**Quickstart** [OBS live `help`/`ls --help` @ 3.7.2; SRC S3PCli.caf:115-133 —
composed from the CLI surface, not quoted verbatim from the README, which only
shows `npx s3p help` and an `ls --api-example` example]:
```sh
npx s3p ls   --bucket my-bucket --region us-east-1          # keys
npx s3p ls   --bucket my-bucket --long                       # key + size + date
npx s3p summarize --bucket my-bucket                         # counts & size histogram
```
Bucket/prefix may also be given positionally as `s3://bucket/prefix` or an
`https://…amazonaws.com/…` URL [SRC LibMisc.caf:60-89 @ 5a23b22e].

**Large-listing configuration, per the project's own guidance**: the docs give
no explicit large-listing recipe beyond "it's fast by default." The one
performance lever the docs name for listing is parallelism itself; the project
claims "almost 50,000 items/second (as-of v3.5)" on `ls` [DOC README.md]. The
actionable knob is `--list-concurrency` (raise it for very large/dense buckets);
`--max-list-requests` caps cost. No hinted/two-pass workflow exists; `--prefix`
/ `--start-after` / `--stop-at` are the only scoping hints and they simply narrow
the range.

**Environment prerequisites**:
- Node.js (and, *only for copying files >100 MiB*, the `aws` CLI — irrelevant to
  listing) [DOC README.md § Requirements].
- Region via `--region` or `AWS_REGION` [SRC S3.caf:27-29; S3PCli.caf:25].
- 3.7.x adds an `--endpoint` / `S3_ENDPOINT` override and `--max-sockets`
  [SRC S3.js constructor @ 3.7.2 build].

**Auth setup — and the critical footgun**: s3p "uses the same credentials
aws-cli uses" via the AWS SDK default credential chain, and **that is the only
option**. There is **no `--no-sign-request` / anonymous / unsigned mode** — not
in the v3.6.0 source, not in the 3.7.2 CLI help, and none can be reached via
env/config. The `S3Client` is constructed with only `{region, endpoint?,
requestHandler?, useAccelerateEndpoint, forcePathStyle}`; the `credentials` field
is never set, so with no ambient credentials the SDK's node credential provider
throws before any request [SRC S3.caf:26-29 @ 5a23b22e; confirmed unchanged in
the 3.7.2 build; OBS live `help`/`ls --help` @ 3.7.2 show no such flag]. Against
a **public** bucket this means s3p cannot list at all without real credentials.
See §8.

**Other footguns**: (a) keys with characters outside the 95-char alphabet break
bisection (§6); (b) the published **v3.6.0** artifact does not run at all
(missing `colors` dependency; §9); (c) the library `ls()` API accumulates all
keys in memory (§2).

---

## 5. Output and observability

**Formats** (from §3): `ls` = keys; `ls --long` = date/size/key; `ls --raw` =
JSON-per-line (full Contents element); `summarize` = aggregate report. Object
data goes to **stdout**; progress/heartbeat and final-stats lines go through
`art-standard-lib` `log` to **stderr** [SRC S3Comprehensions.caf:285-307,
S3PCliCommands.caf:21 @ 5a23b22e] — so stdout is clean object data on a
successful run. (On a *failed* run s3p also prints the fatal error to stdout —
observed in the probe [RUN _capability/anon-ls/stdout.txt].)

**`normalize.sh` contract** (staged; `<mode> [prefix]`):

| Mode | Emits | Notes |
| --- | --- | --- |
| `ls` | `key TAB - TAB - TAB - TAB -` | key only; other fields not exposed |
| `ls-raw` | full 5 fields | ETag unquoted, `LastModified` millis stripped to `…Z` |
| `ls-long` | key only + `-`×4 | human-size lossy; key recoverable only if it has no spaces |
| `summarize` | (nothing) | no per-object records → verification N/A |

s3p prints **full keys** in every mode (never path-relative), so the `prefix`
argument is accepted but unused for key reconstruction. The `ls-raw` and `ls`
paths are validated by synthetic fixtures under `receipts/smoke/_adapter/`
(no live listing was possible; see §8). Because containers run `TZ=UTC`, any
local-time output is UTC by construction; `ls-raw` mtimes are already ISO-Z from
the SDK.

**Metrics/counters s3p exposes** [SRC S3Comprehensions.caf:296 (heartbeat),
:524 (final stats) @ 5a23b22e]: a once-per-second stderr heartbeat with `items`, `items/s`,
`listRequests`, and (with `--verbose`) `efficiency`, `outstanding`,
`listWorkers`, `listQueue`; and a final stats object (`items`, `requests`,
`itemsPerSecond`, `averageItemsPerRequest`, …). **`requests`/`listRequests`
counts the *logical* `s3.list` operations s3p issues** (incremented `+= 2` per
node *before* the call [SRC S3Comprehensions.caf:378]) — **not** underlying HTTP
attempts, so SDK-level retries are invisible to it. Useful as a first-order
request-efficiency signal (logical LISTs vs unique keys), but for true wire-cost
the benchmark must count HTTP requests independently. `--verbose` enables the
richer set.

---

## 6. Failure surface

- **Character-set correctness boundary** [DOC README.md; SRC S3Keys.caf:57-58
  @ 5a23b22e]: keys outside the 95-char ASCII alphabet (space…`~`) — e.g. any
  multibyte UTF-8 key, or control bytes — cause `getBisectKey` to throw "Invalid
  character found", and the divide-and-conquer assumptions may skip or
  mis-partition such keys. This is the single biggest fidelity risk and exactly
  what an edge-case bucket (unicode/weird keys) would exercise. `EDGE_BUCKET=none`
  here, so **these checks are deferred** (§8). [INFERRED severity: high for
  buckets with non-ASCII keys; unverified at runtime.]
- **No anonymous access** [SRC S3.caf:26-29 @ 5a23b22e; RUN §8]: hard blocker
  for credential-free listing of public buckets.
- **Packaging fragility** [RUN §9]: the published v3.6.0 could not `require`
  `colors`; a clean install was non-functional until 3.6.1.
- **Memory growth (library API)** [SRC S3P.caf:36-38 @ 5a23b22e]: `list()`/`ls()`
  as a library call buffers every key; unbounded for huge buckets. CLI `ls`
  streams and is not affected. The inherited dossier cites an OOM report (issue
  #23) at ~100M objects; I did **not** read that issue this phase and do **not**
  claim it is this accumulate path — the source establishes only *that* the
  library API accumulates, not that it is the cause of #23. [INFERRED;
  scale-dependent, not settleable at smoke scale; issue #23 unread.]
- **Retry/interruption** [SRC S3.caf:55-67 @ 5a23b22e]: no s3p-level retry or
  checkpoint; an interrupted long listing restarts from scratch (or from a
  manual `--start-after`). Throttling behaviour is whatever the AWS SDK does.
  [INFERRED; unverified.]
- **AWS SDK node-version deprecation** [OBS stderr @ 3.7.2]: SDK v3 warns it will
  require node ≥22 after Jan 2027; the smoke image runs node 20 (works today).

---

## 7. Container

**No upstream Docker image and no upstream Dockerfile exist** for s3p (v3.6.0 or
master); distribution is npm-only (`npx s3p` / `npm i -g s3p`) [SRC repo tree,
`.github/workflows/` has only a test workflow @ 5a23b22e]. Per brief § Stage B
(neither image nor Dockerfile upstream), the image is **study-authored**:
`tools/s3p/Dockerfile`, base `node:20-bookworm-slim` pinned by digest
`sha256:2cf067…febfc0`, `npm install -g --ignore-scripts s3p@3.7.2`,
`ENTRYPOINT ["s3p"]`. Built image digest
`s3p@sha256:622d7ec0e110…573894b91` (arm64). [RUN receipts/smoke/_build/]

**Version choice — source pin vs smoked version**: `[SRC]` anchors need a git
checkout, and the latest git *tag* is v3.6.0 → source is pinned there. But
`npm i -g s3p` installs **3.7.2** (npm `latest`), and the published **3.6.0**
artifact **cannot start** (missing `colors` dep; §9). Smoking 3.7.2 therefore
tests what users actually get; its listing architecture and its absence of any
anonymous path are identical to v3.6.0 (verified against the installed 3.7.2
build). The benchmark phase should pin a single coherent version — recommend
**3.7.2** (or whatever npm `latest` is at benchmark time), and record that git
tags lag npm.

**Architecture matrix** — s3p is pure interpreted JS (no native addons); arch =
the node base image's arch:

| Channel | amd64 | arm64 |
| --- | --- | --- |
| Upstream image | none | none |
| Upstream Dockerfile | none | none |
| npm package (used here) | native | native |
| Prebuilt binaries | none | none |

Smoke ran **native arm64** (host arm64), **not emulated** [RUN run.meta
`image_arch=arm64 host_arch=arm64`]. amd64 (the expected campaign common
denominator) is equally native — flag: pick node base arch = campaign arch.

---

## 8. Smoke results

**Auth blocker — all listing modes blocked, not skipped.** s3p has no
anonymous/unsigned request path (§4). Under the wrapper's credential-starved
anonymous mode, every listing mode fails identically at AWS-SDK credential
resolution *before any LIST completes*. This is the designed
finalize-early/blocked path for "a signed-requests-only tool with `CREDS=none`"
(brief § Finalize early). `CREDS=none` on this subject card → the modes are
**recorded as untested-for-this-reason**, per the brief's auth protocol.

Capability probes (real runs under the wrapper):

| Probe | Invocation (argv after `ENTRYPOINT s3p`) | Exit | Wall | Result | Receipt |
| --- | --- | --- | --- | --- | --- |
| `ls`, anon | `ls --bucket noaa-normals-pds --region us-east-1 --list-concurrency 8 --prefix normals-hourly/` | 1 | 0.221s | `CredentialsProviderError: Could not load credentials from any providers` | `receipts/smoke/_capability/anon-ls/` |
| `ls-raw`, anon | `ls --raw --bucket … --list-concurrency 8 --prefix normals-hourly/` | 1 | 0.213s | same error | `receipts/smoke/_capability/anon-ls-raw/` |
| `summarize`, anon | `summarize --bucket … --list-concurrency 8 --prefix normals-hourly/` | 1 | 0.224s | same error | `receipts/smoke/_capability/anon-summarize/` |

All three errors originate in `@aws-sdk/credential-provider-node` [RUN
_capability/anon-ls/stderr.txt]. The probes cover **two genuinely different
subcommands** (`ls`, incl. its `--raw` output variant, and `summarize`) and fail
identically, confirming the block is **command-independent**
(shared credential-less `S3Client`). The remaining output variant `ls --long`
traverses the identical path; it is blocked by the same finding, marked
blocked-by-inheritance from these receipts rather than re-run. (The `ls` and
`ls-raw` probes' stdout/stderr payloads are **byte-identical** — expected,
because the failure precedes any mode-specific output; the runs are nonetheless
distinct: `run.meta` shows `mode=ls` vs `mode=ls-raw`, `utc_start` 12:01:18Z vs
12:02:29Z, and different wall/RSS/cgroup samples. The `summarize` probe's payload
differs in `mode`/timestamps as well.) No `verify-listing.sh` verdict was issued:
no mode produced a listing to verify, so the manifest pre-flight/verification
path was not exercised (nothing to compare against).

- **Auth mode**: `auth=anonymous` (credential-starved), enforced by the wrapper
  and recorded in `run.meta` [RUN run.meta `auth=anonymous`].
- **Request behaviour observed**: even while failing, the `ls` probe **scheduled
  two** `listObjectsV2` attempts with different `StartAfter` values (left +
  computed midpoint) — evidence the bisection *logic* fans out, not proof of
  simultaneous wire execution (both aborted at credential resolution before any
  HTTP LIST; §2).
- **Concurrency**: pinned `--list-concurrency 8` ≤ `CONCURRENCY_CAP=8`.
- **Edge-case fidelity checks** (unicode/weird keys, size+ETag assertions):
  **deferred** — `EDGE_BUCKET=none`. These are the checks that would exercise the
  character-set boundary of §6, so they matter more than usual for this tool.

**Adapter validation** (no live data): `normalize.sh` `ls-raw`/`ls`/`ls-long`
paths verified against synthetic fixtures — ETag unquoted, mtime canonicalized to
whole-second `…Z`, key-only modes correct, `summarize` empty
[receipts/smoke/_adapter/].

---

## 9. Notable findings

- **Parallel *listing* via keyspace bisection is real and (per the author)
  unusual.** s3p throws continuation tokens out entirely and bisects the keyspace
  with synthetic midpoint keys so that LISTs themselves run concurrently
  [SRC S3Comprehensions.caf, S3Keys.caf @ 5a23b22e; DOC README.md]. The README
  frames this as distinctive versus tools that "list items in serial" [DOC]; I
  did **not** independently audit the other study tools' listing strategies, so
  the *comparative* uniqueness is the README's claim, not my finding.
- **It pays for that speed with a hard character-set restriction.** Because S3
  can't list in descending order, the bisection needs a known alphabet; keys
  outside 95 ASCII chars throw or risk being mis-partitioned [SRC S3Keys.caf:5,
  :57-58 @ 5a23b22e; DOC]. A correctness/parallelism tradeoff few tools make so
  explicitly.
- **The published v3.6.0 is dead on arrival.** `npm i -g s3p@3.6.0` →
  `Cannot find module 'colors'`; `colors` is `require`d at runtime but missing
  from the published dependency closure (present in the repo lockfile — a publish
  slip). Fixed in 3.6.1 (commit `5610411` "fixed deps (colors)"). [RUN
  receipts/smoke/_build/; SRC package.json @ 5a23b22e]
- **Releases/tags lag npm by several versions.** Latest git tag v3.6.0 (2024);
  npm `latest` 3.7.2 (2026). A reader trusting GitHub releases would pin a broken
  version. [3P npm; SRC clone]
- **Single-core by design.** The author notes s3p is "still only a single-core
  NodeJS application" and invites collaboration for multi-process/distributed
  scaling [DOC README.md] — relevant to the study's language-bottleneck
  hypothesis (a JS event loop driving up to 100 concurrent SDK calls).
- **It shells out to `aws s3 cp` for >100 MiB copies** [DOC; SRC S3.caf:160-180
  @ 5a23b22e] — irrelevant to listing but explains the aws-cli requirement.

---

## 10. Open questions for the benchmark phase

1. **Anonymous access is impossible** — s3p simply cannot benchmark against a
   public bucket without real credentials. The benchmark must either (a) supply
   list-scoped credentials, or (b) exclude s3p from anonymous-bucket runs and
   note why. This is the dominant open question.
2. **`--list-concurrency` sweep** — the primary parallelism knob. Proposed range:
   `{1 (serial baseline), 8, 25, 50, 100 (default), 200, 400}`, paired with
   `--max-sockets` at matching values. Watch for the point where added
   concurrency stops helping (SDK socket pool, single-core CPU, or S3 throttling).
3. **Request efficiency** — s3p's bisection uses ≥2 LISTs per range node and
   trims overlap; measure `requestsUsed` vs a serial paginator's `ceil(N/1000)`
   to quantify the request-count overhead it trades for latency. s3p exposes the
   counter directly (`--verbose` → `requests`).
4. **Character-set fidelity at scale** — requires an edge-case bucket
   (`EDGE_BUCKET` currently none). Does bisection actually drop or mis-order
   non-ASCII / URL-special keys, or just throw? High-value correctness question.
5. **Library-API memory growth** — CLI `ls` streams, but `list()`/`ls()` as an
   API buffers all keys; not measurable via the CLI harness. Note only.
6. **Node version** — benchmark on node ≥22 (SDK v3 deprecation) and record it.
7. **`aggressive` / `usePrefixBisect`** — internal-only optimizations with no CLI
   flag; would need an API driver to benchmark. Note only.

---

## 11. Sources

**Primary source (pinned checkout)** — `<sources>/s3p` @
`5a23b22e3f551a12de278491eebea5eb6d952eff` (git tag v3.6.0). Key files:
`source/S3Parallel/S3Comprehensions.caf` (the `each`/`eachRecursive` engine),
`source/S3Parallel/Lib/S3Keys.caf` (bisection math + alphabet),
`source/S3Parallel/Lib/S3.caf` (SDK client + `list`),
`source/S3Parallel/S3PCli.caf` (CLI option definitions),
`source/S3Parallel/S3PCliCommands.caf` (command wiring),
`source/S3Parallel/S3P.caf` (summarize/compare/copy/sync),
`source/S3Parallel/Lib/PromiseWorkerPool.caf` (concurrency pool),
`package.json`, `LICENSE.md`.

**Docs** [accessed 2026-07-17]:
- README (repo, v3.6.0): https://github.com/generalui/s3p/blob/v3.6.0/README.md
- Live CLI help captured from `s3p@3.7.2` in-container (`help`, `ls --help`) —
  see `receipts/smoke/_build/build-notes.md`.
- S3P blog post: https://medium.com/@shanebdavis/s3p-massively-parallel-s3-copying-9a9e466d0d74 [DOC/3P — not re-fetched this phase]

**Third-party** [accessed 2026-07-17]:
- GitHub API `repos/generalui/s3p` (health: 323★, 33 open issues, ISC).
- npm registry `s3p` (`dist-tags.latest = 3.7.2`; versions list showing 3.6.1,
  3.7.0–3.7.2 published beyond the latest git tag).

**Smoke image**: `s3p@sha256:622d7ec0e110f49e8cddf1b65b8bae98f641690b0d6db317df6f21e573894b91`
(node:20-bookworm-slim base `sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0`).

**Receipt index**:
- `receipts/smoke/_build/build-notes.md` — image build, version/help, colors bug, arch matrix.
- `receipts/smoke/_capability/anon-ls/` — anonymous `ls` blocked (CredentialsProviderError).
- `receipts/smoke/_capability/anon-ls-raw/` — anonymous `ls --raw` blocked (same).
- `receipts/smoke/_adapter/` — `normalize.sh` fixture validation (ls-raw/ls/ls-long).

**Manifest checked against** (for any future credentialed run):
`noaa-normals-pds.2026-07-17.tsv.gz`, sha256
`c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb`, 148,917 keys.
