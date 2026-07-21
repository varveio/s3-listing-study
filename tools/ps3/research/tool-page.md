# pS3

> **Historical landing page (2026-07-20, capsule migration).** This is the full
> pre-restructure landing page. Any `current-state` wording below is historical
> as of the date it records and is superseded by the root README and `data/`.
> Only this banner and link targets changed; body prose and evidence
> qualifications are preserved.

`pS3` ("parallel S3") is a Go CLI (`jboothomas/ps3`) that
lists an S3 bucket by **discovering prefixes through a brute-force
character-walk** and then paginating them in parallel. This page consolidates an
source-and-run groundwork pass (v0.1.16, pinned commit `9428492`, 2026-07-17) that
cloned and read the source, built a study container, and smoke-attempted the
tool. **pS3 did not produce a listing at smoke**: two issues prevented listing,
and a third left us without a native build suitable for later benchmark timing.
The pass produced capability and build records, not a listing. See
`research/report.md` for the full report,
`research/reconciliation.md` for how every inherited claim was checked, and
`research/codex-review.md` for the critical cross-check that narrowed several
promotions (16 findings, all resolved) before this consolidation.

## What limited the run

Any one of these prevents a run under the current setup. The first two prevent
listing (no output can be produced, or produced within the shared cap); the
third explains why the capability probes used emulation and why we do not have
a native timing run.

1. **No unsigned / anonymous request path.** pS3 builds its session with the
   default credential chain only — no `--no-sign-request`, no
   `AnonymousCredentials` [SRC `cmd/listObjectsV2.go:90-99 @ 9428492`], corroborated
   at runtime by a trace showing `NoCredentialProviders` [OBS
   `receipts/smoke/_capability/silent-empty-obs.md`]. The campaign is
   `CREDS=none`, so every listing mode is **blocked, not skipped**. The one
   committed capability receipt settles this **narrowly**: for v0.1.16, the
   single `list-objects-v2 --bucket noaa-normals-pds --region us-east-1`
   invocation, under the harness anonymous env, exit 1, no listing
   [RUN `receipts/smoke/_capability/list-anon`]. That pS3 has *no unsigned path
   at all* keeps its source+trace corroboration at **VERIFIED: no** — it is not
   settled by that single receipt; the other two modes are unrun (blocked by the
   same shared session path, by inference).
2. **The current setup cannot cap listing concurrency.** The brute-force discovery walks a
   fixed **81-character** alphabet; the parallel pager is bounded by a package
   `var maxSemaphore = 256` (never reassigned, no flag) while prefix
   **discovery** spawns **unbounded** goroutines (`go discoverPrefixes`) with no
   semaphore at all [SRC `cmd/root.go:44`, `cmd/listObjectsV2.go:241 @ 9428492`;
   RUN `receipts/smoke/_capability/help`]. Neither concurrency source can be
   brought within `CONCURRENCY_CAP=8`. Even with credentials, participation would
   need a separate run window and an explicit setup decision rather than the
   shared cap.
3. **We do not have a native runnable path for the benchmark.** The source **does not
   compile** at the pinned HEAD (missing `log`/`sync/atomic` imports, unused
   `os`, a selector/type error at `listObjectsV2.go:186`) — that compile attempt
   ran natively on arm64 [RUN `receipts/smoke/_build`]. The only working artifact
   is upstream's committed prebuilt binary, which is **amd64-only**, so every
   probe that *ran the binary* (list-anon, help, silent-empty) ran the amd64
   image **under qemu emulation** on this arm64 runner (`emulated=yes`). Emulation
   was useful for the smoke probes but is not suitable for benchmark timing,
   and there is no path to a native timing run without first fixing the
   source build. The qemu caveat **rides every binary-run-derived line on this
   page and its companions** — but not the `_build` source-compile evidence,
   which was native arm64.

