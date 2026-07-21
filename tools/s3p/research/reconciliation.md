# s3p — Stage D reconciliation

Walks **every** inherited claim in `tools/s3p/README.md` (the secondhand
hypothesis sheet) against this phase's independent work
(`research/report.md` + smoke receipts). Verdicts:

- **Corroborated** — independent work found the same (evidence stated).
- **Contradicted** — found otherwise (both sides + evidence).
- **Unaddressed** — not touched this phase; stays an open hypothesis.
- **Settled by smoke run** — a committed receipt genuinely decides it.

**Promotion rule honored**: source reading never promotes a claim past
`VERIFIED: no`; only a committed receipt does, and only at the scope it covers
(version X, invocation Y, the registered smoke bucket at its snapshot size).
Mechanism claims below are Corroborated **by source reading** — that is not a
promotion and they remain `VERIFIED: no`.

Source pin for every `[SRC]`: `generalui/s3p` @ git tag **v3.6.0**, SHA
`5a23b22e3f551a12de278491eebea5eb6d952eff`.

## Headline

The dossier's central open question — *"whether its listing behavior can be
isolated and timed at all"* (Testability; What-to-verify #1) — is **answered,
and the answer reframes the page**: listing **is** cleanly separable (`ls` and
`summarize` are first-class, list-only, non-mutating subcommands — not a
copy side-effect). But a blocker the dossier never anticipated dominates: **s3p
has no anonymous/unsigned request path**, and under `CREDS=none` every listing
mode is blocked at AWS-SDK credential resolution before any LIST completes
[Settled by smoke run — receipts/smoke/_capability/anon-ls{,-raw}/]. So s3p
cannot be timed against a public bucket at all without real credentials — an
owner routing decision, not a tuning detail.

## Metadata / testability

| # | Inherited claim | Verdict | Evidence |
|---|---|---|---|
| M1 | Repo `github.com/generalui/s3p` | Corroborated | canonical home; latest git tag v3.6.0, npm `latest` 3.7.2 [SRC clone; 3P npm] |
| M2 | Language JS / CaffeineScript (`.caf`) | Corroborated | [SRC source tree, package.json @ 5a23b22e] |
| M3 | **License MIT** | **Contradicted** | Actual **ISC** [SRC LICENSE.md "ISC License (ISC) Copyright 2020, GenUI"; package.json `"license":"ISC"` @ 5a23b22e]. Editorial correction applied to README. |
| M4 | Version reviewed "unconfirmed — pin one" | Settled | Pinned v3.6.0 / SHA `5a23b22e…` for source; smoked npm-latest **3.7.2** (see V1–V2) [report §1,§7] |
| M5 | Testability "believed straightforward: Node.js, npm install; open Q is whether listing is isolable/timable" | Contradicted (reframed) | Listing **is** isolable (`ls`/`summarize`) [SRC S3PCliCommands.caf; OBS live help]. Install is **not** straightforward for the tagged version (V2), and timing is blocked by auth (H-Auth). |

## Mechanism

| # | Inherited claim | Verdict | Evidence |
|---|---|---|---|
| X1 | Adaptively bisects an unknown keyspace (**"unique among the set"**) | Corroborated (mechanism) / Unaddressed (uniqueness) | Mechanism confirmed: `eachRecursive` + `getBisectKey` [SRC S3Comprehensions.caf:361-503; S3Keys.caf:46-90 @ 5a23b22e]. The **comparative** "unique among the set" cannot be established from s3p's source alone — I did not audit the other study tools this phase; that half stays an open comparison. |
| X2 | `getBisectKey`: first divergent char → ASCII-midpoint from fixed alphabet, zero sampling of real keys | Corroborated (source) | [SRC S3Keys.caf:46-90 @ 5a23b22e] |
| X3 | `eachRecursive` fires **two** full `ListObjectsV2` per split node; a side recurses only if its page returned a full 1000 | Corroborated (source) | two `pwp.queue -> s3.list` calls per node [SRC S3Comprehensions.caf:381-384]; `recurseLeft = leftCount >= limit` (limit=1000) [SRC :440-441,:244] |
| X4 | Adjacent LIST calls **overlap**; each 1000-page yields ~500 *new* keys, ~500 already seen (~50% waste) — a deliberate trade | Unaddressed (+ source nuance) | The two per-node calls keep **disjoint** sets (left filtered `Key<=middleKey`, right `Key<=stopAt`) [SRC :389-390], so "overlap" is redundant **raw** fetching in dense ranges, not a 50/50 kept-set overlap. The exact waste fraction is a scale/efficiency measurement — **not run** (auth-blocked), scale-dependent. Maps to report §10 Q3 (measure `requestsUsed` vs `ceil(N/1000)`). |
| X5 | LIFO worker pool, default `listConcurrency = 100` | Corroborated (source) | LIFO: `queue`→`push`, `_work`→`pop` [SRC PromiseWorkerPool.caf:30,40]; default 100 [SRC S3Comprehensions.caf:246] |
| X6 | Opt-in `bisectPrefix` splits at next `/`; author found it no faster; off by default | Corroborated, with correction | Behavior + author note confirmed [SRC S3Keys.caf:29-35,60-66; S3Comprehensions.caf:448]. **Correction**: it is **not user-opt-in** — there is no CLI flag; it is library-internal and auto-triggered when a right page returns 0 items [SRC S3Comprehensions.caf:448; absent from all `--help` @ 3.7.2]. |
| X7 | "Primary purpose is copying; listing may not be a standalone, separately invokable operation" | **Contradicted** | `ls` and `summarize` are standalone list-only subcommands using `listObjectsV2` [SRC S3PCliCommands.caf:11-39; S3PCli.caf:115-133; OBS live `help`]. The `ls` probe reached and issued real LIST calls before the auth failure [Settled by smoke run — _capability/anon-ls/]. |
| X8 | Alphabet size disputed: 94-char vs ~96-char | Settled (source) | It is **95** chars — printable ASCII space `0x20`…`~` `0x7E` inclusive (0x7E−0x20+1=95) [SRC S3Keys.caf:5 @ 5a23b22e]. Neither 94 nor 96. Editorial correction applied. |

