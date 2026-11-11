# Go replay qualification, wave 4 (downsizing)

Bounded diagnostic qualification of the replay instrument. Varve maintains this
study and builds Swath; Swath here is the Java 0.3.2 listing subject, never
Swath-Go. This is not a calibrated benchmark or a ranking. **Not submitted.**

## Why a fourth wave

Waves 2 and 3 qualified the Go replay server on SeekParquet fixtures at the
allocations the study's replay plans use today: NARA and FourCast in wave 2
(`../go-replay-qualification-2/`), blockchain Swath c256 in wave 3
(`../go-replay-qualification-3/`), where the 16 vCPU / 5 GiB rung also met
wave 1's headroom conditions (replay cpuset mean 29.4%, p90 48.0%, server RSS
1.72 GiB of 5 GiB). Every downsizing statement so far is a fit-based inference
on the host the cell already ran on (n4-highcpu-80 for blockchain), not a
measurement on a smaller VM.

This wave asks, for each heavy cell, which of two tested replay rungs (16/5 and
8/4) is the smallest that still qualifies, measured on the smallest catalogued
N4 highcpu host the subject and replay cpusets fit. Smaller replay allocations
are not tested. It only measures: changing the study's run specs is a separate
reviewed change.

## What changes, and what does not

Ten plans, eleven jobs, one submission group per plan (`goq4-<plan>`), every
one on wave 3's Go replay image,
`swath-replay@sha256:f8b70f0f1c337271007b6aa8b00bf87f68bdd8d361dbbb7a19401edb5f629f90`
(Swath main `43ffab2f`, PR #443). Each plan is a copy of its counterpart:
blockchain's is wave 3's `blockchain-ladder-go` (same image), the others are
wave 2's plan of the same cell (image `3b358446…`, Swath `545a10cb`).

| Plan | Role | Counterpart | Replay vCPU / GiB | Host | Server cpuset | Subject cpuset | Host remainder | Reps |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `blockchain-swath-go-16-5` | host change | w3 `blockchain-ladder-go` (`swath.07cb7eabf980.s1`) | 16/5 (unchanged) | n4-highcpu-80 → **-32** | `0-7,16-23` | `8-11,24-27` (was `8-11,48-51`) | 8 vCPU / 51 GiB (was 56 / 147) | 2 |
| `blockchain-swath-go-8-4` | downsizing rung | w3 `blockchain-ladder-go` | 16/5 → **8/4** | n4-highcpu-80 → **-32** | `0-3,16-19` | `4-7,20-23` | 16 vCPU / 52 GiB | 1 |
| `nara-swath-go-20-20` | same-image control | w2 `nara-swath-go` (`swath.773bf96d14ef.s1`) | 20/20 (unchanged) | n4-highcpu-32 (unchanged) | `0-9,16-25` | `10-13,26-29` (unchanged) | 4 vCPU / 36 GiB | 1 |
| `nara-swath-go-16-5` | downsizing rung | w2 `nara-swath-go` | 20/20 → **16/5** | n4-highcpu-32 | `0-7,16-23` | `8-11,24-27` | 8 vCPU / 51 GiB | 1 |
| `nara-swath-go-8-4` | downsizing rung | w2 `nara-swath-go` | 20/20 → **8/4** | n4-highcpu-32 | `0-3,16-19` | `4-7,20-23` | 16 vCPU / 52 GiB | 1 |
| `nara-s7cmd-go-20-20` | same-image control | w2 `nara-s7cmd-go` (`s7cmd.8b7670175058.s1`) | 20/20 (unchanged) | n4-highcpu-32 (unchanged) | `0-9,16-25` | `10-13,26-29` (unchanged) | 4 vCPU / 36 GiB | 1 |
| `nara-s7cmd-go-16-5` | downsizing rung | w2 `nara-s7cmd-go` | 20/20 → **16/5** | n4-highcpu-32 | `0-7,16-23` | `8-11,24-27` | 8 vCPU / 51 GiB | 1 |
| `nara-s7cmd-go-8-4` | downsizing rung | w2 `nara-s7cmd-go` | 20/20 → **8/4** | n4-highcpu-32 | `0-3,16-19` | `4-7,20-23` | 16 vCPU / 52 GiB | 1 |
| `fourcast-s5cmd-go-8-8` | same-image control | w2 `fourcast-s5cmd-go` (`s5cmd.c6cfb2bf8dd8.s1`) | 8/8 (unchanged) | n4-highcpu-16 (unchanged) | `0-3,8-11` | `4-6,12-14` (unchanged) | 2 vCPU / 8 GiB | 1 |
| `fourcast-s5cmd-go-8-4` | downsizing rung | w2 `fourcast-s5cmd-go` | 8/8 → **8/4** | n4-highcpu-16 | `0-3,8-11` | `4-6,12-14` | 2 vCPU / 12 GiB | 1 |

Cpusets are as rendered by the dry run; host remainder is the derived
`derived_host_headroom` (box minus replay minus subject, `replay.allocation_summary`).

**Same-image controls.** NARA and FourCast last ran on the wave-2 image, so
each of their cells gets one control at its wave-2 allocation and host, with
only the server image changed to wave 3's. Each downsizing rung then differs
from its cell's control only in the replay allocation (and the host remainder
that follows from it). The controls also answer, on one repetition, whether
wave 3's server changes NARA and FourCast at all.

