# ps3 — Stage E adversarial cross-model review (codex) + resolutions

> **Link normalization (2026-07-17, pre-publication).** This file is immutable
> evidence: its findings, verdicts, wording, and every link *label* are
> untouched. Only broken link *targets* were repaired — they pointed at absolute
> paths inside the ephemeral worktree this review ran in
> (`<checkout>/...`), so every link was broken and named a checkout no
> reader can have. In-repo targets are now repo-relative; targets pointing into
> the pinned upstream source checkout (never part of this repo) had their dead
> hyperlink removed with the visible text kept verbatim. No label text changed.

Reviewer: `codex exec -m gpt-5.6-sol -c model_reasoning_effort=xhigh --sandbox
read-only`, run against the pinned checkout `9428492…`, the study data dir, and
the staged `tools/ps3/` artifacts. One round. It confirmed the pinned SHA,
found no secrets and no hardcoded bucket names in `run.sh`/`normalize.sh`, and
returned 16 findings. Verbatim review appended below; resolutions here.

Codex ran ~15 min at `xhigh` on the (tiny) source tree and completed within the
stall guard — no scoping reduction was needed.

## Resolutions

| # | Sev | Finding (short) | Resolution |
| --- | --- | --- | --- |
| 1 | Critical | "No unsigned path" over-promoted to CONFIRMED from one narrow run | **Fixed.** README Receipts now scopes CONFIRMED to *exactly* "v0.1.16, this one invocation, harness anon env, no listing"; the general "no unsigned path" is labeled `[SRC]`+`[OBS]`, not receipt-settled; two other modes marked unrun-by-inference. Mirrored in report §8 and reconciliation new-findings + T-note. |
| 2 | Critical | `[RUN --help]` had no committed artifact | **Fixed.** Captured `receipts/smoke/_capability/help/help.txt` (`--version` + all `--help`). All `[RUN --help]` labels now cite `help/`. |
| 3 | Important | exit-0 `[OBS]` presented as a settled receipt | **Fixed.** Added raw capture `receipts/smoke/_capability/silent-empty/` (exit 0, 0-byte streams). Report/README now state explicitly `[OBS]` is never a receipt and only the exit-1 half is receipt-backed. |
| 4 | Important | Alphabet is 81 not 79, and a `var` not a const | **Fixed** (confirmed: counted 81; `root.go:19` opens a `var(` block). Corrected in report ×3, reconciliation A4/W2, README alphabet row. |
| 5 | Important | 256 caps only the pager; discovery goroutines unbounded | **Fixed.** Corrected throughout (report headline/§2/§3/§9/§10, reconciliation T1, README concurrency row) to "256 pager `var` + unbounded discovery". |
| 6 | Important | ">999 / IsTruncated" threshold described imprecisely | **Fixed.** report §2 and reconciliation A3 now state `len>999`, never `IsTruncated`, and the exactly-1000 misclassification. |
| 7 | Important | Leading-space "loss" is false; real newline-key loss omitted | **Fixed.** Removed the false leading-space-loss claim (adapter strips one known delimiter space, leading spaces survive); added the embedded-newline gap `[DOC AWS]` in report §5 and normalize.sh comment. |
| 8 | Important | Wrapper receipt: template verdict left in; "0 API calls" as measured; determinism not in the receipt | **Fixed.** receipt verdict → "n/a — BLOCKED"; "0 API calls" relabeled `[INFERRED]`; report §8 splits the single `[RUN]` receipt from the `[OBS]` 5/5+3/3 repeats. |
| 9 | Important | Some `[SRC]` labels lack anchors; `s3headObject:13-28` wrong file | **Fixed.** `head-objects` anchor → `cmd/s3SDKfunctions.go:13-28` and its lists-then-HEADs behavior labeled `[INFERRED]` (command source absent). Bare-label spots given file/line where load-bearing. |
| 10 | Important | "Same tunables for every subcommand" contradicts source (`--prefix-count` is local) | **Fixed.** report §3 now notes `--prefix-count` is local to `list-objects-v2` in HEAD source; the binary's other subcommands declare their own per `help/`. |
| 11 | Important | Quickstart/"any list error aborts"/"never pushed" overstated | **Fixed.** Quickstart marked "not observed running (blocked)"; error handling split (pager `log.Fatalln` vs discovery swallow); "newer/never-pushed" relabeled `[INFERRED]`, file-set mismatch is the fact. |
| 12 | Important | Dossier current-state/provenance lines contradict the edits | **Partially addressed / reasoned.** The Groundwork-update banner (top) and Mixed-provenance block explicitly scope which cells are now firsthand and that mechanism/number claims stay `VERIFIED: no`. Per BRIEF.md the dossier "remains the hypothesis sheet" and is not rewritten; the inherited "what to verify / not freshly checked" prose is left intact as historical lineage, now bracketed by those two callouts. Deliberately not deleted. |
| 13 | Minor | `[3P]` blog does not mention prefix-count tuning | **Fixed.** report §4 now says the author documents no tunable at all. |
| 14 | Minor | Reconciliation totals/labels inconsistent | **Fixed.** Recounted: Corroborated 10, Contradicted 6, Settled 1 (M3), Unaddressed 6; M5 is Contradicted (receipt-backed), not "Settled". |
| 15 | Minor | "syntax error" at :186 is really a selector/type (compile) error | **Fixed.** report §7 and _build/receipt.md wording → "compile error (parses as a selector, fails type-checking)". |
| 16 | Minor | Stale comments in run.sh:11 and silent-empty-obs.md:10 | **Fixed.** run.sh comment now lists the persistent root flags too; silent-empty-obs.md path reference corrected to the sibling `list-anon/`. |

