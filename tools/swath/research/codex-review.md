# swath — Stage E adversarial cross-model review

**Reviewer:** Codex CLI (`codex-cli 0.144.5`), model `gpt-5.6-sol`,
`model_reasoning_effort=xhigh`, `--sandbox read-only`. Source checkout and data
dir added read-only. Run 2026-07-17 (repo phase). Pinned SHA re-confirmed by the
reviewer: `f1009db599861a7e905a539778d915f1bb5426eb`.

**Coverage the reviewer reported completing:** verified all 12 receipt payload
pairs against the sha256 in their `run.meta` (all match) and the registry+manifest
hashes; secret-scanned repo materials and the external payload dir (none found);
independently re-derived each mode's normalized output against the manifest;
re-checked every `[SRC]` anchor by targeted lookup against the checkout; reviewed
report/reconciliation/dossier-diff/run.sh/normalize.sh. No stall; one round.

**Rounds:** one round + resolutions (below). A second round was **not** run: the
repo phase is on a 1-hour budget and the finalize-early threshold was reached; all
12 findings were **accepted** and resolved by scoping/labeling/provenance edits (no
contested behavioral claim remained that a re-review would re-adjudicate). The
reviewer is invited to confirm the resolutions in a later pass.

---

## Resolutions (all findings ACCEPTED)

| # | Sev | Finding (short) | Resolution |
| --- | --- | --- | --- |
| F1 | HIGH | Image not bound to pinned source SHA | Added `receipts/smoke/_build/build.md` recording the source→image build; reworded report Image/version rows + README to state the source→image link is an **agent-asserted build fact, not receipt-embedded**; recommend a future OCI source-SHA label. |
| F2 | HIGH | normalize.sh raw-key contract vs control chars | Scoped the "byte-exact for every mode" claim to **this ASCII corpus** (report §5); documented in `normalize.sh` that text sinks escape control bytes and the adapters do not de-escape; flagged `--raw-output`+de-escape hardening for the benchmark. |
| F3 | HIGH | Smoke promoted into proof of internal tiling (M2) | Removed the "no-gap/no-overlap tiling M2" clause from the receipt-backed promotion; scoped it strictly to **observable output** (no missing/extra/dup rows). M2 internal mechanism stays `[DOC]`/`[SRC]`, `VERIFIED: no`. |
| F4 | MED | Private source called "public" / competitor-equivalent | Reworded README modes-update + provenance: **first-party private/pre-release source**, explicitly NOT the public-docs basis competitors rest on; fairness asymmetry preserved. |
| F5 | MED | W3 reconciled against a premise the dossier never made; seed-cost direction | W3 verdict changed from "Contradicted (premise)" → **"Unaddressed (comparative) + standalone note"**; corrected the direction: default `--seed shallow` used **fewer** calls (339) than `--seed none` (516) — the seed *reduced* calls; one run/arm is non-causal. |
| F6 | MED | "every run peak_in_flight=8, splits>0" is false | Scoped to the full-bucket recursive-tsv run; added that `hourly` had `splits=0` and `seed-none` peaked at 6/4 — parallelism is scope-dependent. |
| F7 | MED | Capability receipts don't prove all 15,625 keys written | Reworded to **self-reported `objects=15625`; dataset destroyed, no verifier ran** — execution proven, fidelity not. |
| F8 | MED | Aligned normalize assumes 24-char time; false row-type drop claim | Documented the fixed-column/second-precision assumption and that aligned carries **no row_type** to filter (relies on recursive listing emitting only objects), in `normalize.sh` + report §5. |
| F9 | MED | Metadata citations missing/contradictory; THIRD_PARTY cites a nonexistent LICENSE | License evidence corrected: cite the **absent file** `[OBS]` + `THIRD_PARTY_NOTICES.md:13`'s **dangling LICENSE reference** `[SRC]` (not `:1`); added the `api.github.com/repos/varveio/swath` URL; relabeled releases-absence `[3P]`; noted `[RUN]` proves the version string only. |
| F10 | MED | Error-classification claim broader than anchor | Scoped typed-fatal to the exact `(status,code)` pairs; extended anchors to `S3PageFetcher.java:300-478` + `633-671`; dropped the unsupported "no panics-on-transient" phrasing. |
| F11 | MED | Native amd64 promoted without amd64 build/run | Relabeled amd64 support `[INFERRED]` from the Dockerfile (arch-neutral bytecode + multi-arch base), **not built/run**; softened "unconstrained"; benchmark must confirm an amd64 build+run. |
| F12 | LOW | Dossier edits' new behavioral claims lacked labels | Added `[SRC]` anchors to the README modes-update claims (hints-throws, inspect/diff stubs, no delimiter output) and to the License/version rows. |