Unchanged in every plan: the SeekParquet fixtures and their adopted digests,
the subjects (tool, mode, concurrency, capsule config), `subject_vcpus`, the
subject memory ceiling, timeouts, list index on with `max_bytes` 67108864,
reader-decode ceiling (128 blockchain, 64 NARA, 8 FourCast), 4,096 request
slots, 1 GiB response budget, 192 cache windows of 12,500 rows, heap share 75%,
latency deadlines, declared warm-up, and the delivered-treatment grader and its
thresholds. GOMAXPROCS and GOMEMLIMIT follow the allocation, as in every ladder.
The toolbox worker and image set are wave 2's (harness `1a0f845`; no harness
source has changed since).

Tests in `benchmark/tests/test_campaign.py`:

- `test_wave4_plans_differ_from_their_counterparts_only_in_replay_allocation_and_host`
  resolves each wave-4 case and checks it equals its counterpart case once the
  replay vCPUs and memory, host shape and repetitions are set back and the
  counterpart is moved to wave 3's image;
- `test_wave4_controls_differ_from_their_wave2_cells_only_in_the_replay_image`
  checks each control against its wave-2 case and that every wave-2 cell in
  this wave has its control;
- `test_wave4_hosts_are_the_smallest_n4_highcpu_shape_that_fits` checks that every
  smaller N4 highcpu box in `benchmark/plans/instances.yaml` refuses the
  allocation;
- `test_wave4_plans_render_the_downsized_replay_on_the_declared_hosts`
  dry-renders the eleven attempts;
- `test_wave4_readme_stages_submit_every_plan_once` checks the staged submit
  lists below.

### Host shapes, and why

The harness derives the cpusets (`replay.allocation_cpu_sets`): replay takes
whole physical cores from core 0, the subject the next whole cores, both SMT
siblings each, so the two never share a core; the box must leave at least one
vCPU and, with a subject ceiling, 1 GiB outside both. A host fits when
replay plus subject cores are at most half its vCPUs and the remainder is
positive. The catalogue has no N4 highcpu size between 16 and 32.

- **Blockchain → n4-highcpu-32 at both rungs.** Subject 8 + replay 16 = 24 vCPUs
  fit 32 (8 left); 8 + 8 = 16 leaves nothing on n4-highcpu-16. The subject keeps
  four whole cores and its 8 GiB ceiling; only the CPU numbers change, as they
  did between wave 3's rungs.
- **NARA (Swath and s7cmd) → n4-highcpu-32, as in wave 2.** 8 + 16 and 8 + 8
  both need more than n4-highcpu-16 offers, so the host does not change; the
  subject moves from cores 10-13 to 8-11 or 4-7.
