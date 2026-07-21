# swath — dossier reconciliation (Stage D)

Walks **every inherited claim** in `tools/swath/README.md` (and the
swath-naming claims in `docs/open-questions.md`) against my independent
Stage A–C work (report: `research/report.md`; pinned checkout
`f1009db599861a7e905a539778d915f1bb5426eb`; receipts under `receipts/smoke/`).

Verdicts: **Corroborated** (independent work found the same) · **Contradicted**
(found otherwise, both sides shown) · **Unaddressed** (my work didn't touch it —
stays an open hypothesis) · **Settled by smoke run** (a committed receipt
genuinely decides it, scoped to version/invocation/bucket-at-snapshot).

Promotion discipline (AGENTS.md): only a committed receipt moves a claim out of
`VERIFIED: no`; source/doc reading never does. Any swath performance number in
the checkout or dossier is `[DOC]` self-published — never comparative evidence.

## Metadata table

| Inherited | Verdict | Evidence |
| --- | --- | --- |
| Repo `github.com/varveio/swath` | **Corroborated** | GitHub API confirms canonical org repo `[3P]` |
| Language: Java 25 | **Corroborated** | JDK 25 toolchain `[SRC build-logic/.../swath.java-conventions.gradle.kts:24]`; ~593 `.java` |
| License: TBD | **Corroborated + sharpened** (editorial) | Still unspecified — **no `LICENSE`/`COPYING` file at the pinned SHA**, GitHub `license=null`; `THIRD_PARTY_NOTICES.md` covers bundled deps only `[SRC THIRD_PARTY_NOTICES.md:1]` |
| Version reviewed: pre-release | **Corroborated + sharpened** | `swath 0.1.0-SNAPSHOT` `[RUN]`; no releases/tags (only `backup/*`), HEAD `f1009db` pinned |

## Mechanism & claimed properties

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| M1 | Adaptive, density-aware **parallel LIST** of a single bucket | **Corroborated** | Single `WorkStealingScan` engine; LISTs run in parallel (`peak_in_flight=8` at T=8, splits/steals>0) `[RUN recursive-tsv/full]` `[DOC algorithms.md:1-20]` |
| M2 | Samples the keyspace at runtime → **density-proportional, disjoint ranges**, avoiding blind midpoints (S3P) and up-front hints (s3-fast-list) | **Corroborated w/ nuance** | Disjoint tiling is real (CAS-guarded `splitTxn`, I2/I3) `[DOC algorithms.md:53-70,656-696]`. But the *default* pivot is a **byte-midpoint bisection** with density-reflected placement + demand-driven stealing, not pure sampling; runtime **mass-aware sampling** exists and is default-on (`mass_aware_seed`), and an empirical-CDF `--seed-scatter-scout` is **experimental** `[DOC usage.md:322-389]` `[SRC ListCommand.java:257-281]`. "Sampling rather than blind midpoints" overstates the default; both mechanisms coexist. |
| P1 | Splits flat dense prefixes via **sampling** rather than blind midpoints | **Corroborated w/ nuance** | As M2. `mass_aware_seed` (default on) + `radix_bands` + demand-driven `structure_probes` handle dense/flat regions; the base pivot is still byte-midpoint `[DOC usage.md:346-378]` |
| P2 | Disjoint ranges ⇒ **exactly-once falls out by construction** (no dedup pass) | **Settled by smoke (completeness+no-dup half) / Unaddressed (crash half)** | Every verified mode/scope: `dups=0 missing=0 extra=0` against the manifest `[RUN verify.md across recursive-tsv/jsonl/aligned/seed-none]`. This settles no-duplicate + complete listing **for a clean single-shot run at smoke scale**; exactly-once *under crash/resume* was not tested (smoke used `--checkpoint none`). |
| P3 | Checkpointed crash-resume | **Unaddressed** | Design read only `[DOC algorithms.md:611-748]`; no crash/SIGKILL/resume run performed (smoke was `--checkpoint none`). Stays `VERIFIED: no`. |
| P4 | Byte-exact **Parquet** output | **Unaddressed** | Parquet mode's `-o DIR` output is **not capturable by the stdout-only smoke wrapper** (no volume mount); capability probe proves it *executes* (`output_files=3`) but fidelity unverified `[RUN _capability/parquet-probe]`. Text sinks *are* byte-exact vs manifest, but that is not the Parquet claim. Stays `VERIFIED: no`. |
| P5 | Bounded memory at scale | **Unaddressed (scale)** | Smoke RSS held ~320–560 MB across all scopes `[RUN]`, JVM-baseline-dominated; the 148k-key receipt scope cannot test large-scale bounded-memory behavior. It stays `VERIFIED: no` and must be reproduced under this harness before classification. |
| P6 | AIMD adaptation to 503s | **Corroborated (exists) / Settled-by-smoke (did not engage)** | Code path present `[SRC S3PageFetcher.java:300-331]` `[DOC algorithms.md:752-831]`. On this public bucket at T≤8: `throttle_events=0 aimd_votes=0 errors=0` every run `[RUN]` — 503s absent, AIMD never engaged. Settles *"AIMD did not fire in these runs"*; does **not** settle *"dead weight"* (needs benchmark-scale concurrency). |

