# s7cmd

[s7cmd](https://github.com/nidor1998/s7cmd) lists S3 buckets through its
[s3ls-rs](https://github.com/nidor1998/s3ls-rs) engine and emits text, TSV, one-line or JSON records. The source-researched
next candidate is official **v1.8.5**, with engine **1.3.4**; historical
study runs used **v1.5.0 / engine 1.0.3**. The updated engine retains concurrency
permits across deep-prefix leaf scans; runtime qualification is still pending.

> **Study status (2026-09-scale-diagnostics).** This tool's standing in the current release:
> Matched the 13.5M-object fixture's count in 601.0 s (`s7cmd.bdba69aad415.s1`); returned no count at 66.4M and reached the 7,200 s cap at 143M.
> The release is diagnostic: no attempt in it carries `purpose = measurement`, so
> nothing here is a calibrated benchmark or a ranking. Report and data:
> [`results/2026-09-scale-diagnostics/REPORT.md`](../../results/2026-09-scale-diagnostics/REPORT.md).

## In the current release

The release `2026-09-scale-diagnostics` is diagnostic: it settles what ran,
what each run returned, and how much memory it used; no row in it is a
calibrated measurement, and nothing here is a ranking. s7cmd ran as version
1.5.0 in three adapter modes, `recursive-one-nosort`, `recursive-tsv` and
`recursive-tsv-nosort`, all of them upstream `ls -r`; the
`--max-parallel-listings` of each arm is in its row.

| fixture | attempts | outcomes | timing grades of completed rows | row cited in the report |
| --- | ---: | --- | --- | --- |
| FourCast 4.08M | 10 | SUCCEEDED 10 | CAPACITY_FAILED 5, PRESSURE_DEGRADED 3, NOT_APPLICABLE 2 | none cited |
| NARA 13.5M | 8 | SUCCEEDED 8 | INSUFFICIENT_EVIDENCE 3, CAPACITY_FAILED 5 | `s7cmd.bdba69aad415.s1` |
| real-changesets 13.9M (flat) | 1 | SUCCEEDED 1 | TIMING_VALID 1 | `s7cmd.97b265107a89.s1` |
| NBM 66.4M | 4 | SUCCEEDED 4 | INSUFFICIENT_EVIDENCE 4 | none cited |
| blockchain 143M | 1 | SUCCEEDED 1 | CAPACITY_FAILED 1 | `s7cmd.a9b999169187.s1` |

"Outcomes" is the settled state of the attempt, and a settled row can carry
no count: the four NBM rows have no `row_count` and a subject exit code of 1,
and the 143M row has no `row_count` and a subject exit code of 124, the
7,200 s cap. On the flat 13.9M fixture the cited row's count matched the
staged fixture count. On the 13.5M fixture the cited row's count matched the
capture report's count, which is in the study's working notes because no
fixture count was staged there (the report's "Per-tool dispositions" labels it
"= capture").

Largest fixture attempted: 143M, with no count. The report's "largest fixture
attempted" table gives the reason no larger one was scheduled: parallel prefix
discovery, then serial pagination per leaf (from its source); on the skewed
143M fixture it reached the 7,200 s cap with exit 124, and that one dominant
leaf was still draining is a diagnosis from the runner log, not a release
field.

No row of the report's "Where the setup was not equal" table names s7cmd.

What `TIMING_VALID`, `PRESSURE_DEGRADED`, `CAPACITY_FAILED`,
`INSUFFICIENT_EVIDENCE` and `NOT_APPLICABLE` mean is on
[`docs/instrument.md`](../../docs/instrument.md#how-a-release-grades-the-delivered-treatment);
a grade describes how the replay instrument delivered its latency treatment
during the run, not whether the tool succeeded.

Report:
[`results/2026-09-scale-diagnostics/REPORT.md`](../../results/2026-09-scale-diagnostics/REPORT.md).
Findings: [`RESULTS.md`](../../RESULTS.md). Rows:
[`results/2026-09-scale-diagnostics/attempts.jsonl`](../../results/2026-09-scale-diagnostics/attempts.jsonl).

The rows are an allowlisted public projection of the campaign ledger; the
original result files and logs are private. The receipts under `receipts/` in
this directory are groundwork evidence and do not cover the release rows.

## At a glance

The proposed next official release is **v1.8.5**, commit
`1e8c7d0644f30cb5da927b87277376620fcd0554`, with no source patch. It pins the
listing crate at **s3ls-rs 1.3.4**, commit
`2993e4f8fd23316c63f63d04a6b6f2102cfdd41b` (`current-subject-1-8-5`).
[Identity and study state](data/tool.json) and [build registration](build/image.json)
still register historical v1.5.0; retargeting is pending. Real CLI validation, smoke and benchmarks are **NOT RUN**.

Issue [#29](https://github.com/varveio/s3-listing-study/issues/29) requested
then-latest v1.8.4; v1.8.5 is the latest official release checked 2026-10-15.
Its listing engine is the same 1.3.4; build registration is pending. The [source update](research/update-1.8.5-2026-10-15.md)
explains the exact fix and the version selection.

## How it works

The candidate engine discovers slash-delimited prefixes to its fan-out depth,
then drains each leaf with sequential pagination. It holds a permit throughout
that drain. Sorting buffers all entries by default; `--no-sort` streams.
[Current source mechanism](docs/mechanism.md).

## Modes and study coverage

Existing recursive TSV, JSON, aligned and one-line modes, version listing,
max-depth and shallow listing remain adapter declarations after source review.
None has been executed with candidate v1.8.5; the historical v1.5.0 smoke matrix
lives in [running](docs/running.md#historical-v150-operating-record).

## What we learned

Candidate source claim IDs resolve in the
[versioned research ledger](research/update-1.8.5-2026-10-15.claims.json).
The canonical [data/claims.json](data/claims.json) continues to describe v1.5.0.

- **Deep leaf scans now count against the concurrency limit.** The old early
  permit release allowed unbounded simultaneous leaf requests. The fix changes
  request pressure; it does not by itself prove a speedup or missing-object bug
  (`current-leaf-permit-fix`).
- **Prefix shape still governs fan-out.** Source defaults are concurrency 64
  and fan-out depth 2; flat layouts still have no prefix parallelism
  (`current-parallel-dispatch`, `current-defaults-source`).
- **Runtime claims require new evidence.** Earlier timings, memory, exits,
  smoke PASS and endpoint receipts belong to v1.5.0. They cannot qualify the
  newer engine or its actual CLI/output (`current-real-cli-validation`).

## Limitations and open questions

Real CLI/output compatibility, completeness, scale behavior, signed access and
custom endpoint behavior need future qualification. Matching counts is weaker
than content verification. Source mechanisms do not establish performance.

## Navigate this directory

[Mechanism](docs/mechanism.md) separates current source findings from the
historical account. [Running](docs/running.md) records the selected build,
inherited setup flags, deferred validation and prior receipts.
[adapter/](adapter/) declares commands and normalization; [build/](build/)
selects the artifact. [Research](research/) preserves the original derivation
and adds a versioned update. Its frozen [tool-page](research/tool-page.md) and
[claims migration](research/claims-migration.md) retain the migration record.
[receipts/](receipts/) remain unchanged historical observations.

## Provenance

**Mixed provenance.** This page combines firsthand source, build, and smoke work
— a pinned source read of s7cmd v1.5.0 (`d589df7`) and its s3ls-rs v1.0.3 engine
(`bf42067`), twelve committed anonymous smoke receipts, and two rounds of
critical cross-check — with inherited secondhand notes compiled from public
sources. The seed was not a run record. The inherited s3ls-rs dossier is
inherited background held in internal notes and is not included in this
public repository. See [`research/tool-page.md`](research/tool-page.md) and
[`research/reconciliation.md`](research/reconciliation.md).

The versioned update adds source-only candidate research; it is not a run record.

## Evidence boundary

No new run evidence exists. The release table remains historical v1.5.0 data;
source review cannot upgrade the candidate v1.8.5 runtime state.

Varve maintains this study and builds Swath. The same source, evidence and
independent-review rules apply to s7cmd. No new measurement or ranking is claimed.
