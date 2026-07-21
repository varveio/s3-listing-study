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

Reviewer: `codex exec -m gpt-5.6-sol -c model_reasoning_effort=xhigh --sandbox
read-only`, run 2026-07-17 over the full `tools/s4cmd` groundwork with the source
checkout (`<sources>/s4cmd`, SHA `80059bf` confirmed by codex) and
the data dir added read-only. One round.

**Coverage / gaps:** codex verified all cited payload sha256s and found no
secrets; it re-verified `[SRC]` anchors by targeted lookup against the pinned
checkout, and did web lookups for the botocore/issue-#139 claims. It could **not**
run `git diff` on the dossier (the worktree's `.git` file did not resolve under
its read-only sandbox), so it reviewed `README.md`'s current content directly
rather than as a diff — a minor gap, noted for the orchestrator. No second round:
all findings were addressed in round one.

The 13 findings are reproduced verbatim below, each followed by **Resolution**.

---

## Findings (verbatim) and resolutions

> - **High — The proposed API-call counter cannot observe paginated LIST requests.** [research/report.md:184](report.md) (also lines 225–228, 346–348, 387–390) claims `S3APICALL` exposes every call and can count LISTs. The wrapper logs only `get_paginator`; the returned boto3 paginator makes actual `list_objects` page requests outside that wrapper (s4cmd.py:278, lines 398–404 and 1173–1176). It can count directory-walk tasks, not API pages. The “per-call” wording at [receipt.md:108](../receipts/smoke/_capability/anon-nocredentials/receipt.md) also overstates observability.
> 
> - **High — The dossier promotes an operational finding using a narrative, not an auditable run receipt.** [README.md:19](tool-page.md) and [reconciliation.md:26](reconciliation.md) label the modern-boto3 import failure `[RUN]`, but [build.md:31](../receipts/smoke/_build/build.md) contains no failed-install transcript, build log, exit codes, raw `--version`/`--help` output, image inspection, or package freeze. The source mechanism is plausible and AWS confirms removal beginning with botocore 1.13.0, but that does not prove the claimed run occurred. [AWS removal notice](https://aws.amazon.com/blogs/developer/removing-the-vendored-version-of-requests-from-botocore/). The `localhost:5000` image payload is also unavailable from the staged evidence, while [Dockerfile:22](../build/Dockerfile) leaves apt packages and Python transitive dependencies unresolved, so the cited image digest cannot be independently reconstructed.
> 
> - **Medium — The anonymous pre-flight `[RUN]` has no receipt.** [research/report.md:304](report.md) asserts an exit-0 anonymous list at a precise timestamp, and [NOTES.md:41](../receipts/smoke/_capability/NOTES.md) uses it to conclude the bucket was not at fault. No invocation, stdout, metadata, or hash exists under `tools/s4cmd/receipts/`. The hashed registry records a different, prior verification, not this claimed pre-flight.
> 
> - **Medium — Both `[OBS]` probes are unbound, and the bare-env interpretation cites the wrong handler.** `direct-bare-env.stderr.txt` and `obs-multiprefix.stderr.txt` have no invocation, image/version, environment, exit-code metadata, or hashes, yet [research/report.md:330](report.md) and [reconciliation.md:35](reconciliation.md) assert those facts. Moreover, `NoCredentialsError` is caught inside the worker by the generic `except Exception` and converted to `RuntimeError` (s4cmd.py:540); the trace never reaches the specific handler cited at line 1933. Thus “matching the explicit handler” at report lines 335–336 and NOTES lines 37–39 is false.
> 
> - **Medium — The adapter overpromises key fidelity and its `[RUN]` evidence is incomplete.** [normalize.sh:4](../adapter/normalize.sh) promises “Raw key bytes,” but upstream calls `rstrip()` on the complete output line (s4cmd.py:1622), destroying trailing whitespace in keys; newline-bearing keys are inherently split by the line-oriented formatter/parser. The fixtures cover only ordinary and interior-space keys, and contain neither expected normalized output nor a recorded test result, so “validated” and `[RUN receipts/smoke/_adapter/]` at [research/report.md:221](report.md) are unsupported.
> 
> - **Medium — `run.sh` does not enforce the stated concurrency cap.** [run.sh:17](../adapter/run.sh) says overrides are kept at `<=8`, but line 29 accepts any string or integer unchanged. `S4CMD_SMOKE_THREADS=99` emits `-c 99`, contradicting both that comment and [research/report.md:151](report.md).
> 
> - **Medium — The retry analysis misses a correctness failure.** [research/report.md:102](report.md) describes whole-task requeueing, but not that objects and child-directory tasks from earlier pages remain in shared state. A retryable failure after partial progress requeues the directory from page one (s4cmd.py:529) without rolling back objects appended at lines 1208–1223 or already queued children at 1184–1185. Successful retry can therefore duplicate objects and subtree walks.
> 
> - **Medium — One recursive capability probe is promoted as settling every mode.** [research/report.md:339](report.md), [reconciliation.md:65](reconciliation.md), and [README.md:113](tool-page.md) generalize the recursive receipt to shallow, show-directory, and `du`. The conclusion is defensible from additional handler routing in source, but it is a source inference—not “settled by smoke run”—and the attached anchors do not show the `du`/`ls` routes through the common constructor.
> 
> - **Low — The upstream-health summary misstates its own git evidence.** [research/report.md:29](report.md) says the default branch is 14 commits beyond the tag and then says “132 commits total.” The pinned tag has 132 commits; the default branch therefore has 146, while all fetched refs contain 149. “Mostly dependabot bumps and a maintainer-list edit” is also inaccurate: only a minority of those 14 commits are Dependabot-related. The same claim recurs at [reconciliation.md:58](reconciliation.md).
> 
> - **Low — The UTC explanation is not supported by its `[SRC]` anchor.** [normalize.sh:16](../adapter/normalize.sh) and [research/report.md:214](report.md) say `TZ=UTC` makes the displayed timestamp UTC “by construction.” Upstream merely formats the datetime’s existing fields without timezone conversion (s4cmd.py:1597). UTC, if present, comes from the S3/botocore timestamp, not the container timezone.
> 
> - **Low — The reconciliation’s verdict count contradicts itself.** [reconciliation.md:70](reconciliation.md) reports eight contradicted claims, then enumerates ten distinct ones.
> 
> - **Low — The issue-age claim is stale by almost two years.** [research/report.md:366](report.md) says “six years after users asked.” Issue #139 opened on 2018-10-23, nearly eight years before this 2026-07-17 report. [GitHub issue #139](https://github.com/bloomreach/s4cmd/issues/139).
> 
> - **Low — The container `[3P]` citation is not reproducible.** [research/report.md:259](report.md) cites only “docker hub / github search” to prove no upstream image and names `graymic/s4cmd`; [the source list](report.md) supplies no URLs or queries. `victorlap/s4cmd` and `poldracklab/s4cmd` are verifiable community images, but the citation as written cannot substantiate the negative upstream-publication claim or the `graymic` example. [victorlap image](https://hub.docker.com/r/victorlap/s4cmd), [poldracklab image](https://hub.docker.com/r/poldracklab/s4cmd).
> 
> - **Low — `run.sh` contradicts the report about request granularity.** [run.sh:36](../adapter/run.sh) says “one … `list_objects` per prefix level”; the source and [research/report.md:62](report.md) correctly describe one paginator per discovered pseudo-directory, potentially many at the same depth.

---

## Resolutions (keyed to the findings above, in order)

1. **High — API-call counter cannot observe paginated LISTs.** *Fixed (confirmed
   independently).* `--debug` raises only s4cmd's own logger (s4cmd.py:102), and
   the paginator's `list_objects` pages run on the raw boto3 client, bypassing the
   `S3APICALL` wrapper. Corrected report §2, §5, §8, §10.2, and the receipt's
   "API call count" note: `S3APICALL` counts `get_paginator` (directories walked),
   not LIST pages; per-page counting deferred to the replay-server phase.

2. **High — import-failure promoted via narrative, labeled `[RUN]`.** *Fixed —
   and the underlying claim was FALSE.* On test, s4cmd 2.1.0 imports and runs
   under current boto3 (1.33.13 and latest 1.43.50); `botocore.vendored.requests`
   still resolves. The whole "won't import / capability finding" claim is
   **retracted** across report §1/§7/§9, README (Testability reverted to
   **Corroborated**), reconciliation (M6 → Corroborated), Dockerfile, and
   build.md. New `[RUN]` receipts committed under `_build/modern-boto3-import/`.
   The pin is now stated as reproducibility-only. (Exactly the untested inherited
   assumption this study exists to catch — logged as a self-correction.)

3. **Medium — anonymous pre-flight had no receipt.** *Fixed.* Captured a real
   receipt: `_capability/preflight-anon/` (meta.md + stdout.txt + sha256, exit 0);
   report §8 now cites it instead of a bare timestamp.

4. **Medium — `[OBS]` probes unbound; bare-env cited the wrong handler.** *Fixed.*
   Added `_capability/OBS-probes.md` binding invocation/image/exit/sha for both
   probes. Corrected the handler citation everywhere (report §8, NOTES.md): the
   worker-thread `NoCredentialsError` is caught by the generic `except Exception`
   (s4cmd.py:540 → `[Thread Failure]` 469), NOT the main-thread handler at 1933.

5. **Medium — adapter overpromises "raw key bytes"; `[RUN]`/"validated"
   unsupported.** *Fixed.* Added the tool-side `rstrip()`/newline lossy-key caveat
   to normalize.sh and report §5; added checked-in expected outputs
   (`_adapter/expected-*.tsv`) and a README; downgraded the wording from
   "validated `[RUN]`" to a synthetic construction check.

6. **Medium — run.sh doesn't enforce the `<=8` cap.** *Fixed.* run.sh now rejects
   `S4CMD_SMOKE_THREADS` outside 1..8 (exit 2) instead of emitting it unchanged.

7. **Medium — retry analysis misses a correctness failure (duplication).**
   *Fixed (good catch).* Added a "retry can duplicate keys" item to report §6 and
   §9: whole-directory requeue without rolling back appended objects/queued
   children can duplicate on successful retry `[SRC s4cmd.py:529-539,1195-1206]`.

8. **Medium — one recursive probe generalized to every mode.** *Fixed.* Report §8
   and reconciliation N1 now scope the receipt to `recursive` (**settled by run**)
   and mark shallow/show-directory/du as **`[INFERRED]`** via the shared
   `BotoClient` constructor path, not four receipts.

9. **Low — upstream-health miscounts its git evidence.** *Fixed.* Report §1 now
   says the tag's history is 132 commits, the default branch is 14 further (146),
   and softens "mostly dependabot" to the actual mix.

10. **Low — UTC "by construction" unsupported by anchor.** *Fixed.* Corrected
    report §5 and normalize.sh: UTC comes from botocore's tz-aware datetime
    formatted as-is (s4cmd.py:1602), not from `TZ=UTC`.

11. **Low — reconciliation verdict count self-contradiction.** *Fixed.* Counts
    corrected (Corroborated 8, Contradicted 9 distinct) and M6 moved to
    Corroborated.

12. **Low — issue #139 age stale ("six years").** *Fixed.* Changed to "~8 years
    (issue #139, opened 2018-10)" in report §9 and Sources.

13. **Low — container `[3P]` citation not reproducible.** *Fixed.* Report §7/§11
    now anchor the negative (no upstream image/Dockerfile) on `[SRC]` repo listing
    and give Docker Hub URLs for the community images.

## Residual disagreements

None. All findings accepted and addressed.

---

## Consolidation review

Second adversarial cross-model pass, run **2026-07-17** after the three-doc
consolidation (`README.md` rewrite + new `mechanism.md` + new `running.md`).
Reviewer: `codex exec -m gpt-5.6-sol -c model_reasoning_effort=xhigh
--sandbox read-only`, at full (xhigh) effort — no reduction. It reviewed the
consolidation diff (`git diff HEAD -- tools/s4cmd/{README.md,mechanism.md,running.md}`)
against `research/reconciliation.md` and the pre-consolidation README, hunting
for reconciliation rows with no destination, statuses changed without a receipt,
softened hypotheses, lost provenance (Mixed-provenance callout, boto3 retraction),
and contradictions across the three pages. (An earlier invocation died mid-read
with exit 144 before emitting findings; this is the clean re-run — same effort.)
Six findings; all addressed. Reproduced verbatim, each with its **Resolution**.

> - **P1 — Nine source-only mechanism claims are promoted to `CORRECTED` without receipts.** [README.md:90](tool-page.md) through [README.md:101](tool-page.md) mark C2, C3, C4, T1, T2, S1, W1, W2, and WV `CORRECTED`, although their evidence is only `[SRC]`/`[OBS]`. This contradicts the reconciliation’s explicit no-promotion scope at [reconciliation.md:7](reconciliation.md), as well as the consolidated page’s own status rule at [README.md:75](tool-page.md) and “were NOT promoted” assertion at [README.md:174](tool-page.md).
>
> - **P1 — A wrapper-specific construction failure is generalized to every credential-less run.** [README.md:35](tool-page.md) and [mechanism.md:116](../docs/mechanism.md) say a credential-less client cannot be constructed and always fails before an S3 request. [running.md:86](../docs/running.md) attributes that behavior specifically to the wrapper’s web-identity neutralization, while [running.md:93](../docs/running.md) records the general bare-environment case constructing the client and reaching `list_objects`. The no-unsigned capability remains valid, but the failure point and mechanism exceed the receipt’s scope.
>
> - **P2 — The boto3 false finding is misattributed as inherited provenance.** [README.md:55](tool-page.md) correctly says a Stage A note introduced the assertion, but [README.md:63](tool-page.md) immediately calls it an “inherited assumption.” The inherited M6 claim was the opposite—“Trivial — `pip install s4cmd`”—and [reconciliation.md:26](reconciliation.md) plus [running.md:33](../docs/running.md) identify the error as an earlier study draft.
>
> - **P2 — The immutable reconciliation still contradicts the claimed boto3 retraction.** The consolidation presents `research/reconciliation.md` as an immutable input at [README.md:20](tool-page.md), yet its W3 row still asserts “won’t import under current boto3” at [reconciliation.md:58](reconciliation.md), directly contradicting [README.md:55](tool-page.md) and [running.md:33](../docs/running.md). The consolidated W3 row silently replaces that evidence rather than recording that the reconciliation row itself is stale.
>
> - **P2 — W3 is narrowed from current-S3 compatibility to dormancy and CLI startup.** [README.md:100](tool-page.md) retains “current-S3 compatibility” in the inherited-claim cell, but its disposition only establishes release dormancy and successful installation/`--version`. The original hypothesis at `HEAD:tools/s4cmd/README.md:58-60` required verifying behavior against the current S3 API, and that unresolved test is absent from the open hypotheses at [README.md:113](tool-page.md).
>
> - **P3 — The inherited “no claimed numbers” statement has no destination.** The pre-consolidation section at `HEAD:tools/s4cmd/README.md:39-42` says no throughput figures were inherited. It appears neither in the claim ledger nor elsewhere in the three consolidated pages, despite [README.md:72](tool-page.md) claiming every inherited claim is represented.

### Resolutions (keyed to the findings above, in order)

1. **P1 — nine rows labeled `CORRECTED` on source alone.** *Fixed (good catch).*
   Relabeled all nine (C2, C3, C4, T1, T2, S1, W1, W2, WV) from `CORRECTED` to
   **CONTRADICTED** — the reconciliation's own verdict word, and the status the
   sibling `s7cmd` ledger already uses. Added a `CONTRADICTED` legend entry stating
   the corrected mechanism it points to is itself `VERIFIED: no` at runtime, and
   that source reading never promotes to `CONFIRMED`. Each row's Status cell now
   carries the explicit runtime-`VERIFIED: no` scope (and, for the multiple-prefix
   rows C4/T2/S1/W2, notes the `[OBS] obs-multiprefix` receipt backs only the
   *rejection*, not the replacement). Reconciled the tally footer and the
   Provenance "were NOT promoted → NOT promoted **to CONFIRMED**" wording. `CORRECTED`
   now appears only on M4 (editorial version correction).

2. **P1 — no-unsigned failure point overgeneralized.** *Fixed.* Both the README
   headline bullet and `mechanism.md` § "No unsigned path" now state the failure
   **point depends on the credential environment**: under the harness's
   web-identity neutralization it fails at `BotoClient.__init__` before any S3
   request (the committed `recursive` receipt); in a plain bare environment the
   client *does* construct and the error moves to the first `list_objects` call in
   the worker thread (`NoCredentialsError` → `[Thread Failure]`, `[OBS]`). Both
   produce zero keys. This matches `running.md` and report §8; the capability
   finding (no unsigned listing) is unchanged, only its mechanism is now scoped.

3. **P2 — boto3 false finding misattributed as inherited.** *Fixed.* The
   retraction bullet now says explicitly the false "won't import" claim was a
   **Stage A study-draft note, not an inherited-dossier claim** — the inherited
   testability claim was the opposite ("Trivial — `pip install s4cmd`"), which held.

4. **P2 — immutable reconciliation W3 still says "won't import."** *Fixed (flagged,
   not edited).* `research/` is immutable, so the stale clause is left in place as
   the historical record; the consolidated W3 row now adds an **immutable-file
   note** that `reconciliation.md`'s W3 evidence cell still carries the
   pre-retraction "won't import under current boto3" phrase, which is stale and
   superseded by the M6 retraction [RUN `_build/modern-boto3-import/`]. No silent
   replacement.

5. **P2 — W3 narrowed from current-S3 compatibility to dormancy.** *Fixed.* The W3
   row now splits the inherited hypothesis into its two halves: **release cadence**
   (corroborated — dormant) and **current-S3 API compatibility (NOT settled** — no
   listing mode could execute, `CREDS=none`). The compatibility half is carried into
   Open hypotheses as an explicit item flagged "carried from claim W3 (not in report
   §10)": whether the legacy `list_objects` v1 path still lists correctly against
   live S3 is unverified, settled only for install/startup.

6. **P3 — "no claimed numbers" statement had no destination.** *Fixed.* The
   Claim-ledger footer now records that the inherited dossier carried a "Claimed
   numbers: None inherited" note (no throughput figures ever supplied), which has no
   ledger row because there is no numeric claim to check — it survives as that
   sentence, and the benchmark phase starts from zero inherited numbers.

### Residual disagreements (consolidation review)

None. All six findings accepted and addressed; no status was promoted to
`CONFIRMED` on source alone.
