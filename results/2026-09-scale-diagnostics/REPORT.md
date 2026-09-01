# S3 listing tools from 4 million to 1.07 billion objects: a diagnostic release

Release `2026-09-scale-diagnostics` · data as of 2026-09-01 · 270 settled attempts

This is the written companion to the release data in this directory. Every
number below is quoted from `summary.csv` / `attempts.jsonl` unless the text
says otherwise, and every claim names the attempt it rests on.

## Release status

> **This is a diagnostic release. Nothing in it is a measurement-grade
> comparison.**
>
> No attempt in this release carries `purpose = measurement`. Every row is a
> diagnostic or a preparation, and `manifest.json.claim_ceiling` says so in
> machine-readable form:
>
> | flag | value |
> | --- | --- |
> | `controlled_replay_diagnostics` | `true` |
> | `calibrated_replay_benchmark` | `false` |
> | `live_s3_performance` | `false` |
> | `universal_tool_ranking` | `false` |
>
> The replay instrument these runs were screened on has a known defect
> ([The instrument and its defect](#the-instrument-and-its-defect)) that
> penalises exactly one subject, Swath, in exactly one direction — slower. The
> live-S3 rows are single uncontrolled observations of one tool. Both facts
> bound everything here.

## What this release is

Eleven listing tools were installed, pinned by image digest, and driven through
one harness against staged replay fixtures of real S3 buckets, plus a small set
of runs against live S3. The purpose was to find out which approaches survive a
rising object count at all, and where each one stops — a screening funnel, not a
benchmark. The result is a settled record of what ran, what it returned, how
much memory it used, and, where a tool stopped, the mechanism that stopped it.

Three things are settled well enough to publish:

- **The funnel.** Ten subjects completed a 4.08M-object fixture. Four reached
  143M. The tools that stopped earlier did so for identifiable mechanical
  reasons — serial pagination, memory growth, request amplification, per-leaf
  serialisation — not because a stopwatch said they were slower.
- **Exact counts.** Where a run is recorded as returning a count, the count is
  the fixture's exact object count, or this report says how it differs. Nothing
  here is sampled or estimated.
- **A live-S3 anchor.** Swath listed a 1.07-billion-object public bucket on one
  VM, with an exact count, in a few minutes. That row is n=1 and involves no
  other tool.

Everything else — relative speed, scaling curves, tuning conclusions — is
diagnostic and is written as such.

## The funnel

Each rung is a staged replay fixture built from a real bucket's key listing.
Object counts are from `fixtures.json`; "reached" means at least one attempt of
that subject settled on that fixture, whatever its outcome.

| rung (fixture) | objects | subjects that reached it |
| --- | ---: | --- |
| `noaa-nws-fourcastnetgfs-pds` | 4,081,170 | aws-cli, minio-mc, ps3, rclone, s3-fast-list, s3kor, s3p, s5cmd, s7cmd, Swath |
| `nara-1950-census` | 13,540,310 | rclone, s3-fast-list, s3p, s5cmd, s7cmd, Swath |
| `real-changesets` (flat) | 13,868,442 | rclone, s7cmd, Swath |
| `idc-open-data` | 56,311,145 | Swath |
| `noaa-nbm-grib2-pds` | 66,405,936 | rclone, s3-fast-list, s3p, s5cmd, s7cmd, Swath |
| `aws-public-blockchain` | 143,008,674 | rclone, s3p, s7cmd, Swath |

The roster figure for the first rung is
[`charts/fourcast-roster.svg`](charts/fourcast-roster.svg), with the exact rows
behind it in [`charts/fourcast-roster.csv`](charts/fourcast-roster.csv): one
8-vCPU / 8-GiB arm per tool, all ten from the single group
`fc-cpu-corrected-20260828`, each returning the fixture's exact 4,081,170
objects. It is ordered by wall clock and it is not a ranking: three of those
subjects expose no listing-concurrency control at all, one was fed keyspace
cut-points by the harness, and one was penalised by the instrument.

Why each subject stopped where it did:

| subject | last rung reached | why it went no further |
| --- | --- | --- |
| aws-cli | 4.08M | One serial `ListObjectsV2` chain; the tool exposes no listing-parallelism control. At 143M objects that is roughly 143,000 sequential pages — arithmetic, not a measurement worth a VM. |
| minio-mc | 4.08M | Serial client-side iterator; same reasoning. |
| s3kor | 4.08M | Serial listing; its parallelism is in transfers, not listing. Same reasoning. |
| ps3 | 4.08M | Request amplification. The fairness arm at `--prefix-count 5000` (`ps3.31a9e4da68b2.s1`) reached the 1,800 s cap without returning a count. |
| s5cmd | 66.4M | No listing fanout of its own: the comparable arms are shard lists the harness supplies (`fanout-fixture-with-dirs`). Not carried to 143M. |
| s3-fast-list | 66.4M | Memory. Two of its three NBM attempts failed at the container limit; the arm that completed (`s3-fast-list.246cf7252988.s1`, 16 GiB) peaked at 11,347,320 KiB RSS on a 66.4M-object fixture. Carried no further, by decision. |
| s7cmd | 143M, without a count | Parallel prefix discovery, then sequential pagination per leaf. On the skewed 143M fixture `s7cmd.a9b999169187.s1` reached the 7,200 s cap (exit 124) with one dominant leaf still draining. |
| s3p | 143M | Completed: `s3p.1b77f20ed931.s1`, exact 143,008,674 in 4,238.8 s at c16. CPU-bound in its cheapest key-only mode; width does not help it. |
| rclone | 143M | Completed: `rclone.6319ec57665d.s1`, 667.0 s at c64, 1,893,836 KiB peak RSS. It returned 143,008,665 rows against the fixture's 143,008,674 — nine fewer, consistent with a hierarchical walk not emitting directory markers. The c128 arm (`rclone.3eff2ab4f661.s1`) was slower, at 821.0 s. |
| Swath | 143M | Completed: `swath.be4140354dd1.s1`, exact 143,008,674 in 173.7 s at c256. Every Swath row on this fixture is `CAPACITY_FAILED` — see the next section. |

## The instrument and its defect

The replay instrument is swath-replay: a server that answers `ListObjectsV2`
from a staged Parquet copy of a real bucket's keyspace, under a declared latency
budget per request shape. Three shapes are declared and enforced:

- `worker_page` — an ordinary listing page,
- `structure_probe` — a `delimiter=/` rollup that returns CommonPrefixes,
- `pivot_probe` — a single-key point lookup.

Each attempt's `replay.latency_model.deadlines_ms` records the budget it ran
under, and the harness classifies the *delivered* timing after the fact. That
classification is `replay_timing` in `summary.csv`, and it is fail-closed: a run
whose instrument missed its budget cannot be published as a measurement, however
the subject behaved.

**The defect.** On the fixtures with many small directories — NARA, NBM and
`aws-public-blockchain` — the server overruns the `structure_probe` budget under
page load. On `aws-public-blockchain` at c256 (declared 94 / 55 / 46 ms, replay
allocated 64 vCPU and 20 GiB, `swath.be4140354dd1.s1`) the delivered
structure-probe mean is roughly twice the declared 55 ms and just under half of
those probes overrun. The cause is in the server's sorted-Parquet seek: a
delimiter rollup reopens a row-group reader and decodes a whole key page per
seek, so replay's rollup is far more expensive than its own page — the inverse
of live S3, where a rollup is cheaper than a page because it returns less.

**Who this affects.** Only Swath issues structure probes at volume. rclone,
s5cmd, s3p, s7cmd and the serial tools do not, and their rows on the same
fixtures under the same treatment classify `TIMING_VALID` or
`PRESSURE_DEGRADED`. Every Swath-versus-other comparison in this release is
therefore biased *against* Swath, and any Swath ratio here is a lower bound.

**Live S3, for calibration.** The 2026-09-01 live-S3 runs recorded per-shape
client-side latencies against real buckets: structure probes at p50 in the
50–100 ms range with heavy p99 tails, and worker pages at roughly 216 ms p50 for
a 1,000-entry page. The flat 55 ms structure budget is therefore close to live
S3's median rather than generous, which makes the instrument's overrun a
one-directional penalty and not a modelling artefact. These per-shape latency
distributions live in the campaign ledger's replay evidence and in the runs' own
summaries; this release's row schema carries the declared deadlines and the
delivered classification, not the delivered distributions.

**A negative result, disclosed.** The leading hypothesis for the overrun was
cold-path cost — JIT warm-up and lazily opened reader pools. It was tested
directly: `swath.6d4bdaf4f615.s1` repeated the `aws-public-blockchain` c256 case
on an instrument warmed before the measurement window by a declared
2,000 / 200 / 200 request warm-up, visible in the export as
`replay.requests.before = 2400` against `0` for the cold run
`swath.be4140354dd1.s1`. The warm-up completed in 7.3 s over 27,891 prefixes and
changed nothing: structure-probe mean ratio 2.03 warmed against 2.07 cold,
overrun fraction 45.4% against 46.0%, with the subject's wall clock unmoved
(174.5 s warmed, 173.7 s cold, both exact at 143,008,674). Warm-up is falsified
as the explanation; the per-seek constant is the defect. The ratio, overrun and
warm-up-duration figures come from the ledger's delivered-timing evidence, not
from this release's rows; the wall clocks, counts and `requests.before` values
are in `summary.csv` and `attempts.jsonl`.

One consequence of the warm-up block being new: the warmed row's replay evidence
does not satisfy the classifier's contract, so it exports as
`replay_timing = MALFORMED` rather than as a delivered-timing verdict. It is
kept, labelled and disclosed rather than dropped.

## Per-tool dispositions

One row per subject: the best-completing arm actually recorded, and the standing
disposition. "Exact" means the run returned the fixture's exact object count.
Every figure is `outcome.wall_seconds`, `outcome.row_count` and
`outcome.max_rss_kb` for the named attempt.

| subject | version | best completing arm | fixture | wall | count | peak RSS (KiB) | delivered timing | disposition |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| aws-cli | 2.36.1 | `aws-cli.a7d9377bd706.s1` `s3api-v2-text` | FourCast 4.08M | 700.0 s | exact | 85,076 | `TIMING_VALID` | Serial by construction; kept as the reference point, not carried up the ladder. |
| minio-mc | RELEASE.2025-08-13T08-35-41Z | `minio-mc.04c17e5ac8da.s1` `recursive` | FourCast 4.08M | 419.7 s | exact | 49,920 | `TIMING_VALID` | Serial iterator; correct, and flat in memory. Not carried up. |
| s3kor | v0.0.37 | `s3kor.8514c3397199.s1` `list` | FourCast 4.08M | 411.0 s | exact | 50,176 | `TIMING_VALID` | Serial listing; its parallelism is in transfers. Not carried up. |
| ps3 | 0.1.16 | `ps3.55c79d26bce0.s1` `list` c256 | FourCast 4.08M | 369.3 s | exact | 92,116 | `TIMING_VALID` | Completes at 4M; the wider fairness arm timed out. Not carried up. |
| s5cmd | v2.3.0 | `s5cmd.962211b4b344.s1` `fanout-fixture-with-dirs` c64 | NBM 66.4M | 352.2 s | exact | 1,380,592 | `TIMING_VALID` | Completes only with harness-supplied shards; that asymmetry is disclosed wherever it is charted. |
| s3-fast-list | 1.1.0 | `s3-fast-list.246cf7252988.s1` `list-hinted-fixture` c100 | NBM 66.4M | 333.8 s | exact | 11,347,320 | `PRESSURE_DEGRADED` | Fast when fed cut-points; memory is the binding constraint. Two NBM attempts died at the container limit. |
| s3p | 3.7.2 | `s3p.1b77f20ed931.s1` `ls` c16 | blockchain 143M | 4,238.8 s | exact | 335,352 | `TIMING_VALID` | Reaches 143M correctly, CPU-bound. |
| s7cmd | 1.5.0 | `s7cmd.bdba69aad415.s1` `recursive-one-nosort` c16 | NARA 13.5M | 601.0 s | exact | 111,160 | `CAPACITY_FAILED` | Correct at 13.5M. Returned no count on NBM (exit 1, all four arms) and reached the 7,200 s cap on the skewed 143M fixture. |
| rclone | 1.74.4 | `rclone.6319ec57665d.s1` `recursive-walk` c64 | blockchain 143M | 667.0 s | 143,008,665 of 143,008,674 | 1,893,836 | `TIMING_VALID` | Reaches 143M. Nine rows short of the fixture count, consistent with directory markers not being emitted. Memory is the constraint on a flat namespace — see below. |
| Swath | 0.3.1 | `swath.be4140354dd1.s1` `recursive-tsv-dataset` c256 | blockchain 143M | 173.7 s | exact | 2,232,392 | `CAPACITY_FAILED` | Reaches 143M. Every Swath row on the small-directory fixtures is instrument-penalised, so its numbers there are lower bounds. Built by the organisation that maintains this study. |

s4cmd is registered in the roster and has no attempt in this release.

## The flat cell

A flat namespace — no interior slashes, so no directory structure for a walker
to fan out across — is the shape that separates prefix-parallel designs from
range-parallel ones. The fixture is the retained product of a live-S3 Swath
capture of `real-changesets`: fixture `real-changesets/current-2908fdf`,
13,868,442 rows, SHA-256 `295177c9…c57edd`, one part, no companions.

Every arm ran under one declared allocation: subject 8 vCPU / 8 GiB, replay
20 vCPU / 20 GiB with 64 Parquet connections and 512 concurrent requests,
declared deadlines 122 ms `worker_page` / 88 ms `structure_probe` / 88 ms
`pivot_probe`, on `n4-highcpu-32`.

| arm | attempt | wall | count | peak RSS (KiB) | outcome |
| --- | --- | ---: | --- | ---: | --- |
| Swath c64 `recursive-tsv-dataset` | `swath.666e471aac76.s1` | 47.5 s | exact 13,868,442 | 369,512 | Completed, over 14,845 replay requests. |
| s7cmd c16 `recursive-one-nosort` | `s7cmd.97b265107a89.s1` | 1,289.5 s | exact 13,868,442 | 50,944 | Completed as one serial drain: with no sub-prefixes to discover, its prefix-parallel phase has nothing to divide. |
| rclone c64 `recursive-walk`, 8 GiB | `rclone.795fbd66217b.s1` | 1,401.8 s | none | 8,369,376 | **Failed.** Killed at the container memory cap (subject exit -9) after issuing all 13,869 pages, having written nothing. |
| rclone c64 `recursive-walk`, 16 GiB | `rclone.d92a513cb0f2.s1` | 1,377.6 s | exact 13,868,442 | 8,121,012 | Completed. The memory control for the row above: the walk holds about 8.1 GB resident for a 13.9M-entry directory, so the 8 GiB cap was the edge of that footprint, not a leak. |
| rclone `lsjson --fast-list -R`, 8 GiB | `rclone.997236778cca.s3` | 1,851.8 s | exact 13,868,442 | 73,764 | Completed on the third Spot attempt; the first two were preempted before producing evidence and are exported as failures with no outcome. rclone's default streaming path holds about 74 MB resident, so the memory profile above belongs to the walk mode, not the tool. |

What the completed rows show: on a flat namespace the per-directory walk holds
the whole result set in memory and is killed at 8 GiB before emitting anything,
and the prefix-discovery design degenerates to a single sequential pagination.
The range-splitting design is unaffected, because it does not need directories
to divide the keyspace. rclone's default streaming mode settles the memory
question: it completes the same cell in 74 MB, so the 8 GB footprint belongs to
`recursive-walk`, not to rclone. Both rclone modes are one sequential pagination
of 13,869 pages. Their wall clocks differ because of the replay deadline each
request shape draws, 88 ms for the walk's delimiter pages against 122 ms for the
streaming mode's plain pages, not because one mode lists faster.

## Swath on live S3

These rows ran against live public buckets from a single VM in GCP `us-east1`,
unsigned reads, n=1 each, no other subject present and no instrument between the
tool and S3. They are observations, not measurements: the buckets are not under
our control, the machine types and output modes differ between rows, and nothing
was repeated. `manifest.json` discloses them under
`real-s3-rows-are-single-observations`.

| bucket | objects (exact) | machine | mode | wall | objects/s over process wall | attempt |
| --- | ---: | --- | --- | ---: | ---: | --- |
| `real-changesets` | 13,868,442 | n4-highcpu-16 | sorted Parquet c1024 | 62.7 s | 221,259 | `swath.85ccd37c1b88.s1` |
| `fah-public-data-covid19-absolute-free-energy` | 522,925,693 | n4-highcpu-16 | sorted Parquet c1024 | 370.7 s | 1,410,484 | `swath.d7668a13dc4c.s1` |
| `fah-public-data-covid19-absolute-free-energy` | 522,925,693 | n4-highcpu-32 | sorted Parquet c2048 | 281.9 s | 1,854,866 | `swath.4b2db6e9f6a9.s1` |
| `fah-public-data-covid19-absolute-free-energy` | 522,925,693 | n4-highcpu-32 | TSV + zstd c2048 | 187.3 s | 2,791,827 | `swath.a0f1b2c053d8.s1` |
| `janelia-cosem-datasets` | 959,831,933 | n4-highcpu-16 | sorted Parquet c1024 | 724.2 s | 1,325,437 | `swath.0b5db9b35947.s1` |
| `janelia-cosem-datasets` | 959,831,933 | n4-highcpu-32 | sorted Parquet c2048 | 585.1 s | 1,640,372 | `swath.0d01af45ef74.s1` |
| `sentinel-cogs` | 1,068,443,985 | n4-highcpu-32 | sorted Parquet c1024 | 707.4 s | 1,510,392 | `swath.6b1ffae260c0.s1` |
| `sentinel-cogs` | 1,068,477,307 | n4-highcpu-32 | TSV + zstd c2048 | 341.4 s | 3,129,747 | `swath.7b028bd8c692.s1` |

The two `sentinel-cogs` counts differ because they are listings of a live bucket
taken hours apart. Both are exact counts of what was there at the time.

The figure is [`charts/real-s3-ladder.svg`](charts/real-s3-ladder.svg), with its
rows in [`charts/real-s3-ladder.csv`](charts/real-s3-ladder.csv).

### The landmark, and its boundary

**A billion objects listed in about five minutes.** On `sentinel-cogs`,
`swath.7b028bd8c692.s1` returned an exact 1,068,477,307 objects: the listing
phase took 5 m 12 s, the compressed TSV was complete on disk at about 5 m 39 s,
and the process exited at 341.4 s (5 m 41 s) — the figure this release's rows
carry. The listing-phase and product-on-disk milestones come from the run's own
summary in the campaign ledger; only the process wall is projected into
`summary.csv`.

Its boundary, to be stated with it every time it is quoted:

- **n = 1.** One run, one day, one bucket. Not repeated; no interval exists.
- **One machine, and a modest one.** A single Standard `n4-highcpu-32` — 32
  vCPU — in GCP `us-east1`, listing a bucket in `us-west-2` over the public
  internet. No cluster, no fleet, no privileged access.
- **CPU-bound on the sink, not on S3.** The run spent about 25 of its 32 cores
  producing compressed TSV, and S3 returned no `SlowDown`. The bottleneck was
  ours.
- **This is not a 5M keys/s result.** The whole-process rate is 3.13M
  objects/s; the listing phase alone is faster. Neither reaches 5M/s, and this
  release does not claim it.
- **One tool.** No other subject has run against live S3 at all, so this says
  nothing comparative.
- **Listing, not inventory.** The product is a key listing with size, ETag,
  modification time and storage class. It is not a comparison against S3
  Inventory, which is a different mechanism with a different latency.

## What this study does not establish

- **It does not rank listing tools.** The subjects were built for different
  jobs, several expose no listing-parallelism control at all, and the arms
  compared are not equivalent configurations of one design.
- **It does not establish anyone's performance on live S3**, with the single
  exception of the one-tool observations above, which are n=1 and uncontrolled.
- **It does not establish a calibrated replay comparison.** The instrument
  missed its declared budget on one request shape, so no row here may be read as
  a measurement. The claim ceiling says so in machine-readable form.
- **It does not establish correctness.** Counts are checked against fixture
  object counts; no content verifier binds a run's output to a reference
  manifest key by key. "Ran" and "verified" remain separate facts.
- **It does not establish scaling behaviour.** Each live-S3 point has a
  different machine type, output mode, region and bucket shape. Four sizes on a
  chart are not a curve.
- **It does not establish tuning conclusions.** Concurrency arms separated by
  single-run noise under a pressured instrument are not evidence of a knee.
- **It says nothing about a bucket it did not list**, and nothing about
  S3-compatible object stores, versioned buckets, or credentialed access paths.

Independence: Swath is built by the organisation that runs this study. That is
disclosed on every page, the run-record rules applied to it are the ones applied
to every other subject, and the one instrument defect found in this release
happens to penalise Swath rather than favour it.

## Reproduction pointers

- **The data.** `attempts.jsonl` is the canonical public dataset, one object per
  attempt; `summary.csv` is the same rows flattened; `manifest.json` carries the
  claim ceiling, counts, groups and disclosures; `fixtures.json` and
  `subjects.json` carry fixture and subject identity as digests;
  `checksums.sha256` seals the directory. `results/README.md` states the release
  contract.
- **Checking a claim.** Every claim above names an attempt id. Filter
  `summary.csv` on `attempt_id`, or read the full row — including the verbatim
  `invocation.argv`, the image digests and the evidence digests — in
  `attempts.jsonl`.
- **Checking a figure.** Each chart has the exact rows it drew beside it as
  CSV, and the selection rule itself is committed in
  `benchmark/publication/charts.yaml`.
- **Re-running a case.** Plans live under `benchmark/plans/campaigns/`; the
  group-to-plan attribution is `manifest.json.groups`, declared in the release
  spec. `benchmark/README.md` and `benchmark/docs/running.md` describe the
  harness, and `benchmark/docs/publishing.md` describes this release pipeline.
- **Validating this directory.** From a clone, with no private access:
  `uv run python -m benchmark.public_validate --release-dir results/2026-09-scale-diagnostics`
  checks the seal, the row ordering, the claim gate and the absence of private
  estate, using nothing but the committed files.

## Publication decisions for this release

- Fixtures are published as digests and shape metrics only: no listing data and
  no key material.
- Failed and cancelled attempts stay in `summary.csv` and `attempts.jsonl`, with
  their `state` and `state_detail`. A settled attempt with no evidence is data,
  not an omission.
- No generated site. The release is this directory and this report.
- Cost may be published as a Spot list-price estimate, clearly labelled as one.
  The row schema in this release carries no cost field, so no cost figure is
  quoted here; a cost column, if it is added, arrives in a later export and
  carries that label.

## Corrections

A release directory is immutable once tagged. A correction is a new release
whose manifest names what it supersedes; see the correction policy in
`results/README.md`.
