# Stage E — adversarial cross-model review (codex)

## Outcome: codex review did NOT converge within the repo-phase budget

Two runs of the Codex CLI (`codex-cli 0.144.5`, model `gpt-5.6-sol`) were
attempted per BRIEF.md Stage E, and **neither produced a findings file inside
the 1-hour repo-phase budget**. This is recorded honestly rather than papered
over; the brief's own budget guard anticipates exactly this ("codex cannot
finish a large source tree at xhigh … A scoped review honestly labeled beats a
timed-out full one").

| Run | Config | Scope | Result |
| --- | --- | --- | --- |
| 1 | `-m gpt-5.6-sol -c model_reasoning_effort=xhigh`, `--add-dir` source checkout + data dir | Full: report, reconciliation, receipts + external payloads, run.sh, normalize.sh, Dockerfile, dossier diff, **plus [SRC] anchor re-verification against the pinned checkout** | Ran ~14 min with no output file; killed per the budget guard (the two `--add-dir` source trees are the slow case the brief warns about). |
| 2 | `-m gpt-5.6-sol -c model_reasoning_effort=high`, `--add-dir` **data dir only** (source checkout dropped) | Repo artifacts only; [SRC] anchor re-verification declared OUT OF SCOPE | Ran ~9 min (observed reading the artifacts, e.g. the build-failure log) with no findings file before the repo-phase 75% Finalize-early gate; killed. |