No finding was a disagreement; all 16 were accepted (12 with a reasoned partial
scope). No second round: nothing was left unresolved that a re-run would move.

---

## Verbatim codex review

Pinned SHA matched: `9428492291ef3aa824dba0b495583279c3d33760`.

Highlighted evidence status: (a) compile failure is backed; (b) binary/subcommand divergence lacks a committed help receipt; (c) anonymous-mode conclusions are overscoped; (d) only the exit-1 half of the environment split is receipt-backed. I found no secrets or hardcoded bucket names in `run.sh`/`normalize.sh`.

## Critical

1. **The dossier improperly promotes “no unsigned-request path” from one narrower run.**  
   [README.md:212](tool-page.md) calls it `CONFIRMED`, while the cited [receipt.md:21](../receipts/smoke/_capability/list-anon/receipt.md) proves only that one `list-objects-v2` invocation, with config files deliberately redirected to nonexistent paths, exited 1. Source reading supports the checkout’s primary command lacking explicit anonymous credentials, but not receipt-backed behavior of the divergent binary or the two unrun modes. The same overreach appears at [report.md:25](report.md), [report.md:159](report.md), [report.md:274](report.md), and [reconciliation.md:63](reconciliation.md).

2. **`[RUN --help]` has no committed receipt or raw output.**  
   No file under `receipts/smoke/` captures help. Consequently, the assertions that the binary exposes three source-absent subcommands and that the full flag surface was observed are not valid `[RUN]` evidence at [report.md:113](report.md), [report.md:380](report.md), [reconciliation.md:39](reconciliation.md), [reconciliation.md:42](reconciliation.md), and [reconciliation.md:65](reconciliation.md). The build receipt merely repeats the allegation at [receipt.md:34](../receipts/smoke/_build/receipt.md); it does not evidence the binary’s help output.

## Important

3. **The exit-0 half of the credential split is `[OBS]`, not a receipt, but is presented as settled negative behavior.**  
   [silent-empty-obs.md:21](../receipts/smoke/_capability/silent-empty-obs.md) contains prose only—no raw stdout/stderr, exit capture, timestamp, or full box record. That is permissible as `[OBS]`, but `[OBS]` is explicitly never a receipt. Nevertheless, [README.md:205](tool-page.md) places it under “What the receipts settle,” [README.md:172](tool-page.md) calls `[OBS]` a committed smoke receipt, and [report.md:317](report.md) elevates it to the “most dangerous behavior.” The two-environment split is therefore only half receipt-backed.

4. **The alphabet anchor is misread twice: it has 81 elements and is not a compile-time constant.**  
   root.go:19 begins a `var` block; root.go:36 contains 81 string literals. The staged claims say “79” and “compile-time const” at [report.md:45](report.md), [reconciliation.md:31](reconciliation.md), and [README.md:76](tool-page.md). The cited source directly contradicts both details.

