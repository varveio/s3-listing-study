# S3 listing tools from 4 million to 1.07 billion objects

Release `2026-09-scale-diagnostics` · data as of 2026-09-01 · 270 settled attempts

This is the written companion to the data in this directory. Every number is
quoted from `summary.csv` or `attempts.jsonl` unless the text says otherwise,
and every claim names its attempt in a table. The short version is
[`RESULTS.md`](../../RESULTS.md); the instrument is explained once in
[`docs/instrument.md`](../../docs/instrument.md).

## What this release is

Ten listing tools were pinned by image digest and driven through one harness
against staged replay fixtures of real S3 buckets, from 4.08 million objects
to 143 million, plus a small set of Swath runs against live S3. The question
was which approaches survive a rising object count and where each one stops.

It is a **screening release**. It settles what ran, what each run returned,
how much memory it used, and the mechanism that stopped each tool. It does
not settle relative speed. The machine-readable ceiling in `manifest.json`:

| flag | value |
| --- | --- |
| `controlled_replay_diagnostics` | `true` |
| `calibrated_replay_benchmark` | `false` |
| `live_s3_performance` | `false` |
| `universal_tool_ranking` | `false` |

Two facts bound every timing here. The replay server missed its own latency
budget on one request shape under load, and the tool that issues that shape at
volume is Swath, the study's own tool, so every Swath timing on the three
large fixtures failed the timing gate. And the live-S3 rows are single runs of
one tool.

## The funnel

Each rung is a staged fixture built from a real bucket's listing. "Reached"
means at least one attempt of that tool settled on that fixture, whatever its
outcome.

| rung | objects | tools that reached it |
| --- | ---: | --- |
| `noaa-nws-fourcastnetgfs-pds` | 4,081,170 | aws-cli, minio-mc, ps3, rclone, s3-fast-list, s3kor, s3p, s5cmd, s7cmd, Swath |
| `nara-1950-census` | 13,540,310 | rclone, s3-fast-list, s3p, s5cmd, s7cmd, Swath |
| `real-changesets` (flat) | 13,868,442 | rclone, s7cmd, Swath |
| `idc-open-data` | 56,311,145 | Swath only, with no latency treatment on that fixture |
| `noaa-nbm-grib2-pds` | 66,405,936 | rclone, s3-fast-list, s3p, s5cmd, s7cmd, Swath |
| `aws-public-blockchain` | 143,008,674 | rclone, s3p, s7cmd, Swath |

Two notes on the counts. `fixtures.json` also carries a second, digest-named
FourCast record from the 2026-08-26 screens; its count of 4,081,171 is the
largest row count observed on it, which is s3kor's known one-row surplus, and
every FourCast figure here uses the staged bundle's 4,081,170. s4cmd is in the
roster and has no attempt in this release.

The first-rung figure is [`charts/fourcast-roster.svg`](charts/fourcast-roster.svg),
rows in [`charts/fourcast-roster.csv`](charts/fourcast-roster.csv): one
8-vCPU / 8-GiB arm per tool from group `fc-cpu-corrected-20260828`, each
returning the exact count. It is ordered by wall clock and it is not a
ranking. Three of those tools have no listing-concurrency control, one was fed
cut-points by the harness, and one was slowed by the instrument.

## Where each tool stopped, and why