**Disagreements:** none — every finding was a legitimate overstatement, scoping, or
provenance gap and was corrected. The corrections uniformly *narrow* claims toward
the receipts; none change a PASS verdict or a receipt.

---

## Reviewer output (sanitized)

The finding text is preserved, but private filesystem paths have been removed;
references to files in this repository use portable relative links.

## Findings

1. **HIGH — Tested image is not bound to pinned source SHA.**  
   [report.md:24](report.md), [report.md:251](report.md), [README.md:20](tool-page.md), [README.md:161](tool-page.md), all `run.meta` files, e.g. [run.meta:7](../receipts/smoke/recursive-tsv/full/run.meta). Receipts bind runs to an image digest and generic `0.1.0-SNAPSHOT` version, but contain no source SHA, OCI revision label, or build receipt. They cannot establish that the tested bytes came from `f1009db`; therefore `[RUN] ... @ f1009db` is unsupported.

2. **HIGH — `normalize.sh` violates its raw-key contract for control characters.**  
   [normalize.sh:7](../adapter/normalize.sh), [normalize.sh:29](../adapter/normalize.sh), [normalize.sh:39](../adapter/normalize.sh), [normalize.sh:49](../adapter/normalize.sh), [report.md:191](report.md), [report.md:206](report.md). TSV/aligned output escapes control bytes as `\xHH`, while JSONL’s `jq @tsv` escapes tabs/newlines; none of the adapters decodes them. The ASCII-only smoke bucket cannot support the report’s general “raw keys” or “byte-exact for every mode” claim.

3. **HIGH — Smoke output is promoted into proof of the internal tiling mechanism.**  
   [reconciliation.md:78](reconciliation.md). The receipts prove no missing, extra, or duplicate rows for the tested invocations and bucket. They do not prove that internal ranges were disjoint, that no-gap/no-overlap “falls out by construction,” or that the invariant holds for other keyspace shapes. Explicitly promoting M2 exceeds the receipts.

4. **MEDIUM — Private source is described as “public” and equivalent to competitors’ public evidence.**  
   [README.md:17](tool-page.md), [README.md:48](tool-page.md), [README.md:54](tool-page.md), [README.md:152](tool-page.md), [report.md:18](report.md). The repository is recorded as private at research time, yet the dossier calls the checkout “public source” and claims the same public-only basis used for competitors. That fairness/provenance claim is internally contradictory.

5. **MEDIUM — W3 is reconciled against a premise the dossier never made.**  
   [README.md:103](tool-page.md), [README.md:107](tool-page.md), [reconciliation.md:51](reconciliation.md). The inherited hypothesis compares swath-cold with `s3-fast-list`-cold and `s3-fast-list`-hinted; it never posits a “swath-hinted” arm. Calling that premise contradicted is a strawman. Moreover, 516 calls for `seed none` versus 339 for `shallow` shows this particular seed pass reduced total calls; it does not demonstrate that discovery itself “costs something,” nor is one run per arm a causal measurement.

6. **MEDIUM — “Every run” request-behavior claim is false.**  
   [report.md:65](report.md). The report says every run reached `peak_in_flight=8` with steals and splits greater than zero. The hourly run recorded `splits=0` ([stderr.txt:15](../receipts/smoke/recursive-tsv/hourly/stderr.txt)); seed-none full peaked at 6 ([stderr.txt:14](../receipts/smoke/seed-none/full/stderr.txt)); seed-none monthly peaked at 4 ([stderr.txt:14](../receipts/smoke/seed-none/monthly1991/stderr.txt)).

7. **MEDIUM — Capability receipts do not prove all 15,625 keys were written.**  
   [report.md:318](report.md), [parquet receipt.md:15](../receipts/smoke/_capability/parquet-probe/receipt.md), [sort receipt.md:15](../receipts/smoke/_capability/sort-probe/receipt.md). Both probes have empty stdout, no verifier verdict, and destroyed file output. Exit zero and self-reported counters establish that the paths executed, not that their datasets contained all keys. “Listing all 15,625 keys” contradicts the report’s own statement that output was uncapturable and unverifiable.

8. **MEDIUM — Aligned normalization assumes a timestamp width the source does not guarantee.**  
   [report.md:193](report.md), [report.md:204](report.md), [normalize.sh:50](../adapter/normalize.sh), `S3PageFetcher.java:724`, `Fields.java:13`. Source preserves microseconds and `ISO_INSTANT` may emit more than 24 characters; the aligned formatter treats 24 as a minimum, while the normalizer unconditionally takes 24 characters and starts the key at column 43. Fractional microseconds therefore truncate the timestamp and shift bytes into the key. Its claim to drop CommonPrefix/DeleteMarker rows is also false for aligned output, which exposes no row type.