5. **The report mischaracterizes listing concurrency as a universal hardcoded 256 cap.**  
   `maxSemaphore` is a mutable package variable, not a const (root.go:44). It limits only the later `listObjectsInParallel` pager at listObjectsV2.go:291. Prefix discovery spawns recursive goroutines without that semaphore at listObjectsV2.go:240. Thus [report.md:29](report.md), [report.md:68](report.md), [reconciliation.md:39](reconciliation.md), and [README.md:78](tool-page.md) overstate the mechanism. The absence of a user-facing cap remains supported.

6. **The 1,000-key threshold is described incorrectly.**  
   The code recurses whenever `len(resp.Contents) > 999` and never checks `IsTruncated` (listObjectsV2.go:222). Therefore an exactly-1,000-key, non-truncated response is treated as large; “page full ⇒ more exist” and “≤1000 is a leaf” are false. This affects [report.md:50](report.md) and the explicitly contradictory verdict at [reconciliation.md:30](reconciliation.md).

7. **The adapter’s stated leading-space loss is false, while a real newline-key loss is omitted.**  
   The tool emits one known delimiter space after the final tab; [normalize.sh:43](../adapter/normalize.sh) removes exactly that one space, preserving genuine leading spaces. Thus [report.md:184](report.md) is wrong. Conversely, valid S3 keys may contain newlines, but pS3 embeds the raw key in a newline-terminated record and the line-oriented AWK parser at [normalize.sh:32](../adapter/normalize.sh) will split/drop such keys. The synthetic fixture does not test this. AWS documents newline characters in object keys in its [object-key guidance](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-keys.html).

8. **The wrapper receipt remains incomplete and overstates what was measured.**  
   [receipt.md:15](../receipts/smoke/_capability/list-anon/receipt.md) still contains the template “filled in by” verifier placeholder rather than `n/a/BLOCKED`. Its “0 S3 API calls” conclusion at [receipt.md:104](../receipts/smoke/_capability/list-anon/receipt.md) was not captured by an API observer; the checkout uses the identical session-failure message at two sites, before and after `GetBucketLocation`. It should be inference, not measured `[RUN]`. Likewise, the 5/5 + 3/3 determinism claim at [report.md:280](report.md) is not present in the single cited wrapper receipt.

9. **Several `[SRC]` labels are not valid anchors or do not support their attached behavior.**  
   Bare labels such as [report.md:79](report.md), [report.md:95](report.md), [report.md:97](report.md), and [reconciliation.md:56](reconciliation.md) omit file, line, and SHA. [report.md:121](report.md) cites nonexistent file anchor `s3headObject:13-28`; the helper is actually in `cmd/s3SDKfunctions.go`, and that helper alone does not prove the binary’s `head-objects` command lists and then HEADs every object.

10. **The “same tunables for every subcommand” claim is source-contradicted.**  
    [report.md:124](report.md) says the table applies to every subcommand, but `--prefix-count` is a local flag on `listObjectsV2Cmd` at listObjectsV2.go:43, not a persistent/global flag. The absent command sources and missing help receipt prevent extending that tunable to the shipped binary’s other commands.

11. **Material behavioral claims are unlabeled or presented as facts about an untested divergent binary.**  
    [report.md:145](report.md) states a working quickstart and binary output contract despite no live listing. [report.md:227](report.md) says “any list error aborts,” directly contradicting the cited helper that swallows discovery errors. [report.md:313](report.md) asserts the binary came from a “newer/divergent tree that was never pushed” and that the source is stale; mismatch is supported, but chronology and “never pushed” are unlabeled inference.

12. **The dossier’s current-state and provenance sections contradict its own edits.**  
    [README.md:3](tool-page.md) still says every claim is unverified and inherited; [README.md:81](tool-page.md) says a runnable artifact/help has not been located; [README.md:158](tool-page.md) still instructs the reader to clone and attempt the build; and [README.md:199](tool-page.md) says the repo/license were not freshly checked. These conflict with the groundwork update and mixed-provenance block.

## Minor