|  |  |
|---|---|
| **Repo** | <https://github.com/jboothomas/ps3> |
| **Language** | Go (built with go1.20.3; `aws-sdk-go v1.44.249`, `cobra v1.7.0`, `viper v1.15.0`, read from the shipped binary's build metadata) |
| **License** | GPL-3.0 [SRC `LICENSE @ 9428492`] — *the inherited MIT cell was wrong* |
| **Version reviewed** | v0.1.16, pinned commit `9428492291ef3aa824dba0b495583279c3d33760` — default-branch HEAD; the project cuts **no releases and no tags** [SRC `cmd/root.go:16`; RUN `receipts/smoke/_capability/list-anon`] |
| **Tier** | 1 — primary subject |
| **Testability** | **Blocked.** Source does not build at HEAD (compile errors); no unsigned mode, so untestable under `CREDS=none`; only artifact is upstream's committed prebuilt binary (amd64-only, run under qemu at smoke). See `running.md` |

## What we tried and saw

- **Silent exit-0, empty output, no error on bare no-creds.** With a *bare*
  no-credentials env (only `AWS_EC2_METADATA_DISABLED=true`, no config/creds-file
  redirect), pS3 exits **0 with zero objects and no error** — a false success a
  caller cannot distinguish from an empty bucket. This is **[OBS]**
  (`receipts/smoke/_capability/silent-empty-obs.md`), **not a receipt and not
  settled**: `[OBS]` is never a receipt. The harness's stricter starvation
  (config/creds files → nonexistent path) instead fails at session creation,
  exit 1 — and *that* exit-1 half is the committed wrapper receipt. Both
  environments are documented.
- **The pinned source did not build, and the committed binary differs from it.**
  HEAD source has compile errors and the binary `pS3.0-1-16` exposes three subcommands
  (`head-objects`, `list-object-versions`, `list-test`) whose source files are
  **absent** from the checkout [RUN `receipts/smoke/_build`, `_capability/help`].
  The file-set mismatch is the fact; that the binary's tree is newer / was never
  pushed is [INFERRED]. **CONFIRMED for HEAD `9428492`: we could not reproduce
  the shipped binary from the repository source.**
- **No releases, no tags, no README, no docs, no `go.mod`.** 16 commits total,
  last 2024-01-02 (~2.5 yr stale as of 2026-07-17), single author. HEAD is
  pinned because there is nothing else to pin to.
- **The author's 160s / 1110s / 733s figures remain [3P] blog numbers.** One
  Medium post reports 15,000,000 objects listed in 160 s (~94K obj/s), ~7× vs
  `aws s3api` (1110 s) and ~5× vs `s5cmd` (733 s) — one bucket, one box, one run,
  on **local (non-AWS) S3**; we have not reproduced it. Both comparison tools
  are in this project, so we can check the figures if we get pS3 running.

## Notes, questions, and observations

Every inherited observation from the original (secondhand) tool page is shown
alongside the 2026-07-17 groundwork (`research/report.md`) and row-by-row review in
`research/reconciliation.md`. Per `AGENTS.md`, **source reading alone never
promotes a claim past `VERIFIED: no`** — only a committed receipt does. So every
behavioral claim below, however well corroborated by reading the source, stays
`VERIFIED: no` unless a receipt settles it. The reconciliation status
(**Corroborated** / **Contradicted** / **Settled** / **Unaddressed**) is shown
alongside; **Contradicted** rows show both sides.

### Metadata / bookkeeping

| # | Inherited observation | Reconciliation status | Current status and supporting record |
| --- | --- | --- | --- |
| M1 | License **MIT** | **Contradicted** (editorial) | Corrected: file is in full **GNU GPL v3** [SRC `LICENSE @ 9428492`]. Editorial correction; promotes no behavior |
| M2 | Version "Unknown — no revision recorded" | **Contradicted** (editorial) | Corrected: **0.1.16** [SRC `cmd/root.go:16`; RUN `_capability/list-anon` `pS3 version 0.1.16`] |
| M3 | "version-less until we pin one" | **Settled** (editorial) | Pinned to `9428492…` (HEAD; no tags/releases exist) |
| M4 | Repo `github.com/jboothomas/ps3`, Go | **Corroborated** | **VERIFIED: no** behaviorally; cloned and read [SRC `@ 9428492`] |
| M5 | "Testability: needs a Go toolchain, otherwise straightforward" | **Contradicted** | **CONFIRMED** does-not-build (receipt-backed): source does **not** compile [RUN `receipts/smoke/_build`]. Not straightforward; only artifact is the committed prebuilt binary |
| M6 | pS3 exists/public/obtained, source read; anchors are a real read | **Corroborated** | Confirmed by independent clone + full source read [SRC `@ 9428492`] |

### Mechanism

| # | Inherited observation | Reconciliation status | Current status and supporting record |
| --- | --- | --- | --- |
| A1 | Brute-force character-by-character prefix expansion; not S3P-style bisection | **Corroborated** | **VERIFIED: no** (source-corroborated) [SRC `cmd/listObjectsV2.go findPrefixes/discoverPrefixes:190-289 @ 9428492`; 3P blog] |
| A2 | Per character, start a 1-char-prefix listing; recurse to (N+1)-char extensions on page overflow | **Corroborated** | **VERIFIED: no** (source-corroborated) [SRC `:213-241` — `nextPrefix := currentPrefix + c` then `go discoverPrefixes` on overflow] |
| A3 | Recursion threshold = 1000; ≤1000 ⇒ leaf | **Corroborated, with a correction** | **VERIFIED: no** (source-corroborated). Correction: branch tests `len(Contents) > 999` and **never** `IsTruncated`, so an exactly-1000-key *non-truncated* prefix is misclassified "large" and needlessly recursed [SRC `:222-224,243`] |
| A4 | Alphabet is "each printable byte / character class in some variants"; possibly configurable | **Contradicted** | Corrected: a **fixed 81-element package `var`** (not a const), **not configurable**, **not** "each printable byte" — omits `" # % < > [ \ ] ^ \` { \| } ~` and all non-ASCII [SRC `cmd/root.go:36-39`; RUN `_capability/help` — no flag] |
| A5 | Code anchor `cmd/listObjectsV2.go` `discoverPrefixes` lines 196-241 | **Corroborated** | **VERIFIED: no** (source-corroborated); the closure is at line 196, char-loop body 213-241 exactly [SRC `@ 9428492`] |
| A6 | Needs no bootstrap / hints / prior keyspace knowledge | **Corroborated** | **VERIFIED: no** (source-corroborated); discovery starts from `""` [SRC] |

### Tunables / modes

| # | Inherited observation | Reconciliation status | Current status and supporting record |
| --- | --- | --- | --- |
| T1 | "Concurrency / worker-count knobs — Unknown; presumably exists" | **Contradicted** | Corrected: no knob. Pager fan-out + printer-worker count is a package `var maxSemaphore = 256` (never reassigned); prefix **discovery** goroutines are **unbounded**; **no flag** for either [SRC `cmd/root.go:44`, `listObjectsV2.go:241`; RUN `_capability/help`]. Cannot cap to `CONCURRENCY_CAP=8` |
| T2 | "Output mode(s) — Unknown" | **Contradicted / refined** | A `--output {json,text}` flag exists [RUN `_capability/help`], **but** `readObjectsV2` ignores `fOutput` and always prints one fixed line — **inert in HEAD source** (binary may differ; untested — blocked) [SRC `:155-188`] |
| T3 | "Page-fit threshold for recursion = 1000" | **Corroborated** | **VERIFIED: no** (source-corroborated); `maxKeys=1000`, a package `var` [SRC `root.go:42`, `listObjectsV2.go:224`] |
| T4 | "This table is almost certainly incomplete … full flag surface needs a real --help" | **Corroborated & completed** | Full surface captured [RUN `_capability/help`]; see `mechanism.md` and `running.md` |

### Published numbers and estimates (author's [3P] blog — provenance kept exact)

| # | Inherited observation | Reconciliation status | Current status and supporting record |
| --- | --- | --- | --- |
| N1 | 15,000,000 objects in 160 s (~94K obj/s) | **Unaddressed** | **VERIFIED: no** — no benchmark ran (blocked); one Medium post, one bucket, one run, local S3. Not reproduced |
| N2 | ~7× vs `aws s3api` (1110 s) | **Unaddressed** | **VERIFIED: no** — same [3P] single data point; internally checkable later (aws-cli is a study subject); **promoted nowhere** |
| N3 | ~5× vs `s5cmd` (733 s) | **Unaddressed** | **VERIFIED: no** — same [3P] single data point; s5cmd is a study subject; on *local* (non-AWS) S3; **promoted nowhere** |

### Tradeoffs and questions to test

| # | Inherited observation | Reconciliation status | Current status and supporting record |
| --- | --- | --- | --- |
| W1 | Discovery tax up to `alphabet^N` speculative LISTs; worst on sparse deep-shared-prefix keyspaces | **Unaddressed** (mechanism plausible) | **VERIFIED: no** — source confirms speculative per-character LISTs exist; the cost at scale is a benchmark question, unrun |
| W2 | Alphabet expansion breaks on non-ASCII / arbitrary-byte keyspaces; "rare in practice" is an assertion | **Corroborated at source; runtime Unaddressed** | **VERIFIED: no** (runtime). Source proves the 81-char set cannot express out-of-alphabet lead bytes, so such keys are silently dropped by construction [SRC `root.go:36-39`]; a live demonstration needs the edge fixture (`EDGE_BUCKET=none` → deferred) + credentials (blocked) |
| W3 | Headline throughput / ratios may not reproduce off the author's box | **Unaddressed** | **VERIFIED: no** — blocked; the benchmark phase's job |
| W4 | Multipliers are internally falsifiable (our own aws-cli / s5cmd numbers) | **Unaddressed** (noted) | **VERIFIED: no** — correct in principle; deferred to benchmark. Promote nothing now |

**Ledger totals** (matching `research/reconciliation.md`): Corroborated 10
(A1, A2, A3, A5, A6, M4, M6, T3, T4, W2) · Contradicted 6 (A4, M1, M2, M5, T1, T2
— all editorial/source-level except M5, whose does-not-build is receipt-backed
[RUN `_build`]) · Settled 1 (M3) · Unaddressed 6 (N1, N2, N3, W1, W3, W4). Every
reconciliation row has a destination above.

The inherited tool page's "Claimed strengths" section (not itself a reconciliation
row-set) maps as follows: its *zero-config / no-bootstrap* bullet is row A6; its
*"works on arbitrary keyspaces in principle, not just tree-shaped ones — unlike
delimiter-based discovery, which degenerates on flat buckets"* bullet is a
**structural property, source-corroborated** (pS3's byte-walk needs no `/`
hierarchy, so it does not degenerate on flat buckets the way delimiter listing
does) but **qualified**: the walk is bounded by the fixed 81-char alphabet, so it
silently drops keys whose distinguishing byte is out-of-alphabet (row W2 /
`mechanism.md` § Keyspace division). It is not dropped — it is stated here with
that qualifier, **VERIFIED: no** at runtime like every other behavioral claim.