## Claimed numbers (throughput)

| # | Inherited claim | Verdict | Evidence |
|---|---|---|---|
| N1 | ~20K / ~35K items/s; "5–50×"; a conflicting "15–100×"; 9 GB/s copy | Unaddressed | All author-self-reported, no third-party repro; **not benchmarked** (auth-blocked, and smoke produces no comparative numbers by design). My README read found the same lineage (README claims "almost 50,000 items/s as-of v3.5", "8–9 GB/s" copy) [DOC README.md] — still `VERIFIED: no`. |

## Claimed weaknesses (hypotheses)

| # | Inherited weakness | Verdict | Evidence |
|---|---|---|---|
| W1 | ~50% wasted LIST work | Unaddressed | Same as X4 — scale/efficiency, not run. Falsifiable via API-call count in the benchmark; s3p exposes a **logical** LIST counter (`--verbose` → `requests`, incremented `+= 2`/node before the call — not HTTP attempts) [SRC S3Comprehensions.caf:296,524,378]. |
| W2 | Blind ASCII-midpoint splits cost extra rounds on skewed keyspaces | Unaddressed | Benchmark-phase question; not run. |
| W3 | Single-core Node bottleneck; throughput plateaus at high concurrency | Corroborated (architecture) / Unaddressed (plateau) | Author states s3p "is still only a single-core NodeJS application" [DOC README.md]; single Node process confirmed [SRC]. The **plateau** is scale-dependent — not settleable at smoke scale. Ties to open-questions §2 (Node language tier). |
| W4 | OOM at ~100M objects (issue #23) | Unaddressed (+ source nuance) | Not run (cannot reach that scale; auth-blocked anyway). Source nuance: CLI `ls` **streams** each item (bounded) [SRC S3PCliCommands.caf:20-25]; the library `list()`/`ls()` API **accumulates** all keys [SRC S3P.caf:36-38]. I did **not** read issue #23 this phase, so I do **not** attribute the reported OOM to that path — the source shows only *that* the library API accumulates, not that it is #23's cause. Stays a hypothesis. |
| W5 | No LIST retry/backoff → a transient 503 crashes/corrupts the run | Contradicted (partial) / Unaddressed (runtime) | **Source-level correction**: s3p adds no retry of its own (`list` only `.tapCatch`→rethrow [SRC S3.caf:55-68]) but it does **not** disable the AWS SDK v3 client's default retries either [SRC S3.caf:26-29 — client built with no retry override], so retries are **not absent** as the claim assumes [INFERRED: SDK v3 default = standard mode, 3 attempts, backoff]. Whether that survives sustained throttling is **not run**. |
| W6 | Cannot bisect non-ASCII keys — throws at the alphabet boundary | Corroborated (source); still `VERIFIED: no` | `getKeyCharIndex` returns −1 for any char outside `supportedKeyChars`; `getBisectKey` throws `"Invalid character found in inputs"` [SRC S3Keys.caf:11,54-58 @ 5a23b22e]. Independent read reaches the dossier's own conclusion. **Not executed** — `EDGE_BUCKET=none`, edge checks **deferred**; becomes settled only when a non-ASCII bucket is listed. |
| W7 | UTF-16 code-unit ordering (native `<`) diverges from S3 unsigned-byte order on astral chars | Corroborated (source); **NOT subsumed** (corrects my earlier read) | Native `<`/`<=` used throughout, **including before the alphabet lookup**: `getBisectKey` opens `if startAfter < stopAt` [SRC S3Keys.caf:46] and `eachRecursive` guards on `startAfter >= stopAt` [SRC S3Comprehensions.caf:362] — native comparisons that run *before* the `charIndex < 0` throw [SRC S3Keys.caf:57-58]. So a boundary pair like BMP `U+E000` then astral `U+10000` (reversed in UTF-8) can make the comparison mis-decide a range empty and **return before** the throw. The divergence is reachable, **not** subsumed by W6. Observable effect Unaddressed (not run). |
| W8 | Author self-flags a bug: "bisectKey could be after stopAt" | Corroborated (source) + nuance | Comment present verbatim [SRC S3Keys.caf:36 @ 5a23b22e]. Nuance: `getBisectKey` guards it with a runtime post-condition assertion that **throws** `"Whoops! …"` unless `startAfter <= bisectKey <= stopAt` [SRC S3Keys.caf:87-88]. Observable failure Unaddressed (not run). |

## Code anchors (re-verified against the pinned checkout)

| Inherited anchor | Verdict |
|---|---|
| `S3Keys.caf` `getBisectKey` 46–90 | Corroborated [SRC @ 5a23b22e] |
| `S3Keys.caf:5` `supportedKeyChars` | Corroborated (alphabet = 95 chars; see X8) |
| `S3Keys.caf:56-57` indexOf throw | Corrected → lookup `getKeyCharIndex` at :11; the `charIndex < 0` throw is at **:57-58** [SRC] |
| `S3Keys.caf:46,87` native `<` | Corroborated (`<` at :46, `<=` at :87) [SRC] |
| `S3Keys.caf:36` author bug comment | Corroborated [SRC] |
| `eachRecursive` "believed S3Comprehensions.caf, line range not captured" | Corrected → **S3Comprehensions.caf:361-503** [SRC] |
| LIFO pool + `listConcurrency=100` "no file:line" | Corrected → pool `PromiseWorkerPool.caf:26-48`; default 100 at `S3Comprehensions.caf:246` [SRC] |

## New findings (not in the dossier)

| # | Finding | Status | Evidence |
|---|---|---|---|
| V1 | Git tags/GitHub releases **lag npm**: latest tag v3.6.0 (2024); `master` HEAD is 16 untagged commits ahead at **version 3.6.1** [SRC `git show d8d6dcac:package.json` → `"version":"3.6.1"`]; npm `latest` = **3.7.2**, whose version has **no corresponding commit in the cloned git history at all** | Settled | [SRC `git rev-list v3.6.0..master --count`=16; `git show d8d6dcac:package.json`; 3P `npm view s3p dist-tags`/`versions`] |
| V2 | The **published v3.6.0 artifact cannot start**: `s3p version` → `Cannot find module 'colors'`; `colors` is `require`d at runtime but absent from v3.6.0's published dependency closure (present in the repo lockfile). Fixed in **3.6.1** (commit `5610411` "fixed deps (colors)") | **[OBS]** (direct container run — **not** a wrapper receipt) | Observed via `docker run` of an `s3p@3.6.0` image; documented in `receipts/smoke/_build/build-notes.md` (a build note, not a `smoke-run.sh` receipt); [SRC package.json @ 5a23b22e has no `colors` dep]. Scoped to `npm i -g s3p@3.6.0` on node:20. Not promoted past `VERIFIED: no` in the dossier for lack of a wrapper receipt. |
| H-Auth | **No anonymous/unsigned access path** (no `--no-sign-request`, no credentials/signer hook). With `CREDS=none`, all listing modes blocked at credential resolution | Settled by run | `S3Client` built with only `{region,endpoint?,requestHandler?,useAccelerateEndpoint,forcePathStyle}` [SRC S3.caf:26-29 @ 5a23b22e; confirmed in 3.7.2 build; OBS live help]; empirical `CredentialsProviderError` across **three** wrapper receipts spanning two subcommands — `ls`, `ls --raw`, and `summarize` [RUN _capability/anon-ls/, anon-ls-raw/, anon-summarize/] — scoped to s3p 3.7.2, anonymous mode, the registered smoke bucket. (`ls --long` blocked-by-inheritance, same code path, not re-run.) |
| V3 | 3.7.x adds `--max-sockets`, `--endpoint`/`S3_ENDPOINT`, `--group-by`, and a mutating `delete` command — none present in v3.6.0 source | **[OBS]** (live help + installed 3.7.2 build — not a wrapper receipt) | [OBS live `help`/`ls --help` @ 3.7.2; SRC S3.js build @ 3.7.2] |

## Claims about *other* tools / S3 itself — routed to the orchestrator (not edited here)

`docs/open-questions.md` names s3p in four places; all are consistent with
my findings, none edited by me (not my page):
- **Preamble / §StartAfter** — "S3P bisects to a computed midpoint" via synthetic
  `StartAfter` — **Corroborated** [SRC S3Keys.caf, S3Comprehensions.caf].
- **§1** — "best published parallel-list rate (S3P ~35K items/s)" — an unverified
  author self-report (my N1); left as `VERIFIED: no` context.
- **§2 language tier** — lists S3P under **Node**; single JVM entrant (swath) —
  consistent with W3 [DOC README.md].
- **§ Excluded tools** — legacy Python `s3p` is distinct from `generalui/s3p` —
  **Corroborated**; I pinned only `generalui/s3p` per the card.
