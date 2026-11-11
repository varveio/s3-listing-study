# Go replay qualification, wave 3 (window-cache fills)

Bounded diagnostic qualification of the replay instrument. Varve maintains this
study and builds Swath; Swath here is the Java 0.3.2 listing subject, never
Swath-Go. This is not a calibrated benchmark or a ranking.

## Why a third wave

Wave 2 (`../go-replay-qualification-2/`, receipt
`../../../receipts/go-replay-qualification-2-cloud-20261110/`) qualified the Go
replay server on NARA and FourCast, but every blockchain Swath c256 job was
PRESSURE_DEGRADED: worker pages overran their 94 ms deadline on 2.6-3.0% of
requests at replay 64 vCPU / 20 GiB and 8.3% at 16 / 5, against the 1% validity
threshold. Its Java-era-fixture follow-up placed the regression in the
SeekParquet read path, not the server change. On the wave-2 runs the replay
server's own window-cache meters show where the time went: a
`swath.replay.prefetch.window.fill` took 40.6-42.1 ms on average (p99 92-101 ms)
on the SeekParquet dataset against 11.9 ms (p99 27 ms) on the Java-era parts with
the same server, 3.4-3.5 times the mean (table below; provenance: each attempt's
bound `result.json`, `replay_evidence.after`).

Since wave 2, the server reads less and copies once per fill: seekparquet #50
caches each checked value-column OffsetIndex instead of reloading it per request,
#54 adds `Dataset.RangeBatches`, and swath PR #443 fills the window cache from
SeekParquet's borrowed batches instead of an owning read and a copy (it pins
seekparquet `b1c848a`). The diagnosis behind these changes comes from local
profiling in private working notes, not from a committed run. This wave asks whether the rebuilt server
passes the timing grade on blockchain at the same allocations, and whether the
16 vCPU / 5 GiB rung now leaves enough headroom for the replay downsizing
decision on SeekParquet fixtures (wave 1's ladder showed 16-vCPU headroom on the
Java-era parts only).

## What changes from wave 2, and what does not

The three plans are copies of the wave-2 plans of the same names with **one
change: the Go replay server image.** A drift guard
(`test_wave3_plans_differ_from_their_wave2_cells_only_in_the_replay_image`)
resolves every wave-3 case and checks it equals its wave-2 case once the image is
set back to wave 2's `3b358446…` digest.

- `blockchain-swath-go`: blockchain Swath c256, replay 64 vCPU / 20 GiB, list
  index on, 2 reps.
- `blockchain-swath-go-index-off`: the same with `replay_list_index: false`,
  1 rep (the within-wave control).
- `blockchain-ladder-go`: the same as `blockchain-swath-go` at replay
  16 vCPU / 5 GiB, 1 rep (the downsizing input).

Four jobs, all on n4-highcpu-80. The image is the Go replay image built from
Swath main `43ffab2f`, where PR #443 merged (see *Pre-submit record*).

**What the new image contains.** It is built from Swath main, so besides #443 it
carries every Swath main commit since wave 2's `545a10cb`
(`git log --oneline 545a10cb..43ffab2f`):

| Commit | PR | Subject |
| --- | --- | --- |
| `0100e7ad` | #413 | deps: bump the go-objfile group in /go/objfile |
| `a1acefba` | #405 | ci: bump the actions group |
| `27ebca3c` | #427 | ci: setup-go v7 in the composite action |
| `05e36feb` | #428 | deps: Go 1.27.3 toolchain in the three modules and Dockerfile.go |
| `bc431b74` | #426 | go/listing: the default row budget follows memory |
| `5c2fa6e5` | #433 | reader: baseline Scan and Stream benchmarks and an allocation guard |
| `29b06cc9` | #429 | go/cli: --row-budget is --sort-buffer |
| `b546596a` | #434 | deps: azblob 1.8.3 and Go dependency refresh; Azure wire snapshot |
| `82512438` | #438 | deps: Go 1.27.4 and the release dependency refresh |
| `2a37d77a` | #441 | reader: borrowed column-batch scans with field selection |
| `c370b4d6` | #432 | ci: bump the actions group |
| `4121c6fd` | #440 | go/azblob: list in the Arrow format, XML when the service answers XML |
| `5803bf93` | #442 | ci: DuckDB 2.0.0, OSV-Scanner 2.7.1, Syft 1.57.1 (CI files only) |
| `43ffab2f` | #443 | go/replay: fill the window cache from SeekParquet's borrowed batches |

