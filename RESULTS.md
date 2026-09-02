# What we found

We install S3 listing tools, study how they work, and run them against staged
copies of real buckets. This release publishes an allowlisted row for every
releasable settled attempt, including failures and cancellations. The original
ledger, logs, listing products and fixture bytes stay private. This page is
the short version; every claim on it names its run in the table at the end,
and the public rows are one click below.

## What we did

Ten tools were pinned by image digest and driven through versions of one
harness against **replay fixtures**: staged copies of real buckets' key
listings, served back by a local server that imitates S3's `ListObjectsV2`
with a fixed per-request latency taken from the real bucket. Fixtures rose
from 4.08 million objects to 143 million. A separate, smaller set of runs went
against live S3.

The release combines several diagnostic campaigns run over one week while the
harness, the replay server and Swath were evolving. Every attempt pins its
exact tool, image, fixture and replay identities; rows from different
campaigns are not comparable merely because they share a release.

The question was not "which tool is fastest". This release cannot answer that.
It asks: **what could each listing approach complete in the campaigns we ran,
what happened at the largest fixture we attempted, and why did we or did we
not carry it further?**

A few terms: a *rung* is one fixture size; an *arm* is one tool in one
configuration on one rung; *c64* means 64 requests in flight; *fan-out* is a
tool splitting the listing across prefixes; *cut-points* are keyspace
boundaries handed to a tool that cannot find its own; *RSS* is resident
memory.

## What we found

Eleven tools were researched. Ten have attempts in this release; s4cmd lists
through the legacy v1 API, which the replay server does not serve, and needs
credentials on live S3. Ten tools completed the 4.08-million-object rung. Four
were taken to 143 million. Where a tool was not taken further, that was a
study decision with a stated reason, not a measured limit of the tool.

