# s5cmd

> **Historical landing page (2026-07-20, capsule migration).** This is the full
> pre-restructure landing page. Any `current-state` wording below is historical
> as of the date it records and is superseded by the root README and `data/`.
> Only this banner and link targets changed; body prose and evidence
> qualifications are preserved.

`s5cmd` is a Go CLI for S3 and local-filesystem object operations — listing,
transfer (`cp`/`sync`/`mv`/`rm`), and batch execution (`run`). Its published
speed work is about **transfers**: a worker pool fans out over objects once a
listing (or glob expansion) has already produced them. This page consolidates
a source-and-run groundwork pass (v2.3.0, commit `991c9fb`, 2026-07-17) that read
the tool's own source and smoked every listing mode against the registered
smoke bucket — see `research/report.md` for the full report,
`research/reconciliation.md` for how every inherited claim was checked, and
`research/codex-review.md` for the critical cross-check that resolved 19
findings (all fixed) before this consolidation.

|  |  |
|---|---|
| **Repo** | <https://github.com/peak/s5cmd> |
| **Language** | Go |
| **License** | MIT |
| **Version tested** | v2.3.0 (commit `991c9fb`) |
| **Tier** | 1 — included in the planned comparative runs |
| **Testability** | Trivial. Prebuilt binaries published per release [DOC]; groundwork ran the pinned container image (v2.3.0 by digest, see `running.md`) rather than a bare binary |

## What we tried and saw

- **A single `ls` is one serial LIST chain.** `ls` issues one
  `ListObjectsV2PagesWithContext` call and consumes it with a plain serial
  loop over an unbuffered channel — no worker pool, no prefix sharding
  [SRC `storage/s3.go:309,317`, `command/ls.go:197` @ 991c9fb]. **Parallel
  listing is not built in.** See `mechanism.md` for the full source trace.
- **A user can fan `ls` invocations out by hand, via `s5cmd run` +
  `--numworkers`.** s5cmd ships no native keyspace split, but `run <file>`
  dispatches a batch of per-prefix `ls` lines through its worker pool. Smoked:
  4 prefix shards + the unprefixed remainder, `--scope union` **PASS —
  148,917 keys, 0 duplicates, 0 missing, 0 extra** [RUN
  `receipts/smoke/fanout/union/union-verify.md`]. This settles the
  workaround's *completeness*; its *speed* against a native parallel lister
  is unmeasured (`VERIFIED: no`, benchmark phase). See `mechanism.md` §
  "The `run`/`--numworkers` fan-out dispatch" and `running.md` for the exact
  procedure.
- **`--log trace` exposes per-request pages on stdout — API counts are
  obtainable.** s5cmd has no built-in numeric request counter, and `--stat`
  only tallies operations (`ls 1 0 1`) — but `--log trace` writes the full
  AWS SDK request/response records to stdout, and a 3-page trace of
  `normals-hourly/` shows 1 `HeadBucket` + 3 sequential `ListObjectsV2` pages,
  each carrying the prior page's continuation-token [OBS
  `receipts/smoke/_capability/observability`]. (An earlier probe draft missed
  this by checking stderr instead of stdout — see `mechanism.md`.)
- **Every smoked mode PASSed unsigned.** All ten smoke receipts ran
  `--no-sign-request` (`auth=anonymous`) against `noaa-normals-pds`
  (148,917 keys, 2026-07-17 snapshot) and PASSed completeness/correctness —
  see `running.md` § "Every smoked mode" for the full table and `receipts/`
  for the raw evidence.

## Notes, questions, and observations

Every inherited observation from the original (secondhand) tool page is shown
alongside the 2026-07-17 groundwork (`research/report.md`) and its row-by-row
review in
`research/reconciliation.md`. Status values: **CONFIRMED** (receipt-backed),
**CORRECTED** (found different from the original claim; both sides shown),
**VERIFIED: no** (not settled by any receipt — a hypothesis, however
corroborated by source reading), **UNVERIFIABLE** (cannot be tested with
resources on hand). Per `AGENTS.md`, source reading alone never promotes a
claim past `VERIFIED: no`.

