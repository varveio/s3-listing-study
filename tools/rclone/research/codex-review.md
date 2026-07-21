# Stage E — adversarial cross-model review (codex, gpt-5.6-sol)

## Coverage and how this review was run (read first)

Two codex runs were made:

1. **Full review, xhigh, with the source tree** (`--add-dir …/rclone`, per the
   brief's canonical command). It **hit its 780 s cap with no output** —
   exactly the failure the brief's Stage E budget guard anticipates for a large
   Go checkout at `xhigh`.
2. **Budget-guard fallback — scoped review, `medium` effort, source tree
   dropped** (only the repo artifacts + `<data>` for payload
   hashing/secret-scan). This **completed** and produced the eight findings
   below.

**What this review covered:** `report.md`, `reconciliation.md`, `README.md`
(+ its diff), `run.sh`, `normalize.sh`, every smoke receipt (`run.meta`,
`receipt.md`, `verify.md`), and the external payloads under
`<data>/receipts/rclone/` (sha256 re-verification and secret scan).

**Explicit gap (for the orchestrator to close out-of-band):** because the source
tree was dropped in the completed run, codex did **not** re-verify the `[SRC]`
line-number anchors against the `v1.74.4` / `5bc93a2a7` checkout. As a partial
substitute the authoring agent re-verified the load-bearing anchors by hand
(ModTime :4166, MimeType :5281, `lsjson.go` :181/:184, anon :1510, delimiter
:2432, MaxKeys :2454, checkers `fs/walk/walk.go` :380, pacer
`lib/pacer/pacers.go` :86-101/NewS3 :43-51) and they hold. An independent
anchor audit of the remaining `[SRC]` citations is the un-reviewed remainder.

**Round count:** one substantive round. Two Highs were found; both were
**resolved by fixing the artifacts** (not disputed), and no severe *disagreement*
remained, so — with the repo-phase budget exhausted (finalize-early in force) — a
second codex round was forgone. This is the sanctioned scoped-review fallback:
an honestly-labeled scoped review beats a timed-out full one.

## Findings (verbatim) and resolutions

> **High** — normalize.sh:8/49/65: the adapter promises "raw key bytes, no
> re-encoding," but `jq @tsv` escapes tabs, newlines, carriage returns, and
> backslashes. Such S3 keys are emitted with different bytes, while the smoke run
> explicitly deferred weird-key testing.

**Resolved (fixed).** Correct — `jq @tsv` C-escapes TAB/NL/CR/BACKSLASH. The
"raw key bytes, no re-encoding" wording was removed from `normalize.sh` and
replaced with an explicit key-byte-fidelity caveat: NOAA keys contain none of
those bytes (so output is byte-exact for this bucket), genuine weird-key fidelity
is a deferred edge-case check (`EDGE_BUCKET=none`), and an edge bucket would need
a raw-bytes decode path first.

> **High** — README.md:41, reconciliation.md:24/44: "neither mode shards" and the
> default walk's concurrency mechanism are promoted as **settled by smoke**, but
> the request-shape probe covers only `--fast-list`. The hierarchical receipt
> verifies output completeness; it records no request trace or observed
> concurrency. Those default-walk conclusions remain `[SRC]`, not run-settled.

**Resolved (fixed).** Correct and important. The README smoke-settled note and
reconciliation M5 + W1 now **split the labels**: `--fast-list`'s serial single
chain is `[RUN _capability/debug]`; the default walk's across-directory-only
parallelism (no intra-prefix sharding) is downgraded to `[SRC fs/walk/walk.go:380]`
— read from source, *not* traced at smoke (the hierarchical receipt verifies
completeness only).

> **Medium** — report.md:226, recursive-hierarchical/receipt.md:104,
> delimiter-shallow/receipt.md:104: `ceil(keys / list_chunk)` is generalized to
> "any scope." It applies to a flat undelimited chain, not hierarchical or
> delimiter listings, whose requests are divided by directory.

**Resolved (fixed).** `report.md` §5 now scopes the `ceil(keys/list_chunk)`
formula to flat/undelimited (`--fast-list`/full-recursive) listings and states
that hierarchical/delimiter listings issue one LIST per directory, so their count
is set by tree shape, not key total.

> **Medium** — reconciliation.md:35, report.md:137: `--s3-list-chunk` is marked
> **settled by smoke**, although smoke only observed the default `max-keys=1000`;
> no non-default value was run.

**Resolved (fixed).** Reconciliation T4 downgraded from "Settled by smoke run" to
"**Corroborated** (default observed; tunability not smoked)" — the receipt
supports the observed default parameter, not the flag's effect. Flagged for the
benchmark sweep. Report §3's list-chunk line already reads "left at 1000 … a pure
sweep item."

> **Medium** — README.md:205/218, _capability/debug/receipt.md:15: the receipt
> table says all entries are **PASS**, but the included debug probe has no
> verifier verdict and no `verify.md`.

**Resolved (fixed).** README receipts intro reworded: "every **verifier-checked
mode** is PASS; the request-shape probe carries **no verifier verdict**"
(underscore `_capability/` dir, exempt); the probe's table row is relabeled
"_request-shape probe (no verdict)_".

> **Medium** — report.md:166/321: the report says plain default `rclone lsjson -R`
> adds approximately 149 LIST requests. ~149 is the flat full-bucket page count and
> cannot be inferred for the default (per-directory) traversal from the key count.

**Resolved (fixed).** Both passages (§4 footgun, §9 notable) now attribute ≈149
pages to `--fast-list` specifically and note the default hierarchical walk issues
*more* (one LIST per directory), with the HEAD storm dominating either way;
labeled `[INFERRED]` for the LIST-count comparison.

> **Medium** — report.md:137/173/306/313: several behavioral conclusions lack the
> report's required evidence labels: smaller list chunks "only add pages,"
> `--checkers` bounds memory, no hinted/two-pass workflow exists, no fan-out
> workaround applies, and several modes have internal concurrency exactly one.

**Resolved (fixed).** Labels added to each: list-chunk "only add pages"
`[INFERRED, SRC :2472]`; `--checkers`/no-two-pass `[INFERRED, SRC fs/walk/walk.go:380; DOC]`;
fan-out N/A `[SRC fs/walk/walk.go:380]`; internal-concurrency-1
`[SRC backend/s3/s3.go:2472]`.

> **Low** — README.md:179/201: the provenance section still states that no
> behavioral claim was checked upstream and "Nothing on this page has been run by
> us," contradicting the immediately following firsthand-source section and the
> added smoke receipts.

**Resolved (fixed).** Both sentences scoped to "as originally written" and marked
superseded (2026-07-17) for the `[smoke-settled]` claims, pointing at the
firsthand addendum and Receipts section; the scale-dependent claims are noted as
still unrun.

## Net

All eight findings accepted and fixed; none disputed. No promotion survived that a
receipt does not back, and every default-walk / tunability conclusion the probe
did not actually trace was pulled back to `[SRC]`/`Corroborated`. Un-reviewed
remainder: independent `[SRC]` anchor audit against the checkout (gap noted above).

## Round 2 — orchestrator-run anchor audit (out-of-band, xhigh, source included)

The Stage-E gap named above — no cross-model `[SRC]` anchor verification, because
the completed scoped run had dropped the source tree — was closed out-of-band by
the orchestrator: **codex `gpt-5.6-sol` @ `model_reasoning_effort=xhigh` with the
pinned source** (`<sources>/rclone` @ `5bc93a2a7`) ran a targeted
anchor verification. It returned **7 findings (3 High, 3 Medium, 1 Low)**. Each was
re-verified by hand against the pinned source and the live tool before acting; all
7 were **confirmed correct** and resolved. The review is reproduced verbatim, then
each finding's resolution.

### Findings (verbatim)

> - **High — the "recursive-hierarchical" mode never exercised hierarchical
>   traversal.** run.sh:49, report.md:36, report.md:121, reconciliation.md:21,
>   README.md:41, receipt.md:21. Recursive `lsjson` calls `walk.ListR` directly
>   (lsjson.go:248); with unbounded `-R`, it selects S3's backend `ListR`
>   regardless of `--fast-list` (walk.go:149). Therefore `--checkers 4` was inert,
>   the receipt is another flat undelimited listing, and the promotions claiming
>   both request patterns ran—or that `--checkers` affected this pure listing—are
>   unsupported.
>
> - **High — the planned OOM experiment targets a different code path and version
>   from the cited failure.** README.md:81, README.md:136, reconciliation.md:45,
>   report.md:356. Issues #7966 and #7974 concern `sync`, originally on v1.67-era
>   behavior; the pinned v1.74.4 contains the v1.70 `--list-cutoff` fix, which
>   externally sorts directories over one million entries (config.go:281,
>   sorter.go:26, faq.md:285). Conversely, the proposed `lsjson --fast-list` path
>   streams entries, as the report itself notes at report.md:96. A cgroup test of
>   that pure-listing path cannot confirm or refute the cited sync failure or its
>   wrapper-level exit behavior.
>
> - **High — the exit-0 provenance clarification is factually wrong, although
>   `VERIFIED: no` is correctly retained.** README.md:103, README.md:108,
>   reconciliation.md:46. Issue #7974 explicitly repeats the exit-0 allegation and
>   links #7966. Both issues were filed five days apart by the same reporter about
>   the same datalake/reorganization scenario. They are distinct issue records, but
>   the evidence does not support "different reports, not one described twice," and
>   directly contradicts "#7974 does not allege exit 0."
>
> - **Medium — the pacer correction cites the wrong calculator and states the wrong
>   success endpoint.** README.md:60, README.md:65, reconciliation.md:25,
>   reconciliation.md:51. Cited lines 43–101 implement `Default`, not `S3`; `NewS3`
>   and `S3.Calculate` are at pacers.go:220 and pacers.go:270. The S3 calculator
>   decays sleep to zero once below `minSleep`, not "toward `minSleep`." The broader
>   error-driven-not-latency conclusion survives, but its attached anchor and
>   formula do not.
>
> - **Medium — the default-walk memory recommendation overstates its source.**
>   report.md:181. `fs/walk/walk.go:380` establishes a checker-sized channel, not
>   traversal "one level at a time." Workers asynchronously enqueue child
>   directories while other jobs continue (walk.go:393); levels may overlap.
>
> - **Medium — the delimiter receipt still generalizes the fast-list page-count
>   formula to an unsupported mode.** delimiter-shallow/receipt.md:112. The debug
>   trace proves `ceil(keys/list_chunk)` only for its undelimited chain. A delimiter
>   listing pages objects plus `CommonPrefixes`; total descendant object count does
>   not determine requests. codex-review.md:68 says this finding was resolved, but
>   only the report was corrected.
>
> - **Low — the verdict summary still counts T4 as smoke-settled after downgrading
>   it.** reconciliation.md:35 labels `--s3-list-chunk` merely corroborated because
>   no non-default value ran, while reconciliation.md:85 still includes T4 among
>   nine claims "Settled by smoke run."

### Per-finding resolutions

**Finding 1 (High) — RESOLVED, with a genuine walk now RUN.** Confirmed against
source **and empirically**: `lsjson -R` calls `walk.ListR` [SRC fs/operations/lsjson.go:248],
which uses the flat backend `ListR` whenever `maxLevel<0` and **never consults
`--fast-list`** [SRC fs/walk/walk.go:149-163]; a live `-vv --dump headers` trace of
the exact old `recursive-hierarchical` argv issues **0 `delimiter=` requests, one
undelimited chain** — proving `--checkers 4` was inert and the receipt was a third
flat listing. Fixed everywhere: report §2 (rewritten with the correct ListR-selection
logic), §3 modes table + tunables, §5, §8, §9; reconciliation M2/M3/M5/T1/T3/W1 +
verdict counts + new finding #5; README mechanism block, `--checkers` note, receipts
table; and the `recursive-hierarchical` **receipt annotated with a correction block**
(history preserved, not rewritten). **Genuine walk then smoked:** `--disable ListR`
forces the per-directory `Walk` [SRC fs/features.go:216-249, fs/walk/walk.go:152-160];
run and traced as new receipts — `recursive-walk` **PASS 9841/9841** (0 missing/extra/
dups/field-mismatches, standard verifier) and `_capability/walk-debug` (**13
`ListObjectsV2` requests, every one `delimiter=%2F`, one per directory**, 0
`Authorization`). Hierarchical path is therefore **VERIFIED: yes** at smoke scale.
(Bucket-drift note: NOAA re-uploaded `normals-hourly/`+`normals-monthly/` mid-session,
so the walk was verified on the still-un-drifted `normals-annualseasonal/1981-2010/`
scope; the drift is independently confirmed by the harness re-list — not a tool
finding.)

**Finding 2 (High) — RESOLVED.** Confirmed: `list_cutoff` default 1,000,000 with
on-disk external sort [SRC fs/config.go:281, fs/list/sorter.go:26 + `lanrat/extsort`].
reconciliation W2 rewritten with the honest experiment spec: what a cgroup test of
the streaming `lsjson --fast-list` path CAN establish (memory-boundedness, RSS,
exit-code at scale), what it CANNOT (the sync-path OOM or its exit behaviour — needs
a sync-shaped workload), and the version delta (allegation is v1.67-era sync; pinned
v1.74.4 postdates the v1.70 fix). Claim stays `VERIFIED: no` with the design caveat.
Mirrored in README's exit-0 block and the benchmark-gates note.

**Finding 3 (High) — RESOLVED (highest-stakes; wording verbatim).** Confirmed by
reading both issues in full via the GitHub API: **#7974 explicitly states "when the
rclone process gets a kill signal, it will exit 0" and links #7966**; both by
reporter `zackees`, five days apart (2024-07-20 / -07-25), same datalake scenario.
Corrected wording (README + reconciliation W3): **two distinct issue records, same
reporter/scenario, both alleging exit-0** — explicitly superseding both the round-1
"one described twice" and the round-1 overcorrection "#7974 does not allege exit 0."

**Finding 4 (Medium) — RESOLVED.** Confirmed: the S3 backend uses the `S3`
calculator — struct at [SRC lib/pacer/pacers.go:220], `NewS3` at [:233], `Calculate`
at [:270-294]; round-1's `:86-101`/`:43-51` is the `Default` calculator. And on
success `S3.Calculate` **drops sleep to zero below `minSleep`** (`if sleepTime <
c.minSleep { sleepTime = 0 }` [:289-293]), not "toward `minSleep`" (that is
`Default`'s floor). Anchors + the decay endpoint corrected in README and
reconciliation M6/M7/W8; error-driven-not-latency conclusion retained.

**Finding 5 (Medium) — RESOLVED.** Confirmed: workers asynchronously enqueue child
directories [SRC fs/walk/walk.go:393] behind a `--checkers`-deep channel [:380], so
levels overlap. report §4 "one level at a time" replaced with the accurate model
(footprint bounded by the checker-deep job channel + retained `dirMap`/sort state,
not by tree depth).

**Finding 6 (Medium) — RESOLVED.** Confirmed the `ceil(keys/list_chunk)` formula was
still generalized in `delimiter-shallow/receipt.md`. Added a correction block scoping
the formula to the flat undelimited chain only, noting a delimiter/hierarchical
listing pages per directory (`CommonPrefixes` are not descendant objects), and
pointing at the `_capability/walk-debug` trace (13 delimited requests). The
`recursive-hierarchical` receipt's inherited copy of the same formula is covered by
its correction block.

**Finding 7 (Low) — RESOLVED.** T4 removed from the "Settled by smoke run" tally in
reconciliation's verdict counts (now 8, listing the genuine `recursive-walk` in
place of the removed T1/T4); T4 is listed under Corroborated, consistent with its row.

### Net (round 2)

All 7 findings confirmed against the pinned source and resolved. The one materially
new outcome beyond edits: the **genuine hierarchical walk was run and verified**
(PASS + delimited per-directory trace), closing Finding 1's "actually smoke the
walk" requirement and moving the hierarchical path from an unsupported promotion to
a receipt-backed **VERIFIED: yes** at smoke scale — while the mislabeled
`recursive-hierarchical` receipt is preserved and annotated rather than deleted.

## Consolidation review

The consolidation into the owner-adopted 3-doc shape (README.md, mechanism.md,
running.md) was reviewed by **codex `gpt-5.6-sol` @ xhigh**, read-only, against
`research/reconciliation.md` and the pre-consolidation README (`git show
HEAD:tools/rclone/README.md`), hunting for: reconciliation rows with no destination,
statuses changed without a receipt, exit-0-on-OOM provenance drift, hypotheses
narrowed/softened, provenance lost, and cross-page contradictions. It returned **6
findings (4 P1, 2 P2)** — all consistency/fidelity issues in the consolidation, all
accepted and fixed.

- **P1 — exit-0 provenance not verbatim / safeguards dropped / benchmark spec drops
  "scaled bucket".** Fixed: the README Verdict block now carries the full round-2
  safeguards — "this corrects the citation… nothing more: it does not settle the
  behavioural claim (`VERIFIED: no`)", "every run this phase exited 0 legitimately;
  nothing here reproduces or refutes the allegation" — and the benchmark spec #2
  restores the **scaled bucket** requirement alongside sync-shape + cgroup cap +
  exit code.
- **P1 — source/docs-only claims promoted to `CONFIRMED`.** Fixed: added a
  **CORROBORATED** status to the ledger (mapped to reconciliation's "Corroborated")
  and reclassified M4-tradeoff, T3-`--transfers`, T5, T6-noParquet/W7, and the
  stars/backends/pacer-exists strengths from CONFIRMED to CORROBORATED. The HEAD-storm
  row now splits CONFIRMED (suppressed correct path, receipt) from CORROBORATED (the
  storm magnitude, source/inferred, never run); the selector row marks the "plain
  `-R` is flat" runtime check as `[OBS]` (ad-hoc trace), not a committed receipt,
  while keeping CONFIRMED for the receipt-backed `--disable ListR` flip.
- **P1 — memory hypothesis narrowed into a conclusion (mechanism.md).** Fixed: the
  memory-model paragraph now labels the streaming/`dirMap` mechanism as `[SRC]`/
  `[INFERRED]` and states explicitly that "nothing discarded until finish" and
  whole-listing residency stay `VERIFIED: no` (M4/W2) — source does not settle
  scale.
- **P1 — crash-resume status contradicts across pages.** Fixed: mechanism.md's
  Resume/checkpoint paragraph now frames "no checkpoint state" as a source
  observation and explicitly keeps the claim `VERIFIED: no` (W6), pending the
  SIGKILL-and-resume protocol — consistent with the README ledger.
- **P2 — "every listing mode" overclaims.** Fixed: the README intro now scopes the
  claim to every mode that changes the S3 request pattern, and states the versions
  API was not smoked (unversioned bucket) and `ls`/`lsl`/`lsd` are output-format
  variants not separately timed.
- **P2 — inherited-dossier provenance had no destination.** Fixed: a **Provenance**
  section restored to the consolidated README, preserving the claim-level lineage
  (inherited-secondhand vs the not-inherited metadata/flag cells vs the firsthand
  source+receipt additions, including the separate #7966 note) and pointing to
  `reconciliation.md` and `report.md` § 11.

No finding disputed the round-1 corrections or the genuine-walk receipt; all six
were consolidation-fidelity issues, now resolved.
