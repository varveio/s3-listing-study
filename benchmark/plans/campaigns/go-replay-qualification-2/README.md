# Go replay qualification, wave 2 (list index)

Bounded diagnostic qualification of the replay instrument. Varve maintains this
study and builds Swath; Swath here is the Java 0.3.2 listing subject, never
Swath-Go. This is not a calibrated benchmark or a ranking. **Not submitted.**

## Why a second wave

Wave 1 (`../go-replay-qualification/`, receipt
`../../../receipts/go-replay-qualification-cloud-20261110/`) passed the Go
replay server everywhere except the blockchain Swath c256 cells: all five Go
runs were PRESSURE_DEGRADED because about 2-3% of `delimiter=/` structure probes
overran their 55 ms deadline. The cause is in the server, not the allocation: a
delimited listing decoded a key page per emitted common prefix. The fix is the
SeekParquet list boundary index (seekparquet PR #33, unmerged), which the Go
server builds at startup over a SeekParquet dataset (swath branch
`go/replay-list-index`, `bcd2a9d4`). This wave asks whether the index lets the
same cells, at the same allocations, pass the timing grade. A same-wave index-off control separates the index
effect from the fixture-format change.

## What changes from wave 1, and what does not

The index-on plans follow the wave-1 Go plans of the same names with these
changes; the added index-off control is described below:

- **Fixture.** The SeekParquet conversion of the same release fixture, a
  footer-only rewrite whose data bytes are those of the wave-1 parts:
  `fixture_uri` names the dataset root, `fixture_sha256` is its `manifest.json`,
  and `fixture_adopted_sha256` is the wave-1 fixture digest, recomputed from the
  manifest's `adopted_from` rows and checked again by staging.

  | Fixture | Dataset root (`fixtures/...`) | Manifest SHA-256 | Wave-1 digest (adopted) |
  | --- | --- | --- | --- |
  | aws-public-blockchain | `aws-public-blockchain/seekparquet-c4a42eb4e7fb/` | `c4a42eb4…2b29` | `f4a839f2…d4b0` |
  | nara-1950-census | `nara-1950-census/seekparquet-6ce6d7d12442/` | `6ce6d7d1…c5a0` | `48468b7c…5db4` |
  | noaa-nws-fourcastnetgfs-pds | `noaa-nws-fourcastnetgfs-pds/seekparquet-eb0e7f5af5a2/` | `eb0e7f5a…6684` | `1b8be9d0…0ad4` |

- **Server.** `replay_list_index: true` and `replay_list_index_max_bytes:
  67108864` (the server default, stated so the record names it). The image is
  the placeholder `swath-replay@sha256:REPLAY_GO_IMAGE_TBD`; `campaign submit`
  refuses the plans until it is replaced by the digest of a Go replay image
  built with the list index.
- **Repetitions.** Blockchain Swath at replay 64 vCPU / 20 GiB: 2 reps
  (`blockchain-swath-go`). Blockchain Swath at 16 vCPU / 5 GiB: 1 rep
  (`blockchain-ladder-go`; the 32/10 and 8/4 rungs are dropped). NARA Swath
  c128, s7cmd `recursive-one-nosort` c64 on NARA, and the s5cmd FourCast anchor:
  1 rep each. The added `blockchain-swath-go-index-off` cell runs Swath Java
  0.3.2 c256 with replay 64 vCPU / 20 GiB for 1 rep on the same SeekParquet
  fixture and server image, with `replay_list_index: false`. It omits
  `replay_list_index_max_bytes`, which is invalid with the index off. Seven jobs
  across six plans. This owner-approved control extends wave 1's planned
  six-job follow-up limit by one job.

Unchanged: subject settings, concurrency, machine shapes (n4-highcpu-80 /
-32 / -16), subject and replay cpusets and memory, reader-decode ceiling 128 on
blockchain, 4,096 request slots, 1 GiB response budget, 192 cache windows of
12,500 rows, latency deadlines, declared warm-up, the timing grader
and its thresholds.

There is no Java replay-server arm: the Java server does not serve a
SeekParquet dataset (the harness refuses the combination). Comparisons are to wave 1's Java
repetitions on the bytes this dataset was converted from, which makes them
cross-wave, cross-day comparisons; report them as such.

## Predeclared reading

Before results are inspected: an index-on cell qualifies if every repetition
completes with the fixture's row count, zero server errors and refusals, grades
TIMING_VALID, and its receipt's `list_index` block reports `delivered: true`.
That requires the job's exported `state=built` server line attached with
`receipt --server-log` and agreeing with the meters; the meter conditions alone
(one recorded build, `max_bytes` 67108864, and on blockchain a non-zero interval
`skips`) are necessary but leave `delivered` null, which does not qualify. The agreement band stays ±10% of the subject wall time
against the wave-1 Java median of the same cell (blockchain 155.3 s, NARA Swath
64.5 s, NARA s7cmd 602.2 s, FourCast s5cmd 420.6 s). Report every repetition,
the structure-probe overrun fraction, list-index skips and declines by reason,
and the build seconds. A single repetition supports a bounded diagnostic
recommendation, not a precision claim. The 16/5 rung's verdict is a fit-based
inference on n4-highcpu-80, not a measurement on a smaller VM.

The index-off control must complete with the expected row count, zero server
errors/refusals, zero recorded index builds and zero interval skips; capture its
`state=off` server line as well. Report its timing grade even if degraded. Compare
its wall time and structure-probe overruns with both 64/20 index-on repetitions
within this wave; the non-zero blockchain skips requirement remains for every
index-on blockchain repetition.

## Before submitting

1. Merge seekparquet PR #33 and build the Go replay image from swath
   `go/replay-list-index` (or its merged successor); replace
   `REPLAY_GO_IMAGE_TBD` in all six plans with its `@sha256:` digest.
2. At submit time, rebuild and publish the toolbox worker from the final clean
   revision containing this harness change: the wave-1 worker (`7e6534d5…`)
   refuses the new
   `replay_list_index*` and `fixture_adopted_sha256` fields and the dataset-root
   `fixture_uri`. Emit a fresh image set from it.
3. Dry-run each plan, record a priced estimate, then submit with Spot and the
   study VPC, as wave 1 did:

   ```sh
   uv run python benchmark/src/benchmark/campaign.py --state "$STATE/campaign.db" submit \
     --suite go-replay-qualify --group goq2-<plan> \
     --plan benchmark/plans/campaigns/go-replay-qualification-2/<plan>/<bucket>.yaml \
     --project <project> --location us-east1 --zone us-east1-b \
     --results-bucket <result-store> --image-set "$STATE/images.json" \
     --anonymous-worker-sa <anonymous-worker-sa> \
     --network <network> \
     --subnetwork <subnetwork> \
     --provisioning SPOT --boot-disk-gb 200 --dry-run
   ```

4. After settlement, export each Go job's `swath_replay list_index` line from
   Cloud Logging and attach it with `benchmark.receipt --server-log` (see
   `benchmark/docs/running.md`, *Export a receipt draft*). Keep this post-hoc
   capture; no Swath subject flag is needed.

## Budget

Retain failures; only a preempted job may be resubmitted once; poll at least
five minutes apart; stop any job stuck past 90 minutes. Wave 1's observed
create-to-terminal durations for the six index-on jobs, plus the mean duration
of its two 64/20 Go blockchain repetitions for the added control, price seven jobs at about $0.80
of compute using the rates recorded in the
[wave-1 receipt](../../../receipts/go-replay-qualification-cloud-20261110/README.md).
The added control contributes about $0.15; index-build startup time is not
measured by that proxy. With one preemption resubmission of the largest job and
the $1 non-compute reserve wave 1 used, the planning estimate is about $1.96. If every
job ran to its provider deadline the compute ceiling would be $18.38, so the
90-minute stop rule is what bounds it (seven jobs stopped at 90 minutes: about
$13.55). These are estimates, not an invoice.

## Pre-submit record

Images, recorded before submission:

- Go replay: `<study-registry>/swath-replay@sha256:3b3584460761de559c07e73b79c4aa7bffd77318108fdbf12ae36c41a98933b5`
  (tag `go-545a10cb`), built from a clean detached worktree of Swath main
  `545a10cbdada373419e105202dc8cd9be57f4fa3` (PR #425, pins seekparquet
  `6acba1a`) with `docker build -f Dockerfile.go --target swath-replay
  --platform linux/amd64 --build-arg SWATH_REVISION=<sha>`. `--version` prints
  `swath-replay devel (545a10cb…)`; `serve --help` lists `-list-index` and
  `-list-index-max-bytes`. A local smoke over the staged NARA dataset printed
  `state=built ... has_index=true seconds=1.094`. The merged line adds
  `has_index`, which the harness reads since `1a0f845`.
- Toolbox worker: `<study-registry>/benchmark-toolbox@sha256:a878e4296bb7c07816eb4437b78f1209c9698e5c3ce891caf53cffe1a7a40e85`
  (tag `go-replay-qualification-2`), built by `benchmark/src/benchmark/build_image.py`
  at clean harness revision `1a0f845d7798b84771f85425a6f3dc8dcc6f5e33`, toolbox
  manifest `e44c4056…125f`; the image set was emitted by the same tool.

Pre-submit estimate (N4 Spot us-east1 rates from the wave-1 receipt, wave-1
create-to-terminal durations of the matching cells; the index-off control
priced as the mean of the two wave-1 64/20 Go repetitions):

| Plan | Jobs | Machine | Estimated compute |
| --- | --- | --- | --- |
| blockchain-swath-go | 2 | n4-highcpu-80 | $0.2947 |
| blockchain-swath-go-index-off | 1 | n4-highcpu-80 | $0.1473 |
| blockchain-ladder-go (16/5) | 1 | n4-highcpu-80 | $0.1360 |
| nara-swath-go | 1 | n4-highcpu-32 | $0.0286 |
| nara-s7cmd-go | 1 | n4-highcpu-32 | $0.1409 |
| fourcast-s5cmd-go | 1 | n4-highcpu-16 | $0.0526 |
| **Total** | 7 | | **$0.80** |

The list-index build (about 10 s on blockchain) adds under $0.01 per job. With
one preemption resubmission and the $1 non-compute reserve, about $1.96. The
owner-authorized spend is $10; seven jobs stopped at 90 minutes would exceed
it ($13.55), so any job still running at 60 minutes is stopped (seven jobs at
60 minutes: $9.04).


## Results (2026-11-10)

Seven Spot jobs, submitted 17:48 UTC, all SUCCEEDED by 18:03 UTC with no
preemption or retry. Receipt:
[`../../../receipts/go-replay-qualification-2-cloud-20261110/`](../../../receipts/go-replay-qualification-2-cloud-20261110/README.md).
Every subject exited zero with the expected rows, with zero server errors and
refusals, and every receipt reports `list_index.delivered: true` with no
disagreement between the attached server line and the meters (index-on jobs
`state=built`, the control `state=off` with zero builds and skips).

Per-shape cells are mean server service time / deadline, then overrun %.

| Job | Replay | Grade | worker_page | pivot_probe | structure_probe | Index skips / dense-page declines | Build s | CPU mean / p90 | Server RSS | Wall s | vs wave-1 Java median | Cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| blockchain c256 `swath.954f110b5470.s1` | 64/20, index on | PRESSURE_DEGRADED | 13.8/94 ms, 2.81% | 2.2/46 ms, 0.28% | 0.9/55 ms, 0% | 11,419 / 61,509 | 3.20 | 15% / 28% | 2.56 GiB | 146.2 | −5.9% | $0.150 |
| blockchain c256 `swath.954f110b5470.s2` | 64/20, index on | PRESSURE_DEGRADED | 14.1/94 ms, 3.00% | 1.6/46 ms, 0.18% | 1.0/55 ms, 0% | 11,328 / 61,475 | 3.33 | 14% / 25% | 2.37 GiB | 152.3 | −1.9% | $0.160 |
| blockchain c256 `swath.2076e4dd8761.s1` | 64/20, index **off** | PRESSURE_DEGRADED | 13.2/94 ms, 2.56% | 2.2/46 ms, 0.19% | 9.3/55 ms, 2.55% | 0 / 0 | – | 15% / 25% | 2.55 GiB | 150.0 | −3.4% | $0.159 |
| blockchain c256 `swath.5d9e6e396e48.s1` | 16/5, index on | PRESSURE_DEGRADED | 23.1/94 ms, 8.28% | 6.1/46 ms, 4.57% | 1.0/55 ms, 0% | 11,273 / 61,453 | 3.10 | 52% / 84% | 2.28 GiB | 138.8 | −10.6% | $0.159 |
| NARA Swath c128 `swath.773bf96d14ef.s1` | 20/20 | TIMING_VALID | 0.8/86 ms, 0% | 0.7/44 ms, 0% | 1.3/50 ms, 0% | 224 / 50,406 | 0.59 | 4% / 9% | 1.04 GiB | 64.5 | −0.1% | $0.038 |
| NARA s7cmd c64 `s7cmd.8b7670175058.s1` | 20/20 | TIMING_VALID | 1.3/86 ms, 0% | – | 3.3/50 ms, 0% | 4 / 261 | 0.58 | 0.3% / 0.2% | 1.13 GiB | 603.3 | +0.2% | $0.151 |
| FourCast s5cmd `s5cmd.c6cfb2bf8dd8.s1` | 8/8 | TIMING_VALID | 0.2/85 ms, 0% | – | – | 0 / 0 | 0.17 | 0.5% / 0.6% | 0.98 GiB | 445.1 | +5.8% | $0.058 |

Server RSS is the lifetime VmHWM (includes startup, index build and warm-up); costs charge create-to-terminal
time at N4 Spot rates (total $0.8757; $0.7263 by `runDuration`).

**The list index fixed the probe it targeted.** On blockchain at 64/20, structure
probes went from 9.3 ms mean and 2.55% overrun with the index off to 0.9-1.0 ms
and 0% with it on, matching wave 1's index-free 4.3-5.0 ms / 1.9-2.5%. The build
took 3.1-3.3 s and retained 1.17 MB. Wall time did not separate: 146.2 and
152.3 s on versus 150.0 s off, inside run-to-run spread.

**Blockchain still does not qualify, for a different shape.** Every blockchain
job, index on or off, overran worker pages: 2.6-3.0% at 64/20 (13-14 ms mean)
and 8.3% at 16/5, against the 1% validity threshold. Wave 1's Go server, at
Swath `a93035d` on the Java-era parts, served the same worker pages at 4.7-5.0
ms with 0% overrun at 64/20. Because the index-off control shows the same
regression, it is not the index: it is either the server change between
`a93035d` and `545a10cb` or reading the SeekParquet dataset rather than the
legacy parts. This wave does not separate those two. The 16/5 rung is also
CPU-bound (p90 84%) and its 10.6% wall gap is outside the ±10% band.

**NARA and FourCast qualify** on the SeekParquet datasets with the index on:
TIMING_VALID, zero overruns, walls within 0.2% of wave 1's Java medians for
NARA Swath and s7cmd. The single FourCast repetition is 5.8% above the wave-1
Java median (inside the band) and 7.5% above wave 1's three Go repetitions;
one repetition does not establish a shift.

These are cross-day comparisons with wave 1 and single repetitions except the
two 64/20 index-on runs; they support bounded diagnostic statements, not
precision claims.

## Follow-up beyond wave 2: Java-era fixture (2026-11-10)

One owner-approved job outside the seven-job wave
(`followup-blockchain-javaera-go/`): blockchain Swath c256, replay 64/20, the
same Go image at Swath `545a10cb`, index off, on wave 1's Java-era fixture
instead of its SeekParquet conversion. It differs from the wave-2 index-off
control only in the fixture. Submitted 18:07 UTC, SUCCEEDED 18:12 UTC, expected
rows, zero server errors and refusals, server line `state=off` (the Java-era
parts have no index), cost $0.141.

| Job | Server | Fixture | Index | Grade | worker_page | pivot_probe | structure_probe | CPU mean / p90 | Server RSS | Wall s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| wave 1 `swath.58e78e55aa7d.s1` | `a93035d` | Java-era | n/a | PRESSURE_DEGRADED | 4.7/94 ms, 0% | 0.5/46 ms, 0% | 5.0/55 ms, 2.54% | 6.7% / 12.3% | 1.92 GiB | 147.3 |
| wave 1 `swath.58e78e55aa7d.s2` | `a93035d` | Java-era | n/a | PRESSURE_DEGRADED | 5.0/94 ms, 0% | 0.7/46 ms, 0% | 4.3/55 ms, 1.87% | 6.4% / 11.3% | 1.88 GiB | 155.9 |
| follow-up `swath.98f424ce32cc.s1` | `545a10cb` | Java-era | off | PRESSURE_DEGRADED | 5.1/94 ms, 0% | 0.7/46 ms, 0% | 6.2/55 ms, 3.27% | 6.9% / 11.7% | 1.98 GiB | 147.8 |
| wave 2 `swath.2076e4dd8761.s1` | `545a10cb` | SeekParquet | off | PRESSURE_DEGRADED | 13.2/94 ms, 2.56% | 2.2/46 ms, 0.19% | 9.3/55 ms, 2.55% | 14.8% / 25.4% | 2.55 GiB | 150.0 |
| wave 2 `swath.954f110b5470.s1/.s2` | `545a10cb` | SeekParquet | on | PRESSURE_DEGRADED | 13.8-14.1/94 ms, 2.81-3.00% | 1.6-2.2/46 ms, 0.18-0.28% | 0.9-1.0/55 ms, 0% | 14-15% / 25-28% | 2.37-2.56 GiB | 146.2-152.3 |

**The worker-page regression is the SeekParquet read path, not the server
change.** On the Java-era parts the `545a10cb` server reproduces wave 1:
5.1 ms worker pages with no overrun, half the server CPU, and the same
structure-probe overruns wave 1 had (3.27%, no index on that path). On the
SeekParquet conversion of the same bytes the same server, index off, takes
13.2 ms per worker page with 2.56% overrun, about twice the CPU and 0.6 GiB
more peak RSS. The list index removes the structure-probe overruns only on the
SeekParquet path, where worker pages now overrun instead, so no blockchain
configuration in this wave or follow-up grades TIMING_VALID. One repetition of
the follow-up; a diagnostic attribution, not a precision claim.