| tool | largest fixture attempted | what happened there | why no larger fixture was scheduled |
| --- | ---: | --- | --- |
| aws-cli, minio-mc, s3kor | 4.08M | Completed; count matched (three of s3kor's nine rows returned one row more) | One page at a time; the tool has no listing-parallelism control |
| ps3 | 4.08M | Completed at c256; the wide arm hit the 30-minute cap | About 115 replay requests per page of the fixture |
| s5cmd | 66.4M | Completed; count matched | No listing fan-out of its own; it ran on shard lists the harness supplied |
| s3-fast-list | 66.4M | One of three attempts completed, at 10.8 GiB resident; one was killed at an 8 GiB limit; one failed at 16 GiB with exit 0 | Memory; study decision |
| s7cmd | 143M | Timed out at the 2-hour cap with no count | Paginates each directory serially; that one dominant directory was still draining is a runner-log diagnosis |
| s3p | 143M | Completed, in its key-only mode; count matched | Largest fixture in the release |
| rclone | 143M | Completed; nine rows short of the fixture count | Largest fixture in the release |
| Swath | 143M | Completed; count matched | Largest fixture in the release |

Two more findings:

- **Counts are checked where a staged count exists.** For the 66.4M, 143M and
  flat fixtures the release carries the staged bundle's object count and says
  whether each run matched it. For the 4.08M, 13.5M and 56.3M fixtures no
  bundle summary was staged, so the release records what each run returned and
  never infers the fixture's count from a tool's output; where this page says
  "count matched" for those, the reference is the capture's own count from the
  study's working notes, not a release field. Either way it is count
  agreement, not key-by-key verification, and nothing is sampled.
- **A flat namespace separates the designs.** With no directories to fan out
  across, rclone's per-directory walk filled an 8 GiB memory cap and was
  killed; s7cmd's prefix discovery collapsed to one serial drain; Swath's
  range splitting completed the cell in one run whose delivered-timing grade
  is `INSUFFICIENT_EVIDENCE`, so its wall clock is a functional result, not a
  timing.

The figures live in the report beside the rows they draw, each captioned
with what it is and is not. None of them is a ranking.

## Swath on live S3

In one uncontrolled live-S3 run, Swath 0.3.1 returned 1,068,477,307 rows from
a public bucket and exited after 341.4 seconds, including writing compressed
TSV to disk from one 32-vCPU VM. The bucket has no independent manifest and
changed between runs, so this is a one-run observation, not a verified
inventory and not a comparative result. The listing-phase time inside that
run is in a private run summary, not in the public row; the report says so
where it quotes it. The full set of live-S3 runs is
[in the report](results/2026-09-scale-diagnostics/REPORT.md#swath-on-live-s3).

## What this does not mean

- **Not a ranking, not a benchmark.** No run here is measurement-grade. The
  release's `manifest.json` says so in machine-readable form
  (`calibrated_replay_benchmark: false`).
- **The instrument has a known defect, and it slows our own tool.** The replay
  server misses its latency budget on directory-rollup requests under load.
  Swath is the tool that issues those at volume, so every Swath timing on the
  three small-directory fixtures failed the study's own timing gate. See
  [how the instrument works](docs/instrument.md).
- **Replay is not S3.** Its per-request latency is a fixed number per bucket
  with no tail, no throttling and no rise under load. An internal check from
  the study's working notes, not a release field, put it within 2 to 14% of
  what a serial client sees on the same bucket, on the slow side in Virginia.
  It flatters a client fast enough to push S3, and a few rows ran at request
  rates where live S3 would have started to. The replay server and the tool
  under test also share one VM's boot disk and page cache.
- **The setup was not equal everywhere.** Swath's fastest 143M rows ran
  against a replay server three times larger than the other tools', on a
  larger host. Its rows on the same server as the others took 209 to 284 s
  across widths, but those are an older build, so nothing separates the build
  from the server size for the fastest row. s5cmd and s3-fast-list
  ran on shards and cut-points the harness generated. The tools write
  different outputs (text, JSON, Parquet, compressed TSV), so a wall clock is
  never one common operation. The report lists every such asymmetry.
- **"Ran" and "verified" are separate facts.** Counts are checked against a
  staged fixture count where one exists, not key by key.
- **Auditable, not independently reproducible.** Every configuration, digest
  and outcome is public. The fixture bytes and the original evidence store are
  not, and the source buckets change, so the identical experiment cannot be
  rerun from this repository alone.
- **We build Swath and we run this study.** Every page says so.

## The runs behind this page

"Row" means the fact is a field of the public row; "row + log" means the
number is in the row and the explanation is a diagnosis from the runner log
or the tool's source.

| claim | attempt | source |
| --- | --- | --- |
| aws-cli, 4.08M, count matched | `aws-cli.a7d9377bd706.s1` | row |
| ps3 wide arm, 4.08M, timed out at 1,800 s | `ps3.31a9e4da68b2.s1` | row |
| s5cmd on harness shards, 66.4M, count matched | `s5cmd.962211b4b344.s1` | row |
| s3-fast-list, 66.4M, count matched, 11,347,320 KiB peak RSS | `s3-fast-list.246cf7252988.s1` | row |
| s3-fast-list killed at 8 GiB; failed at 16 GiB with exit 0 | `s3-fast-list.500509011e5c.s1`, `s3-fast-list.540930a67436.s1` | row + log |
| s7cmd, 143M, timed out at 7,200 s | `s7cmd.a9b999169187.s1` | row + log |
| s3p, 143M, count matched | `s3p.1b77f20ed931.s1` | row |
| rclone, 143M, 143,008,665 of 143,008,674 | `rclone.6319ec57665d.s1` | row |
| Swath, 143M, count matched, on a 64-vCPU replay server | `swath.be4140354dd1.s1` | row |
| Swath (older build), 143M, count matched, on the 20-vCPU replay server the other tools had | `swath.9ad793f7eea7.s1` | row |
| rclone walk killed at 8 GiB on the flat fixture | `rclone.795fbd66217b.s1` | row + log |
| s7cmd serial drain on the flat fixture | `s7cmd.97b265107a89.s1` | row + source |
| Swath on the flat fixture, `INSUFFICIENT_EVIDENCE` | `swath.666e471aac76.s1` | row |
| Swath, 1,068,477,307 rows returned on live S3, 341.4 s | `swath.7b028bd8c692.s1` | row |

## Drill down

- **[The full report](results/2026-09-scale-diagnostics/REPORT.md)**: every
  rung, every tool's disposition, the instrument's defect in numbers, the
  live-S3 runs, and what the study does not establish.
- **[How the instrument works](docs/instrument.md)**: the replay server, its
  latency deadlines and where they come from, and its known skews.
- **[The measurement plan](docs/methodology.md)**, written before the
  comparative runs, with its dated changes.
- **[Per-tool pages](tools/README.md)**: how each tool lists, its groundwork
  evidence, and its standing in this release.
- **The public rows**, in `results/2026-09-scale-diagnostics/`:
  [`attempts.jsonl`](results/2026-09-scale-diagnostics/attempts.jsonl) (one
  object per run, canonical),
  [`summary.csv`](results/2026-09-scale-diagnostics/summary.csv) (the same
  rows, flat),
  [`manifest.json`](results/2026-09-scale-diagnostics/manifest.json) (claim
  ceiling and disclosures), and
  [`results/README.md`](results/README.md) for the release contract and what
  stays private. Check a release from a clone, with no private access:

  ```
  uv run python -m benchmark.public_validate \
      --release-dir results/2026-09-scale-diagnostics
  ```