### Additive findings (not in the inherited tool page)

These entered via the source-and-run groundwork, not the seeded hypothesis sheet;
`research/reconciliation.md` § "New independent findings NOT in the tool page"
records them without a tool-page status. Named here as **additive**:

- **No unsigned/anonymous request path** — [SRC `listObjectsV2.go:90-99`] +
  [OBS `silent-empty-obs.md`]. The committed [RUN `_capability/list-anon`]
  receipt settles it **narrowly** (v0.1.16, that one invocation, harness anon
  env, exit 1, no listing); the general "no unsigned path at all" stays
  **VERIFIED: no**; the two unrun modes are blocked by shared-session inference.
- **Silent exit-0 empty output on bare no-creds** — [OBS
  `silent-empty-obs.md`]; env matters (full starvation → exit 1 at session
  creation; bare env → exit 0, no error). `[OBS]` only; only the exit-1 half is
  receipt-backed.
- **Binary not reproducible from repo** — three subcommands with source absent
  from the checkout, plus compile errors [RUN `_build`, `_capability/help`].
- **GetBucketLocation region bug** [SRC `listObjectsV2.go:107-117`],
  **error-swallowing nil-deref** in `s3ListObjectsWithBackOff` [SRC
  `s3SDKfunctions.go:74-77`], **`--debug`/`--trace` suppress object output**
  [SRC `readObjectsV2:164-169`] — all source-level, VERIFIED: no. See
  `mechanism.md`.