| tool | last rung | why it went no further | attempt |
| --- | --- | --- | --- |
| aws-cli | 4.08M | One serial `ListObjectsV2` chain; no listing-parallelism control. 143M objects would be about 143,000 sequential pages. | `aws-cli.a7d9377bd706.s1` |
| minio-mc | 4.08M | Serial client-side iterator. | `minio-mc.04c17e5ac8da.s1` |
| s3kor | 4.08M | Serial listing; its parallelism is in transfers. | `s3kor.8514c3397199.s1` |
| ps3 | 4.08M | Request amplification. The fairness arm at `--prefix-count 5000` reached the 1,800 s cap without a count. | `ps3.31a9e4da68b2.s1` |
| s5cmd | 66.4M | No listing fan-out of its own; the comparable arms ran on shard lists the harness supplied. Not carried to 143M. | `s5cmd.962211b4b344.s1` |
| s3-fast-list | 66.4M | Memory. Two of three NBM attempts died at the container limit; the one that completed, at 16 GiB, peaked at 11,347,320 KiB. Not carried further, by decision. | `s3-fast-list.246cf7252988.s1` |
| s7cmd | 143M, no count | Parallel prefix discovery, then serial pagination per leaf. On the skewed 143M fixture it hit the 7,200 s cap (exit 124) with one dominant leaf still draining. | `s7cmd.a9b999169187.s1` |
| s3p | 143M | Completed, exact, 4,238.8 s at c16. CPU-bound in its cheapest key-only mode; width does not help. | `s3p.1b77f20ed931.s1` |
| rclone | 143M | Completed in 667.0 s at c64. Returned 143,008,665 rows, nine short, consistent with a walk not emitting directory markers. The c128 arm was slower, 821.0 s. | `rclone.6319ec57665d.s1`, `rclone.3eff2ab4f661.s1` |
| Swath | 143M | Completed, exact, 173.7 s at c256. Every Swath row on this fixture failed the timing gate; see below. | `swath.be4140354dd1.s1` |

## Per-tool dispositions

The best-completing arm actually recorded for each tool. "Exact" means the run
returned the fixture's object count. Figures are `wall_seconds`, `row_count`
and `max_rss_kb` for the named attempt.

| tool | version | arm | fixture | wall | count | peak RSS (KiB) | delivered timing | attempt |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| aws-cli | 2.36.1 | `s3api-v2-text` | FourCast 4.08M | 700.0 s | exact | 85,076 | `TIMING_VALID` | `aws-cli.a7d9377bd706.s1` |
| minio-mc | 2025-08-13 | `recursive` | FourCast 4.08M | 419.7 s | exact | 49,920 | `TIMING_VALID` | `minio-mc.04c17e5ac8da.s1` |
| s3kor | v0.0.37 | `list` | FourCast 4.08M | 411.0 s | exact | 50,176 | `TIMING_VALID` | `s3kor.8514c3397199.s1` |
| ps3 | 0.1.16 | `list` c256 | FourCast 4.08M | 369.3 s | exact | 92,116 | `TIMING_VALID` | `ps3.55c79d26bce0.s1` |
| s5cmd | v2.3.0 | `fanout-fixture-with-dirs` c64 | NBM 66.4M | 352.2 s | exact | 1,380,592 | `TIMING_VALID` | `s5cmd.962211b4b344.s1` |
| s3-fast-list | 1.1.0 | `list-hinted-fixture` c100 | NBM 66.4M | 333.8 s | exact | 11,347,320 | `PRESSURE_DEGRADED` | `s3-fast-list.246cf7252988.s1` |
| s3p | 3.7.2 | `ls` c16 | blockchain 143M | 4,238.8 s | exact | 335,352 | `TIMING_VALID` | `s3p.1b77f20ed931.s1` |
| s7cmd | 1.5.0 | `recursive-one-nosort` c16 | NARA 13.5M | 601.0 s | exact | 111,160 | `CAPACITY_FAILED` | `s7cmd.bdba69aad415.s1` |
| rclone | 1.74.4 | `recursive-walk` c64 | blockchain 143M | 667.0 s | 143,008,665 of 143,008,674 | 1,893,836 | `TIMING_VALID` | `rclone.6319ec57665d.s1` |
| Swath | 0.3.1 | `recursive-tsv-dataset` c256 | blockchain 143M | 173.7 s | exact | 2,232,392 | `CAPACITY_FAILED` | `swath.be4140354dd1.s1` |

Standing notes per tool:

- **aws-cli, minio-mc, s3kor**: serial by construction; correct; flat in
  memory; not carried up the ladder.
- **ps3**: completes at 4M; the wider fairness arm timed out.
- **s5cmd**: completes only with harness-supplied shards; that asymmetry is
  disclosed wherever it is charted.