- **FourCast → n4-highcpu-16, as in wave 2.** The subject's 16 GiB ceiling plus
  any replay memory exceeds n4-highcpu-8's 16 GiB, so 16 is already the floor.

### What each comparison can and cannot show

- **Blockchain 16/5, n4-highcpu-80 → -32.** The replay allocation is fixed, so
  this tests the combined host-shape change. It does not separate its parts.
  The server and subject share the boot disk, page cache and memory bandwidth
  ([`docs/instrument.md`](../../../../docs/instrument.md), *What the server
  shares with the subject*), and the subject writes a 22.6 GB product to that
  disk in about 150 s (`swath.07cb7eabf980.s1` receipt, `product.size_bytes`).
  On -32 the host keeps 8 vCPUs instead of 56 for the Batch agent, Docker, the
  worker's sampler and unbound kernel work such as writeback; host memory, and
  with it the page cache, is 64 GiB instead of 160; the provider's per-VM
  Hyperdisk and network limits scale with vCPU count (provider documentation,
  not measured here; no disk metric is exported); and whether n4-highcpu-80
  spans more than one NUMA node is not recorded in any committed run. The
  comparison is also cross-day and cross-VM, two new repetitions against one.
- **16/5 → 8/4 within a cell** (blockchain, NARA Swath, NARA s7cmd). Host and
  image are fixed, but CPU and memory change together, with GOMAXPROCS,
  GOMEMLIMIT, the derived cpuset placement and the host remainder. No separate
  CPU or memory effect is inferred.
- **FourCast 8/8 → 8/4.** Memory (and GOMEMLIMIT) alone, against the
  same-image control on the same host and cpusets.

### Deviation from the requested matrix

FourCast has no 16/5 rung. Its wave-2 allocation is already replay 8 vCPU /
8 GiB (CPU mean 0.5%, p90 0.6%, RSS 0.98 GiB); 16 vCPU would be an upsizing and
would need an n4-highcpu-32 host (6 + 16 = 22 vCPUs).

## Predeclared reading

Before results are inspected, wave 2's per-repetition reading applies, with the
error condition stated precisely: a repetition **qualifies** if it completes
with the fixture's row count (blockchain 143,008,674), zero HTTP errors
(`http_errors`), zero admission and serving refusals, grades TIMING_VALID, and
its receipt's `list_index` block reports `delivered: true` (the `state=built`
server line attached with `receipt --server-log` and agreeing with the meters).
Parquet-reader errors (`headroom.parquet_errors_delta`) are not a gate and are
reported for every attempt: the counterparts recorded 2 (`swath.07cb7eabf980.s1`)
and 0 (NARA, FourCast); wave 2's 16/5 blockchain rung recorded 55. A rung
qualifies when every repetition does.

Each rung's outcome is one of:

- **failed timing gate**: graded PRESSURE_DEGRADED or CAPACITY_FAILED, or a
  completed run that misses another qualifying condition;
- **not evaluable**: INSUFFICIENT_EVIDENCE (fewer than the grader's five
  10-second resource samples; wave 2's NARA Swath run had six; the workload is
  not stretched and the gate is not relaxed), a preemption without a qualifying
  resubmission, a job stopped by the 20-minute or spend rule, or binding or
  collection errors;
- **TIMING_VALID**: qualifies as above;
- **meets recommendation headroom**: qualifies and also meets wave 1's
  conditions: replay cpuset mean utilization below 60%, p90 below 80%, server
  lifetime peak RSS below 75% of the replay container ceiling (3.75 GiB at
  5 GiB, 3.0 GiB at 4 GiB), response-buffer peak below 75% of the 1 GiB budget,
  and subject wall within ±10% of **both** of the cell's references:

| Cell | Reference walls (±10% of each) |
| --- | --- |
| blockchain Swath c256 | wave-1 Java median 155.3 s and wave 3's Go 64/20 index-on median 150.5 s, as wave 3 required; also reported against wave 3's 16/5 rung (149.3 s, n4-highcpu-80) |
| NARA Swath c128 | wave-1 Java median 64.5 s and this wave's control `nara-swath-go-20-20`; also reported against wave 2's Go run (64.5 s, cross-image) |
| NARA s7cmd c64 | wave-1 Java median 602.2 s and `nara-s7cmd-go-20-20`; also wave 2's Go run (603.3 s) |
| FourCast s5cmd | wave-1 Java median 420.6 s and `fourcast-s5cmd-go-8-8`; also wave 2's Go run (445.1 s) |

Per cell, the result is the **smallest qualifying tested rung on the smallest
fitting catalogued N4 highcpu host**. A downsizing recommendation for a rung
requires that rung to meet recommendation headroom and every larger tested
rung of the cell to qualify. So:

- if 8/4 qualifies and 16/5 has a failed timing gate, the result is reported as
  non-monotonic and the cell gets no recommendation;
- if 16/5 is not evaluable, the cell gets no recommendation from this wave, and
  the result is not called non-monotonic;
- if the 16/5 blockchain pair does not qualify, the statement is "16/5 did not
  qualify on the measured n4-highcpu-32 runs; cause unresolved", and blockchain
  keeps wave 3's fit-based statement on n4-highcpu-80. The per-shape overruns
  of both hosts are reported side by side either way.

**Controls.** Each control is expected to qualify, as its cell did in wave 2,
and to fall within ±10% of the wave-1 Java median. If a control does not
qualify (failed timing gate or not evaluable), attribution for that cell is
unresolved: its downsizing rungs are still graded and reported, even if one
qualifies independently, but the cell gets no control comparison and no
recommendation from this wave.

Single repetitions support bounded diagnostic statements, not precision claims;
comparisons with waves 1-3 are cross-day.

## Evidence to extract after the run

For every attempt, the columns of wave 3's Results table (grade, per-shape
service time / deadline and overrun, index skips and declines, build seconds,
replay CPU mean/p90, server RSS against the replay ceiling, response-buffer
peak against the budget, wall, cost), the HTTP error, refusal and
parquet-reader error counts, plus:

1. **Per-shape overruns.** `uv run python benchmark/src/benchmark/report.py
   --state "$STATE/campaign.db" --group goq4-<plan>` prints each shape's mean
   against its deadline and its overrun fraction (`replay_timing_assessment`).
   Compare `worker_page`, `pivot_probe` and `structure_probe` with the cell's
   control and the counterpart baselines below.
2. **Window-cache meters.** From each attempt's bound `result.json`, the
   `before` and `after` values of `swath.replay.prefetch.window.fill` (timer:
   count, mean, p50, p99, max), `swath.replay.prefetch.window.hit` and
   `swath.replay.prefetch.window.miss` (counters, by `reason`); `BUCKET` is
   `aws-public-blockchain`, `nara-1950-census` or `noaa-nws-fourcastnetgfs-pds`:

   ```sh
   gcloud storage cat gs://s3-listing-study-results-29c02004/go-replay-qualify/BUCKET/ATTEMPT_ID/result.json \
     | jq -c '.replay_evidence as $e | ("before","after") as $w
         | $e[$w].metrics.meters[]
         | select(.name | test("^swath\\.replay\\.prefetch\\.window\\.(fill|hit|miss)$"))
         | {at: $w, name, tags, count, mean_ms, p50_ms, p99_ms, max_ms}'
   ```

   Timer quantiles are server-lifetime values (they include the warm-up fills
   recorded in `before`); counts are compared as `after − before`. Hit share is
   hits / (hits + misses).
3. **Host-side samples**, for the host-shape change: from the same file,
   `.replay_evidence.resource_samples[]` gives `subject_cpuset_utilization`,
   `host_mem_available_kb` and `host_load1` per 10-second sample. Report subject
   cpuset mean/max, minimum available host memory and maximum one-minute load
   for every attempt, and compare the blockchain 16/5 pair with
   `swath.07cb7eabf980.s1`.
