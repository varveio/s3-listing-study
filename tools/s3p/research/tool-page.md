# s3p

> **Historical landing page (2026-07-20, capsule migration).** This is the full
> pre-restructure landing page. Any `current-state` wording below is historical
> as of the date it records and is superseded by the root README and `data/`.
> Only this banner and link targets changed; body prose and evidence
> qualifications are preserved.

`s3p` ("S3 Parallel") is a Node.js CLI whose distinctive move is discovering an
unknown keyspace by **recursive bisection** — synthetic midpoint keys and two
concurrent `ListObjectsV2` calls per range node — rather than the serial
continuation-token page loop every other tool in this survey uses. By this
study's reading it is the survey's **only key-space bisector** — though that
comparative claim is the README's/author's own framing, **not** independently
audited this phase (the other study tools' listing strategies were not read
here); the *mechanism* is source-confirmed, the *"unique among the set"*
comparison is not. See ledger row X1.

**What we saw.** s3p **cannot make anonymous requests.** Its `S3Client` is built
with region/endpoint only — there is no `--no-sign-request`/unsigned/credentials
hook anywhere in the v3.6.0 source, the 3.7.2 CLI help, or reachable via
env/config [SRC S3.caf:26-29 @ 5a23b22e; OBS live help @ 3.7.2]. Under this
study's credential-starved smoke (`CREDS=none`) **every listing mode is blocked**
at AWS-SDK credential resolution before any LIST completes — receipts committed
across two subcommands (`ls`, `ls --raw`, `summarize`) [RUN
`receipts/smoke/_capability/anon-ls{,-raw}/`, `anon-summarize/`]. This is an
setup requirement, not a tunable: **a benchmark run needs list-scoped
credentials supplied for this project.** Listing itself **is** cleanly
separable (`ls`/`summarize` are first-class list-only subcommands) — the blocker
is auth, not separability.

Full detail lives in three companion documents plus the immutable groundwork:

- [`mechanism.md`](../docs/mechanism.md) — the source-anchored architecture (bisection
  engine, the `(startAfter, stopAt]` interval, the character-set boundary,
  pagination, concurrency, retry and memory models, the two counter caveats).
- [`running.md`](../docs/running.md) — the study-authored image, every capability
  receipt with its exact invocation, what a credentialed run would need, and the
  architecture matrix.