- **s3-fast-list**: fast when fed cut-points; memory is the binding constraint.
- **s3p**: reaches 143M correctly, CPU-bound.
- **s7cmd**: correct at 13.5M; returned no count on NBM (exit 1, all four
  arms); capped on the skewed 143M fixture.
- **rclone**: reaches 143M; memory is the constraint on a flat namespace.
- **Swath**: reaches 143M; slowed by the instrument on the small-directory
  fixtures; built by the organisation that maintains this study.

## The instrument in this release

The replay server and its latency model are explained in
[`docs/instrument.md`](../../docs/instrument.md). This section records only
how the instrument behaved in this release.

**The defect.** On the fixtures with many small directories, NARA, NBM and
blockchain, the server missed the `structure_probe` deadline under page load.
The cause is the server's sorted-Parquet seek: a `delimiter=/` rollup reopens a
row-group reader and decodes a whole key page per seek, so a rollup costs far
more than a page, the inverse of S3.

| measurement on `aws-public-blockchain`, c256, replay 64 vCPU / 20 GiB | cold server | warmed server |
| --- | ---: | ---: |
| attempt | `swath.be4140354dd1.s1` | `swath.6d4bdaf4f615.s1` |
| declared deadlines, worker / structure / pivot (ms) | 94 / 55 / 46 | 94 / 55 / 46 |
| delivered structure-probe mean, as a ratio of 55 ms | 2.07 | 2.03 |
| structure probes that overran | 46.0% | 45.4% |
| warm-up requests before the run (`replay.requests.before`) | 0 | 2,400 |
| subject wall | 173.7 s | 174.5 s |
| count | exact | exact |
| delivered timing | `CAPACITY_FAILED` | `CAPACITY_FAILED` |

The warm-up (2,000 / 200 / 200 requests over 27,891 prefixes, 7.3 s) tested the
hypothesis that the overrun was cold-path cost. It changed nothing, so the
per-seek cost is the defect. The ratio and overrun figures come from the
ledger's delivered-timing evidence; the walls, counts and `requests.before`
values are in the release rows. A warmed server is a different treatment
identity, so the warmed row is exported as its own case.

**Who it touches.** Swath issues structure probes at volume alongside its
page load, and every Swath row on the three fixtures is `CAPACITY_FAILED`.
Other tools also have `CAPACITY_FAILED` rows: s7cmd on NARA and blockchain,
one s3-fast-list row on NBM, and several FourCast rows of rclone, ps3, s3p and
s7cmd from the earlier, smaller replay allocation. This release's row schema
does not carry the failing reason for those. rclone's directory walk, which
also issues delimiter requests at volume, classifies `TIMING_VALID` on NARA,
NBM and blockchain.

**What that means for comparisons.** Where a Swath row is set beside a
`TIMING_VALID` or `PRESSURE_DEGRADED` row of another tool, the defect runs
against Swath. The treatment's other skews (no tail, no rise under load,
pricing by request syntax) are not measured well enough to call any
cross-tool ratio a bound in either direction. Where both rows are
`CAPACITY_FAILED`, nothing is established.

**The deadlines, checked.** After this release's rows settled, a same-bucket
serial control was run on the four replay fixtures (2026-09-02, from the same
zone, 100 unsigned pages one at a time over one keep-alive connection):

| fixture | worker deadline | serial p50 | serial mean |
| --- | ---: | ---: | ---: |
| FourCast | 85 | 74.5 | 78.8 |
| NBM | 87 | 78.0 | 81.0 |
| NARA | 86 | 92.3 | 95.6 |
| blockchain | 94 | 95.8 | 101.1 |

The deadlines sit within about 10% of a serial client's median on the same
bucket. The control and the August serial-tool cross-check are described in
[`docs/instrument.md`](../../docs/instrument.md); both are internal checks,
not published receipts, and no row here depends on them. The manifest
carries no disclosure id for the latency model's limits; that page and this
section are the disclosure for this release.

## The flat namespace

