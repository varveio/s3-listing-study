# Stage E — adversarial cross-model review (codex) + resolutions

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

Reviewer: `codex exec -m gpt-5.6-sol -c model_reasoning_effort=xhigh
--sandbox read-only`, scoped to `tools/s3kor` artifacts with the pinned checkout
(`<sources>/s3kor` @ `844fe3d`) and `<data>` added
read-only. Round 1 completed in full (~12 min); it covered report.md,
reconciliation.md, both capability receipts, run.sh, normalize.sh, the
Dockerfile, and the uncommitted dossier diff, and re-verified `[SRC]` anchors by
targeted lookup. No stall; the budget-guard fallback (drop source dirs) was not
needed.

Findings are reproduced **verbatim** below, each followed by my resolution.
Severity is codex's. Where I disagree I say so; everything else was fixed.

---

## Round 1 — findings (verbatim) and resolutions

**High — The bounded-memory/backpressure model is false.** [report.md:86] claims
the 50-page channel backpressures the paginator and bounds retained pages. The
callback instead spawns a goroutine and immediately returns ([list.go:187]);
blocked senders accumulate while pagination continues. This also contradicts the
later unbounded-goroutine warning at [report.md:239].

> **RESOLVED (accepted).** Correct — the callback is spawn-and-return, so there
> is no backpressure. Rewrote the memory-model paragraph (§ 2) to state that
> listing is streaming but **not** back-pressured, that page goroutines
> accumulate if the single printer lags, and that peak memory can grow with
> in-flight pages rather than being bounded to ~50 — explicitly flagged as a
> correction of the prior wrong claim, and reconciled with the § 6 unbounded-
> goroutine caveat. Labeled `[INFERRED]`, scale-OOM deferred to benchmark.

**High — "Set-correctness is unaffected" overlooks two source-visible concurrency
races.** [report.md:78] limits the risk to ordering. But `List` starts the
printer, then replaces the channel it may already be ranging over, and registers
the printer with the wait group only after listing returns ([list.go:216],
[list.go:247]). The printer can bind to the abandoned channel and deadlock, or
call `Done` before the corresponding `Add` and panic.

> **RESOLVED (accepted).** Verified in source: `List` starts
> `go bl.printAllObjects` (list.go:218) *before* reassigning `bl.objects`
> (list.go:225), with `wg.Add(1)` sequenced after the listing call. Retracted
> "set-correctness is unaffected" in § 2 and replaced it with the concurrency-
> race description (abandoned-channel hang or `Done`-before-`Add` panic),
> labeled `[SRC][INFERRED]`, and stated I could not observe runtime behavior
> because every listing was credential-blocked. Also surfaced in § 9.

**High — Current `CONFIRMED`/"Settled by smoke run" promotions have no committed
receipt.** The dossier promotes at [README.md:72] and [README.md:128], while
reconciliation defines/uses committed-receipt settlement. Both receipt
directories are presently untracked, not committed or staged; committing the
tracked dossier edit alone would publish unsupported promotions.

> **RESOLVED (accepted — process).** This is a staging-order hazard, not a
> content error: the receipts existed but were untracked. Fixed by committing
> **all** of `tools/s3kor/` — including `receipts/smoke/` — in the same commit
> as the dossier edits, so every cited receipt is tracked alongside the
> promotion that cites it. Verified with `git status`/`git show --stat` before
> finalizing (see commit SHA in the handoff).

**Medium — The dossier contradicts its own provenance and the reconciliation.**
It still says nobody read s3kor's listing source, serial listing remains
analogy-only, and the result should collapse into s5cmd ([README.md:26/60/95]),
while provenance says serial listing is now source-confirmed ([README.md:108]),
and reconciliation claims those mechanism/weakness rows were updated when the
diff shows they were not ([reconciliation.md:108]).