9. **MEDIUM — Metadata citations are missing, non-reproducible, or contradictory.**  
   [report.md:17](report.md), [report.md:22](report.md), [README.md:19](tool-page.md), [README.md:20](tool-page.md), [reconciliation.md:21](reconciliation.md). `[3P github api]` is not the promised URL and no API payload preserves visibility, issue count, creation/push dates, `license=null`, or release results. `[RUN]` proves the version string, not absence of releases/tags. `THIRD_PARTY_NOTICES.md:1` does not prove absence of a license; the same file explicitly says swath is covered by the repository’s own `LICENSE` (`THIRD_PARTY_NOTICES.md:13`), although no such file exists.

10. **MEDIUM — Error-classification claim is broader than its source anchor.**  
    [report.md:78](report.md), [report.md:231](report.md), `S3PageFetcher.java:300`. Not every 403/404 receives the claimed specific type: only `(403, AccessDenied)` and `(404, NoSuchBucket)` do (`S3PageFetcher.java:640`). The cited range also ends before the socket-closure handler at line 432. “No panics-on-transient” is therefore not established by the anchor.

11. **MEDIUM — Native amd64 support is promoted without an amd64 build or run receipt.**  
    [report.md:264](report.md). Only arm64 was exercised. Dockerfile comments about architecture-neutral bytecode and bundled native libraries do not verify the base-image manifest or an actual amd64 build/runtime. “Both amd64 and arm64 are natively supported” and “common-denominator arch is unconstrained” overstate the evidence.

12. **LOW — The dossier’s evidence-label guarantee is already violated by its edits.**  
    [README.md:54](tool-page.md), [README.md:146](tool-page.md). The newly added behavioral claims—unimplemented hints, stub commands, and absence of delimiter output—carry no attached `[SRC]`/`[RUN]` anchors, despite the provenance section asserting that every firsthand fact is so labeled.
---

## Consolidation review

**Reviewer:** Codex CLI (`codex-cli 0.144.5`), model `gpt-5.6-sol`,
`model_reasoning_effort=xhigh`, `--sandbox read-only`. Run 2026-07-17
(consolidation phase). Reviewed the consolidation diff (`git diff HEAD --
tools/swath/{README.md,mechanism.md,running.md}`) against
`research/reconciliation.md` and the pre-consolidation README. One round; no
stall. Nine findings raised (3 HIGH, 6 MEDIUM); all **ACCEPTED** and resolved by
narrowing/scoping edits to the three consolidation pages only (research/ and
receipts/ untouched except this append).

| # | Sev | Finding (short) | Resolution |
| --- | --- | --- | --- |
| C1 | HIGH | Inadmissible internal benchmark history had been cited, contrary to the study's evidence policy | Removed the private run history. Sort-memory behavior remains a study-owned, **VERIFIED: no** hypothesis that must be tested under this harness before it is classified. |
| C2 | HIGH | Self-reported concurrency promoted to wire-level proof ("genuinely parallel at the wire"); M1 marked CONFIRMED for the full "adaptive, density-aware" phrase | Dropped "at the wire"; reworded to "genuinely parallel — multiple LISTs concurrently in flight … on swath's own self-reported counters (not an independent wire capture)." M1 ledger row split: CONFIRMED for *parallelism only*; "adaptive/density-aware" is the M2 sampling nuance, VERIFIED: no. |
| C3 | HIGH | Ledger inflated `Corroborated` into `CONFIRMED` — repo/language ([3P]/[SRC], no receipt) and W6 ("Corroborated at smoke" → "CONFIRMED") | Added a distinct **Corroborated** status (source/[3P]-agreed, not receipt-backed) to the legend. Relabeled repo, language, version rows and W6 to Corroborated; CONFIRMED now reserved for receipt-settled run facts. |
| C4 | MED | Seed comparison retained a causal claim ("seed reduced calls") its own caveat disclaims | Reworded headline + running.md to observational: the run *with* the seed *recorded* fewer calls (339 vs 516); receipts settle the observed counts, not a causal effect. Caveat kept. |
| C5 | MED | Two smoke runs generalized into a "scale-sensitive" law (confounds size + shape, no repeats) | Reworded to "probe overhead was far higher on the small prefix (43.9 vs 2.28/1k) — these two runs vary both size and keyspace shape, no repeats, so they settle the two ratios, not a general law"; cross-referenced the open question. |
| C6 | MED | "Smaller scopes parallelize less" contradicts the table — `hourly` reached the `peak_in_flight=8` cap (splits=0), only un-seeded runs peaked lower | Corrected across all three pages: splitting is scope-dependent (`hourly` splits=0 but hit the cap via steals); only the un-seeded `seed-none` path reached lower peak concurrency (6/4). "Small scope = less parallel" explicitly disclaimed. |
| C7 | MED | Inherited unfavorable capability gap (no delimiter/CommonPrefix output mode) moved below the fold, absent from the Unfavorable-findings block | Added "No `ls`-style delimiter/CommonPrefix *output* mode" as an explicit bullet in the README Unfavorable-findings block, `[SRC]`-anchored. |
| C8 | MED | A reconciliation routing row lost its destination — M2/W1/N1/N2's characterizations of S3P ("ASCII midpoints") and s3-fast-list (".ks hints", 3.1M obj/s) | Added a routing paragraph naming those as claims about other tools' pages, routed to the orchestrator per reconciliation § "Items routed to the orchestrator"; this page uses them only as inherited framing, never adjudicated fact. |
| C9 | MED | Strongest same-treatment safeguard dropped — the old "verify identical harness treatment first" priority had no equivalent parity gate | Added item 0 "Same-treatment audit (the parity gate — verify this first)" to the Open-hypotheses queue: identical definition-of-done, sweep, repeat count, box, window; any special-casing is a study bug, taking precedence over the swath-specific experiments. |