4. **The `list_index` server lines**, exported from Cloud Logging and attached
   with `benchmark.receipt --server-log`, as in waves 2 and 3.

Counterpart baselines (bound receipts' `metrics.jsonl`; window-cache figures
are the grader's `after − before` deltas, `headroom.cache_fills_delta` and
`cache_hits_delta`):

| Attempt | Wave / image | Host | Replay | worker_page | pivot_probe | structure_probe | CPU mean / p90 | Server RSS | Resp. peak | Parquet errors | Fills / hits | Wall s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| blockchain `swath.07cb7eabf980.s1` | 3 / `43ffab2f` | n4-highcpu-80 | 16/5 | 8.0/94 ms, 0.056% | 1.2/46 ms, 0% | 0.5/55 ms, 0% | 29.4% / 48.0% | 1.72 GiB | 128 MiB | 2 | 58,456 / 97,986 | 149.3 |
| NARA Swath `swath.773bf96d14ef.s1` | 2 / `545a10cb` | n4-highcpu-32 | 20/20 | 0.8/86 ms, 0% | 0.7/44 ms, 0% | 1.3/50 ms, 0% | 4.1% / 9.3% | 1.04 GiB | 62 MiB | 0 | 3,163 / 13,094 | 64.5 |
| NARA s7cmd `s7cmd.8b7670175058.s1` | 2 / `545a10cb` | n4-highcpu-32 | 20/20 | 1.3/86 ms, 0% | – | 3.3/50 ms, 0% | 0.3% / 0.2% | 1.13 GiB | 73 MiB | 0 | 1,400 / 13,355 | 603.3 |
| FourCast s5cmd `s5cmd.c6cfb2bf8dd8.s1` | 2 / `545a10cb` | n4-highcpu-16 | 8/8 | 0.2/85 ms, 0% | – | – | 0.5% / 0.6% | 0.98 GiB | 0.5 MiB | 0 | 316 / 4,080 | 445.1 |

Wave 3's README holds the lifetime fill timer (mean / p50 / p99 / max) for
`swath.07cb7eabf980.s1`: 12.9 / 9.4 / 43.0 / 138.0 ms over 58,856 fills.

## Before submitting

1. No image is built. The Go replay image is wave 3's, pinned in all ten plans.
   The toolbox worker is wave 2's
   (`benchmark-toolbox@sha256:a878e4296bb7c07816eb4437b78f1209c9698e5c3ce891caf53cffe1a7a40e85`,
   harness `1a0f845d7798b84771f85425a6f3dc8dcc6f5e33`); wave 3's
   `images.json` is copied unchanged (SHA-256 `e4d43a73…b30d`) into the new
   state directory `~/s3-listing-study-state/2026-11-11-go-replay-qualification-4/`.
2. Dry-run every plan (done; outputs `dryrun-<plan>.txt` in the state
   directory): eleven attempts, zero slots. Add `--dry-run` to `submit_stage`'s
   command to repeat it.
3. Submit in two stages (below), with the spend checks running from the first
   submission until every job is terminal.
4. After settlement, export each job's `swath_replay list_index` line and attach
   it with `benchmark.receipt --server-log` (`benchmark/docs/running.md`,
   *Export a receipt draft*).

## Budget

The owner approved about $1 of compute, with a hard cap of **$3 in total**.
Wave 2's $1 non-compute reserve stays inside that cap, so compute may not
exceed **$2.00**. Retain failures; only a preempted job may be resubmitted, at
most once.

Pre-submit estimate: each job is priced at the observed create-to-terminal
duration of its counterpart, at the us-east1 N4 Spot rates the wave-1 receipt
records ($0.722752/hour for n4-highcpu-32, $0.361376/hour for n4-highcpu-16).
The 20-minute ceiling is the cost of a job stopped at 20 minutes.