13. **The `[3P]` citation does not contain the claimed tuning guidance.**  
    [report.md:152](report.md) attributes implicit `--prefix-count` tuning guidance to the author. The [Medium post](https://jboothomas.medium.com/fast-listing-s3-objects-from-buckets-with-millions-billions-of-items-380052fb6faf) supports the brute-force algorithm and the 160/1110/733-second figures, but does not mention `prefix-count` or tuning it.

14. **Reconciliation totals and status labels are internally inconsistent.**  
    [reconciliation.md:73](reconciliation.md) reports eight corroborated rows, although the tables contain nine; it calls M5 “Settled-by-receipt” in the count while M5’s actual verdict is “Contradicted,” and M3 is the row labeled “Settled.”

15. **The build failure is real, but line 186 is not technically a syntax error.**  
    [build-output.txt:61](../receipts/smoke/_build/build-output.txt) and the pinned source conclusively support “does not compile.” However, `"debug: item count=".atomic` parses as a selector expression and fails type checking; calling it a syntax error at [receipt.md:33](../receipts/smoke/_build/receipt.md) and [report.md:248](report.md) is imprecise.

16. **Two script/receipt comments are factually stale.**  
    [run.sh:11](../adapter/run.sh) says subcommands expose only `--bucket` and `--prefix-count`, contradicting the global flags and its own `--region` use. [silent-empty-obs.md:10](../receipts/smoke/_capability/silent-empty-obs.md) points to `../_capability/list-anon/`, which resolves to a nonexistent doubled `_capability` path.
---

## Consolidation review

Reviewer: `codex exec -m gpt-5.6-sol -c model_reasoning_effort=xhigh --sandbox
read-only`, run 2026-07-17 against the consolidation diff
(`README.md` rewrite + new `mechanism.md`/`running.md`) reconciled against
`research/reconciliation.md` and the pre-consolidation README (`git show
HEAD:tools/ps3/README.md`). One round; codex completed at `xhigh` well inside
the stall guard (the first launch died early at exit 144 before writing output
and was re-run; the re-run completed clean, exit 0). It returned 7 findings
(6 Important, 1 Minor). All 7 accepted and fixed; resolutions below, verbatim
review after.

| # | Sev | Finding (short) | Resolution |
| --- | --- | --- | --- |
| 1 | Important | Inherited "works on arbitrary keyspaces … unlike delimiter-based discovery" strength dropped with no destination; `mechanism.md` coverage-gap contradicts it | **Fixed.** Added a ledger-footer note mapping the inherited "Claimed strengths" section: zero-config → A6; the arbitrary-keyspaces bullet is a source-corroborated *structural* property (byte-walk needs no `/` hierarchy) **qualified** by the 81-char alphabet coverage gap (W2 / `mechanism.md`), stated VERIFIED: no at runtime — not silently dropped. |
| 2 | Important | "Open hypotheses … carried verbatim" but `--output json` question (T2/report §10 Q1) omitted and W1 softened (dropped `alphabet^N`, sparse deep-shared-prefix adversary, matched shallow/flat comparison, API-calls-per-key metric, 100/500/2000/10000 sweep) | **Fixed.** Rewrote the section (now 8 items): restored the `--output json`-in-binary question; restored the full `--prefix-count` sweep values 100/500/2000/10000; added a dedicated W1 discovery-tax item carrying the `alphabet^N` bound, the sparse deep-shared-prefix adversary, the matched shallow/flat comparison, and the API-calls-per-unique-key metric. Dropped the word "verbatim" for "with its detail, not compressed." |
| 3 | Important | "No native runnable path" labeled a smoke blocker, but qemu was acceptable for smoke and the binary runs natively on amd64 — it is an arm64-runner/benchmark-timing constraint, not a categorical absence | **Fixed.** Verdict intro now says the *listing* is blocked two ways and there is no *native* runnable path for the *benchmark*; ground 3 retitled "No native runnable path for the benchmark," states the probes ran (emulated) and that emulation was acceptable at smoke but disqualifying for benchmark timing. |
| 4 | Important | qemu umbrella internally false: README:45/running.md:5 say every receipt-derived line inherits qemu, but the `_build` source-compile ran native arm64 | **Fixed.** Scoped the caveat to lines derived from *runs of the amd64 binary* (list-anon, help, silent-empty) in README ground 3, `mechanism.md` header, `running.md` intro and qemu-posture section; the `_build` native-arm64 compile is now the explicit exception in all three. |
| 5 | Important | Provenance calls the whole Claim ledger firsthand (contradicts [3P] rows N1-N3 and inherited hypotheses); drops the pre-consolidation attribution (source-note Sources line + design-doc) behind inherited repo/license | **Fixed.** Reworded: the ledger's *verdicts and evidence* are firsthand, the *claims it checks* are inherited/[3P] (N1-N3 stay third-party). Restored the inherited attribution: repo/license/anchors from a source-level research note's Sources line and swath's design-doc attribution ("PS3 (jboothomas, MIT)", licence since corrected to GPL-3.0); numbers from a single Medium post. |
| 6 | Important | `mechanism.md` region bug says "Confirmed in the trace" but cites only `[OBS]`, conflicting with the receipt-only promotion rule and "`[OBS]` never settles" | **Fixed.** Reworded: the bug is source-level `[SRC]`; the trace merely *shows* the fallback firing — an `[OBS]` illustration, not a settling receipt. Removed "Confirmed." |
| 7 | Minor | `mechanism.md` flag catalog omits the required per-subcommand `--bucket` and root `--version` (both in `help.txt`) | **Fixed.** Added `--bucket string` (per subcommand, required) as a flag-table row and a lead-in note, and noted root `--version` (prints `pS3 version 0.1.16` and exits) alongside the persistent root flags. |

No finding was a disagreement; all 7 were accepted. No second round: nothing was
left unresolved that a re-run would move.

---

### Verbatim consolidation review

## Important

1. [README.md:87](tool-page.md) claims every inherited claim was reconciled, but the pre-consolidation strength “works on arbitrary keyspaces … unlike delimiter-based discovery” (`HEAD:tools/ps3/README.md:106`) has no reconciliation row or destination. [mechanism.md:50](../docs/mechanism.md) materially contradicts it, so it needs an explicit contradicted row rather than silent deletion.

2. [README.md:175](tool-page.md) says the benchmark hypotheses are carried verbatim, but they are not. The `--output json` binary-verification question from [report.md:363](report.md) and reconciliation T2 is omitted. W1 is softened at [README.md:185](tool-page.md): it drops the `alphabet^N` bound, sparse deep-shared-prefix adversary, matched shallow/flat comparison, API-calls-per-key metric, and the resolved 100/500/2000/10000 sweep from [reconciliation.md:56](reconciliation.md) and [report.md:365](report.md).

3. [README.md:40](tool-page.md) labels “No native runnable path” an independent smoke blocker, but [running.md:39](../docs/running.md) says qemu was explicitly acceptable for smoke and [running.md:149](../docs/running.md) says the prebuilt binary runs natively on amd64. This is an arm64-runner limitation and benchmark-timing constraint, not a categorical absence of a native runnable path or a blocker to the smoke probes that already ran.

4. The emulation scope is internally false. [running.md:5](../docs/running.md) and [README.md:45](tool-page.md) say every run/receipt-derived statement inherits qemu, while [running.md:102](../docs/running.md) and the [_build receipt](../receipts/smoke/_build/receipt.md) say the source-build run was native arm64. The umbrella incorrectly scopes M5/build evidence as emulated.

5. [README.md:203](tool-page.md) classifies the entire claim ledger as firsthand, contradicting its [3P] number rows at [README.md:127](tool-page.md), inherited hypotheses, and its own acknowledgment at [README.md:207](tool-page.md). It also drops the pre-consolidation provenance identifying the source-level note’s Sources line and the separate design-doc attribution behind the inherited repo/license claims (`HEAD:tools/ps3/README.md:183-202`).

6. [mechanism.md:204](../docs/mechanism.md) says the region behavior was “Confirmed in the trace,” but cites only `[OBS]`. That conflicts with the receipt-only promotion rule at [README.md:89](tool-page.md) and the explicit statement that `[OBS]` never settles a claim at [README.md:62](tool-page.md). Reconciliation records this only as a source-level additive finding at [reconciliation.md:66](reconciliation.md).

## Minor

7. T4 says the full flag surface has a destination at [README.md:125](tool-page.md), but the catalog at [mechanism.md:138](../docs/mechanism.md) omits the required per-command `--bucket` flag and root `--version`. Both are present in the cited help artifact at [help.txt:25](../receipts/smoke/_capability/help/help.txt) and [help.txt:43](../receipts/smoke/_capability/help/help.txt).