**Disagreements:** none — every finding narrowed a claim toward its receipt,
restored a dropped unfavorable/safeguard, or fixed a cross-page contradiction;
none changed a PASS verdict or a receipt. The corrections are uniformly
narrowing.

### Reviewer output (sanitized)

The private run particulars in C1 have been removed. The remaining finding text
is preserved.

- **HIGH — Inadmissible internal benchmark history is resurrected.** The
  consolidation cited private run history. This contradicts the study policy
  that Swath's internal benchmark history is inadmissible and must not be cited.

- **HIGH — Self-reported concurrency is promoted to wire-level proof.** README.md:49 says LISTs are "genuinely parallel at the wire," while mechanism.md:182 explicitly says the supporting counters are not an independent wire-level measurement. The receipt settles only that swath reported `peak_in_flight=8`; it does not independently settle wire behavior. The compound M1 status at README.md:125 additionally marks "adaptive, density-aware parallel LIST" confirmed although the receipt bears only on parallelism.

- **HIGH — The claim ledger inflates `Corroborated` into `CONFIRMED`.** README.md:112 defines `CONFIRMED` as receipt-backed, but README.md:121 and README.md:122 confirm repo/language using only `[3P]` and `[SRC]`; reconciliation classified both merely `Corroborated` at reconciliation.md:21. More seriously, README.md:141 changes W6 from "Corroborated at smoke scale" to "CONFIRMED," although zero 503s settles only that AIMD was idle—not the latency-bound/dead-weight hypothesis.

- **MEDIUM — The seed comparison retains a causal claim that its own caveat disclaims.** README.md:90 and running.md:86 say `--seed none` "cost MORE" and shallow "reduced" calls, then acknowledge one run per arm is non-causal. Reconciliation line 53 supports only the observed counts, 516 versus 339; it does not settle that the seed choice caused the difference.

- **MEDIUM — Two smoke scopes are generalized into a scale result.** README.md:96 and running.md:90 declare probe overhead "scale-sensitive" and say probes "do not amortize on a small keyspace." The runs vary both size and keyspace shape and have no repeats, so they settle only 2.28 versus 43.9 calls/1k for those invocations. README.md:162 simultaneously leaves overhead-versus-scale/shape as an open benchmark question.

- **MEDIUM — The claimed small-scope parallelism reduction contradicts the run table.** README.md:53 and mechanism.md:31 infer that "smaller … scopes parallelize less" from the hourly run, but running.md:73 shows that run reached the full `peak_in_flight=8` cap. `splits=0` shows less dynamic splitting, not less concurrent LIST parallelism. Only the unseeded runs recorded lower peak concurrency.

- **MEDIUM — An inherited unfavorable capability gap was moved below the fold.** `HEAD:tools/swath/README.md:60-65` prominently records that swath has no delimiter/CommonPrefix output mode. It is absent from the new "Unfavorable findings (full prominence, not below the fold)" block at README.md:78 and survives only deep in mechanism.md:165, contradicting the prominence policy at README.md:7.

- **MEDIUM — A reconciliation routing row has no destination.** reconciliation.md:98 routes M2's claims about S3P's ASCII midpoints and `s3-fast-list`'s hints for validation on those tools' pages. The consolidated ledger anonymizes them as "blind midpoints and up-front hints" at README.md:126, while the routing paragraph at README.md:147 covers only cross-cutting claims. The promised destination is lost despite README.md:143 asserting no inherited claim was dropped.

- **MEDIUM — The strongest same-treatment safeguard was dropped.** `HEAD:tools/swath/README.md:141-150` made identical harness treatment—definition of done, sweep, repeats, box, and window—the first verification priority. The replacement benchmark queue at README.md:153 contains only swath-specific experiments and no equivalent parity gate. The generic tuning-parity sentence at README.md:205 does not preserve that concrete audit requirement.