| Stage | Plan | Jobs | Host | Counterpart duration | Estimate | 20-min ceiling |
| --- | --- | --- | --- | --- | --- | --- |
| A | nara-swath-go-20-20 | 1 | n4-highcpu-32 | 189.4 s (`swath.773bf96d14ef.s1`) | $0.0380 | $0.2409 |
| A | nara-s7cmd-go-20-20 | 1 | n4-highcpu-32 | 753.5 s (`s7cmd.8b7670175058.s1`) | $0.1513 | $0.2409 |
| A | fourcast-s5cmd-go-8-8 | 1 | n4-highcpu-16 | 576.0 s (`s5cmd.c6cfb2bf8dd8.s1`) | $0.0578 | $0.1205 |
| A | blockchain-swath-go-16-5 | 2 | n4-highcpu-32 | 307.4 s (`swath.07cb7eabf980.s1`) | $0.1234 | $0.4818 |
| **A** | | **5** | | | **$0.3705** | **$1.0841** |
| B | blockchain-swath-go-8-4 | 1 | n4-highcpu-32 | 307.4 s (same) | $0.0617 | $0.2409 |
| B | nara-swath-go-16-5 | 1 | n4-highcpu-32 | 189.4 s (same) | $0.0380 | $0.2409 |
| B | nara-swath-go-8-4 | 1 | n4-highcpu-32 | 189.4 s (same) | $0.0380 | $0.2409 |
| B | nara-s7cmd-go-16-5 | 1 | n4-highcpu-32 | 753.5 s (same) | $0.1513 | $0.2409 |
| B | nara-s7cmd-go-8-4 | 1 | n4-highcpu-32 | 753.5 s (same) | $0.1513 | $0.2409 |
| B | fourcast-s5cmd-go-8-4 | 1 | n4-highcpu-16 | 576.0 s (same) | $0.0578 | $0.1205 |
| **B** | | **6** | | | **$0.4982** | **$1.3250** |
| **Total** | | **11** | | | **$0.87** | **$2.41** |

By `runDuration` instead of create-to-terminal the eleven come to about $0.75.
The estimate does not model fixture staging on a smaller host (the blockchain
counterpart staged on n4-highcpu-80) or a slower subject at a smaller replay.
With one resubmission of the largest job ($0.15) and the $1 reserve, the
planning figure is about $2.02. Eleven jobs at the 20-minute ceiling would be
$2.41 of compute, over the $2.00 compute limit, so the rules below, not the
estimate, enforce the cap. The rendered `maxRunDuration` (8,405 s blockchain,
3,005 s others) does not.

**Rules.**

- **Clock.** A job's age is measured from its Batch `createTime` (the start of
  the create-to-terminal span the estimate charges), to its `updateTime` once
  terminal or to now while it runs.
- **Check cadence.** From the first stage-A submission until every job is
  terminal, run the check below every 5 minutes: not more often (the polling
  rule), not less (the stop rules).
- **20-minute stop.** A job that is not terminal at 20 minutes of age is
  cancelled at that check (`cancel --group goq4-<plan>`; it cancels every
  non-terminal job in the group, both blockchain 16/5 repetitions included) and
  retained as a failure, not resubmitted. With 5-minute checks a job may run
  up to 25 minutes ($0.3012 on -32, $0.1506 on -16).
- **Aggregate spend stop.** When accrued compute reaches **$1.50**, cancel every
  non-terminal wave-4 group and submit nothing further. Between two checks at
  most six jobs run (stage B), adding at most $0.33; allowing about two minutes
  for a cancellation to take effect on six jobs ($0.13, an allowance: `cancel`
  is `VERIFIED: no` in `running.md`), the worst case is about $1.96 of compute,
  inside $2.00, and $2.96 with the reserve, inside $3.
- **Resubmissions, budgeted one at a time.** A preempted job may be resubmitted
  once, through `retry --group goq4-<plan>`, only if accrued compute plus that
  job's 25-minute ceiling stays at or below $1.50 at the time (accrued at most
  $1.1988 for a -32 job, $1.3494 for a -16 job). `retry` acts on each case's
  latest ordinal (`running.md`, *retry*); a group whose job failed for any other
  reason is not retried.
