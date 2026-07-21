# Swath

> **Historical landing page (2026-07-20, capsule migration).** This is the full
> pre-restructure landing page. Any `current-state` wording below is historical
> as of the date it records and is superseded by the root README and `data/`.
> Only this banner and link targets changed; body prose and evidence
> qualifications are preserved.

> **Varve builds Swath and maintains this study.** We know Swath better than we
> know the other tools, and Varve decides what gets merged here. We use the same
> harness, buckets, and run-record requirements for every tool, and we welcome
> help from maintainers who know their projects better than we do. See
> [Varve and Swath](#varve-and-swath).

This page consolidates a 2026-07-17 source-and-run groundwork pass — a firsthand
source read of Swath at pinned SHA `f1009db` (`0.1.0-SNAPSHOT`) plus anonymous
smoke runs against the registered smoke bucket. It was **not** new research at
consolidation time: every claim below was already receipt- or source-backed, and
narrowed by a separate cross-model review (12 findings, all resolved) before
this rewrite. Full detail lives in three companion documents plus the immutable
research trail:

- [`mechanism.md`](../docs/mechanism.md) — the source-anchored architecture
  (`WorkStealingScan`, half-open ranges, `start-after` pagination, retries, AIMD,
  the resume design, the output formatters, the counter caveat).
- [`running.md`](../docs/running.md) — the agent-built image, every smoked mode with its
  receipt, the parquet capability probes and the wrapper boundary, the
  concurrency cap, and the architecture matrix.
- [`research/`](.) — `report.md` (the source-and-run groundwork report),
  `reconciliation.md` (every inherited claim walked row-by-row), and
  `codex-review.md` (the critical cross-check and its 12 resolved findings, plus
  the consolidation review). These preserved files may use the project's older
  terminology; this page is the current summary.

|  |  |
|---|---|
| **Repo** | <https://github.com/varveio/swath> — **private / pre-release** at research time (GitHub `visibility=private`) `[3P api.github.com/repos/varveio/swath, accessed 2026-07-17]` |
| **Language** | Java (JDK 25 toolchain) `[SRC build-logic/.../swath.java-conventions.gradle.kts:24 @ f1009db]` |
| **License** | **None present** at `f1009db` — no `LICENSE`/`COPYING` file exists (`ls LICENSE*` fails) `[OBS checkout listing]`, GitHub reports `license=null` `[3P github api]`, and `THIRD_PARTY_NOTICES.md:13` **claims** Swath's modules are covered by "the repository's own LICENSE" — a **dangling reference to a file that does not exist** `[SRC THIRD_PARTY_NOTICES.md:13 @ f1009db]`. A real finding for a repo slated to go public. |
| **Version reviewed** | `0.1.0-SNAPSHOT` @ HEAD `f1009db` — **no releases, no version tags** (only `backup/*`) `[3P github api]` `[RUN]` (the `--version` string proves the version only, not the source SHA — see [Provenance](#provenance)) |
| **Tier** | 1 — included in the planned comparative runs |
| **Testability** | Straightforward for us because we build it; that familiarity is a study limitation — see [Varve and Swath](#varve-and-swath) |

## What we tried and saw

What we saw first, and scoped to exactly what the receipts settle: the smoke bucket
is `noaa-normals-pds` at its 2026-07-17 snapshot (148,917 keys), all runs
anonymous (`--no-sign-request`), concurrency capped at `--max-parallel-listings
8`, text modes `--checkpoint none`.

**What the receipts settle (smoke scale, this version + bucket only):**

- **Swath's LISTs run genuinely in parallel — multiple LISTs concurrently in
  flight, not a serial paginator.** The full-bucket `recursive-tsv` run reached
  `peak_in_flight=8` at `T=8`, with `splits=7`, `steals=98` `[RUN
  receipts/smoke/recursive-tsv/full]`. These are Swath's **own self-reported
  counters** (not an independent wire capture — see `mechanism.md` § Counters);
  they are internally consistent with the manifest-verified output. Behaviour is
  **scope-dependent, not uniform**: the 2,549-key `hourly` run reached the same
  `peak_in_flight=8` cap but did it entirely through steals (`splits=0`), while
  the un-seeded `seed-none` runs peaked lower — `peak_in_flight=6` (full) / `4`
  (monthly) `[RUN hourly, seed-none/*]`. So splits are scope-dependent and the
  un-seeded path reaches lower peak concurrency; "smaller scope = less parallel"
  is **not** what the receipts show. See `mechanism.md`.
- **Zero throttling; AIMD idle.** Every run recorded `throttle_events=0`,
  `aimd_votes=0`, `errors=0` on this clean public bucket `[RUN]`.
- **Every stdout mode PASSed byte-exact against the manifest — scoped to this
  ASCII-keyed corpus.** tsv/jsonl/aligned/seed-none, full-bucket and three
  prefixes, every `verify.md`: `dups=0 missing=0 extra=0`, fields matched where
  the mode exposes them `[RUN]`. This scope matters: the text sinks
  escape control bytes and the adapters do not de-escape, so byte-exactness is
  **not** proven for keys with control characters (`EDGE_BUCKET=none` here). See
  [Known caveats](#known-caveats-carried-forward).

**What stays `VERIFIED: no` — and why smoke cannot reach it:**

- **The internal tiling mechanism (M2) is not receipt-settled.** Byte-exact
  output proves no missing/extra/duplicate *rows* for these runs. It does **not**
  prove that internal ranges were disjoint, that no-gap/no-overlap "falls out by
  construction," or that the invariant holds for other keyspace shapes. M2 stays
  a `[DOC]`/`[SRC]` design claim, `VERIFIED: no`. (This was a promotion the
  review explicitly removed — it is not resurrected here.)
- Crash-resume / exactly-once-under-kill, bounded memory at scale, Parquet
  byte-exactness, and whether AIMD is necessary — none settleable at smoke scale.
  All routed to [Open hypotheses for the benchmark](#open-hypotheses-for-the-benchmark).

**Unfavorable findings (full prominence, not below the fold):**

- **No OSS license**, with the dangling `THIRD_PARTY_NOTICES.md:13` reference
  above — a real gap for a repo slated to go public.
- **Pre-release posture:** `0.1.0-SNAPSHOT`, no releases/tags, README states
  "Phase 8 partial." The tool builds and lists correctly, but is not a shipped
  product.
- **`--seed hints` is unimplemented** — it throws `[SRC ListCommand.java:257-260
  @ f1009db]` `[DOC usage.md:322]`. There is no "Swath-hinted" mode.
- **`inspect` and `diff` subcommands are stubs** that print *"not yet
  implemented"* `[SRC InspectCommand.java:25 @ f1009db]` `[SRC DiffCommand.java:28
  @ f1009db]`.
- **No `ls`-style delimiter/CommonPrefix *output* mode.** `swath list` always
  fully enumerates objects; `delimiter=/` is used only internally (seeding,
  structure probes). A caller who wants a shallow one-level listing has no such
  mode `[SRC ListCommand.java @ f1009db]`.
- **The un-seeded run recorded MORE API calls, not fewer:** full-bucket
  `api_calls` was **516** (`--seed none`) vs **339** (default `--seed shallow`)
  `[RUN seed-none/full vs recursive-tsv/full]`. This is the corrected direction —
  the run *with* the up-front `delimiter=/` seed made fewer calls, the opposite of
  "discovery costs extra." Both PASS. One run per arm is not a causal measurement:
  the receipts settle the observed counts, not that the seed choice *caused* the
  gap.
- **Probe overhead was far higher on the small prefix:** **43.9** api_calls/1k on
  the 2,549-key `hourly` prefix vs **2.28**/1k on the full bucket `[RUN]` — the
  parallelization probes did not amortize there. These two runs vary both size and
  keyspace shape with no repeats, so they settle the two observed ratios, not a
  general overhead-vs-scale law (that is an open benchmark question, below).
- **Parquet / sorted-parquet fidelity is unverifiable at smoke.** These modes
  write a dataset *directory* (`-o`) that the stdout-only smoke wrapper does not
  capture or mount; the container (and its output) is destroyed. The capability
  probes prove the paths *execute* to exit 0 with a **self-reported**
  `objects=15625` — that is not proof the dataset contains all keys `[RUN
  _capability]`. See `running.md`.

## Notes, questions, and observations

Every claim inherited from the original (secondhand) tool page, checked against the
2026-07-17 groundwork and reconciled row-by-row in
[`research/reconciliation.md`](reconciliation.md). Status values:
**CONFIRMED** (a committed **receipt** settles it, scoped to the run);
**Corroborated** (source/`[3P]` reading agrees, but no receipt — so **not**
promoted past `VERIFIED: no` in the sense that matters for behaviour under test;
used here for metadata facts); **VERIFIED: no** (not settled by any receipt — a
hypothesis, however corroborated by source/doc reading); **Open** (routed to the
benchmark queue below). Per `AGENTS.md`, source/doc reading alone never promotes
a claim past `VERIFIED: no`, and any Swath performance number in the checkout is
`[DOC]` self-published, never comparative evidence.

| # | Inherited claim | Status | Evidence / scope |
| --- | --- | --- | --- |
| — | Repo `github.com/varveio/swath` | **Corroborated** | Canonical org repo `[3P]` |
| — | Language: Java 25 | **Corroborated** | JDK 25 toolchain `[SRC ...gradle.kts:24]` |
| — | License: TBD | **VERIFIED: no → finding** | No `LICENSE` file at `f1009db`; dangling `THIRD_PARTY_NOTICES.md:13` ref `[OBS][SRC]` |
| — | Version: pre-release | **Corroborated (sharpened)** | `0.1.0-SNAPSHOT`, no releases/tags, HEAD `f1009db` `[RUN][3P]` |
| M1 | Adaptive, density-aware **parallel LIST** of a single bucket | **CONFIRMED (parallel-LIST only, scope-dependent) / VERIFIED: no (adaptive/density-aware)** | Receipts settle *parallelism*: `peak_in_flight=8` at T=8 with splits/steals>0 (full-bucket), on Swath's own counters; un-seeded runs peak lower `[RUN]`. The "adaptive, density-aware" characterization is the M2 sampling nuance — **not** receipt-settled `[DOC algorithms.md:1-20]` |
| M2 | Samples keyspace → **density-proportional, disjoint ranges**, avoiding blind midpoints and up-front hints | **VERIFIED: no (with nuance)** | Byte-midpoint bisection **coexists** with runtime mass-aware sampling (default on); "sampling rather than blind midpoints" overstates the default. Internal tiling **not** receipt-settled `[DOC usage.md:322-389]` `[SRC ListCommand.java:257-281]` |
| P1 | Splits flat dense prefixes via **sampling** rather than blind midpoints | **VERIFIED: no (with nuance)** | As M2: `mass_aware_seed` + demand-driven probes handle dense regions; base pivot is still byte-midpoint `[DOC usage.md:346-378]` |
| P2 | Disjoint ranges ⇒ **exactly-once by construction** (no dedup pass) | **CONFIRMED (smoke OUTPUT only) / Open (crash half)** | `dups=0 missing=0 extra=0` every verified mode/scope `[RUN verify.md]`. Settles no-duplicate + complete listing for a **clean single-shot run at smoke scale** only — not the internal mechanism, not exactly-once under crash |
| P3 | Checkpointed crash-resume | **VERIFIED: no → Open (P3)** | Design read only `[DOC algorithms.md:611-748]`; no SIGKILL/resume run (smoke was `--checkpoint none`) |
| P4 | Byte-exact **Parquet** output | **VERIFIED: no → Open (P4)** | `-o DIR` output not capturable by the stdout-only wrapper; probe proves execution, not fidelity `[RUN _capability]` |
| P5 | Bounded memory at scale | **VERIFIED: no → Open (P5)** | Smoke RSS ~320–560 MB, JVM-baseline-dominated; the 148k-key receipt scope cannot test large-scale bounded-memory behavior `[RUN]` |
| P6 | AIMD adaptation to 503s | **VERIFIED: no (necessity) / CONFIRMED (idle here)** | Code path present `[SRC S3PageFetcher.java:300-331]`; `throttle_events=0 aimd_votes=0` every run — 503s absent, AIMD never engaged `[RUN]`. Settles "did not fire"; does **not** settle "dead weight" |
| S1 | No existing tool combines zero-config + streaming bounded memory + resume + Parquet — a claim about **everyone else** | **Open (S1)** | Comparative, spans all tools; groundwork scope is Swath only. Routed |
| N1 | Design target: throughput within ~10% of s3-fast-list at equal concurrency | **Open (N1)** | `[DOC]` self-published target, not used as a comparative result; no throughput comparison run |
| N2 | s3-fast-list published 3.1M obj/s at c=1000 | **Open (N2, other tool)** | Claim about s3-fast-list; not Swath's to verify |
| W1 | May lose to s3-fast-list on hinted throughput | **Open (W1)** | Comparative/scale; benchmark phase |
| W2 | Java may be a real handicap at high list rates (startup, GC) | **VERIFIED: no → Open (W2)** | Not tested at high list rates. Smoke note: ~3.4–4.0 s wall floor even on a 2,549-key prefix, consistent with JVM startup; not a high-rate result `[RUN]` |
| W3 | Zero-config sampling costs something; useful comparison is Swath-cold vs s3-fast-list-cold **and** -hinted | **Open (W3, comparative) + standalone finding** | The hypothesis is about *s3-fast-list*'s two arms; nothing contradicts it. Standalone fact: Swath has **no hinted mode** (`--seed hints` throws); and at smoke, seed cost points **opposite** to "discovery costs" — shallow used 339 vs none's 516 `[RUN]` `[SRC ListCommand.java:257-260]` |
| W4 | Checkpointed resume must face the same SIGKILL, incl. mid-checkpoint | **Open (W4)** | Not tested; interruption-test phase |
| W5 | Exactly-once is a correctness claim, checked by the duplicate-detection pass | **CONFIRMED (no-dup, clean run) / Open (under crash)** | Same as P2: `dups=0` every verified mode via the shared verifier `[RUN]`; crash-path exactly-once untested |
| W6 | 503/AIMD may be dead weight if listing is latency- not throttle-bound | **Corroborated at smoke (direction only)** | Receipts settle only that AIMD was **idle** (`throttle_events=0 aimd_votes=0` every run, `[RUN]`); the latency-bound/dead-weight hypothesis itself is **not** settled — that is a scale/concurrency conclusion smoke, T≤8, cannot reach |

No inherited claim was dropped: every row above traces to
`research/reconciliation.md`. Rows that emerged from the codex review rather than
the inherited tool page are named in the [footer](#additive-rows-from-the-codex-review).

**Cross-cutting claims naming Swath** (`docs/open-questions.md` §1
latency-vs-throttle-bound, §2 client-language bottleneck, §3 nobody-but-Swath
has crash-resume) span multiple tools and are **out of this page's editing
scope**; `research/reconciliation.md` § "Cross-cutting claims naming Swath"
records what the smoke pass found for the orchestrator to route.

**Observations about *other tools*** are also routed to the orchestrator rather
than settled here: M2/W1/N1/N2 name S3P's "ASCII midpoints" and `s3-fast-list`'s
".ks hints" and its published 3.1M obj/s figure. Those characterizations belong
on the S3P and `s3-fast-list` tool pages and are validated there — see
`research/reconciliation.md` § "Items routed to the orchestrator." This page uses
them only as inherited context, never as a settled result.

## Open hypotheses for the benchmark

These are the Swath questions we still need to check on this harness:

0. **Comparable-treatment check.** Before using any Swath number, confirm its
   harness treatment is **identical** to
   every other tool's: same definition-of-done, same concurrency sweep, same
   repeat count, same box, same measurement window. Any special-casing for Swath
   is a problem with the study setup, not a result. Check this before the
   experiments that follow.
1. **`--max-parallel-listings` sweep (P-primary).** Default 64; smoke capped at
   8. The parallelism ratio, api_calls/1k, wall-clock, peak RSS, and AIMD `T`
   trajectory at higher `T` are all unmeasured.
2. **Probe-overhead vs scale.** Does api_calls/1k converge toward ~1.0 on very
   large buckets, and how does it behave on skewed/flat vs broad-shallow shapes?
3. **Crash-resume / exactly-once-under-kill (P3, W4).** SIGKILL including
   mid-checkpoint; the headline resume claim faces the same kill every tool does.
4. **Parquet & sorted-parquet fidelity + cost (P4).** Needs a volume-mounting
   harness path to capture and verify the dataset; plus staging-disk and
   sort-memory behavior at scale.
5. **Bounded memory at scale (P5).** Including `--sort`; this is untested here
   and must be reproduced under this harness before its behavior is classified.
6. **`--seed none` vs `shallow` vs (future) `hints`** as a request-pattern axis
   on deep-tree vs flat buckets (W3, standalone seed-cost finding).
7. **Comparative arms (S1, N1, N2, W1, W2).** Swath-vs-everyone combination
   claim; Swath vs s3-fast-list cold/hinted throughput; the JVM-handicap
   hypothesis at high list rates. All benchmark-phase, all currently `VERIFIED:
   no`.
8. **AIMD necessity (P6/W6).** Whether the 503 controller is dead weight — a
   scale/concurrency conclusion, not reachable at T≤8.
9. **Common-denominator architecture.** amd64 support is `[INFERRED]` from the
   Dockerfile (see `running.md`); confirm an actual amd64 build+run before
   settling on it, and ensure Swath's arm64 smoke numbers are never silently
   compared against amd64 runs of other tools.

## Known caveats carried forward

- **KEY-BYTE FIDELITY (ASCII corpus only).** The text sinks escape control bytes
  as `\xHH` by default (`--raw-output` off) and JSONL's `jq @tsv` escapes
  embedded tabs/newlines; none of the `normalize.sh` adapters de-escapes. On the
  whitespace-free NOAA corpus this is the identity, but byte-exactness is **not**
  proven for keys containing control characters. Deferred with the edge-case
  fixture (`EDGE_BUCKET=none`). The aligned adapter additionally assumes S3's
  second-precision timestamp and fixed column offsets; a sub-second
  `ISO_INSTANT` would shift columns — safe for S3 today, flagged for hardening.
  See `mechanism.md`.
- **Image↔source binding is agent-asserted.** The source→image link is a build
  fact recorded in `receipts/smoke/_build/build.md`, **not** a binding embedded
  in each receipt: a `run.meta` proves "this digest, `--version
  0.1.0-SNAPSHOT`", not "built from `f1009db`". A future build should stamp the
  source SHA into an OCI label. See `running.md`.
- **First-party private source, not the public-docs basis.** The groundwork read
  Swath's own source at the pinned SHA while the repo was **private/pre-release**
  — this is first-party source, unlike our starting point for the other tools.
  The familiarity difference described next still applies.

## Varve and Swath

Before building Swath, Varve studied how existing listing tools approached the
problem, and that work informed decisions about how Swath should work. We also
know Swath's performance envelope and tuning options more deeply than we know the
other tools. That history is useful context, and it means we are participants in
the space we are studying. We wrote the comparison plan down before the runs,
use each tool's documented setup, put comparable effort into tuning, and ask
maintainers when we are unsure.

The earlier research and Swath's internal benchmark history do not count as
results in this project; any number has to be produced again with this harness.
Documentation can inform what the study tests, but it is not a run record.
Sort-memory and bounded-memory behavior at scale remain **VERIFIED: no** and
must be reproduced under this harness before classification. See `mechanism.md`
§ Memory model.

The run records are published so readers can inspect the setup and help us
improve it. This is a Varve-maintained project for the object-storage community,
not a sales comparison.

## Provenance

**Mixed lineage (as of 2026-07-17).** The hypotheses, mechanism claims, and
weakness list were **sourced from Swath's own design documentation** and remain
inherited and — except where a receipt is cited — unverified, notably **not
verified by anyone without a stake in the answer**. Layered on top: the
source-and-run groundwork pass ([`research/report.md`](report.md), derived
firsthand from Swath's source at pinned SHA `f1009db` plus its own smoke runs
against this harness) and the row-by-row reconciliation. Firsthand facts on
this page are labeled `[SRC]`/`[RUN]`/`[OBS]`/`[3P]`; everything unlabeled is
still inherited hypothesis. The seeding notes are **out of reach** (AGENTS.md
provenance discipline): ambiguities are resolved by running the tool or reading
its public source, never by the upstream that seeded the tool page.

## Receipts

Committed under [`receipts/smoke/`](../receipts/smoke/) — all anonymous
(`--no-sign-request`) against `noaa-normals-pds` at its 2026-07-17 snapshot
(148,917 keys, manifest sha256 `c78a…2adb`), image `swath 0.1.0-SNAPSHOT` built
from `f1009db` (see `running.md`), `--max-parallel-listings 8`:

- `recursive-tsv/{full,hourly,monthly1991,asaccess1981}` — **PASS**
- `recursive-jsonl/{full,monthly1991}` — **PASS**
- `recursive-aligned/{full,monthly1991}` — **PASS**
- `seed-none/{full,monthly1991}` — **PASS**
- `_capability/{parquet-probe,sort-probe}` — file-sink modes execute (exit 0);
  output not capturable by the stdout-only wrapper (**unverified fidelity**)
- `_preflight/preflight.md` — bucket matches manifest (no drift)
- `_build/build.md` — the agent-asserted source→image build fact

Large stdout payloads live outside the repo at
`<data>/receipts/swath/` with sha256 recorded in each `run.meta`.
Full invocation table and reproduction: [`running.md`](../docs/running.md).

### Additive rows from the codex review

Findings that entered via the critical cross-check rather than the inherited
tool page, and are woven into the pages above (not new inherited claims):
image↔source binding is agent-asserted (F1); normalize control-char scope (F2);
smoke does **not** promote internal tiling M2 (F3); private-source ≠ public-docs
basis (F4); W3 premise + seed-cost direction (F5); parallelism is scope-dependent,
not "every run" (F6); capability probes prove execution not fidelity (F7); aligned
timestamp/column assumption (F8); dangling LICENSE reference (F9); error-class
scope (F10); amd64 `[INFERRED]`, not built/run (F11); firsthand modes-update
claims carry `[SRC]` anchors (F12). Verbatim findings and resolutions:
[`research/codex-review.md`](codex-review.md).