> **RESOLVED (accepted).** Two fixes: (1) added an **Update callout** at the top
> of the dossier's Mechanism section noting the serial claim is now source-
> confirmed and does not collapse into s5cmd, while **keeping the original
> hypothesis prose verbatim** as the pre-registration record (the brief forbids
> rewriting the dossier into the report). (2) Corrected the reconciliation's
> "Dossier edits made" bullet so it accurately describes what was done (a
> callout + verbatim-preserved rows with verdicts living in reconciliation.md),
> rather than claiming inline row edits that were not made.

**Medium — The generic "no credentials → startup panic" claim exceeds the
receipts.** The runs specifically set a nonempty `AWS_WEB_IDENTITY_TOKEN_FILE`
while emptying `AWS_ROLE_ARN`, manufacturing a session-configuration error
([report.md:187/232/370], [reconciliation.md:81]). The receipts prove the
harness-specific panic, not generic credential absence behavior.

> **RESOLVED (accepted).** Rescoped every generalization: the **panic** is
> specific to the session-**build** error the harness env induces (web-identity
> token file set + role ARN emptied); a bare empty-credential env would instead
> fail at **request** time. Fixed in § 4 (footgun), § 6 (failure surface), § 9
> (notable findings), the § 8 scope note (already present), and reconciliation
> net-new #2 + X4/W2. The root capability finding — no unsigned listing path —
> is stated independently of the panic and stands.

**Medium — The claimed source-wide anonymous-credential sweep is wrong.**
[report.md:108], [README.md:73], [reconciliation.md:75] say the `checkBucket`
use is the only/lone `AnonymousCredentials` occurrence. There is another at
[multicopy.go:513]. "No anonymous request path" is also too broad because the
cited `HeadBucket` path is anonymous.

> **RESOLVED (accepted).** Corrected to: `AnonymousCredentials` occurs **twice**
> (region detection `common.go:49` and the S3-to-S3 copy download path
> `multicopy.go:513`), and **neither is the listing client**. Reworded "no
> anonymous request path" → "no unsigned path **for the listing client**",
> acknowledging the region-detection `HeadBucket` is itself anonymous but only
> fetches a region header. Fixed in report § 2/§ 9, dossier capability section,
> reconciliation net-new #1.

**Medium — `list-versions` cannot be validly normalized against a current-object
manifest as described.** Source emits both versions and delete markers
([list.go:126]), potentially with repeated keys. [normalize.sh:33] discards the
version ID and marker identity, while [report.md:210] says the ordinary key
verifier checks this mode. On a versioned bucket, correct output becomes
indistinguishable duplicates/extra deleted keys.