Total codex wall time ~24 min against a hard ~1 h phase budget that also had to
cover Stage D reconciliation and the Stage F commit. Continuing would have blown
the budget with nothing committed — the worse failure per BRIEF.md ("a truthful
committed partial beats … an uncommitted perfect one").

## What independent verification WAS performed (in lieu of codex)

The cross-model check is a **gap** (see routing below). These same-model checks
were done directly and are the current substitute:

1. **Anchor audit (self).** Every `[SRC]` anchor cited in the report was
   re-verified against the pinned checkout `6c72f59` during Stage D and tabulated
   in `research/reconciliation.md` § "Code anchors — accuracy audit", including
   corrections to the inherited dossier's anchors. This is self-review, not an
   independent model — it does not substitute for codex, but the anchors are not
   un-checked.
2. **Label discipline sweep (self).** Grepped report/reconciliation for
   behavioral claims (`verif|PASS|complete|correct`) lacking an evidence label or
   overstating one; the load-bearing correctness claims are consistently labelled
   **[OBS]** (direct-capture diff), never presented as a certified
   `verify-listing.sh` verdict — the verifier verdict is recorded as **BLOCKED**
   everywhere (harness capture incompatibility). No unlabeled behavioral claim
   found.
3. **Hardcoded-bucket check.** `run.sh` and `normalize.sh` contain **0**
   occurrences of the registered bucket name (the wrapper's own scan gate also
   passed at run time).
4. **Secret scan.** `harness/scan-tree.sh` over the full staged tree: **clean, 24
   files, 0 flagged**. External payloads under
   `<data>/receipts/s3-fast-list/` scanned clean (8 files) and each
   verified against the sha256 its `run.meta`/`direct-capture.sha256` cites.
5. **Scope-honesty self-check.** No claim is promoted out of `VERIFIED: no` on
   correctness grounds; the `-k` parallel path, `diff` mode, `ks-tool`, and all
   scale/throughput hypotheses are explicitly left unverified; the fork-vs-upstream
   provenance is stated in the report, reconciliation, dossier banner, and every
   receipt.

## Findings and resolutions

None from codex (no findings file produced). No self-review issue required a fix
beyond what Stage D already corrected (the report's ObjectProps byte-count, fixed
40 vs an initial 48; the build-log filenames renamed off the repo's `*.log`
ignore so the third-party build-failure evidence actually commits).

## Routed to the orchestrator — OPEN GAP

**An independent cross-model adversarial review of this groundwork has NOT been
performed.** The recommended closure is an out-of-band codex (or other
different-model) pass, run with a budget separate from this phase's, EITHER:
- scoped to the repo artifacts only at reduced effort (fast, no anchor
  re-verification), OR
- a dedicated anchor-audit run with the source checkout mounted and a budget that
  tolerates xhigh over a Rust tree.

Until then, the anchor and label checks rest on same-model self-review (items
1–5 above), which the brief explicitly distinguishes from an independent review.

## Round 2 — orchestrator-run cross-model review (out-of-band)

The Stage E gap above (no independent cross-model review inside the repo-phase
budget) was closed out-of-band by the orchestrator after the in-budget attempts
were killed. Run parameters:

- Reviewer: codex, model **gpt-5.6-sol**, `model_reasoning_effort=xhigh`.
- Scope: **full** — repo artifacts (report.md, reconciliation.md, dossier
  README, run.sh, normalize.sh, Dockerfile, receipts) **plus** the pinned source
  checkout at `<sources>/s3-fast-list @ 6c72f59` **plus** the
  external direct-capture payloads. `[SRC]` anchors re-verified against source.
- Run by the **orchestrator** on a budget separate from the repo phase, after the
  two in-budget attempts (documented above) were killed.
- Result: **12 findings — 2 Critical, 6 Important, 4 Minor.**

Each finding was independently re-verified against the pinned checkout and the
repo artifacts before resolution. Verdicts below are this groundwork's, not the
reviewer's.

### Review verbatim

1. **Critical — the range model misses objects whose keys equal a hint.** report.md:45 and reconciliation.md:35 claim `[A,B)` coverage and cross-slice de-duplication. The code requests `start_after=A` and rejects `key >= B` before insertion (tasks_s3.rs:111, tasks_s3.rs:261). `StartAfter` is exclusive, so a noninitial slice covers `(A,B)`, and an object exactly equal to a hint is omitted from both adjacent slices. The map cannot de-duplicate a missing object. This invalidates the "correctness regardless of balance" verdict and the notable-finding summary. [AWS ListObjectsV2 documentation]

2. **Critical — the dossier's `--force-path-style` correction is false and unreceipted.** README.md:357, reconciliation.md:61, and reconciliation.md:100 call the flag an override that contradicts the original weakness. It is an enable-only boolean, and `cli.force_path_style || opt_endpoint.is_some()` is always true when an endpoint is supplied (main.rs:52, main.rs:146). There is still no way to select virtual-hosted style with a custom endpoint. No endpoint receipt exists, so the `[CORRECTED]` dossier promotion is unsupported as well.

3. **Important — fatal S3 errors can yield partial output and exit 0, but the report only says they "abort the slice."** The incomplete summary is at report.md:78, its failure surface at report.md:182, and the corroborated verdict at reconciliation.md:43. A fatal errno causes `ctx.complete()` followed by a normal return (tasks_s3.rs:95); the data-map then dumps what it has and signals normal shutdown (data_map.rs:372); `main` returns normally (main.rs:346). This is a silent-incompleteness hypothesis requiring an explicit run, not merely an "abort" detail.

4. **Important — the direct-capture correctness claims are not provenance-bound, and the receipt cells overstate them as verifier PASSes.** The assertions appear at report.md:256, HARNESS-INCOMPATIBILITY.txt:20, and line 15 of all four receipts, e.g. full/receipt.md:15. direct-capture.sha256:1 binds hashes to Parquets, but there is no direct-run metadata binding those files to the claimed command, image digest, authentication, exit status, or time, and no verification transcript. Their contents match the manifest, but "identical invocation" is not independently established and the standard verifier never returned PASS.

5. **Important — the negative upstream build claim lacks an exact, provenance-complete receipt.** report.md:203 and reconciliation.md:99 say the pinned upstream Dockerfile at `6c72f59` fails today. build-rust1.86-FAIL.txt:1 is only a build-log tail: it contains no invocation, checkout identity, Dockerfile hash, date/box, or builder-image digest. It demonstrates a rustc incompatibility but does not establish which exact source/build context produced it.

6. **Important — `normalize.sh` cannot preserve valid keys containing tabs or newlines.** normalize.sh:49 emits DuckDB list output without quoting, using tab and newline as structural delimiters. A tab in `Key` creates an extra field; a newline creates an extra record, violating the five-column contract claimed at report.md:165. Deferring the edge bucket does not make the adapter safe for that later test.

7. **Important — hints do not inherently require a prior listing or Inventory.** report.md:66 and report.md:271 state that the hints file "needs" one of those sources. The source accepts arbitrary lines, and reconciliation.md:39 itself lists hand-written hints as a third source. "Typically two-pass" would be supportable; "inherently" is not.

8. **Important — container claims violate the report's evidence-label promise.** Despite report.md:5, the trixie/`GLIBC_2.39` result is described as "also observed" without a receipt at report.md:209, and the architecture table declares successful amd64 and arm64 builds at report.md:219 although only arm64 build/run evidence exists. Multi-architecture base images do not prove the complete project builds on both architectures.

9. **Minor — "PLAIN encoding" does not describe the produced Parquets.** report.md:157 and reconciliation.md:50 infer this from `.set_encoding(PLAIN)`, but the direct payload metadata records `PLAIN`, `RLE`, and `RLE_DICTIONARY` for every column. The setting does not disable dictionary encoding.

10. **Minor — the `[3P]` citation is generalized beyond what it says.** report.md:193 applies the clustered-prefix caveat to "prefix/range-split listing." The cited article discusses fixed `Prefix` requests, not arbitrary `StartAfter` range cuts; balanced range cut-points can divide a clustered prefix. This needs an inference label or tool-specific evidence. [Cited blog post: blog.rasc.ch/2025/07/s3-fast-list.html]

11. **Minor — the performance citation does not state 128 GB.** reconciliation.md:70 attributes `m6i.8xlarge/128 GB` wholly to the README, but the cited table names only `m6i.8xlarge`. The memory figure requires a separate EC2-spec citation. [Upstream performance table]

12. **Minor — several `[SRC]` markers are not auditable `file:line` anchors despite the report's declared format.** Examples include report.md:147, report.md:274, reconciliation.md:57, reconciliation.md:103, and reconciliation.md:149. These use `[SRC]`, "as §2," whole files, or "module sources," contrary to report.md:6.

### Resolutions

**F1 (Critical, #1) — ACCEPTED; new source-derived hypothesis registered.**
Verified at tasks_s3.rs:111-114 (`start_after` exclusive) and tasks_s3.rs:261-269
(the `end<=key` break fires *before* the insert at :280). A key equal to a
cut-point is fetched by neither adjacent slice, and the in-map de-dup cannot
recover a never-fetched object. The reviewer's precise coverage `(A,B)` is right;
I add one clarification: because the break is pre-insert and `start_after` is
exclusive, adjacent slices are *strictly disjoint* (no over-read of the boundary
key), so the de-dup guards retry/`next_start` re-delivery, not slice overlap.
Corrected coverage claims in report §2 (the tiling and the boundary paragraph),
§9 (the range-slicing notable), and reconciliation M1/M3 (M3 flipped Corroborated
→ Contradicted, source-derived). Registered as **F1**, an `[SRC]`-hypothesis
(labelled, not run-proven) in report §6 and §10 with a falsification: seed a
bucket where a key equals a cut-point, run `list -k`, count whether it survives.
Only the hinted/parallel path is affected; the no-hints serial smoke runs have no
cut-point and matched the manifest exactly — noted explicitly.

**F2 (Critical, #2) — ACCEPTED; false CORRECTED reverted.** Verified main.rs:52-54
(`force_path_style: bool`, an enable-only clap flag) and main.rs:147
(`opt_force_path_style = cli.force_path_style || opt_endpoint.is_some()`). With an
endpoint set, path-style is always forced; there is no way to select
virtual-hosted style — so the dossier's original "no override" weakness is
*correct*, and the groundwork's `[CORRECTED]` promotion was wrong. Reverted the
README block (now `[SOURCE-CHECKED]`: hypothesis stands, flag is enable-only),
re-verdicted reconciliation's Modes row and weakness #10 from Contradicted →
Corroborated, and fixed the "Dossier changes" note. No endpoint receipt exists;
the endpoint-behavior half stays untested. Also reflected in report §6 endpoint
bullet.

**F3 (Important, #3) — ACCEPTED; rescoped to a silent-incompleteness hypothesis.**
Verified tasks_s3.rs:95-104 (fatal error → `ctx.complete()` + normal return),
data_map.rs:372-376 (dump-what-you-have + normal shutdown), main.rs:346 (`main`
returns → exit 0). Rescoped report §2 ("abort the slice" → fatal-to-slice but
run dumps a partial and exits 0) and added a dedicated §6 hypothesis + §10
falsification (fault-inject a fatal error on one slice, confirm exit 0 + partial
output). reconciliation M9 corrected. Registered as **F2** hypothesis in the
report (benchmark-phase; needs a fault-injection run before it ships as a
finding).

**F4 (Important, #4) — ACCEPTED; provenance record written, receipt cells
corrected.** Wrote `receipts/smoke/_capability/direct-capture.provenance.md`
stating what evidence binds (payload sha256 matching `direct-capture.sha256`;
valid Parquet parse; row counts 148,917 / 2,549 / 15,625 / 9,839 equal to the
manifest; byte-size cross-check against HARNESS-INCOMPATIBILITY.txt; file mtimes
~12:08 UTC) and what is **not recorded** (the exact direct `docker run` command —
`~/.bash_history` last written 09:58 predates the captures; direct-run exit codes;
a per-run image-digest binding; a verification transcript). Corrected all four
receipt verdict cells from "PASS via [OBS] direct capture" to "BLOCKED via
standard path — no verifier PASS; correctness rests on an [OBS] manifest-diff",
and softened "identical invocation" to "intended to replicate this argv, not
independently logged" in the receipts, HARNESS-INCOMPATIBILITY.txt, and report §7/§8.

**F5 (Important, #5) — ACCEPTED; build-log provenance stated.** Confirmed
build-rust1.86-FAIL.txt is a build-log tail (dep-resolution failure + BUILD_EXIT=1)
with no invocation/checkout/Dockerfile-hash/date/box/digest. Prepended a
provenance header reconstructing only what other evidence supports (source
6c72f59; upstream Dockerfile with its pinned `FROM rust:1.86-slim`; arm64 box)
and explicitly listing what is not recorded for the failing attempt. Rescoped
report §7 and reconciliation weakness #9 to call it a build-log tail
demonstrating the rustc incompatibility, not a provenance-complete receipt.

**F6 (Important, #6) — ACCEPTED; adapter caveat added.** Confirmed normalize.sh
emits unquoted DuckDB `-list` output with tab field / newline record separators.
Added a caveat comment in normalize.sh and a note in report §5 + §10: a valid Key
containing a literal TAB or newline breaks the five-column / one-record contract;
the smoke bucket's plain-ASCII keys were unaffected, but the deferred edge-key
bucket cannot be verified through this adapter until it gains binary-safe framing.

**F7 (Important, #7) — ACCEPTED; "inherently" → "typically".** `-k` reads
arbitrary lines, so hand-written hints are a valid third source (reconciliation
already listed it). Changed report §2 and §9 from "inherently two-pass /
inventory-seeded" / "needs a prior full listing or Inventory" to "typically …
(hand-written hints are also accepted)".

**F8 (Important, #8) — ACCEPTED; unreceipted claims labelled/rescoped.** The
trixie/`GLIBC_2.39` "also observed" had no receipt: changed to an [INFERRED]
statement from the Debian release glibc versions (bookworm 2.36, trixie 2.39;
distroless/cc-debian12 provides 2.36) explaining the `-bookworm` pin, not a
claimed run. The architecture table's amd64 "yes" was unsupported: changed to
"not built" / amd64 [INFERRED] buildable-but-not-run, arm64 "built + ran [OBS]",
with prose that a multi-arch base does not prove an amd64 build and §10 now
requires an actual amd64 build before amd64 is relied on.

**F9 (Minor, #9) — ACCEPTED; PLAIN corrected.** Confirmed from the direct-capture
payload metadata that every column carries `PLAIN, RLE, RLE_DICTIONARY`.
`.set_encoding(PLAIN)` is a hint, not a disable. Corrected report §5 and
reconciliation M16 to state the produced Parquets carry all three encodings
[OBS payload metadata] — dictionary encoding is not turned off.

**F10 (Minor, #10) — ACCEPTED; label added.** The blog discusses fixed-`Prefix`
requests; balanced `StartAfter` cut-points can subdivide a clustered prefix.
Reworded report §6 to mark the clustered-keyspace caveat a tool-specific
[INFERRED] extension with the `Prefix`-request account as adjacent [3P] context,
and annotated the §11 source entry accordingly.

**F11 (Minor, #11) — ACCEPTED; attribution split.** The cited README table names
only `m6i.8xlarge`. Corrected reconciliation.md:70 to attribute the 128 GB figure
to the instance-type spec [DOC AWS EC2 M6i], not the README.

**F12 (Minor, #12) — ACCEPTED; anchors made auditable.** Replaced the loose
`[SRC as §2]` / bare `[SRC]` / "module sources" markers with concrete
`file:line @ 6c72f59` anchors in report §9 and §5 (three sites) and reconciliation
(the `-k` Modes row, the no-rate-limit line, and the library-inventory line).

**Disagreements:** none. All 12 findings were verified as stated (with the one
strengthening clarification on F1 that adjacent slices are strictly disjoint, not
over-reading, which makes the boundary-key omission cleaner, not weaker).

## Consolidation review

After Part 1, the public pages were rebuilt into the owner-adopted 3-doc shape
(`README.md` + `mechanism.md` + `running.md`) and reviewed by an independent
cross-model pass before commit.

- Reviewer: codex, model **gpt-5.6-sol**, `model_reasoning_effort=xhigh`,
  `--sandbox read-only`, run by the orchestrator's consolidation step.
- Scope: the consolidation diff (`git diff HEAD` of README/mechanism/running)
  against `research/reconciliation.md` and the pre-consolidation README
  (`git show HEAD:…/README.md`). Hunt targets: reconciliation rows with no
  destination; statuses changed without a receipt; hypotheses narrowed/softened;
  provenance lost; fork-provenance weakened; contradictions between the three
  pages.
- Result: **9 findings — 7 Important, 2 Minor.** Ran to completion (~10 min);
  no stall, no kill.

All 9 were accepted and fixed; none required disagreement.

### Review verbatim

1. **Important — W9 is promoted from partial evidence to settled.** README.md:165 says "Settled by build evidence," while reconciliation.md:99 deliberately says only "partially Settled" because the evidence is a build-log tail without an exact build-context binding. README.md:78 and running.md:16 nevertheless turn it into the categorical public finding that upstream's pinned Dockerfile is broken. No new provenance-complete receipt supports that status change.

2. **Important — `running.md` weakens the load-bearing fork provenance.** running.md:11 describes the smoke image as built from "upstream's own Dockerfile at the pinned SHA" with "one documented deviation," then identifies only the toolchain bump. The pinned SHA is actually the fork containing the 51-line behavioral patch, as README.md:17 and reconciliation.md:14 state. Read independently, the run page makes the artifact appear to differ from upstream only in its builder image.

3. **Important — unreviewed `ks-tool` internals are presented as source-established facts.** mechanism.md:193 and mechanism.md:196 assert the detailed `split` and `inventory` algorithms without evidence labels, even though mechanism.md:200, README.md:129, and reconciliation.md:39 all say those internals were not independently read and remain Unaddressed.

4. **Important — the decision-critical cold-start hypothesis has no destination.** The pre-consolidation dossier required counting the bootstrap pass or Inventory pull in "time to first usable listing" (HEAD:README.md:47-61,218-222,455-460). The replacement benchmark list at README.md:197 retains only hinted-mode sweeps and saturation questions; it drops the explicit end-to-end bootstrap measurement despite claiming at README.md:193 that open hypotheses were carried verbatim.

5. **Important — the inherited panic hypothesis is narrowed to ETags.** The old dossier named three claimed panic surfaces: unexpected ETags, out-of-range match-result values, and channel-send failure (HEAD:README.md:306-312). README.md:160 and README.md:210 preserve only the weird-ETag case; neither of the other inherited hypotheses appears in any of the three consolidated pages.

6. **Important — cancellation latency is simultaneously asserted and Unaddressed.** mechanism.md:39 presents "cancellation feels sluggish" as part of a `[SRC]` statement, but README.md:159 and reconciliation.md:93 explicitly classify actual cancellation latency as Unaddressed because no signal-to-exit run exists.

7. **Important — receipt provenance contradicts the build section.** running.md:140 says every receipt was produced by the shared smoke harness. The same page documents separately executed `docker build` receipts at running.md:41, and README.md:268 includes `_build/` among the committed receipts.

8. **Minor — reconciliation M11 is truncated with no destination for its observability counters.** reconciliation.md:45 corroborates three named atomic error counters and notes they are not surfaced prominently. The replacement M11 row at README.md:111 and the corresponding mechanism text at mechanism.md:39 omit them entirely.

9. **Minor — the unaddressed `ks-tool split` 100 MiB-reader claim disappears.** reconciliation.md:74 explicitly separates the corroborated output buffers from the still-Unaddressed claimed 100 MiB `split` reader. The consolidated numbers table at README.md:140 drops that part, and mechanism.md:188 provides no destination for it.

### Resolutions

1. **FIXED.** README W9 status changed "Settled" → "**Partially** Settled by
   build evidence" (matching reconciliation.md:99), with the build-log-tail
   limitation stated in the cell. The headline bullet changed "is already broken"
   → "no longer builds at its pinned SHA under `rust:1.86-slim`" with the
   build-log-tail / "partially settled, not a provenance-complete receipt"
   qualifier. running.md:16 already labelled the evidence a build-log tail (F5).

2. **FIXED.** running.md Build section now leads with **two** load-bearing
   distinctions from stock upstream: (1) the source is the FORK = upstream
   `b11e385` + the 51-line `--no-sign-request` patch (the binary carries it), and
   (2) the Dockerfile toolchain deviation — so the run page no longer reads as if
   only the builder image differs.

3. **FIXED.** mechanism.md `ks-tool` section now labels the `split`/`inventory`
   algorithm descriptions as the **inherited claim** [DOC + inherited dossier],
   explicitly Unaddressed (internals not read), with the subcommand *existence*
   the only source-verified part [SRC ks-tool/main.rs:15-39].

4. **FIXED.** Added the honest cold-start / "time to first usable listing"
   hypothesis (including the bootstrap `ks-tool inventory` or no-`-k` pass cost,
   and the cold-start-vs-hinted gap as the decision-relevant number) to README's
   Open hypotheses, marked inherited and decision-critical.

5. **FIXED.** README W4 and the Open-hypotheses panic bullet now carry all three
   inherited panic surfaces — weird ETag (located), out-of-range match-result
   enum, and channel-send failure (the latter two not independently located).

6. **FIXED.** mechanism.md now states the no-`CancellationToken` mechanism as
   [SRC] and the "feels sluggish" *latency* as an explicit [INFERRED] consequence
   whose signal-to-exit latency is Unaddressed (no run), cross-linked to the
   benchmark hypotheses.

7. **FIXED.** running.md Reproduction now scopes "produced by the shared harness"
   to the four `list/` smoke rows, and states the `_build/` and `_capability/`
   direct-capture receipts were produced **out of band** — no longer
   contradicting the documented `docker build` and direct-capture procedures.

8. **FIXED.** README M11 and mechanism.md restored the three atomic error
   counters (`task_next_stream_timeout` / `s3_client_timeout` /
   `s3_client_generic_error`, not surfaced prominently) [SRC core.rs:490-492].

9. **FIXED.** README's Parquet-buffers numbers row restored the still-Unaddressed
   `ks-tool split` 100 MiB reader, and mechanism.md's `ks-tool` section notes that
   reader is itself Unaddressed (not read).