A flat namespace, with no interior slashes, is the shape that separates
prefix-parallel designs from range-parallel ones. The fixture is a live-S3
Swath capture of `real-changesets`: 13,868,442 rows, one part, digest
`295177c9…c57edd`. Every arm ran on `n4-highcpu-32` with the subject at 8 vCPU
/ 8 GiB and replay at 20 vCPU / 20 GiB, deadlines 122 / 88 / 88 ms for
worker / structure / pivot.

| arm | wall | count | peak RSS (KiB) | delivered timing | attempt |
| --- | ---: | --- | ---: | --- | --- |
| Swath c64 `recursive-tsv-dataset` | 47.5 s | exact | 369,512 | `INSUFFICIENT_EVIDENCE` | `swath.666e471aac76.s1` |
| s7cmd c16 `recursive-one-nosort` | 1,289.5 s | exact | 50,944 | `TIMING_VALID` | `s7cmd.97b265107a89.s1` |
| rclone c64 `recursive-walk`, 8 GiB | 1,401.8 s | none | 8,369,376 | `TIMING_VALID` | `rclone.795fbd66217b.s1` |
| rclone c64 `recursive-walk`, 16 GiB | 1,377.6 s | exact | 8,121,012 | `TIMING_VALID` | `rclone.d92a513cb0f2.s1` |
| rclone `lsjson --fast-list -R`, 8 GiB | 1,851.8 s | exact | 73,764 | `TIMING_VALID` | `rclone.997236778cca.s3` |

What the rows show:

- **The per-directory walk holds the whole result set in memory.** At 8 GiB it
  was killed (subject exit -9) after issuing all 13,869 pages and writing
  nothing. At 16 GiB it completed with about 8.1 GB resident. That is the
  footprint of `recursive-walk`, not of rclone: its default streaming mode
  completed the same cell in 74 MB.
- **Prefix discovery collapses to one serial drain.** With no sub-prefixes to
  divide, s7cmd paginated the whole bucket sequentially.
- **Range splitting is unaffected**, because it does not need directories to
  divide the keyspace. The Swath arm's 47.5 s run collected fewer than five
  10-second replay samples, so it has no delivered-timing grade; its wall is a
  functional result.
- **Two rclone modes, one pagination, two wall clocks.** Both modes issued
  13,869 pages. The walk paginates with `delimiter=/`, which the server prices
  at the 88 ms structure deadline; the streaming mode uses plain pages at
  122 ms. That 34 ms per-page discount is the instrument pricing by syntax,
  not one mode listing faster, and on this fixture it favours the walk and
  s7cmd's drain. The first two `lsjson` attempts were Spot-preempted before
  producing evidence and are exported as failures.

## Swath on live S3

These rows ran against live public buckets from one VM in GCP `us-east1`,
unsigned reads, one run each, no other subject present. They are observations,
not measurements: the buckets are not under our control, the machine types and
output modes differ, and nothing was repeated. `manifest.json` discloses them
as `real-s3-rows-are-single-observations`.

| bucket | objects | machine | mode | wall | objects/s | attempt |
| --- | ---: | --- | --- | ---: | ---: | --- |
| `real-changesets` | 13,868,442 | n4-highcpu-16 | sorted Parquet c1024 | 62.7 s | 221,259 | `swath.85ccd37c1b88.s1` |
| `fah-public-data-covid19-absolute-free-energy` | 522,925,693 | n4-highcpu-16 | sorted Parquet c1024 | 370.7 s | 1,410,484 | `swath.d7668a13dc4c.s1` |
| same | 522,925,693 | n4-highcpu-32 | sorted Parquet c2048 | 281.9 s | 1,854,866 | `swath.4b2db6e9f6a9.s1` |
| same | 522,925,693 | n4-highcpu-32 | TSV + zstd c2048 | 187.3 s | 2,791,827 | `swath.a0f1b2c053d8.s1` |
| `janelia-cosem-datasets` | 959,831,933 | n4-highcpu-16 | sorted Parquet c1024 | 724.2 s | 1,325,437 | `swath.0b5db9b35947.s1` |
| same | 959,831,933 | n4-highcpu-32 | sorted Parquet c2048 | 585.1 s | 1,640,372 | `swath.0d01af45ef74.s1` |
| `sentinel-cogs` | 1,068,443,985 | n4-highcpu-32 | sorted Parquet c1024 | 707.4 s | 1,510,392 | `swath.6b1ffae260c0.s1` |
| `sentinel-cogs` | 1,068,477,307 | n4-highcpu-32 | TSV + zstd c2048 | 341.4 s | 3,129,747 | `swath.7b028bd8c692.s1` |