> **RESOLVED (accepted).** Added an explicit caveat to report § 5 and a comment
> block in `normalize.sh`: `list-versions` is manifest-comparable **only on an
> unversioned bucket** (the registered smoke bucket's case); on a versioned
> bucket it legitimately emits duplicate/marker keys the current-object manifest
> lacks, and version-level completeness is not checkable against such a manifest
> at all. Flagged for the benchmark phase (edge fixture unseeded).

**Medium — Several `[RUN]` claims have no committed run payload.** The
self-reported version [report.md:38], live-help [report.md:293], and adapter/argv
self-tests [report.md:342] are absent from both receipts, which contain only the
startup-panic payloads, despite `[RUN]` being defined as receipt evidence.

> **RESOLVED (accepted).** These were real runs but done directly (not via the
> wrapper), so the honest label is `[OBS]`, not `[RUN]`. Captured the evidence
> into non-mode dirs — `receipts/smoke/_build/first-exec.txt` (`--version`,
> `--help`, `ls --help`) and `receipts/smoke/_adapter/self-test.txt` (run.sh
> argv + normalize.sh fixtures) — and relabeled each citation `[OBS <how>,
> captured <path>; not wrapper-recorded]`. Both files secret-scanned clean.

**Medium — Transfer concurrency is labeled source-corroborated without
implementation evidence.** [report.md:44], [reconciliation.md:37] assert cp/sync
spawn concurrent uploads; the cited README is marketing and `s3kor.go:48,76`
only declares flags/defaults.

> **RESOLVED (accepted).** Downgraded: cp/sync **expose** concurrency flags
> (defaults 30/20) `[SRC s3kor.go:48,76]` and the README **describes** concurrent
> multipart transfers `[DOC]`; I did not read the transfer worker code (out of
> listing scope) and make **no** source claim about worker spawning. Fixed in
> report § 2 intro and reconciliation X2.

**Low — Both capability receipts retain an unfinished verifier placeholder.**
[list/receipt.md:15] and [list-versions/receipt.md:15] say the verdict "will be
filled in," while the report concludes verification was intentionally not run.

> **RESOLVED (accepted).** Replaced the placeholder in both receipts with an
> explicit **n/a — capability probe** verdict (no listing output to verify;
> startup panic before any S3 request), so the receipts record a decision rather
> than reading as unfinished.

**Low — Several `[SRC]` anchors do not establish the attached SDK/S3 behavior.**
Sequential SDK implementation [report.md:50], default jitter/backoff and timeout
semantics [report.md:73], S3 ordering [report.md:78], continuation handling
[report.md:98] require pinned SDK/AWS docs or labeled inference.

> **RESOLVED (accepted).** Split each: s3kor's own lines keep `[SRC]` for what
> they show (calls `*Pages`, sets `MaxRetries: 30`, sets no `MaxKeys`), and the
> **dependency behavior** (serial paginator loop, default backoff/jitter, S3
> byte-order, transparent continuation) is now labeled `[INFERRED — aws-sdk-go
> v1 / S3 semantics, not anchored in s3kor source]`. Fixed in § 2.

**Low — The release architecture row cites conflicting evidence without resolving
it.** [report.md:285] says Windows amd64-only; the README says that but
[.goreleaser.yml:16] includes `arm` globally and excludes only Windows
arm64/386, implying Windows arm builds.

> **RESOLVED (accepted).** Reworded the arch-matrix row to note the Windows arch
> is **ambiguous** between README (amd64 only) and `.goreleaser.yml` (`arm`
> global, only windows arm64/386 ignored), marked not-settled and **not
> load-bearing** — the benchmark denominator is linux amd64/arm64, both native.

**Low — Multiple `[3P sweep]` citations are not reproducible citations.** The
anonymous-mechanism sweep [report.md:114], absence of scale reports
[report.md:251], and Docker Hub/GHCR absence [report.md:257] provide no URL,
query, or captured result despite `[3P]` being defined as URL-backed.

> **RESOLVED (accepted).** These are **absence-of-evidence** claims, not cited
> documents. Relabeled each `[INFERRED from a 3P sweep — <what was searched> on
> 2026-07-17; absence of a found result, not a cited document]`, and the § 11
> Sources list already enumerates the search surfaces (commits/releases/issues,
> Docker Hub/GHCR). No claim now dresses an absent search result as a `[3P]`
> citation.

---

## Round 2 (scoped: artifacts + resolutions vs pinned source)

A second codex pass (same model/effort, source dir added, targeted lookups)
checked whether the round-1 resolutions actually propagated and whether any
overreached. It found that several round-1 fixes were applied at the *primary*
occurrence but not at *sibling* occurrences. All accepted and fixed in the
follow-up commit; findings (verbatim-summarized) + resolutions:

**High — report.md § 6 still said the design "suggests bounded memory,"
contradicting § 2, and still reduced the `ls` race to ordering only.**
> **RESOLVED.** Rewrote the § 6 memory bullet to state the design is **not**
> bounded (spawn-and-return, no backpressure, goroutine accumulation), and
> replaced the ordering-only bullet with the concurrency-race bullet (can hang
> or panic before complete output; no set-completeness assurance). Now
> consistent with § 2.

**Medium — README capability section still said "no anonymous/unsigned request
path" and "only one `AnonymousCredentials`", and stated the panic generically.**
> **RESOLVED.** Rewrote it: "no unsigned path for the **listing client**"; two
> `AnonymousCredentials` occurrences (`common.go:49`, `multicopy.go:513`), the
> `HeadBucket` one *is* anonymous but only fetches a region header; panic scoped
> to the session-build condition (`AWS_WEB_IDENTITY_TOKEN_FILE` set + empty
> `AWS_ROLE_ARN`), with the bare-empty-env request-time distinction.

**Medium — report.md § 5 asserted the smoke bucket is unversioned without labeled
evidence, and normalize.sh doesn't enforce it.**
> **RESOLVED.** Relabeled: the registry does **not** record versioning state and
> `normalize.sh` does not enforce it, so `list-versions` verifies validly *only
> if* the bucket is unversioned — an unestablished precondition `[INFERRED — not
> a registry/receipt fact]`; and the mode was credential-blocked anyway.

**Medium — reconciliation.md D3 still said transfer concurrency "is real,"
contradicting the corrected X2.**
> **RESOLVED.** D3 reworded to "flag surface only": the flag split is
> source-visible, but transfer worker behavior is doc-attested, not
> source-verified — consistent with X2.

**Low — report.md § 2 "truncated responses handled by the SDK paginator
transparently" was an unlabeled SDK-behavior claim.**
> **RESOLVED.** Relabeled `[INFERRED — aws-sdk-go v1 paginator semantics, not
> anchored in s3kor source]`.

Two rounds run (the brief's maximum). No finding was left unresolved or in
disagreement; every round-1 and round-2 point was accepted and fixed, and each
source-anchored fix was re-verified against the pinned checkout by targeted
lookup. Residual generic phrasings that survive (README header/receipt-summary
lines) are accurate at summary scope and defer to the precise capability
section.

---

## Consolidation review

Reviewer: `codex exec -m gpt-5.6-sol -c model_reasoning_effort=xhigh
--sandbox read-only`, run over the consolidation diff (`git diff HEAD --
tools/s3kor/{README.md,mechanism.md,running.md}`) against
`research/reconciliation.md` and the pre-consolidation README
(`git show HEAD:tools/s3kor/README.md`). Hunted for: reconciliation rows with
no destination, statuses changed without a receipt, hypotheses quietly
narrowed/softened, provenance lost from carried-forward claims, and internal
contradictions across the three new pages. Completed in one pass (~140k tokens);
findings reproduced **verbatim** below, each followed by its resolution.
(An initial invocation with `-o <file>` exited without emitting a final message
and wrote no output file; it was re-run cleanly and this is that run's result.)

**Findings (verbatim):**

> 1. **[P1] Source-only claims are promoted to `CORRECTED` without receipts.** The taxonomy simultaneously allows source/docs/live help to establish `CORRECTED` and says source cannot promote beyond `VERIFIED: no` ([README.md](tool-page.md)). M4, D2, and N4 are then marked `CORRECTED` using only `[SRC]`/`[OBS]` evidence ([README.md](tool-page.md), [README.md](tool-page.md), [README.md](tool-page.md)), contrary to the reconciliation’s receipt rule ([reconciliation.md](reconciliation.md)).
>
> 2. **[P1] The rewrite broadens “no unsigned listing path” into a false tool-wide claim.** The verdict says s3kor “cannot make unsigned requests” ([README.md](tool-page.md)), and the running page says it has “no unsigned code path” ([running.md](../docs/running.md)). The mechanism page explicitly documents two unsigned paths—anonymous region detection and copy download ([mechanism.md](../docs/mechanism.md)). The reconciliation and old README correctly scoped the finding to the listing client.
>
> 3. **[P2] The untested D2 performance hypothesis has no destination.** The pre-consolidation mode row said `--all-versions` was “Worth a quick check for different perf characteristics than plain listing” (`HEAD:tools/s3kor/README.md:54`). The replacement D2 row carries only the flag correction and output contract ([README.md](tool-page.md)); the Open hypotheses section omits the comparison despite claiming every unrun claim was carried forward verbatim ([README.md](tool-page.md)). Both modes remain blocked, so no receipt resolved this hypothesis.
>
> 4. **[P2] Dormancy provenance is internally contradictory and its wording is strengthened without a label.** N6 correctly classifies dormancy as dated third-party evidence ([README.md](tool-page.md)), matching reconciliation N6 ([reconciliation.md](reconciliation.md)), but the provenance section later calls dormancy a “Firsthand” fact ([README.md](tool-page.md)). The headline/mechanism also extend the dated facts into the unlabeled prediction that the doc drift is “unlikely to be fixed” ([mechanism.md](../docs/mechanism.md)), which is absent from the reconciliation claim.
>
> REVIEW COMPLETE

**Resolutions:**

1. **RESOLVED (accepted — legend reworded, statuses kept).** The defect was my
   own legend wording, not the M4/D2/N4 classifications. The brief mandates
   `CORRECTED` for factual corrections (it names the `--detect-region` doc-drift
   as a deliverable) and its routing rule scopes the no-promote clause to
   *corroborated* claims ("Corroborated-by-source stays `VERIFIED: no`"), not to
   *contradicted* factual values; the owner-adopted s5cmd ledger likewise marks
   source-settled factual corrections `CORRECTED`. M4 (version), D2 (`--versions`
   → `--all-versions`), and N4 (`--auto-region` → `--detect-region`) each
   correct a **wrong static value** (both sides shown), which makes no new
   behavioral claim. Reworded the `CORRECTED` and `VERIFIED: no` legend bullets
   so `CORRECTED` is explicitly "a factually wrong static value" and the
   no-promote rule is explicitly scoped to "never promotes a *corroborated*
   claim to `CONFIRMED`" — removing the self-contradiction the finding cited.

2. **RESOLVED (accepted).** A real over-broadening — the same regression the
   groundwork's round-2 review had already fixed ("no anonymous request path" →
   "no unsigned path **for the listing client**"). Rescoped the verdict opener
   ("**s3kor cannot list unsigned**", with the two non-listing
   `AnonymousCredentials` paths named as not the listing client) and the
   running-page phrase ("no unsigned **path for the listing client**"). The
   headline bullet, ledger N1, and mechanism.md already carried the scoped
   wording; the two summary lines now match.

3. **RESOLVED (accepted).** Added a bullet to "Open hypotheses for the
   benchmark" carrying the inherited D2 sub-claim verbatim ("worth a quick check
   for different perf characteristics than plain listing"), labeled
   `[inherited D2 mode row]`, noting the flag/output corrections are settled
   (ledger D2) while the perf-characteristics comparison is unrun (both modes
   credential-blocked) and benchmark-phase.

4. **RESOLVED (4a accepted; 4b reasoned-keep).** (4a) Removed "dormancy" from the
   Provenance section's firsthand-*source* enumeration and relabeled it a
   "dated third-party observation ([3P], 2026-07-17), not a source read —
   labeled as such in the ledger (N6)", resolving the firsthand-vs-3P
   contradiction. (4b) "unlikely to be fixed" is **carried verbatim from
   `research/report.md` § 9** ("the project is dormant (~4 years) so this is
   unlikely to be fixed") and is framed as a conditional inference from the
   dated dormancy, not a standalone prediction; per the fidelity mandate
   (transcribe report.md, do not add or drop), it is retained as written.

All four accepted (three fixed in the pages, one reasoned-keep on 4b's second
half). No status was strengthened or softened by the fixes: 2 rescopes a
listing-only finding back to listing-only, 1 and 3 correct legend/coverage
without touching any verdict, 4 corrects a provenance label.
