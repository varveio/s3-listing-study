# s3p — Stage E adversarial cross-model review

> **Link normalization (2026-07-17, pre-publication).** This file is immutable
> evidence: its findings, verdicts, wording, and every link *label* are
> untouched. Only broken link *targets* were repaired. They pointed at absolute
> paths inside the ephemeral worktree this review ran in
> (`<checkout>/...`), so every link was broken and named a checkout
> no reader can have. In-repo targets are now repo-relative; targets that
> pointed into the pinned upstream *source* checkout (never part of this repo)
> cannot resolve, so the dead hyperlink was removed and its visible text kept
> verbatim. No label text changed — verified by diffing this file against its
> pre-normalization revision with link targets masked out.

Reviewer: Codex CLI (`gpt-5.6-sol`, `model_reasoning_effort=xhigh`), read-only,
run 2026-07-17 over the committed groundwork (report, reconciliation, receipts,
`run.sh`, `normalize.sh`, `Dockerfile`, and the dossier diff), with the pinned
source checkout at `5a23b22e` available for anchor re-verification. One round;
all 15 findings addressed below (fix or reasoned disagreement). The review ran
to completion inside the repo-phase budget (source tree is small), so no scope
was dropped.

---

## Codex findings (verbatim)

## Findings

1. **High — Runtime version and build findings are not receipt-backed.** [`build-notes.md:18`](../receipts/smoke/_build/build-notes.md) paraphrases version/help output and the 3.6.0 failure but includes no raw output, exit codes, package integrity, or hashes. Both [`run.meta:27`](../receipts/smoke/_capability/anon-ls/run.meta) files explicitly say the 3.7.2 version was caller-supplied. Consequently, V2/V3 in [`reconciliation.md:92`](reconciliation.md) and the dossier’s “published v3.6.0 is unstartable” promotion are unsupported as run-settled findings. There are also unresolved contradictions: [`report.md:25`](report.md) says local `master` contains 3.7.x version bumps, but `master` at `d8d6dcac` contains version 3.6.1 and no 3.7 commit; the [official npm page](https://www.npmjs.com/package/s3p) presently reports 3.6.1, not 3.7.2.

2. **High — “No anonymous access across every listing mode” exceeds receipt scope.** The two probes in [`report.md:329`](report.md) are both the same `ls` subcommand; `--raw` only changes formatting. They prove those two credential-starved invocations fail, not that `summarize` or every other listing command fails identically, nor that 3.7.2 has no alternate unsigned configuration. The v3.6.0 source supports a source-level conclusion, but the 3.7.2 help/source payload is absent. Therefore [`reconciliation.md:94`](reconciliation.md) and [`README.md:24`](tool-page.md) incorrectly promote a combined source/inference claim as “Settled by run.”

3. **High — `normalize.sh` violates its raw-key contract for supported keys.** [`normalize.sh:4`](../adapter/normalize.sh) promises raw key bytes without re-encoding, but `jq @tsv` at [line 52](../adapter/normalize.sh) escapes backslashes, tabs, and newlines. Backslash is inside s3p’s supported 95-character alphabet, so this is not limited to deferred Unicode cases. The `ls-long` parser at [line 43](../adapter/normalize.sh) also collapses repeated/leading spaces in keys rather than rejecting an unrepresentable record. Either path can silently produce the wrong key.

4. **Medium — `_adapter/` is not a receipt for the claimed validation.** It contains only three input fixtures—no invocation, expected output, actual output, exit status, or hash binding. There is no `summarize` fixture at all. Thus the “paths verified” conclusion in [`report.md:355`](report.md) and [`README.md:272`](tool-page.md) cannot be reproduced from the staged evidence.

5. **Medium — Empty `ls` results fail normalization.** With `set -o pipefail`, `grep -v '^$'` at [`normalize.sh:41`](../adapter/normalize.sh) exits 1 when input is empty, making a valid empty bucket/prefix an adapter failure instead of successful empty output.

6. **Medium — The Dockerfile does not pin the installed dependency closure.** [`Dockerfile:27`](../build/Dockerfile) pins only `s3p@3.7.2`; global npm installation resolves its ranged dependencies at build time without a lockfile or tarball integrity. Rebuilding later—or building the proposed amd64 image—can therefore produce different SDK/auth/retry behavior despite the “exact pinned version” claim.

7. **Medium — Request-budget and API-call instrumentation claims overstate the source.** [`report.md:159`](report.md) calls `--max-list-requests` a hard cap, but `S3Comprehensions.caf:362` checks the budget before adding requests in pairs at line 378; an odd limit can be exceeded by one. Likewise, `requestsUsed` counts logical `s3.list` invocations before execution, not underlying HTTP attempts made by SDK retries, so calling it a “genuine API-call counter” in [`report.md:245`](report.md) is unsafe for cost/efficiency measurement. The cited `:288,525` anchors in receipts also miss the actual fields at lines 296 and 524.

8. **Medium — The report reverses range inclusivity.** [`report.md:44`](report.md) describes `[startAfter, stopAt)`, but `StartAfter` is exclusive and the implementation retains `Key <= stopAt` at `S3Comprehensions.caf:389`. The implemented interval is `(startAfter, stopAt]`. This matters for manual restart and completeness boundaries.

9. **Medium — W7’s “non-ASCII throws first” conclusion is false.** [`reconciliation.md:73`](reconciliation.md) says astral-character ordering divergence is unreachable because W6 always throws first. But `S3Keys.caf:46` performs native UTF-16 ordering before character lookup. For bounds such as BMP `U+E000` followed in UTF-8 order by `U+10000`, JavaScript reverses the order and returns before the `charIndex < 0` throw. W7 is not subsumed as claimed.

10. **Medium — The receipts do not demonstrate simultaneous LIST execution.** [`report.md:61`](report.md), line 347, and [`anon-ls/receipt.md:113`](../receipts/smoke/_capability/anon-ls/receipt.md) promote two error records with different `StartAfter` values into smoke-confirmed concurrency. The logs contain no timestamps or overlap measurement, and credential resolution failed before an HTTP LIST was sent. `Promise.all` in source supports concurrent scheduling at concurrency 8; the run payload does not independently confirm it.

11. **Medium — `summarize` memory is not always fixed-size.** [`report.md:124`](report.md) claims a small fixed summary object without an evidence label. When `--summarize-folders` is enabled, `S3P.caf:75` builds a nested folder object whose size grows with distinct paths. This also contradicts the report’s own “optional per-folder” mode description.

12. **Medium — Cross-tool uniqueness is marked corroborated by evidence that cannot establish it.** [`reconciliation.md:48`](reconciliation.md) marks “unique among the set” corroborated from s3p source, and [`report.md:364`](report.md) says almost every other S3 tool lists serially. The cited s3p source and README establish s3p’s mechanism only, not what every other study subject does.

13. **Medium — The OOM reconciliation attaches the failure to an unsupported path.** [`reconciliation.md:70`](reconciliation.md) calls the library accumulator the “likelier OOM path,” but [issue #23](https://github.com/generalui/s3p/issues/23) reports a CLI `sync` invocation over roughly 100 million objects, not `list()`/`ls()` library use. The source citation proves that the library API accumulates; it does not explain the cited failure.

14. **Low — The README does not support the attributed quickstart block.** [`report.md:175`](report.md) labels three commands `[DOC README.md]`, but the pinned README contains only general help instructions and an `ls --api-example` example—not those `ls`, `--long`, region, and `summarize` invocations. The commands are derivable from source/help, but the attached citation is wrong.

15. **Low — The dossier remains internally contradictory after promotion.** [`README.md:19`](tool-page.md) says listing separability is answered, while lines 67–71 still claim listing may not be separately invokable, lines 75–83 still call copy the primary interface and request CLI discovery, and lines 211–229 still list separability/full CLI surface as unresolved. Calling the page a hypothesis sheet does not identify those specific claims inline as superseded.
---

## Resolutions

| # | Sev | Disposition | What changed |
| --- | --- | --- | --- |
| 1 | High | **Fixed (label + fact)** | The `_build/build-notes.md` runs (version/help, the v3.6.0 `colors` failure) were direct `docker run`s, **not** `smoke-run.sh` receipts. Relabeled everywhere to **[OBS]**, explicitly "not a wrapper receipt / not a `VERIFIED: no` promotion" (reconciliation V2/V3; README run-settled callout). **Factual correction**: `master@d8d6dca` is version **3.6.1**, not 3.7.x — my "3.7.x in master" was wrong; corrected in report §1 and reconciliation V1 to the stronger, true statement (npm's 3.7.2 has **no** corresponding git commit). Registry `npm view` (authoritative) gives `latest=3.7.2`; kept, with the master=3.6.1 fact beside it. |
| 2 | High | **Fixed (ran it)** | Added a real wrapper receipt `_capability/anon-summarize/` — `summarize` (a genuinely different subcommand) fails with the same `CredentialsProviderError` (exit 1). H-Auth now rests on three receipts spanning two subcommands; `ls --long` marked blocked-by-inheritance, not over-claimed. Report §8 table + reconciliation H-Auth updated. |
| 3 | High | **Fixed (real bug)** | `normalize.sh` `ls-raw` no longer uses `jq @tsv` (which escapes backslash — a legal char in s3p's 95-char alphabet); switched to raw `jq -j` with explicit tab/newline. Verified with a `weird/back\slash-key` fixture surviving as one backslash. `ls-long` documented as a lossy, non-verification mode. |
| 4 | Med | **Fixed** | `_adapter/` now carries committed `*.expected.tsv`, a `summarize` fixture, an empty-input fixture, a `check.sh` that diffs each mode, and a `README.md`. "Paths verified" is now reproducible (`./check.sh` → all PASS). |
| 5 | Med | **Fixed (real bug)** | Empty-input `pipefail` failure removed: `grep -v '^$'` replaced with `awk 'length'` (prints non-empty lines, exits 0 on empty). Verified: empty `ls` input → empty output, exit 0. |
| 6 | Med | **Reasoned disagreement + noted** | The image **digest** is the run's identity per BRIEF § Stage B ("pin what can be pinned … the digest is the run's identity; the Dockerfile is the best-effort recipe"); full npm-closure pinning is not attainable from the published tarball and is out of scope for smoke. Recorded as a benchmark-phase improvement (generate a lockfile / `npm ci`) in report §7/§10. |
| 7 | Med | **Fixed** | (a) `--max-list-requests` reworded from "hard cap" to a soft cap that can overshoot by one pair (check at `:362` precedes `+= 2` at `:378`). (b) `requests`/`listRequests` reworded to a **logical** LIST-operation counter (pre-call, excludes SDK HTTP retries), not a wire-cost counter. (c) Anchors corrected `:288→:296`, `:525→:524` (report §5, receipts, reconciliation W1). |
| 8 | Med | **Fixed** | Report §2 interval corrected from `[startAfter, stopAt)` to `(startAfter, stopAt]` (StartAfter exclusive; trim keeps `Key <= stopAt`). |
| 9 | Med | **Fixed (agreed)** | Reconciliation W7 changed from "subsumed by W6" to **"NOT subsumed"**: native `<` runs at `S3Keys.caf:46` and `S3Comprehensions.caf:362` **before** the alphabet throw, so a BMP/astral boundary pair can mis-order and return before throwing. |
| 10 | Med | **Fixed (scoped down)** | Report §2/§8 and both `_capability/*` receipt notes reworded: the two distinct-`StartAfter` error records show the bisection **scheduled** two ranges, **not** simultaneous wire execution (both aborted at credential resolution before any HTTP LIST; no timestamps). |
| 11 | Med | **Fixed** | Report §2 now notes `summarize` is fixed-size **only by default**; `--summarize-folders` builds a nested per-folder object growing with distinct paths [SRC S3P.caf:75-83]. |
| 12 | Med | **Fixed (scoped down)** | X1 split into Corroborated (mechanism) / Unaddressed (the comparative "unique among the set"); report §9 "almost every other tool lists serially" reattributed to the README's own framing, not my finding — I did not audit the other tools. |
| 13 | Med | **Fixed** | Report §6 and reconciliation W4 no longer call the library accumulator the "likelier OOM path"; I did not read issue #23 this phase, so the source establishes only *that* the API accumulates, not #23's cause. |
| 14 | Low | **Fixed** | Report §4 quickstart relabeled from `[DOC README.md]` to `[OBS live help / SRC S3PCli.caf]` — composed from the CLI surface, not quoted from the README. |
| 15 | Low | **Fixed** | Added inline **[SUPERSEDED]/[ANSWERED]** markers on the dossier's copy-is-primary mechanism bullet and "What to verify first" #1, pointing at the Testability row and reconciliation X7, so the page is no longer self-contradictory after the promotion. |

**No round two.** All High findings were fixable (two were real code/label bugs,
one closed by an added run); no unresolved severe finding remains. The single
reasoned disagreement (#6) is a methodology point settled by the BRIEF, not a
defect.

---

## Consolidation review

Reviewer: Codex CLI (`gpt-5.6-sol`, `model_reasoning_effort=xhigh`), read-only,
run 2026-07-17 over the consolidation diff (`git diff HEAD -- tools/s3p/README.md
tools/s3p/mechanism.md tools/s3p/running.md`) against `research/reconciliation.md`
and the pre-consolidation README (`git show HEAD:tools/s3p/README.md`). One round;
all 9 findings (8 Important, 1 Minor) addressed below (fix or reasoned
disagreement).

### Codex findings (verbatim)

## Findings

- **Important — source-only changes are promoted to `CORRECTED` without receipts.** The ledger says source reading cannot promote past `VERIFIED: no` ([README.md:88](tool-page.md), [reconciliation.md:12](reconciliation.md)), but X6, X8, W5, and three code-anchor changes receive formal `CORRECTED` status solely from source/help/inference ([README.md:113](tool-page.md), [README.md:115](tool-page.md), [README.md:131](tool-page.md), [README.md:140](tool-page.md)).

- **Important — W4 is silently narrowed from an uncharacterized OOM report to a library-only problem.** Reconciliation explicitly says issue #23 was unread and the accumulating library API cannot be attributed as its cause ([reconciliation.md:70](reconciliation.md)); the README preserves that caution ([README.md:130](tool-page.md)). `mechanism.md` nevertheless declares CLI `ls` “not affected by” W4 and routes W4 to library memory growth ([mechanism.md:175](../docs/mechanism.md), [mechanism.md:222](../docs/mechanism.md)). Streaming source structure does not receipt-prove that the CLI cannot OOM.

- **Important — the “every inherited claim” ledger is incomplete.** The pre-consolidation `Claimed strengths` asserted that the full-page heuristic “requires no tuning” and that eager double-LIST lowers discovery latency (`HEAD:tools/s3p/README.md:125-132`). The new X3 destination carries only the mechanics ([README.md:110](tool-page.md), [mechanism.md:30](../docs/mechanism.md)); neither strength has a reconciliation row or destination, despite the exhaustiveness claim at [README.md:85](tool-page.md).

- **Important — W6 loses the inherited opposing hypothesis and its provenance.** The old page deliberately preserved the secondhand claim that s3p worked on “literally any keyspace including ones with non-printable bytes,” alongside the source-only correction (`HEAD:tools/s3p/README.md:159-169`). The new W6 ledger and work queue retain only the correction/throw-or-mispartition hypothesis ([README.md:132](tool-page.md), [README.md:193](tool-page.md)).

- **Important — the provenance summary misclassifies new groundwork claims as inherited.** It says the firsthand strand supplied only a limited enumerated set and that “everything else remains inherited” ([README.md:247](tool-page.md)). That excludes newly derived source nuances now carried in X4, W4, W5, W7, and W8 ([README.md:111](tool-page.md), [README.md:130](tool-page.md), [README.md:131](tool-page.md), [README.md:133](tool-page.md), [README.md:134](tool-page.md)), which reconciliation identifies as products of the independent source read ([reconciliation.md:51](reconciliation.md), [reconciliation.md:70](reconciliation.md)).

- **Important — N1’s per-claim provenance is collapsed.** The old dossier separately tied 20K to GenUI, 35K/5–50×/9 GB/s to Medium, and distinguished the unanchored summary and landscape-survey multiplier (`HEAD:tools/s3p/README.md:103-123`). The new row combines all numbers under `[DOC README.md]`, while the work queue says only “Origin: inherited dossier” ([README.md:121](tool-page.md), [README.md:203](tool-page.md)).

- **Important — the benchmark queue is neither complete nor verbatim as claimed.** It promises every unrun claim with original wording/provenance ([README.md:164](tool-page.md)), but omits X6’s unreproduced “no faster” performance hypothesis despite retaining it in the ledger ([README.md:113](tool-page.md)); changes W5 from failure on a transient 503 to survival under sustained throttling ([README.md:189](tool-page.md), versus `HEAD:tools/s3p/README.md:155-158`); and softens W3 from “well below” multi-core throughput to merely “below” ([README.md:181](tool-page.md), versus `HEAD:tools/s3p/README.md:146-150`).

- **Important — the build note has contradictory evidence labels.** `[RUN]` is defined as a committed wrapper smoke run ([mechanism.md:5](../docs/mechanism.md)), yet the image digest cites `_build/build-notes.md` as `[RUN]` ([running.md:21](../docs/running.md)); the same page later calls that build note `[OBS]`, explicitly not a wrapper receipt ([running.md:171](../docs/running.md)).

- **Minor — X1 is asserted as fact in the lead while its ledger status says the comparison was never audited.** The opening calls s3p the survey’s “only key-space bisector,” then immediately admits that uniqueness was not independently audited ([README.md:3](tool-page.md)); X1 is formally `VERIFIED: no` for that comparative half ([README.md:108](tool-page.md)).

### Resolutions

| # | Sev | Disposition | What changed |
| --- | --- | --- | --- |
| 1 | Important | **Reasoned agreement + clarify (statuses unchanged)** | The statuses are correct per the brief's own routing (`CORRECTED` = "both-sides", `CONFIRMED` = "receipt-backed") and match the owner-adopted **s5cmd** ledger, which likewise marks source-only documentary fixes (e.g. the `command/ls.go` file-attribution, the 1,000-key page-size) `CORRECTED`. The apparent contradiction was **presentation**: the status-definitions paragraph paired "CORRECTED" with "source reading never promotes" without distinguishing the two. Added an explicit clause: `CORRECTED` flags a documentary/factual mislabel (source *can* establish this and it asserts no runtime behavior); "promotes" means lifting an open hypothesis to `CONFIRMED`, which needs a run. X6/X8/W5/code-anchor statuses kept. |
| 2 | Important | **Fixed** | `mechanism.md` § Memory model no longer says CLI `ls` is "not affected by" the OOM report: reworded to a **source-structure** observation (the CLI path holds no in-memory key buffer), explicitly **not** a runtime OOM proof and **not** locating W4/#23 on either path (issue #23 unread/unattributed). Failure-surface W4 line reworded the same way. |
| 3 | Important | **Fixed** | Ledger intro changed from "Every claim inherited from the original dossier" to "Every claim in `research/reconciliation.md`". Added a footer note mapping the pre-consolidation **"Claimed strengths"** (zero-config bisection; full-page heuristic requires **no tuning**; eager double-LIST trades API calls for **lower discovery latency**) to X1/X3/X4, carried there and in `mechanism.md`, each staying `VERIFIED: no`. |
| 4 | Important | **Fixed** | Restored the opposing secondhand claim ("works on literally any keyspace including ones with non-printable bytes") in **both** ledger W6 and the Open-hypotheses W6 — both the claim and the source correction are recorded, as the original page had them. |
| 5 | Important | **Fixed** | Provenance summary's firsthand enumeration expanded to name the independent source nuances now carried in **X4** (disjoint kept-sets), **W4** (streams vs accumulates; #23 unread), **W5** (SDK defaults not disabled), **W7** (native `<` before the throw), **W8** (post-condition assertion). "Everything else inherited" reworded to name only the inherited hypothesis phrasing. |
| 6 | Important | **Fixed** | N1 per-source provenance restored in both the ledger row and the Open-hypotheses entry: **~20K** (GenUI accelerators page); **~35K/5–50×/9 GB/s** (author Medium post, URL cited); the unanchored **"~35K objects/sec"** summary table; and the **conflicting "15–100×"** landscape multiplier (both multipliers trace to the author, neither reproduced). |
| 7 | Important | **Fixed** | (a) Added **X6's "no faster"** unreproduced hypothesis to the Open-hypotheses queue (noting it is library-internal, no CLI flag). (b) **W5** restored to the original verbatim framing — a transient 503/SlowDown "should **crash or corrupt** the run rather than being absorbed" — in ledger and queue. (c) **W3** restored to "**well below**" (from "below") in ledger and queue. |
| 8 | Important | **Fixed** | `running.md` built-image-digest line relabeled `[RUN]` → `[OBS]` ("a build note, not a wrapper `smoke-run.sh` receipt"), consistent with `[RUN]` = committed wrapper smoke run. The adapter-validation line was likewise relabeled `[RUN]` → `[OBS]` (a committed fixture `check.sh`, not a live wrapper run). |
| 9 | Minor | **Fixed** | The lead hedges the "only key-space bisector" claim inline: "By this study's reading … not independently audited this phase; the *mechanism* is source-confirmed, the *comparison* is not." |

**No round two.** No finding required a downgrade of a receipt-backed status; the
one reasoned disagreement (#1) is a presentation clarification that leaves the
brief-mandated routing intact. The other eight were fidelity/label fixes to the
new pages; `research/` and `receipts/` were not modified except this appended
review.