- [`research/`](.) — `report.md` (the source-and-run groundwork report),
  `reconciliation.md` (every inherited claim walked row-by-row), and
  `codex-review.md` (the critical cross-check, 15 findings resolved, plus this
  consolidation's review). These preserved files may use the project's older
  terminology; this page is the current summary.

This page stays the **hypothesis sheet** for the benchmark phase: every claim
not explicitly **CONFIRMED** below is a hypothesis, however corroborated by
source reading.

|  |  |
|---|---|
| **Repo** | <https://github.com/generalui/s3p> — canonical; latest git *tag* v3.6.0, npm `latest` 3.7.2 [SRC clone; 3P npm] (M1) |
| **Language** | CaffeineScript (a CoffeeScript-family language, `.caf`) compiled to JavaScript; Node.js; `@aws-sdk/client-s3` v3 [SRC package.json @ 5a23b22e] (M2) |
| **License** | **ISC** — **CORRECTED** from the original tool page's *MIT* [SRC LICENSE.md "ISC License (ISC)"; package.json `"license":"ISC"` @ 5a23b22e] (M3) |
| **Version** | Source pinned **v3.6.0**, SHA `5a23b22e3f551a12de278491eebea5eb6d952eff` (latest release tag, for `[SRC]` anchors); smoked **s3p@3.7.2** (npm `latest`, what `npm i -g s3p` installs — but **caller-supplied**, not receipt-verified: `run.meta tool_version_source=caller-supplied`). Git tags lag npm; the published 3.6.0 cannot start (V2). (M4) |
| **Tier** | 1 — included in the planned comparative runs |
| **Testability** | **CORRECTED / reframed** from "believed straightforward; open Q is whether listing is isolable/timable". Listing **is** isolable (`ls`/`summarize` are list-only) [SRC S3PCliCommands.caf; OBS live help @ 3.7.2]; install is **not** straightforward for the tagged version (V2); timing is **blocked by auth** (H-Auth). (M5) |

## What we tried and saw

- **No anonymous access — the dominant finding, receipt-backed.** s3p has no
  unsigned/credentials hook; run credential-starved it dies with
  `CredentialsProviderError` before any LIST completes, across three wrapper
  receipts spanning two subcommands. With `CREDS=none` this blocks every listing
  mode. See the summary above and `running.md`.
- **Listing is separable; the tool page's central open question is answered.**
  `ls` and `summarize` are first-class, non-mutating, list-only subcommands, not
  a copy side-effect — the `ls` probe reached and issued real `listObjectsV2`
  calls before the auth failure [RUN `_capability/anon-ls/`; SRC
  S3PCliCommands.caf:11-39]. The original "primary purpose is copying; listing
  may not be standalone" framing is **CORRECTED** (X7).
- **Parallel *listing* via keyspace bisection is real (per source).** s3p throws
  continuation tokens out entirely and bisects with synthetic midpoint keys so
  LISTs themselves run concurrently under a LIFO pool (`--list-concurrency`
  default 100) [SRC S3Comprehensions.caf, S3Keys.caf @ 5a23b22e]. The
  credential-starved `ls` probe **scheduled** two ranges (two `listObjectsV2`
  attempts with distinct `StartAfter`) before both aborted at credential
  resolution — evidence the fan-out *logic* executes, **not** proof of
  simultaneous wire execution (no timestamps; both aborted before any HTTP LIST).
- **It pays for that speed with a hard 95-character-set restriction.** Keys
  outside space `0x20`…`~` `0x7E` make `getBisectKey` throw "Invalid character
  found" [SRC S3Keys.caf:5,57-58 @ 5a23b22e]. Source-read only — `EDGE_BUCKET=none`,
  edge checks deferred (W6/W7).
- **Version-channel findings (documentary/observed, not run-settled).** Git
  tags/GitHub releases lag npm; `master@d8d6dca` is version 3.6.1 (16 untagged
  commits past tag v3.6.0), and npm `latest` 3.7.2 has **no** corresponding git
  commit (V1). The published **v3.6.0 artifact cannot start** — `Cannot find
  module 'colors'`, fixed in 3.6.1 (V2). Both are `[OBS]`/`[SRC]`/`[3P]`, **not**
  wrapper receipts — they do **not** promote past `VERIFIED: no` (codex round 1,
  finding 1).

## Notes, questions, and observations

Every claim in `research/reconciliation.md` (which walks the original secondhand
tool page row-by-row), checked against the 2026-07-17 groundwork
(`research/report.md`). Status values (routed by **receipt-backed** status, never
by reconciliation status alone): **CONFIRMED** (a committed wrapper receipt
settles it, at that receipt's scope), **CORRECTED** (the original claim was found
factually different; both sides shown), **VERIFIED: no** (not settled by any
receipt — a hypothesis, however corroborated by source reading),
**UNVERIFIABLE** (cannot be tested with resources on hand).

Per `AGENTS.md`, source reading alone never **promotes** a claim — it never lifts
a still-open hypothesis from `VERIFIED: no` to `CONFIRMED`; a `CONFIRMED` requires
a run. `CORRECTED` is **not** a promotion: it flags a documentary/factual mislabel
in the tool page (a wrong license, a wrong alphabet size, a wrong `file:line`, a
mischaracterized mechanism), which source reading *can* establish, and it asserts
no runtime behavior as verified. So a row the original got **right** and source
merely corroborates stays `VERIFIED: no`; a row the original got **wrong** is
`CORRECTED` even when the fix is source-only. (This matches the owner-adopted
s5cmd ledger, which likewise marks source-only documentary fixes `CORRECTED`.)

### Metadata / testability (reconciliation M1–M5)

| Claim | Status | Evidence |
| --- | --- | --- |
| M1–M2 Repo `generalui/s3p`; language JS/CaffeineScript | documentary, corroborated | [SRC clone, source tree, package.json @ 5a23b22e; 3P npm]. Carried in the metadata table above. |
| M3 License **MIT** | **CORRECTED** → **ISC** | [SRC LICENSE.md; package.json `"license":"ISC"` @ 5a23b22e]. Editorial correction. |
| M4 Version "unconfirmed — pin one" | documentary | Source pinned v3.6.0/`5a23b22e`; smoked 3.7.2 (caller-supplied; see V1–V2). |
| M5 Testability "isolable/timable open Q" | **CORRECTED** (reframed) | Listing isolable [SRC S3PCliCommands.caf; OBS help]; install not straightforward (V2); timing auth-blocked (H-Auth). |

### Mechanism (reconciliation X1–X8)

| Claim | Status | Evidence |
| --- | --- | --- |
| X1 Adaptively bisects an unknown keyspace; **"unique among the set"** | **VERIFIED: no** — mechanism corroborated by source; the **comparative** uniqueness is the README's own framing, **not** audited (other study tools not read this phase) | [SRC S3Comprehensions.caf:361-503; S3Keys.caf:46-90 @ 5a23b22e] |
| X2 `getBisectKey`: first divergent char → ASCII-midpoint from a fixed alphabet, zero sampling of real keys | **VERIFIED: no** (source-corroborated) | [SRC S3Keys.caf:46-90 @ 5a23b22e] |
| X3 `eachRecursive` fires **two** full `ListObjectsV2` per split node; a side recurses only if its page returned a full 1000 | **VERIFIED: no** (source-corroborated; the anon-`ls` probe **scheduled** two distinct-`StartAfter` LISTs, not confirmed wire-concurrency) | [SRC S3Comprehensions.caf:381-384,440-441,244] [RUN `_capability/anon-ls/`] |
| X4 Adjacent LIST calls **overlap** ~500 new / ~500 seen (~50% waste), a deliberate trade | **VERIFIED: no** (the waste magnitude is unrun, scale-dependent; benchmark) — **source nuance**: the two per-node calls keep **disjoint** kept-sets (left `Key<=middleKey`, right `Key<=stopAt`), so "overlap" is redundant **raw** fetching in dense ranges, not a 50/50 kept-set overlap | [SRC S3Comprehensions.caf:389-390 @ 5a23b22e]; maps to Open hypotheses / report §10 Q3 |
| X5 LIFO worker pool, default `listConcurrency = 100` | **VERIFIED: no** (source-corroborated) | LIFO `queue`→`push`/`_work`→`pop` [SRC PromiseWorkerPool.caf:30,40]; default 100 [SRC S3Comprehensions.caf:246 @ 5a23b22e] |
| X6 Opt-in `bisectPrefix` splits at next `/`; author found it no faster; off by default | **CORRECTED** (source) — it is **not user-opt-in**: no CLI flag; library-internal, auto-triggered when a right page returns 0 items — **/ VERIFIED: no** for the author's "no faster" finding (unreproduced) | [SRC S3Keys.caf:29-35; S3Comprehensions.caf:448; absent from all `--help` @ 3.7.2] |
| X7 "Primary purpose is copying; listing may not be a standalone, separately invokable operation" | **CORRECTED** (receipt-backed) — `ls`/`summarize` **are** standalone list-only subcommands; the `ls` probe issued real LIST calls before the auth failure | [SRC S3PCliCommands.caf:11-39; S3PCli.caf:115-133; OBS live help] [RUN `_capability/anon-ls/`] |
| X8 Alphabet size disputed: 94-char vs ~96-char | **CORRECTED** (source) — it is **95** chars, space `0x20`…`~` `0x7E` inclusive (`0x7E−0x20+1=95`); neither 94 nor 96 | [SRC S3Keys.caf:5 @ 5a23b22e] |

### Published numbers and estimates (reconciliation N1)

| Claim | Status | Evidence |
| --- | --- | --- |
| N1 the throughput/speedup numbers (per-source, as the original tool page carried them): **~20,000 items/s** (GenUI accelerators page); **~35,000 items/s peak** and **"5–50×"** overall (the author's Medium post); **9 GB/s** copy throughput (same Medium post — a copy number, not a listing rate); a separate secondhand summary table's unanchored **"~35K objects/sec"** (no bucket size / wall-clock); and a **second, conflicting "15–100× faster than aws-cli"** multiplier (landscape survey). Both multipliers trace to the author; neither has third-party reproduction | **VERIFIED: no** — all author-self-reported, no third-party reproduction; **not benchmarked** (auth-blocked; smoke produces no comparative numbers by design). The independent README read found the same lineage ("almost 50,000 items/s as-of v3.5", "8–9 GB/s" copy) — still unverified | GenUI accelerators page; author Medium post `shanebdavis.medium.com/s3p-massively-parallel-s3-copying`; [DOC README.md] |

### Tradeoffs and questions to test (reconciliation W1–W8)

| Claim | Status | Evidence |
| --- | --- | --- |
| W1 ~50% wasted LIST work | **VERIFIED: no** (Unaddressed — same as X4; scale/efficiency, not run). Falsifiable via API-call count; note s3p's exposed counter is **logical** (`--verbose` → `requests`, incremented `+= 2`/node **before** the call — not HTTP attempts) | [SRC S3Comprehensions.caf:296,524,378 @ 5a23b22e] |
| W2 Blind ASCII-midpoint splits cost extra rounds on skewed keyspaces | **VERIFIED: no** (Unaddressed — benchmark-phase question, not run) | — |
| W3 Single-core Node bottleneck; throughput should plateau **well below** what multi-core tools achieve at high `listConcurrency` | **VERIFIED: no** — architecture corroborated (author states s3p "is still only a single-core NodeJS application"); the **plateau** is scale-dependent, not settleable at smoke | [DOC README.md] [SRC] |
| W4 OOM at ~100M objects (issue #23) | **VERIFIED: no** (Unaddressed; can't reach scale, auth-blocked) — **source nuance**: CLI `ls` **streams** each item (bounded); the library `list()`/`ls()` API **accumulates** all keys. Issue #23 **unread** this phase; the source does **not** attribute the reported OOM to that path | [SRC S3PCliCommands.caf:20-25; S3P.caf:36-38 @ 5a23b22e] |
| W5 No LIST retry/backoff → a transient 503 crashes/corrupts the run | **CORRECTED** (partial, source) — s3p adds **no** retry of its own (`list` only `.tapCatch`→rethrow) but does **not** disable the AWS SDK v3 client's default retries, so retries are **not absent** as the claim assumes **/ VERIFIED: no** for survival under sustained throttling (not run) | [SRC S3.caf:26-29,55-68 @ 5a23b22e; INFERRED: SDK v3 default = standard mode, 3 attempts, backoff] |
| W6 Cannot bisect non-ASCII keys — throws at the alphabet boundary. **This directly contradicts an earlier secondhand claim (from this same research lineage) that s3p "works on literally any keyspace including ones with non-printable bytes"** — both the original claim and the source correction are recorded, deliberately | **VERIFIED: no** (the source correction is source-only and **not executed** — `EDGE_BUCKET=none`, edge checks deferred; the correction becomes settled only when a non-ASCII bucket is listed and the throw is observed or not) | `getKeyCharIndex` returns −1; `getBisectKey` throws `"Invalid character found in inputs"` [SRC S3Keys.caf:11,54-58 @ 5a23b22e]; opposing claim = the inherited secondhand design-doc note |
| W7 UTF-16 code-unit ordering (native `<`) diverges from S3 unsigned-byte order on astral chars | **VERIFIED: no** (source-corroborated; **NOT subsumed** by W6 — native `<`/`<=` run **before** the alphabet throw: `getBisectKey` opens `if startAfter < stopAt` and `eachRecursive` guards `startAfter >= stopAt`, so a BMP/astral boundary pair can mis-decide a range empty and return before the throw). Observable effect not run | [SRC S3Keys.caf:46; S3Comprehensions.caf:362; throw at S3Keys.caf:57-58 @ 5a23b22e] |
| W8 Author self-flags a bug: "bisectKey could be after stopAt" | **VERIFIED: no** (source-corroborated + nuance: `getBisectKey` guards it with a runtime post-condition assertion that **throws** `"Whoops! …"` unless `startAfter <= bisectKey <= stopAt`). Observable failure not run | comment in full [SRC S3Keys.caf:36]; assertion [SRC S3Keys.caf:87-88 @ 5a23b22e] |

### Code anchors (reconciliation "Code anchors", 7 rows)

| Claim | Status | Evidence |
| --- | --- | --- |
| The 7 inherited code anchors, re-verified against the pinned checkout | **CORRECTED** (3 re-pinned) — the `indexOf` throw is at **:57-58** (was ":56-57"); `eachRecursive` is **S3Comprehensions.caf:361-503** (was "line range not captured"); the LIFO pool is **PromiseWorkerPool.caf:26-48** and default 100 at **S3Comprehensions.caf:246** (was "no file:line") **/ VERIFIED: no** (4 corroborated as source anchors) | Full re-pinned list in [`mechanism.md`](../docs/mechanism.md) § Source anchors [SRC @ 5a23b22e] |

### New findings — additive, not from the inherited tool page (reconciliation V1–V3, H-Auth)

| Claim | Status | Evidence |
| --- | --- | --- |
| **H-Auth** No anonymous/unsigned access path; with `CREDS=none` every listing mode blocked at credential resolution | **CONFIRMED** — `CredentialsProviderError` across **three** wrapper receipts spanning two subcommands (`ls`, `ls --raw`, `summarize`); `ls --long` blocked-by-inheritance (same code path, not re-run). Scoped to s3p 3.7.2, anonymous mode, the registered smoke bucket | [RUN `_capability/anon-ls/`, `anon-ls-raw/`, `anon-summarize/`] [SRC S3.caf:26-29 @ 5a23b22e; OBS live help @ 3.7.2] |
| **V1** Git tags/GitHub releases lag npm: latest tag v3.6.0 (2024); `master@d8d6dca` is version **3.6.1**, 16 untagged commits ahead; npm `latest` **3.7.2** has **no** corresponding commit in the cloned git history | **VERIFIED: no** — documentary; **not** a wrapper receipt (and the smoked 3.7.2 label is caller-supplied) | [SRC `git rev-list v3.6.0..master --count`=16; `git show d8d6dca:package.json` → 3.6.1] [3P `npm view s3p dist-tags`/`versions`] |
| **V2** The published **v3.6.0 artifact cannot start**: `s3p version` → `Cannot find module 'colors'` (runtime dep absent from v3.6.0's published closure; present in repo lockfile). Fixed in 3.6.1 (commit `5610411`) | **VERIFIED: no** — **[OBS]** direct `docker run`, documented in `receipts/smoke/_build/build-notes.md`, a build note **not** a `smoke-run.sh` receipt; does **not** promote (codex round 1, finding 1). Scoped to `npm i -g s3p@3.6.0` on node:20 | [OBS `_build/build-notes.md`; SRC package.json @ 5a23b22e has no `colors` dep] |
| **V3** 3.7.x adds `--max-sockets`, `--endpoint`/`S3_ENDPOINT`, `--group-by`, and a mutating `delete` command — none in v3.6.0 source | **VERIFIED: no** — **[OBS]** live help + installed 3.7.2 build; **not** a wrapper receipt | [OBS live `help`/`ls --help` @ 3.7.2; SRC S3.js build @ 3.7.2] |

**Additive-row disclosure.** Four ledger rows are **additive** — they are not
from the inherited (secondhand) tool page but were produced by the 2026-07-17
groundwork pass (`research/reconciliation.md` § "New findings (not in the
tool page)"): **H-Auth** (no anonymous access), **V1** (tag/npm divergence),
**V2** (v3.6.0 unstartable), and **V3** (3.7.x CLI additions). Every other
ledger row traces to an inherited-claim row in `research/reconciliation.md`
(M1–M5, X1–X8, N1, W1–W8, and the seven code anchors). No reconciliation row is
dropped: the code-anchor rows and metadata rows are carried above and in
[`mechanism.md`](../docs/mechanism.md) § Source anchors; the cross-cutting rows are
routed below.

The original tool page's **"Claimed strengths"** (zero-config bisection of an
unknown keyspace with no hints; the "full page ⇒ keep splitting, else stop"
heuristic being simple and requiring **no tuning**; eager double-LIST-per-node
trading extra API calls for **lower discovery latency**) are the positive framing
of the same mechanics reconciled as X1, X3, and X4 — carried there and in
[`mechanism.md`](../docs/mechanism.md), not as separate rows; none is receipt-settled, so
each stays `VERIFIED: no`.

## Open hypotheses for the benchmark

The benchmark phase's work queue — every **Unaddressed** or **unrun
source-corroborated** claim, carried forward in full with its original
provenance. None is settled by any receipt; all remain `VERIFIED: no`.

- **X1 (comparative uniqueness)** — that s3p's bisection is "unique among the
  set." The mechanism is source-confirmed; the *comparative* half needs an audit
  of the other study tools' listing strategies, not done this phase. Origin:
  inherited tool page (Mechanism) + report §9.
- **X4 / W1 (~50% wasted LIST work)** — "each 1000-key page returns only ~500
  *new* unique keys on average." Falsifiable by an API-call-count instrumented
  run vs total unique keys returned. Origin: inherited tool page (weakness #1);
  maps to report §10 Q3 (`requestsUsed` vs `ceil(N/1000)`), noting the counter
  is logical, not HTTP.
- **W2 (skewed-keyspace extra rounds)** — a clustered/skewed keyspace should make
  the blind ASCII-midpoint bisector repeatedly land in empty/near-empty ranges.
  Falsifiable by running a skewed layout vs a uniform bucket of the same size.
  Origin: inherited tool page (weakness #2).
- **W3 (single-core plateau)** — throughput should plateau **well below** what
  multi-core tools achieve at high `listConcurrency`. Falsifiable by sweeping
  `--list-concurrency` and watching for a throughput plateau alongside one-core
  CPU saturation. Origin: inherited tool page (weakness #3); architecture
  corroborated, plateau unrun.
- **W4 (OOM at ~100M objects, issue #23)** — falsifiable by running at/beyond
  that scale and watching RSS/exit behavior. Issue #23 unread; the source
  establishes only *that* the library API accumulates, not that it is #23's
  cause. Origin: inherited tool page (weakness #4).
- **W5 (no LIST retry/backoff)** — carried in full: "if true, a transient
  503/SlowDown response during a run should **crash or corrupt** the run rather
  than being absorbed." The "no retries" premise is corrected by source (SDK v3
  defaults remain), but survival under sustained throttling is unrun. Falsifiable
  by inducing throttling and observing whether the run survives. Origin: inherited
  tool page (weakness #5).
- **W6 (non-ASCII throw)** — does bisection actually throw (or mis-partition) on
  keys outside the 95-char alphabet, or does it work on "literally any keyspace
  including ones with non-printable bytes" as the earlier secondhand claim
  asserted? The sharpest binary test on this page; the source correction and the
  opposing secondhand claim are both recorded (see ledger W6). Needs an edge-case
  bucket (`EDGE_BUCKET=none`). Origin: inherited tool page (weakness #6).
- **W7 (UTF-16 vs unsigned-byte ordering)** — construct keys straddling the
  BMP/astral divergence and observe whether s3p mis-orders/returns before
  throwing. Not subsumed by W6. Origin: inherited tool page (weakness #7).
- **W8 (author's self-flagged `bisectKey > stopAt` bug)** — what observable
  failure, if any, the guarded post-condition produces once the bisection path is
  exercised. Origin: inherited tool page (weakness #8).
- **X6 (`bisectPrefix` "no faster")** — the author's own unreproduced finding that
  splitting at the next `/` "did not [enhance performance]." Falsifiable
  independently of the author's claim (note: no CLI flag — it is library-internal,
  so an API driver is required; report §10 Q7). Origin: inherited tool page
  (Mechanism).
- **N1 (throughput numbers)** — none benchmarked; all author self-reports.
  Per-source: **~20K items/s** (GenUI accelerators page); **~35K items/s peak**,
  **"5–50×"**, and **9 GB/s** copy (author Medium post); an unanchored **"~35K
  objects/sec"** summary-table figure; and a **conflicting "15–100×"** multiplier
  (landscape survey). Origin: inherited tool page (Claimed numbers).

Report §10 additionally frames: (1) **anonymous access is impossible** — supply
list-scoped credentials or exclude s3p from anonymous-bucket runs and note why
(the dominant open question); (2) the **`--list-concurrency` sweep**
`{1,8,25,50,100,200,400}` paired with `--max-sockets`; (5) **library-API memory
growth** (note only — CLI `ls` streams); (6) **node ≥22** (SDK v3 deprecation);
(7) `aggressive`/`usePrefixBisect` (internal-only, API-driver required).

## Known caveats carried forward

- **The version findings are documentary/observed, not run-settled.** H-Auth is
  the only run-settled (wrapper-receipt) finding. The smoked **3.7.2** label is
  **caller-supplied** (`run.meta`); V1/V2/V3 rest on `[SRC]`/`[3P]`/`[OBS]`
  inspection and a direct `docker run`, **not** a `smoke-run.sh` receipt — they
  do not promote past `VERIFIED: no` (codex round 1, finding 1). The benchmark
  phase should pin one coherent version (recommend **3.7.2** or npm `latest` at
  the time) and record that git tags lag npm.
- **Two counter caveats.** `--max-list-requests` is a **soft** cap (checked
  `>=` *before* each `+= 2`, so it can overshoot by up to one pair) [SRC
  S3Comprehensions.caf:362,378]; and `requests`/`listRequests` counts **logical**
  `s3.list` operations, not underlying HTTP attempts, so SDK-level retries are
  invisible to it [SRC S3Comprehensions.caf:378]. Neither is a wire-cost counter.
  See `mechanism.md`.
- **Edge-case fidelity is deferred.** `EDGE_BUCKET=none`: unicode/weird-key and
  size+ETag checks (the ones that would exercise W6/W7's character-set boundary)
  were not run. `normalize.sh` `ls-raw`/`ls`/`ls-long` paths are validated only
  against synthetic fixtures under `receipts/smoke/_adapter/` (no live listing
  was possible — auth-blocked). `ls --long` is a lossy, non-verification mode
  (human-rounded size). See `running.md`.

## Cross-cutting claims naming s3p

`docs/open-questions.md` names s3p in four places (bisects to a computed
midpoint via synthetic `StartAfter`; "best published parallel-list rate ~35K
items/s"; Node language tier; legacy Python `s3p` excluded as distinct from
`generalui/s3p`). All are consistent with groundwork's findings, none are edited
here — that document is another page's scope. `research/reconciliation.md` §
"Claims about *other* tools / S3 itself" records what groundwork found, for the
orchestrator to route.

## Provenance

**Mixed provenance.** This page began as a secondhand, un-run hypothesis sheet
(two private Swath research notes: a source-level read of `S3Keys.caf` and a
web-research pass supplying the throughput numbers and OOM/no-retry weaknesses).
A 2026-07-17 `groundwork/s3p` pass then pinned the source (v3.6.0,
`5a23b22e`), authored a container, ran three capability-probe smoke receipts,
and passed a separate cross-model review (15 findings, all resolved). The
firsthand strand supplies the summary, the H-Auth/V1/V2/V3 rows, the corrected
License/Testability/alphabet/code-anchor facts, the two counter caveats, **and
the additional source details now carried in X4** (disjoint per-node kept-sets),
**W4** (CLI streams vs library accumulates; #23 unread/unattributed), **W5** (SDK
v3 defaults not disabled), **W7** (native `<` runs before the throw), **and W8**
(the guarded post-condition assertion) — these are products of the fresh source
read, not the inherited notes. The inherited hypothesis phrasing (the throughput
numbers, the eight weakness framings, the mechanism sketch) remains secondhand
and `VERIFIED: no`. The full source-and-run account and the row-by-row
reconciliation are `research/report.md` and
`research/reconciliation.md`; this tool page was **not** rewritten into them — it
stays the hypothesis sheet.

## Receipts

Committed under `receipts/smoke/` (s3p 3.7.2 image
`s3p@sha256:622d7ec0e110…573894b91`, arm64; bucket `noaa-normals-pds` @
2026-07-17 snapshot, manifest sha256 `c78a827…992adb`, 148,917 keys):

- `_capability/anon-ls/` — anonymous `ls` blocked, `CredentialsProviderError`
  (exit 1). Settles **H-Auth**.
- `_capability/anon-ls-raw/` — anonymous `ls --raw` blocked, same error.
- `_capability/anon-summarize/` — anonymous `summarize` blocked, same error (a
  genuinely different subcommand; command-independent block).
- `_build/build-notes.md` — image build, live `--version`/`--help`, the v3.6.0
  `colors` unstartable finding, arch matrix. **[OBS]**, not a wrapper receipt.
- `_adapter/` — `normalize.sh` fixture validation (`ls-raw`/`ls`/`ls-long`/
  `summarize`/empty) with committed `*.expected.tsv` and a `check.sh`; no live
  listing was possible (auth-blocked).

No `verify-listing.sh` run result exists: no mode produced a listing to verify.
Edge-case fidelity checks are deferred (`EDGE_BUCKET=none`). See
[`running.md`](../docs/running.md) for exact invocations and reproduction.