## Claimed strengths / numbers

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| S1 | No existing tool combines zero-config discovery + streaming bounded memory + checkpointed resume + Parquet — a claim about **everyone else** | **Unaddressed** | Comparative claim spanning all tools; my scope is swath only. Routed below. |
| N1 | Design target: throughput **within ~10% of s3-fast-list at equal concurrency** | **Unaddressed** | `[DOC]` self-published target, inadmissible as comparative evidence (AGENTS.md); no throughput comparison run. The dossier's own neutrality note stands. |
| N2 | s3-fast-list published 3.1M obj/s at c=1000 | **Unaddressed (other tool)** | Claim about s3-fast-list; not mine to verify or edit. Routed below. |

## Claimed weaknesses (hypotheses)

| # | Inherited hypothesis | Verdict | Evidence |
| --- | --- | --- | --- |
| W1 | May lose to s3-fast-list on hinted throughput | **Unaddressed** | Comparative/scale; benchmark phase. |
| W2 | Java may be a real handicap at high list rates (startup, GC) | **Unaddressed (scale) + smoke note** | Not tested at high list rates. Smoke note `[RUN]`: a fixed ~3.4–4.0 s wall floor even on a 2,549-key prefix is consistent with JVM startup/warm-up overhead; RSS ~320–560 MB JVM-baseline. Neither is a high-rate result. Stays `VERIFIED: no`. |
| W3 | Zero-config sampling costs something; honest test is swath-cold vs s3-fast-list-cold **and** vs s3-fast-list-**hinted** | **Unaddressed (comparative) + standalone factual note** | **Correction (codex F5):** the hypothesis is about swath-cold vs *s3-fast-list*'s two arms; it never posits a "swath-hinted" arm, so nothing here contradicts it — the comparison is Unaddressed (benchmark). Standalone fact (not part of W3): swath has **no hinted mode** — `--seed hints` is unimplemented/throws `[DOC usage.md:322]` `[SRC ListCommand.java:257-260]` `[DOC algorithms.md:16-18]`. On seed cost, the smoke numbers point the **opposite** way to "discovery costs": default `--seed shallow` used **fewer** calls (339) than `--seed none` (516) full-bucket `[RUN]` — the up-front `delimiter=/` seed *reduced* total calls here. One run per arm is not a causal measurement; benchmark-phase work. |
| W4 | Checkpointed resume must face the same SIGKILL, incl. mid-checkpoint | **Unaddressed** | Not tested; benchmark/adversarial phase. |
| W5 | Exactly-once is a correctness claim, checked by the duplicate-detection pass | **Settled by smoke (no-dup, clean run) / Unaddressed (under crash)** | Same as P2: `dups=0` on every verified mode/scope via the shared verifier `[RUN verify.md]`. The crash-path exactly-once claim is untested. |
| W6 | 503/AIMD may be dead weight if listing is latency- not throttle-bound | **Corroborated at smoke scale (direction only)** | 503s absent, AIMD idle every run `[RUN]` — consistent with the latency-bound hypothesis. **Not** promoted to "dead weight": that is a scale/concurrency conclusion smoke cannot reach (T capped at 8). |