## Open hypotheses for the benchmark

Answerable only with credentials, at scale, natively — none settleable here.
Carried with provenance from `research/report.md` § 10 and the Unaddressed
ledger rows; every unrun claim is preserved with its detail, not compressed.

1. **Does the headline throughput reproduce?** ~94K obj/s, ~7× vs aws-cli, ~5×
   vs s5cmd — all three from a single [3P] blog post, one bucket, one box, one
   run, on local (non-AWS) S3 (N1/N2/N3, W3). Highest-priority falsification.
   Internally checkable because aws-cli and s5cmd are study subjects (W4) — the
   rare inherited claim we can check without trusting anyone's published
   baseline, *if pS3 can be made to run*.
2. **Does `--output json` work in the binary?** It is **inert in HEAD source**
   (`readObjectsV2` ignores `fOutput`), but the committed binary diverges from
   the source, so text-vs-json cannot be treated as two real output modes until
   verified against the binary (T2).
3. **`--prefix-count` sweep** (the primary knob, default 500). Proposed:
   **100 / 500 (default) / 2000 / 10000** against a multi-million-key bucket —
   measure discovery-LIST overhead vs parallelism gained, and completeness at
   each setting (W1).
4. **Discovery tax on a stress keyspace** (W1). Up to `alphabet^N`
   speculative LIST calls to resolve an N-character prefix depth; worst on a
   sparse keyspace with many keys under one long common prefix (e.g. a
   UUID-prefixed layout). Falsifiable by constructing a deep-shared-prefix bucket
   and instrumenting **API-calls-per-unique-key** against a matched shallow/flat
   comparison bucket of similar total size.