| Claim | Status | Evidence |
| --- | --- | --- |
| `ls` is one serial `ListObjectsV2` continuation chain, no keyspace discovery or prefix sharding | **CONFIRMED** (a single `ls` observed at runtime as one serial chain — 3 sequential pages, each carrying the prior page's continuation-token) / **VERIFIED: no** (behavior at scale and across configurations — not receipt-settled) | [SRC `storage/s3.go:309,317`, `command/ls.go:197`] [OBS `_capability/observability`] |
| Believed responsible file is `command/ls.go` | **CORRECTED** — `command/ls.go` only *consumes* the object channel; the LIST request and pagination are *issued* in `storage/s3.go` (`List`, `ListObjectsV2PagesWithContext`) | [SRC `storage/s3.go:299-341,317`] |
| All s5cmd parallelism is transfer-side (worker pool over objects after listing) | **CORRECTED (scoped)** — true for a lone `ls`; **false for `run`**, which dispatches its `ls` lines through the same worker pool | [SRC `command/app.go:18`, `command/run.go:76`] |
| The "5–10× faster" reputation describes transfers, not `ls` | **CONFIRMED** (transfer attribution — docs) / **VERIFIED: no** (the `ls`-vs-`aws s3api` magnitude, a timing comparison out of smoke's scope) | [DOC README.md § Overview] |
| `s5cmd ls 's3://bucket/*'` native listing runs and is the baseline | **CONFIRMED** | [RUN `receipts/smoke/recursive`] |
| Output flags (`-etag`, `-humanize`, `--json`) are formatting-only, don't change the mechanism | **CONFIRMED** | [SRC `command/ls.go:71,75`] [RUN `receipts/smoke/json`] |
| Mandatory: hand-generated per-prefix `ls` piped to `s5cmd run -f <file>` must be measured | **CONFIRMED** (completeness of the fan-out) / **CORRECTED** (flag — v2.3.0 `run` takes the file **positionally** or on stdin; there is **no `-f`**) | [RUN `receipts/smoke/fanout/union`] [OBS `_capability/run-fanout`] |
| `--numworkers N` on `run` — sweep it | **CONFIRMED** (flag exists, sizes the fan-out's concurrency) / **VERIFIED: no** (the sweep itself, a benchmark-phase question) | [SRC `command/app.go:18`] |
| `s5cmd cp`/`sync` (transfer) for separating "fast" from "lists fast" | **VERIFIED: no** — out of listing scope, mutating; guardrail forbids running it here | — |
| 15M objects, 733s, ~20.5K objects/s (Swath survey table) | **VERIFIED: no** — scale + timing; smoke bucket is 148,917 keys and produces no comparative numbers | — |
| `ls` ≈ 1.5× `aws s3api`, not the quoted 5–10× | **VERIFIED: no** — timing comparison, outside smoke; benchmark phase. The original 1.5× figure was itself a self-reported comparison in the notes that seeded this tool page and was never run here | — |
| 11 GB RAM syncing 400k files ([#441](https://github.com/peak/s5cmd/issues/441)) | **VERIFIED: no** — `sync` (not listing), not reproduced. Streaming `ls` used ~40–53 MB at smoke, a listing-path datum only, not a rebuttal of a `sync` report | [RUN `receipts/smoke/recursive`, `receipts/smoke/allversions`] |
| Killed at 15M objects ([#447](https://github.com/peak/s5cmd/issues/447)) | **VERIFIED: no** — scale; not attempted at the 148,917-key smoke bucket | — |
| 2025 re-testing pointer for s5cmd's published speed results (HN thread vs. BigGo aggregator, inconsistently described) | **VERIFIED: no** — neither source has been read; concerns transfer speed, outside listing scope; remains a pointer, not a result | — |
| Docs describe fast transfers using worker-pool parallelism | **VERIFIED: no** — transfer-side, not measured (out of scope, mutating); docs context only | [DOC README.md § Benchmarks] |
| Glob/wildcard support in commands | **CONFIRMED** | [SRC `storage/url/url.go:259-285`] [RUN `receipts/smoke/recursive-hourly`, etc.] |
| Low resource use (transfer side); 400k-file RAM report complicates listing-adjacent `sync` workloads | **VERIFIED: no** — transfer/`sync` at scale, not measured | — |
| Question: how does the hand-rolled `run` fan-out behave when configured carefully? | **CONFIRMED** (correctness/coverage) / **VERIFIED: no** (speed relative to a native parallel lister) | [RUN `receipts/smoke/fanout/union`] |
| 1,000-key page size is a disadvantage vs. tools that raise `MaxKeys` | **CORRECTED** — 1,000 is **S3's own ceiling**; no real-S3 client can exceed it, so this is not an s5cmd deficit. It only matters vs. tools that parallelize *pages* across sharded prefixes | [SRC `storage/s3.go:299-341`] [DOC AWS `ListObjectsV2` API] |

**Cross-cutting claims naming s5cmd** (client-language bottleneck, the 2025
re-testing pointer) live in `docs/open-questions.md` and are out of this
page's editing scope; `research/reconciliation.md` § "Cross-cutting claims
naming s5cmd" records what groundwork found for the orchestrator to route.

All inherited observations remain: every row above traces to
`research/reconciliation.md`, which walks the original tool page row-by-row —
except the final row (the 1,000-key page-size correction), which is additive:
it entered via the codex review (finding I-4, `research/codex-review.md`), not
the inherited tool page.

## Known caveats carried forward

- **KEY-BYTE FIDELITY.** The `normalize.sh` text adapters split on whitespace
  and rejoin with a single space (and the JSON adapter's `jq @tsv` escapes
  tab/newline/backslash), so a key with runs of spaces, tabs, or an embedded
  newline is not reproduced byte-for-byte. Exact for the whitespace-free NOAA
  smoke corpus; general weird-key fidelity is deferred with the edge-case
  fixture (`EDGE_BUCKET=none`). See `mechanism.md`.
- **`allversions` validates the ListObjectVersions request/output contract
  only, on a non-versioned bucket.** The adapter discards version IDs; on a
  versioned bucket, multiple versions of one key would collapse into
  duplicate records. Multi-version/delete-marker fidelity is deferred with
  the same edge-case fixture. See `mechanism.md`.
- **Cosmetic double-prefix cell in the committed receipts.** Every
  prefix-scoped `receipt.md`'s "Prefix scope" cell renders the prefix twice —
  a wrapper template bug (`harness/smoke-run.sh`), fixed in commit `014f74a`.
  The already-committed receipts are left with the malformed cell as an
  accurate record of what the wrapper rendered; `run.meta`, which the verifier
  actually reads, was always correct, and no status was ever affected. See
  `running.md`.
- **The separate Codex review corrected the serial-listing record.** Finding
  I-1 showed that an earlier
  observability probe wrongly concluded "no per-request logging at any
  level" — it had checked stderr, while `--log trace` writes to stdout. The
  re-probe recovered the request trace and made the serial continuation-token
  chain directly visible at runtime, on top of the source-level finding. See
  `mechanism.md` and `research/codex-review.md` I-1.

## Pointers

- **`mechanism.md`** — architecture: the serial LIST chain, the `run`/
  `--numworkers` fan-out dispatch, retry model, page-size ceiling, output
  contracts per mode, scoped caveats, source anchors.
- **`running.md`** — image pin, every smoked mode with invocation, the
  hand-rolled per-prefix fan-out procedure, and how to reproduce any receipt
  via `harness/smoke-run.sh`.
- **`research/`** — `report.md` (the source-and-run groundwork report),
  `reconciliation.md` (every inherited observation, status, and supporting record),
  `codex-review.md` (the critical cross-check and its 19 resolved findings).
  These preserved files may use the project's older terminology; this page is
  the current summary.
- **`receipts/`** — the committed smoke receipts (`recursive`, `delimiter`,
  `json`, `listv1`, `allversions`, `fullpath`, `fanout/`) and capability
  probes (`_capability/observability`, `_capability/run-fanout`,
  `_capability/preflight`). Immutable; read-only inputs to this page.
