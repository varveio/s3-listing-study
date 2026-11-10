# Go replay qualification, wave 3 (window-cache fills)

Bounded diagnostic qualification of the replay instrument. Varve maintains this
study and builds Swath; Swath here is the Java 0.3.2 listing subject, never
Swath-Go. This is not a calibrated benchmark or a ranking. **Not submitted.**

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
delivers the blockchain treatment at the same allocations, and whether the
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

Four jobs, all on n4-highcpu-80. The image is the placeholder
`swath-replay@sha256:REPLAY_GO_IMAGE_TBD`; `campaign submit` refuses the plans
until it is replaced by the digest of the Go replay image built from Swath main
after PR #443 merges.

**What the new image contains.** It is built from Swath main, so besides #443 it
carries every Swath main commit since wave 2's `545a10cb`. As of Swath
`origin/main` `4121c6fd` (2026-11-10, before #443), those are:

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

Of these, `git diff --stat 545a10cb origin/main -- go/internal/replay` shows only
`go/internal/replay/azure` (3 files, +28/−32, from #434 and #440): the Azure
endpoint, which an S3 subject does not call. The `swath-replay` binary also links
`go/reader` (changed by #433, #438 and #441), but the replay uses it only for
product-name constants (`store/sorted.go`; source reading). The changes that
reach the S3 serving path in the binary are therefore #443 itself
(`go/internal/replay/store/seek.go`; comment-only edits to `legacy/scan.go` and
`metrics/defs.go`, no meter renamed), the seekparquet pin `6acba1a` → `b1c848a`
(#42, #43, #48, #50, #54, plus CI and the Go 1.27.4 toolchain bump #51), and the
build: the Go toolchain moves from 1.26.10 to 1.27.4 (#428, #438) and linked
modules move with it (klauspost/compress 1.20.3 → 1.20.4, andybalholm/brotli
1.2.8 → 1.3.0, parquet-go/bitpack 1.0.0 → 1.1.0, golang.org/x/sync 0.24 → 0.25,
golang.org/x/sys 0.49 → 0.50; from `go list -deps ./cmd/swath-replay` at
`4121c6fd` against the `545a10cb` `go.mod`). The one variable is the server
build; the toolchain and dependency bumps are inside it and this wave does not
separate them from #443. Re-check the list against the commit actually built
before pinning it.

Unchanged: the SeekParquet blockchain fixture (`seekparquet-c4a42eb4e7fb/`,
manifest `c4a42eb4…2b29`, adopted from `f4a839f2…d4b0`), the subject (Swath Java
0.3.2, c256, the same capsule config), the toolbox worker (wave 2's image set can
be reused: no harness source changed since `1a0f845`), machine shape, subject and
replay cpusets and memory, list-index settings, reader-decode ceiling 128, 4,096
request slots, 1 GiB response budget, 192 cache windows of 12,500 rows, latency
deadlines, declared warm-up, and the delivered-treatment grader and its
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
   gcloud storage cat gs://s3-listing-study-results-29c02004/go-replay-qualify/aws-public-blockchain/ATTEMPT_ID/result.json \
     | jq -c '.replay_evidence as $e | ("before","after") as $w
         | $e[$w].metrics.meters[]
         | select(.name | test("^swath\\.replay\\.prefetch\\.window\\.(fill|hit|miss)$"))
         | {at: $w, name, tags, count, mean_ms, p50_ms, p99_ms, max_ms}'
   ```

   Timer quantiles are server-lifetime values (they include the 400 warm-up
   fills recorded in `before`); counts are compared as `after − before`.
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

1. After swath PR #443 merges, build the Go replay image from a clean detached
   worktree of that Swath main commit, push it, and replace
   `REPLAY_GO_IMAGE_TBD` in all three plans with its `@sha256:` digest. In the
   same commit, turn the placeholder drift guard into a pinned-image guard, as
   wave 2 did, and record the image under *Pre-submit record*.
2. Reuse wave 2's toolbox worker and image set (harness `1a0f845`), unless a
   harness source change lands first; then rebuild as wave 2 did.
3. Dry-run each plan, then submit with Spot and the study VPC, as wave 2 did:

   ```sh
   uv run python benchmark/src/benchmark/campaign.py --state "$STATE/campaign.db" submit \
     --suite go-replay-qualify --group goq3-<plan> \
     --plan benchmark/plans/campaigns/go-replay-qualification-3/<plan>/aws-public-blockchain.yaml \
     --project varve-oss --location us-east1 --zone us-east1-b \
     --results-bucket s3-listing-study-results-29c02004 --image-set "$STATE/images.json" \
     --anonymous-worker-sa s3-listing-study-worker@varve-oss.iam.gserviceaccount.com \
     --network projects/varve-oss/global/networks/s3-listing-study \
     --subnetwork projects/varve-oss/regions/us-east1/subnetworks/s3-listing-study-us-east1 \
     --provisioning SPOT --boot-disk-gb 200 --dry-run
   ```

4. After settlement, export each job's `swath_replay list_index` line and attach
   it with `benchmark.receipt --server-log` (`benchmark/docs/running.md`,
   *Export a receipt draft*).

## Budget

Retain failures; only a preempted job may be resubmitted once; poll at least
five minutes apart; stop any job still running at 60 minutes.

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
the planning estimate is about $1.79. Four jobs stopped at 60 minutes would
cost $7.23 of compute; at 90 minutes $10.84; at the provider deadline
(`maxRunDuration` 8,405 s) $16.87. These are estimates, not an invoice.