- **Stage gate.** Stage B is submitted only when every stage-A job, including
  any resubmission, is terminal and stage-A accrued compute is at most
  **$0.60**. Otherwise stage B is not submitted without new owner approval.
  Stage B's own 20-minute ceiling ($1.33) is bounded by the aggregate stop, not
  by the gate.

These are estimates, not an invoice.

### Commands

Setup and the spend check (writes `accrue.py` into the state directory; it reads
a `gcloud batch jobs list` export and prints each wave-4 job's age and accrued
compute, flagging the stop rules):

```sh
STATE=~/s3-listing-study-state/2026-11-11-go-replay-qualification-4
W4=benchmark/plans/campaigns/go-replay-qualification-4
cat > "$STATE/accrue.py" <<'EOF'
import datetime as dt, json, os, sys
# Wave-4 spend check. Usage: accrue.py JOBS_JSON START_ISO
# Keeps every wave-4 job it has ever seen in costs.json beside this script, so a
# job that cancellation deleted from Batch still counts. Only SUCCEEDED, FAILED
# and CANCELLED settle a job's cost; anything else (including
# DELETION_IN_PROGRESS, or a job no longer listed) keeps accruing to now, capped
# at its 25-minute ceiling -- conservative by construction.
RATE = {"n4-highcpu-32": 0.722752, "n4-highcpu-16": 0.361376}  # USD/hour, N4 Spot us-east1
SETTLED = {"SUCCEEDED", "FAILED", "CANCELLED"}
CEILING_MIN = 25.0
LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "costs.json")


def when(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


jobs, start = json.load(open(sys.argv[1])), when(sys.argv[2])
now = dt.datetime.now(dt.timezone.utc)
seen = json.load(open(LEDGER)) if os.path.exists(LEDGER) else {}

listed = set()
for job in jobs:
    created = when(job["createTime"])
    if created < start:
        continue
    name = job["name"].rsplit("/", 1)[-1]
    listed.add(name)
    state = job.get("status", {}).get("state", "?")
    machine = job["allocationPolicy"]["instances"][0]["policy"]["machineType"]
    rec = seen.get(name, {"created": job["createTime"], "machine": machine})
    rec["state"] = state
    if state in SETTLED:
        rec["end"] = job["updateTime"]
    seen[name] = rec

total = 0.0
for name, rec in sorted(seen.items(), key=lambda kv: kv[1]["created"]):
    created = when(rec["created"])
    settled = rec.get("state") in SETTLED and "end" in rec
    end = when(rec["end"]) if settled else now
    minutes = (end - created).total_seconds() / 60
    if not settled and name not in listed:
        minutes = min(minutes, CEILING_MIN)
    usd = minutes / 60 * RATE[rec["machine"]]
    if not settled:  # an unsettled charge never goes down between checks
        usd = max(usd, rec.get("max_usd", 0.0))
    rec["max_usd"] = usd
    total += usd
    note = "" if name in listed else " (no longer listed)"
    flag = "  STOP: past 20 min" if not settled and minutes >= 20 else ""
    print(f"{name} {rec.get('state')}{note} {rec['machine']} {minutes:.1f} min ${usd:.4f}{flag}")

json.dump(seen, open(LEDGER + ".tmp", "w"), indent=1)
os.replace(LEDGER + ".tmp", LEDGER)
print(f"accrued compute ${total:.4f}" + ("  STOP ALL: at or past $1.50" if total >= 1.50 else ""))
EOF
check() {
  uv run python benchmark/src/benchmark/campaign.py --state "$STATE/campaign.db" poll
  uv run python benchmark/src/benchmark/campaign.py --state "$STATE/campaign.db" status | grep goq4-
  gcloud batch jobs list --project varve-oss --location us-east1 \
    --filter='labels.suite=go-replay-qualify' --format=json > "$STATE/jobs-now.json"
  python3 -I "$STATE/accrue.py" "$STATE/jobs-now.json" "$(cat "$STATE/wave4-start.txt")"
}
submit_stage() {
  for plan in "$@"; do
    uv run python benchmark/src/benchmark/campaign.py --state "$STATE/campaign.db" submit \
      --suite go-replay-qualify --group "goq4-$plan" \
      --plan "$W4/$plan"/*.yaml \
      --project varve-oss --location us-east1 --zone us-east1-b \
      --results-bucket s3-listing-study-results-29c02004 --image-set "$STATE/images.json" \
      --anonymous-worker-sa s3-listing-study-worker@varve-oss.iam.gserviceaccount.com \
      --network projects/varve-oss/global/networks/s3-listing-study \
      --subnetwork projects/varve-oss/regions/us-east1/subnetworks/s3-listing-study-us-east1 \
      --provisioning SPOT --boot-disk-gb 200 || return 1
  done
}
```