5. **Correctness at scale on out-of-alphabet keys** — does the 81-char alphabet
   silently drop keys on a non-ASCII / arbitrary-byte bucket? Needs the edge
   fixture (`EDGE_BUCKET=none`) (W2).
6. **Concurrency policy** — uncappable via flags (256 pager `var` + unbounded
   discovery); the benchmark must patch the package vars or run pS3 out-of-band
   from the ≤8-capped tools, and cannot fairly compare it under the shared cap.
7. **Memory / throughput at scale** — the streaming model suggests bounded
   object memory, but the in-memory prefix list and 256 workers are untested at
   millions of keys.
8. **Architecture** — pS3 is amd64-only natively; a native benchmark needs a
   working source build (currently impossible) or permanent emulation
   (disqualifying for timing).

## Provenance

**Mixed provenance (as of `groundwork/ps3`, 2026-07-17).** The summary, metadata
table, things we tried, additive findings, and the **table's statuses and
supporting records** are **firsthand**: a clone and full source read of the
pinned checkout [SRC `@ 9428492`], committed `[RUN]` capability/build receipts,
and `[OBS]` observations (never receipts), all labeled inline. The **claims the
ledger checks are not** — they are the inherited secondhand material: the
mechanism and weakness-hypothesis narrative, and the [3P] numbers (rows N1-N3),
remain inherited/third-party regardless of how the groundwork checked them.
Groundwork corroborated the mechanism against source but promoted no behavior
past `VERIFIED: no`. The source-and-run account lives in `research/report.md` and
`research/reconciliation.md`; the critical cross-check that narrowed the
promotions is in `research/codex-review.md`.

The inherited attribution, kept from the pre-consolidation page: the repo URL,
license, and code anchors came from a **source-level research note** (its
Sources line cited `github.com/jboothomas/ps3` and the
`cmd/listObjectsV2.go`/`discoverPrefixes` location) and from **Swath's design-doc
attribution** (which credited "PS3 (jboothomas, MIT)" — the licence the
firsthand source read has since corrected to GPL-3.0); the 160s/1110s/733s
numbers came from a **single Medium post** by the author. The firsthand source
read now confirms the repo and corrects the license.

The inherited notes were explicit that **none of the seed material was
executed** — pS3 was source-read only, never run against a bucket. This
groundwork pass ran the binary far enough to produce the capability and build
records above; it produced **no listing**, for the reasons in
[What limited the run](#what-limited-the-run).

## Pointers

- **`mechanism.md`** — source-anchored architecture: the brute-force
  character-walk discovery (81-char alphabet), the 256-pager-vs-unbounded-
  discovery concurrency, the `>999`/never-`IsTruncated` nuance, no delimiter /
  full-bucket-only, the streaming channel/printer model, retry and memory
  models, output contract, the embedded-newline adapter gap (real) vs the
  updated leading-space claim, and the failure surface.
- **`running.md`** — the study Dockerfile (debian-slim by digest + upstream's
  committed prebuilt binary by content hash), the qemu/amd64 emulation posture,
  the blocked smoke state with capability receipts, source-build failure
  evidence, what participation would require, and the architecture matrix.
- **`research/`** — `report.md`, `reconciliation.md`, `codex-review.md`.
  These preserved files may use the project's older terminology; this page is
  the current summary.
- **`receipts/`** — `_capability/list-anon` (exit-1 wrapper receipt),
  `_capability/help/help.txt`, `_capability/silent-empty` + `silent-empty-obs.md`
  ([OBS]), `_build/`, `_adapter/`. Immutable; read-only inputs to this page.