Objects per second is over the process wall. The two `sentinel-cogs` counts
differ because the bucket changed between runs; each is the exact integer that
run returned, and neither was verified against a key manifest, because none
exists for a changing public bucket. The figure is
[`charts/real-s3-ladder.svg`](charts/real-s3-ladder.svg), rows in
[`charts/real-s3-ladder.csv`](charts/real-s3-ladder.csv).

### The billion-object run, and its boundary

Swath returned an exact 1,068,477,307 objects from `sentinel-cogs` in
5 m 41 s of process wall (`swath.7b028bd8c692.s1`). The listing phase took
5 m 12 s and the compressed TSV was on disk at about 5 m 39 s; those two
milestones come from the run's own summary, and only the process wall is in
the release rows.

| boundary | detail |
| --- | --- |
| one run | one day, one bucket, not repeated; no interval exists |
| one modest machine | a Standard `n4-highcpu-32` in `us-east1`, listing a `us-west-2` bucket over the public internet |
| bottleneck was ours | about 25 of 32 cores produced compressed TSV; S3 returned no `SlowDown` |
| not 5M keys/s | the whole-process rate is 3.13M objects/s; the listing phase alone is faster, and neither reaches 5M/s |
| one tool | no other subject ran against live S3 in this release's window; the August live pass predates this ledger and is not exported |
| listing, not inventory | key, size, ETag, modification time and storage class; not a comparison with S3 Inventory |

## What this study does not establish

- **It does not rank listing tools.** The subjects were built for different
  jobs, several have no listing-parallelism control, and the arms compared
  are not equivalent configurations of one design.
- **It does not establish anyone's performance on live S3**, except as the
  one-tool observations above, which are single uncontrolled runs.
- **It does not establish a calibrated replay comparison.** The instrument
  missed its declared budget on one request shape, so no row is a
  measurement.
- **It does not establish correctness.** Counts are checked against fixture
  object counts, not key by key. "Ran" and "verified" are separate facts.
- **It does not establish scaling behaviour.** Each live-S3 point has a
  different machine, output mode, region and bucket shape.
- **It does not establish tuning conclusions.** Concurrency arms separated by
  single-run noise under a pressured instrument are not evidence of a knee.
- **It says nothing about a bucket it did not list**, nor about
  S3-compatible stores, versioned buckets, or credentialed access.

Swath is built by the organisation that runs this study. That is disclosed on
every page, the run-record rules applied to it are the ones applied to every
other subject, and the one instrument defect found in this release slows Swath
rather than favouring it.

## Reproduction

| to check | where |
| --- | --- |
| a claim | filter `summary.csv` on `attempt_id`, or read the row in `attempts.jsonl`: verbatim `invocation.argv`, image digests, evidence digests |
| a figure | each chart has its rows beside it as CSV; the selection rule is `benchmark/publication/charts.yaml` |
| a case | plans under `benchmark/plans/campaigns/`; group-to-plan attribution in `manifest.json.groups`; the harness in `benchmark/README.md` and `benchmark/docs/running.md` |
| the release itself | `uv run python -m benchmark.public_validate --release-dir results/2026-09-scale-diagnostics`, from a clone, no private access |

## Publication decisions

- Fixtures are published as digests and shape metrics only: no listing data
  and no key material.
- Failed and cancelled attempts stay in the rows with their `state` and
  `state_detail`. A settled attempt with no evidence is data, not an omission.
- No generated site. The release is this directory and this report.
- No cost figure is quoted; the row schema carries none. A later export may
  add a Spot list-price estimate, labelled as such.

## Corrections

A release directory is immutable once tagged. A correction is a new release
whose manifest names what it supersedes; see the policy in
`results/README.md`.