The stages:

```sh
STAGE_A=(nara-swath-go-20-20 nara-s7cmd-go-20-20 fourcast-s5cmd-go-8-8 blockchain-swath-go-16-5)
STAGE_B=(blockchain-swath-go-8-4 nara-swath-go-16-5 nara-swath-go-8-4 nara-s7cmd-go-16-5 nara-s7cmd-go-8-4 fourcast-s5cmd-go-8-4)

# Stage A; then run `check` every 5 minutes.
date -u +%Y-%m-%dT%H:%M:%SZ > "$STATE/wave4-start.txt"
submit_stage "${STAGE_A[@]}"

# Stage B, only once the stage gate holds; keep running `check` every 5 minutes.
submit_stage "${STAGE_B[@]}"
```

Stop and resubmit, by group:

```sh
uv run python benchmark/src/benchmark/campaign.py --state "$STATE/campaign.db" cancel --group goq4-PLAN

uv run python benchmark/src/benchmark/campaign.py --state "$STATE/campaign.db" retry \
  --group goq4-PLAN \
  --project varve-oss --location us-east1 --zone us-east1-b \
  --results-bucket s3-listing-study-results-29c02004 --image-set "$STATE/images.json" \
  --anonymous-worker-sa s3-listing-study-worker@varve-oss.iam.gserviceaccount.com \
  --network projects/varve-oss/global/networks/s3-listing-study \
  --subnetwork projects/varve-oss/regions/us-east1/subnetworks/s3-listing-study-us-east1 \
  --provisioning SPOT --boot-disk-gb 200
```

## Pre-submit record

Dry-run attempts (case IDs are minted from the frozen image set and change if
it changes; job names are `go-replay-qualify-<tool>-<case>-s<n>`):

| Stage | Plan | Attempts | Machine | `maxRunDuration` |
| --- | --- | --- | --- | --- |
| A | nara-swath-go-20-20 | `swath.ca43fae5e517.s1` | n4-highcpu-32 | 3005s |
| A | nara-s7cmd-go-20-20 | `s7cmd.f93baa7f7142.s1` | n4-highcpu-32 | 3005s |
| A | fourcast-s5cmd-go-8-8 | `s5cmd.55ee5e4c62b8.s1` | n4-highcpu-16 | 3005s |
| A | blockchain-swath-go-16-5 | `swath.b9c1ed5ae8a2.s1`, `.s2` | n4-highcpu-32 | 8405s |
| B | blockchain-swath-go-8-4 | `swath.c1505ec66d66.s1` | n4-highcpu-32 | 8405s |
| B | nara-swath-go-16-5 | `swath.73401a67238f.s1` | n4-highcpu-32 | 3005s |
| B | nara-swath-go-8-4 | `swath.bc04b205a545.s1` | n4-highcpu-32 | 3005s |
| B | nara-s7cmd-go-16-5 | `s7cmd.3f553acb0aca.s1` | n4-highcpu-32 | 3005s |
| B | nara-s7cmd-go-8-4 | `s7cmd.5c9e1f4d4685.s1` | n4-highcpu-32 | 3005s |
| B | fourcast-s5cmd-go-8-4 | `s5cmd.37412f5e21c0.s1` | n4-highcpu-16 | 3005s |