## Cross-cutting claims naming swath (`docs/open-questions.md` — NOT edited; routed)

| § | Claim | My smoke bearing |
| --- | --- | --- |
| §1 | Parallel listing is latency-bound, not throttle-bound; swath's AIMD "mostly [unnecessary]" | Supported *in direction* at smoke scale: `throttle_events=0 aimd_votes=0` `[RUN]`. Not a benchmark-scale verdict. |
| §2 | Client language is the bottleneck at high list rates; swath the single JVM entrant | Unaddressed at scale; smoke note W2 (JVM startup floor / baseline RSS). |
| §3 | Nobody but swath has crash-resume | **Unaddressed** — resume not tested here (P3/W4). |

## Editorial corrections applied to `README.md` (bookkeeping, never promotion)

1. License `TBD` → note **no LICENSE file at the pinned SHA** (finding), `[SRC]`-labeled.
2. Version `pre-release` → sharpened to `0.1.0-SNAPSHOT`, no releases/tags, HEAD `f1009db`.
3. W3 premise correction: `--seed hints` is **unimplemented** (no hinted swath exists).
4. Provenance: page is now **mixed lineage** (inherited hypotheses + firsthand
   independent report/receipts) — stated on the page.
5. Receipts section: populated with the committed smoke receipts.
6. Modes section: left as the dossier's deliberate placeholder, with a pointer to
   `research/report.md §3` (modes independently derived from public docs+source) —
   not transcribed, to keep the dossier a hypothesis sheet rather than a copy of the report.

## Receipt-backed promotions (out of `VERIFIED: no`), scoped honestly

- **Completeness + no-duplicate OUTPUT for single-shot listing** (P2/W5 — the
  *observable* output property only): `CONFIRMED for swath 0.1.0-SNAPSHOT, the
  invocations in `run.sh`, against `noaa-normals-pds` at its 2026-07-17 snapshot
  (148,917 keys), across tsv/jsonl/aligned/seed-none, full-bucket and three
  prefixes — every verifier verdict PASS, `dups=0 missing=0 extra=0``. Receipts:
  `receipts/smoke/{recursive-tsv,recursive-jsonl,recursive-aligned,seed-none}/*/verify.md`.
  **Scope guard (codex F3):** this settles the *output* (no missing/extra/dup rows
  for these runs). It does **not** prove the *internal* disjoint-range tiling
  mechanism (M2), that no-gap/no-overlap "falls out by construction", or that the
  invariant holds for other keyspace shapes — M2 stays a `[DOC]`/`[SRC]` design
  claim, not receipt-settled. **Not** promoted: internal-tiling correctness,
  exactly-once-under-crash, bounded-memory-at-scale, Parquet byte-exactness,
  AIMD-necessity — all remain `VERIFIED: no`.

## Items routed to the orchestrator (not edited by me — other tools / cross-cutting)

- S1 (swath-vs-everyone combination claim) and N1/W1 (swath vs **s3-fast-list**
  throughput) — comparative, benchmark phase.
- N2 (s3-fast-list's 3.1M obj/s) and M2's characterizations of **S3P** ("ASCII
  midpoints") and **s3-fast-list** (".ks hints") — claims about other tools' pages.
- `docs/open-questions.md` §1/§2/§3 updates — span tools; smoke bearings above.