`git diff --stat 545a10cb 43ffab2f -- go/internal/replay` shows 8 files,
+463/−56. Three are `go/internal/replay/azure` (+28/−32, from #434 and #440):
the Azure endpoint, which an S3 subject does not call. The other five are #443:
`store/seek.go` (+32/−18, the fill path), its tests
(`store/seekscan_test.go`, `store/columntypes_test.go`) and comment-only edits to
`legacy/scan.go` and `metrics/defs.go` (no meter renamed). The `swath-replay` binary also links
`go/reader` (changed by #433, #438 and #441), but the replay uses it only for
product-name constants (`store/sorted.go`; source reading). The changes that
reach the S3 serving path in the binary are therefore #443's `store/seek.go`,
the seekparquet pin `6acba1a` → `b1c848a`
(#42, #43, #48, #50, #54, plus CI and the Go 1.27.4 toolchain bump #51), and the
build: the Go toolchain moves from 1.26.10 to 1.27.4 (#428, #438) and linked
modules move with it (klauspost/compress 1.20.3 → 1.20.4, andybalholm/brotli
1.2.8 → 1.3.0, parquet-go/bitpack 1.0.0 → 1.1.0, golang.org/x/sync 0.24 → 0.25,
golang.org/x/sys 0.49 → 0.50; from `go list -deps ./cmd/swath-replay` at
`4121c6fd` against the `545a10cb` `go.mod`; #443 changes only the seekparquet
line of `go.mod`). The one variable is the server build; the toolchain and
dependency bumps are inside it and this wave does not separate them from #443.

Unchanged: the SeekParquet blockchain fixture (`seekparquet-c4a42eb4e7fb/`,
manifest `c4a42eb4…2b29`, adopted from `f4a839f2…d4b0`), the subject (Swath Java
0.3.2, c256, the same capsule config), the toolbox worker (wave 2's image set can
be reused: no harness source changed since `1a0f845`), machine shape, subject and
replay cpusets and memory, list-index settings, reader-decode ceiling 128, 4,096
request slots, 1 GiB response budget, 192 cache windows of 12,500 rows, latency
deadlines, declared warm-up, and the timing grader and its
thresholds.

## Predeclared reading

Before results are inspected, wave 2's reading applies unchanged. An index-on
cell qualifies if every repetition completes with the fixture's row count, zero
server errors and refusals, grades TIMING_VALID, and its receipt's `list_index`
block reports `delivered: true` (the `state=built` server line attached with
`receipt --server-log` and agreeing with the meters). The agreement band stays
±10% of the subject wall time against the wave-1 Java median, 155.3 s. The
index-off control must complete with the expected rows, zero server
errors/refusals, zero recorded index builds and interval skips, and its
`state=off` line; report its timing grade even if degraded.

The 16/5 rung supports a downsizing recommendation only if it qualifies as above
and also meets wave 1's headroom conditions: mean replay cpuset utilization
below 60%, p90 below 80%, server lifetime peak RSS below 75% of its container
ceiling, and response-buffer peak below 75% of the budget. Its wall time is
also compared with the median of this wave's two 64/20 index-on repetitions
(±10%). That is a fit-based inference on n4-highcpu-80, not a measurement on a
smaller VM.

Comparisons with wave 2 are cross-day; comparisons with the Java-era follow-up
are cross-day and cross-fixture. A single repetition supports a bounded
diagnostic statement, not a precision claim.

## Evidence to extract after the run

For every attempt, alongside wave 2's columns (grade, per-shape service time and
overrun, index skips and declines, build seconds, CPU mean/p90, server RSS, wall,
cost):

1. **Per-shape overruns.** `report.py --state "$STATE/campaign.db" --group
   goq3-<plan>` prints each shape's mean against its deadline and its overrun
   fraction (the grader's `replay_timing_assessment`). Compare `worker_page`,
   `pivot_probe` and `structure_probe` with wave 2's Results table.
2. **Window-cache meters.** From each attempt's bound `result.json`, the
   `before` (after warm-up) and `after` (end of run) values of
   `swath.replay.prefetch.window.fill` (timer: count, mean, p50, p99, max),
   `swath.replay.prefetch.window.hit` and `swath.replay.prefetch.window.miss`
   (counters, by `reason`):

   ```sh
   gcloud storage cat <result-store>/go-replay-qualify/aws-public-blockchain/ATTEMPT_ID/result.json \
     | jq -c '.replay_evidence as $e | ("before","after") as $w
         | $e[$w].metrics.meters[]
         | select(.name | test("^swath\\.replay\\.prefetch\\.window\\.(fill|hit|miss)$"))
         | {at: $w, name, tags, count, mean_ms, p50_ms, p99_ms, max_ms}'
   ```

   Timer windows differ by statistic (swath `go/internal/replay/metrics/window.go`): count and sum are cumulative
   over the server's life, so `sum / count` is the lifetime mean and includes
   the warm-up fills recorded in `before`; the exported max covers roughly the
   last 2–3 minutes and p50 / p99 roughly the last 40–60 seconds; counts are compared as
   `after − before`.
3. **The `list_index` server lines**, exported from Cloud Logging and attached
   with `benchmark.receipt --server-log`, as in wave 2.

Wave-2 baselines for item 2 (`after` values from the bound `result.json`; every
`before` holds 400 warm-up fills and 400 cold misses):

| Attempt | Server / fixture / replay | fill count | fill mean / p50 / p99 / max ms | hit | miss cold / continuation |
| --- | --- | --- | --- | --- | --- |
| `swath.954f110b5470.s1` | `545a10cb` / SeekParquet / 64/20 on | 46,403 | 41.8 / 23.1 / 92.3 / 169.5 | 111,456 | 7,877 / 31,931 |
| `swath.954f110b5470.s2` | `545a10cb` / SeekParquet / 64/20 on | 46,806 | 42.1 / 26.2 / 100.7 / 175.0 | 110,791 | 8,219 / 32,270 |
| `swath.2076e4dd8761.s1` | `545a10cb` / SeekParquet / 64/20 off | 45,960 | 40.6 / 23.6 / 96.5 / 190.6 | 112,006 | 8,194 / 31,197 |
| `swath.5d9e6e396e48.s1` | `545a10cb` / SeekParquet / 16/5 on | 50,346 | 34.8 / 21.5 / 130.0 / 409.3 | 108,108 | 11,438 / 31,579 |
| `swath.98f424ce32cc.s1` | `545a10cb` / Java-era / 64/20 off | 55,642 | 11.9 / 6.7 / 27.3 / 70.8 | 101,157 | 8,287 / 41,751 |

Compare each wave-3 index-on 64/20 repetition with `954f110b5470` (same cell,
old server) and with `98f424ce32cc` (the Java-era cost the fix aims at); the
control with `2076e4dd8761`; the 16/5 rung with `5d9e6e396e48`.

## Before submitting

1. Build the Go replay image from a clean detached worktree of the Swath main
   commit where PR #443 merged, push it, and pin its `@sha256:` digest in all
   three plans; the drift guard checks the pinned image (done, see *Pre-submit
   record*).
2. Reuse wave 2's toolbox worker and image set (harness `1a0f845`): no harness
   source changed since.
3. Dry-run each plan, then submit with Spot and the study VPC, as wave 2 did:

   ```sh
   uv run python benchmark/src/benchmark/campaign.py --state "$STATE/campaign.db" submit \
     --suite go-replay-qualify --group goq3-<plan> \
     --plan benchmark/plans/campaigns/go-replay-qualification-3/<plan>/aws-public-blockchain.yaml \
     --project <project> --location us-east1 --zone us-east1-b \
     --results-bucket <result-store> --image-set "$STATE/images.json" \
     --anonymous-worker-sa <anonymous-worker-sa> \
     --network <network> \
     --subnetwork <subnetwork> \
     --provisioning SPOT --boot-disk-gb 200 --dry-run
   ```

4. After settlement, export each job's `swath_replay list_index` line and attach
   it with `benchmark.receipt --server-log` (`benchmark/docs/running.md`,
   *Export a receipt draft*).

## Budget

Retain failures; only a preempted job may be resubmitted once; poll at least
five minutes apart. The owner-approved spend cap for this wave is **$3**. Four
n4-highcpu-80 jobs running together cost $7.23 an hour, so the 60-minute stop
rule alone would not hold the cap: any job still running at 20 minutes is
stopped (four jobs at 20 minutes: $2.41 of compute).

Pre-submit estimate: each wave-3 job is priced at the observed create-to-terminal
duration of its wave-2 counterpart, at the us-east1 N4 Spot rate the wave-1
receipt records for n4-highcpu-80 ($1.80688/hour), as in wave 2's
`cost-observed.json`:

| Plan | Jobs | Wave-2 counterpart | Estimated compute |
| --- | --- | --- | --- |
| blockchain-swath-go | 2 | `swath.954f110b5470.s1`, `.s2` | $0.3108 |
| blockchain-swath-go-index-off | 1 | `swath.2076e4dd8761.s1` | $0.1590 |
| blockchain-ladder-go (16/5) | 1 | `swath.5d9e6e396e48.s1` | $0.1588 |
| **Total** | 4 | | **$0.63** |

The three 64/20 jobs are $0.47 of that; by `runDuration` instead of
create-to-terminal the four come to $0.51. A faster server shortens the subject
run but not provisioning, so this is an upper-leaning estimate. With one
preemption resubmission of the largest job and wave 2's $1 non-compute reserve,
the planning estimate is about $1.79, under the $3 cap. Four jobs stopped at
60 minutes would cost $7.23 of compute and at the provider deadline
(`maxRunDuration` 8,405 s) $16.87, which is why the 20-minute stop applies.
These are estimates, not an invoice.

## Pre-submit record

Images, recorded before submission:

- Go replay: `<study-registry>/swath-replay@sha256:f8b70f0f1c337271007b6aa8b00bf87f68bdd8d361dbbb7a19401edb5f629f90`
  (tag `go-43ffab2f`), built from a clean detached worktree of Swath main
  `43ffab2f8c2c11041daba71273451bdf66190fc7` (PR #443, pins seekparquet
  `b1c848a`) with `docker build -f Dockerfile.go --target swath-replay
  --platform linux/amd64 --build-arg SWATH_REVISION=<sha>`; the digest is the
  registry's (`gcloud artifacts docker images describe`). `--version` prints
  `swath-replay devel (43ffab2f…)`; `serve --help` lists `-list-index` and
  `-list-index-max-bytes`. A local smoke over the staged NARA SeekParquet
  dataset printed `state=built ... has_index=true seconds=0.684` and served a
  flat and a `delimiter=/` ListObjectsV2 page.
- Toolbox worker: wave 2's, unchanged:
  `<study-registry>/benchmark-toolbox@sha256:a878e4296bb7c07816eb4437b78f1209c9698e5c3ce891caf53cffe1a7a40e85`
  (harness `1a0f845d7798b84771f85425a6f3dc8dcc6f5e33`), with wave 2's emitted
  image set copied unchanged.

## Results (2026-11-10)

Four Spot jobs, submitted 21:06 UTC with owner approval, all SUCCEEDED by
21:13 UTC with no preemption or retry. Receipt (create and terminal
times):
[`../../../receipts/go-replay-qualification-3-cloud-20261110/`](../../../receipts/go-replay-qualification-3-cloud-20261110/README.md).
Every subject exited zero with the expected 143,008,674 rows, with zero server
errors and refusals, and every receipt reports `list_index.delivered: true` with
no disagreement between the attached server line and the meters (index-on jobs
`state=built`, the control `state=off` with zero builds and skips). Observed
compute is $0.6150 charging create-to-terminal ($0.5076 by `runDuration`),
against the $0.63 estimate and the $3 cap.

Per-shape cells are mean server service time / deadline, then overrun %.

| Job | Replay | Grade | worker_page | pivot_probe | structure_probe | Index skips / dense-page declines | Build s | CPU mean / p90 | Server RSS | Wall s | vs wave-1 Java median | Cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `swath.e40733239507.s1` | 64/20, index on | TIMING_VALID | 4.3/94 ms, 0% | 0.6/46 ms, 0% | 0.6/55 ms, 0% | 10,204 / 61,447 | 2.37 | 7.0% / 12.4% | 1.67 GiB | 148.7 | −4.2% | $0.152 |
| `swath.e40733239507.s2` | 64/20, index on | TIMING_VALID | 4.4/94 ms, 0% | 0.7/46 ms, 0% | 0.5/55 ms, 0% | 11,233 / 61,535 | 2.64 | 6.6% / 12.0% | 1.67 GiB | 152.3 | −2.0% | $0.160 |
| `swath.dc15bf8aa437.s1` | 64/20, index **off** | PRESSURE_DEGRADED | 4.4/94 ms, 0% | 0.7/46 ms, 0% | 7.5/55 ms, 3.55% | 0 / 0 | – | 7.1% / 12.3% | 1.83 GiB | 148.2 | −4.6% | $0.148 |
| `swath.07cb7eabf980.s1` | 16/5, index on | TIMING_VALID | 8.0/94 ms, 0.056% | 1.2/46 ms, 0% | 0.5/55 ms, 0% | 11,150 / 61,520 | 2.86 | 29.4% / 48.0% | 1.72 GiB | 149.3 | −3.9% | $0.154 |

Server RSS is the lifetime VmHWM (includes startup, index build and warm-up).

Window-cache meters at the end of each run (`replay_evidence.after` in the
bound `result.json`). Count and mean are cumulative over the server's life and
include the 400 warm-up fills; p50 / p99 cover roughly the last 40–60 seconds
and max roughly the last 2–3 minutes of the run, not the whole run (see wave
4's README).
Hit share is hits / (hits + misses).

| Attempt | Wave | Server / fixture / replay | fill count | fill mean / p50 / p99 / max ms | hit | miss cold / continuation | Hit share |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `swath.e40733239507.s1` | 3 | `43ffab2f` / SeekParquet / 64/20 on | 56,069 | 10.8 / 7.9 / 22.5 / 45.9 | 100,682 | 7,836 / 42,684 | 66.6% |
| `swath.e40733239507.s2` | 3 | `43ffab2f` / SeekParquet / 64/20 on | 54,814 | 11.2 / 7.9 / 24.6 / 45.2 | 102,621 | 7,950 / 40,718 | 67.8% |
| `swath.dc15bf8aa437.s1` | 3 | `43ffab2f` / SeekParquet / 64/20 off | 56,523 | 10.8 / 7.7 / 24.6 / 52.7 | 100,233 | 8,157 / 42,825 | 66.3% |
| `swath.07cb7eabf980.s1` | 3 | `43ffab2f` / SeekParquet / 16/5 on | 58,856 | 12.9 / 9.4 / 43.0 / 138.0 | 97,986 | 9,618 / 43,674 | 64.8% |
| `swath.954f110b5470.s1` | 2 | `545a10cb` / SeekParquet / 64/20 on | 46,403 | 41.8 / 23.1 / 92.3 / 169.5 | 111,456 | 7,877 / 31,931 | 73.7% |
| `swath.954f110b5470.s2` | 2 | `545a10cb` / SeekParquet / 64/20 on | 46,806 | 42.1 / 26.2 / 100.7 / 175.0 | 110,791 | 8,219 / 32,270 | 73.2% |
| `swath.2076e4dd8761.s1` | 2 | `545a10cb` / SeekParquet / 64/20 off | 45,960 | 40.6 / 23.6 / 96.5 / 190.6 | 112,006 | 8,194 / 31,197 | 74.0% |
| `swath.5d9e6e396e48.s1` | 2 | `545a10cb` / SeekParquet / 16/5 on | 50,346 | 34.8 / 21.5 / 130.0 / 409.3 | 108,108 | 11,438 / 31,579 | 71.5% |
| `swath.98f424ce32cc.s1` | 2 follow-up | `545a10cb` / Java-era / 64/20 off | 55,642 | 11.9 / 6.7 / 27.3 / 70.8 | 101,157 | 8,287 / 41,751 | 66.9% |

**The blockchain index-on cell qualifies at 64/20.** Both repetitions grade
TIMING_VALID with no overrun on any shape, the index delivered, and walls
within −4.2% and −2.0% of the wave-1 Java median (inside ±10%). Worker pages
took 4.3-4.4 ms against 13.8-14.1 ms and 2.8-3.0% overrun in wave 2, and the
server used about half the CPU (7% mean against 14-15%) and 0.7-0.9 GiB less
peak RSS. These levels are those of wave 1 and the Java-era follow-up
(4.7-5.1 ms, 6.4-6.9% CPU).

**Window fills moved to the Java-era cost.** A fill took 10.8-11.2 ms on average
(p99 22.5-24.6 ms) at 64/20, against 40.6-42.1 ms (p99 92-101 ms) on the same
cell with the wave-2 server and 11.9 ms (p99 27.3 ms) on the Java-era parts.
The fill count, hit count and hit share moved with it: about 55-56 thousand
fills and a 66-68% hit share, as on the Java-era parts, against about 46
thousand and 73-74% with the slower wave-2 fills. This wave does not explain
the hit-share change; it is reported, not interpreted.

**The list index is still required for structure probes.** The index-off control,
which differs from the index-on cell only in the index, kept worker pages at
4.4 ms with no overrun but overran 3.55% of structure probes (7.5 ms mean),
as every index-free blockchain run in waves 1 and 2 did (1.9-3.3%). With the
server's read path fixed, the index is what separates TIMING_VALID from
PRESSURE_DEGRADED on this cell.

**The 16/5 rung qualifies with headroom.** It grades TIMING_VALID (worker-page
overrun 0.056%, under the 1% threshold), wall −3.9% against the wave-1 Java
median and −0.8% against the median of this wave's two 64/20 index-on
repetitions (150.5 s), and meets wave 1's headroom conditions: replay cpuset
mean 29.4% (< 60%), p90 48.0% (< 80%), server peak RSS 1.72 GiB of the 5 GiB
container (34%, < 75%), response-buffer peak 128 MiB of the 1 GiB budget
(12.5%, < 75%). That supports a bounded, fit-based recommendation that replay
16 vCPU / 5 GiB serves this cell on SeekParquet fixtures; it is one repetition
on n4-highcpu-80, not a measurement on a smaller VM.

The server build is the only planned difference from wave 2, and it bundles
#443, the seekparquet pin and the Go 1.27.4 toolchain and dependency bumps;
this wave does not attribute the change among them. Comparisons with waves 1
and 2 are cross-day; with the Java-era follow-up cross-fixture as well. These
support bounded diagnostic statements, not precision claims.
