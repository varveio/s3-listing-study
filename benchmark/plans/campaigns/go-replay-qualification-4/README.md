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

This wave asks, for each heavy cell, what the smallest replay allocation and
host shape are at which the cell still grades TIMING_VALID, measured on the
smallest N4 highcpu host the subject and replay cpusets fit. It only measures:
changing the study's run specs is a separate reviewed change.

## What changes, and what does not

Seven plans, eight jobs. Each plan is a copy of its counterpart with only the
replay allocation (`replay_vcpus`, `replay_memory_gb`) and the host
(`vcpus`/`memory_gb`, which resolve the machine type) changed, plus the
repetitions; blockchain's counterpart is wave 3's `blockchain-ladder-go`, the
others wave 2's plan of the same cell.

| Plan | Counterpart | Replay vCPU / GiB | Host | Server cpuset | Subject cpuset | Host remainder | Reps |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `blockchain-swath-go-16-5` | w3 `blockchain-ladder-go` (`swath.07cb7eabf980.s1`) | 16/5 (unchanged) | n4-highcpu-80 → **-32** | `0-7,16-23` | `8-11,24-27` (was `8-11,48-51`) | 8 vCPU / 51 GiB (was 56 / 147) | 2 |
| `blockchain-swath-go-8-4` | w3 `blockchain-ladder-go` | 16/5 → **8/4** | n4-highcpu-80 → **-32** | `0-3,16-19` | `4-7,20-23` | 16 vCPU / 52 GiB | 1 |
| `nara-swath-go-16-5` | w2 `nara-swath-go` (`swath.773bf96d14ef.s1`) | 20/20 → **16/5** | n4-highcpu-32 (unchanged) | `0-7,16-23` | `8-11,24-27` (was `10-13,26-29`) | 8 vCPU / 51 GiB (was 4 / 36) | 1 |
| `nara-swath-go-8-4` | w2 `nara-swath-go` | 20/20 → **8/4** | n4-highcpu-32 | `0-3,16-19` | `4-7,20-23` | 16 vCPU / 52 GiB | 1 |
| `nara-s7cmd-go-16-5` | w2 `nara-s7cmd-go` (`s7cmd.8b7670175058.s1`) | 20/20 → **16/5** | n4-highcpu-32 | `0-7,16-23` | `8-11,24-27` (was `10-13,26-29`) | 8 vCPU / 51 GiB | 1 |
| `nara-s7cmd-go-8-4` | w2 `nara-s7cmd-go` | 20/20 → **8/4** | n4-highcpu-32 | `0-3,16-19` | `4-7,20-23` | 16 vCPU / 52 GiB | 1 |
| `fourcast-s5cmd-go-8-4` | w2 `fourcast-s5cmd-go` (`s5cmd.c6cfb2bf8dd8.s1`) | 8/8 → **8/4** | n4-highcpu-16 (unchanged) | `0-3,8-11` | `4-6,12-14` (unchanged) | 2 vCPU / 12 GiB (was 2 / 8) | 1 |

Cpusets are as rendered by the dry run; host remainder is the derived
`derived_host_headroom` (box minus replay minus subject, `replay.allocation_summary`).

**The NARA and FourCast cells also change server image.** They last ran in
wave 2, on the `545a10cb` image (`3b358446…`); this wave runs every cell on
wave 3's image, `swath-replay@sha256:f8b70f0f…9f90` (Swath main `43ffab2f`,
PR #443), so for those five jobs the image is a second difference from their
counterpart. There is no same-image run of NARA or FourCast at the wave-2
allocation. The TIMING_VALID gate is absolute (deadlines, overrun threshold), so
the per-cell verdict does not depend on a baseline; wall-time and headroom
comparisons with wave 2 are cross-image and cross-day and are reported as such.
A same-image control of the three cells at their wave-2 allocations (three jobs,
about $0.25 at the same pricing as below) would remove that difference; it is
not part of this wave.

Unchanged in every plan: the SeekParquet fixtures and their adopted digests,
the subjects (tool, mode, concurrency, capsule config), `subject_vcpus`, the
subject memory ceiling, timeouts, list index on with `max_bytes` 67108864,
reader-decode ceiling (128 blockchain, 64 NARA, 8 FourCast), 4,096 request
slots, 1 GiB response budget, 192 cache windows of 12,500 rows, heap share 75%,
latency deadlines, declared warm-up, and the delivered-treatment grader and its
thresholds. GOMAXPROCS and GOMEMLIMIT follow the allocation, as in every ladder.
The toolbox worker and image set are wave 2's (harness `1a0f845`; no harness
source has changed since).

Three tests in `benchmark/tests/test_campaign.py` check this:
`test_wave4_plans_differ_from_their_counterparts_only_in_replay_allocation_and_host`
resolves each wave-4 case and checks it equals its counterpart case once the
replay vCPUs and memory, host shape and repetitions are set back and the
counterpart is moved to wave 3's image;
`test_wave4_hosts_are_the_smallest_n4_highcpu_shape_that_fits` checks that every
smaller N4 highcpu box in `benchmark/plans/instances.yaml` refuses the
allocation. `test_wave4_plans_render_the_downsized_replay_on_the_declared_hosts`
dry-renders the eight attempts.

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

**Disclosed confounds of the blockchain host change.** Moving blockchain from
n4-highcpu-80 to -32 changes more than cpuset placement: the server and subject
share the boot disk, page cache and memory bandwidth
([`docs/instrument.md`](../../../../docs/instrument.md), *What the server shares
with the subject*), and the subject writes a 22.6 GB product to that disk in
about 150 s (`swath.07cb7eabf980.s1` receipt, `product.size_bytes`). On -32 the
host keeps 8 or 16 vCPUs instead of 56 for the Batch agent, Docker, the worker's
sampler and unbound kernel work such as writeback; host memory, and with it the
page cache, is 64 GiB instead of 160; and the provider's per-VM Hyperdisk and
network limits scale with vCPU count (provider documentation, not measured
here; no disk metric is exported). Whether n4-highcpu-80 spans more than one
NUMA node is not recorded in any committed run. The 16/5 pair isolates this:
it differs from wave 3's 16/5 rung only in the host, so a change between them
is attributed to the host shape (cross-day; two repetitions against one). The
NARA and FourCast hosts do not change, so their rungs carry no host confound
beyond the larger host remainder the smaller replay leaves.

### Deviation from the requested matrix

FourCast has no 16/5 rung. Its wave-2 allocation is already replay 8 vCPU /
8 GiB (CPU mean 0.5%, p90 0.6%, RSS 0.98 GiB); 16 vCPU would be an upsizing and
would need an n4-highcpu-32 host (6 + 16 = 22 vCPUs). Its 8/4 rung changes only
replay memory, on the same host and cpusets.

## Predeclared reading

Before results are inspected, wave 2's per-repetition reading applies
unchanged: a repetition qualifies if it completes with the fixture's row count
(blockchain 143,008,674), zero server errors and refusals, grades
TIMING_VALID, and its receipt's `list_index` block reports `delivered: true`
(the `state=built` server line attached with `receipt --server-log` and
agreeing with the meters). A rung qualifies when every repetition does. Runs
too short for the grader's five 10-second resource samples keep their
INSUFFICIENT_EVIDENCE grade (wave 2's NARA Swath run had six); the workload is
not stretched and the gate is not relaxed.

Per cell, the **smallest TIMING_VALID allocation** is the smallest qualifying
rung, read from 16/5 down to 8/4. If 8/4 qualifies and 16/5 does not, the cell
gets no downsizing recommendation from this wave; the result is reported as
non-monotonic, not as support for 8/4.

A qualifying rung supports a downsizing recommendation only if it also meets
wave 1's headroom conditions: replay cpuset mean utilization below 60%, p90
below 80%, server lifetime peak RSS below 75% of the replay container ceiling
(3.75 GiB at 5 GiB, 3.0 GiB at 4 GiB), response-buffer peak below 75% of the
1 GiB budget, and subject wall within ±10% of the cell's reference:

| Cell | Reference wall (±10%) | Same-image? |
| --- | --- | --- |
| blockchain Swath c256 | 150.5 s, median of wave 3's two 64/20 index-on repetitions; also reported against the wave-1 Java median 155.3 s and wave 3's 16/5 rung (149.3 s) | yes |
| NARA Swath c128 | 64.5 s, wave-1 Java median (wave 2's Go run 64.5 s) | no (wave 2 image) |
| NARA s7cmd c64 | 602.2 s, wave-1 Java median (wave 2's Go run 603.3 s) | no |
| FourCast s5cmd | 420.6 s, wave-1 Java median (wave 2's Go run 445.1 s) | no |

For blockchain the recommendation is stated for the measured host: a rung that
qualifies on n4-highcpu-32 supports "replay R on n4-highcpu-32 with subject
8/8". If the 16/5 pair does not qualify on -32 while wave 3's 16/5 did on -80,
the reading is that the host shape, not the allocation, failed, and the
blockchain recommendation remains wave 3's fit-based one on n4-highcpu-80.
Report the per-shape overruns of both hosts side by side either way.

Single repetitions support bounded diagnostic statements, not precision claims;
comparisons with waves 1-3 are cross-day.

## Evidence to extract after the run

For every attempt, the columns of wave 3's Results table (grade, per-shape
service time / deadline and overrun, index skips and declines, build seconds,
replay CPU mean/p90, server RSS against the replay ceiling, response-buffer
peak against the budget, wall, cost), plus:

1. **Per-shape overruns.** `uv run python benchmark/src/benchmark/report.py
   --state "$STATE/campaign.db" --group goq4-<plan>` prints each shape's mean
   against its deadline and its overrun fraction (`replay_timing_assessment`).
   Compare `worker_page`, `pivot_probe` and `structure_probe` with the
   counterpart baselines below.
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
3. **Host-side samples**, for the host-shape confound: from the same file,
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

| Attempt | Wave / image | Host | Replay | worker_page | pivot_probe | structure_probe | CPU mean / p90 | Server RSS | Resp. peak | Fills / hits | Wall s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| blockchain `swath.07cb7eabf980.s1` | 3 / `43ffab2f` | n4-highcpu-80 | 16/5 | 8.0/94 ms, 0.056% | 1.2/46 ms, 0% | 0.5/55 ms, 0% | 29.4% / 48.0% | 1.72 GiB | 128 MiB | 58,456 / 97,986 | 149.3 |
| NARA Swath `swath.773bf96d14ef.s1` | 2 / `545a10cb` | n4-highcpu-32 | 20/20 | 0.8/86 ms, 0% | 0.7/44 ms, 0% | 1.3/50 ms, 0% | 4.1% / 9.3% | 1.04 GiB | 62 MiB | 3,163 / 13,094 | 64.5 |
| NARA s7cmd `s7cmd.8b7670175058.s1` | 2 / `545a10cb` | n4-highcpu-32 | 20/20 | 1.3/86 ms, 0% | – | 3.3/50 ms, 0% | 0.3% / 0.2% | 1.13 GiB | 73 MiB | 1,400 / 13,355 | 603.3 |
| FourCast s5cmd `s5cmd.c6cfb2bf8dd8.s1` | 2 / `545a10cb` | n4-highcpu-16 | 8/8 | 0.2/85 ms, 0% | – | – | 0.5% / 0.6% | 0.98 GiB | 0.5 MiB | 316 / 4,080 | 445.1 |

Wave 3's README holds the lifetime fill timer (mean / p50 / p99 / max) for
`swath.07cb7eabf980.s1`: 12.9 / 9.4 / 43.0 / 138.0 ms over 58,856 fills.

## Before submitting

1. No image is built. The Go replay image is wave 3's, already pinned in all
   seven plans:
   `us-east1-docker.pkg.dev/varve-oss/s3-listing-study/swath-replay@sha256:f8b70f0f1c337271007b6aa8b00bf87f68bdd8d361dbbb7a19401edb5f629f90`.
   The toolbox worker is wave 2's
   (`benchmark-toolbox@sha256:a878e4296bb7c07816eb4437b78f1209c9698e5c3ce891caf53cffe1a7a40e85`,
   harness `1a0f845d7798b84771f85425a6f3dc8dcc6f5e33`); wave 3's
   `images.json` is copied unchanged (SHA-256 `e4d43a73…b30d`) into the new
   state directory `~/s3-listing-study-state/2026-11-11-go-replay-qualification-4/`.
2. Dry-run every plan (done; outputs `dryrun-<plan>.txt` in the state
   directory): eight attempts, zero slots.
3. Submit with Spot and the study VPC, as waves 2 and 3 did, one group per plan:

   ```sh
   STATE=~/s3-listing-study-state/2026-11-11-go-replay-qualification-4
   W4=benchmark/plans/campaigns/go-replay-qualification-4
   for plan in blockchain-swath-go-16-5 blockchain-swath-go-8-4 \
       nara-swath-go-16-5 nara-swath-go-8-4 nara-s7cmd-go-16-5 nara-s7cmd-go-8-4 \
       fourcast-s5cmd-go-8-4; do
     uv run python benchmark/src/benchmark/campaign.py --state "$STATE/campaign.db" submit \
       --suite go-replay-qualify --group "goq4-$plan" \
       --plan "$W4/$plan"/*.yaml \
       --project varve-oss --location us-east1 --zone us-east1-b \
       --results-bucket s3-listing-study-results-29c02004 --image-set "$STATE/images.json" \
       --anonymous-worker-sa s3-listing-study-worker@varve-oss.iam.gserviceaccount.com \
       --network projects/varve-oss/global/networks/s3-listing-study \
       --subnetwork projects/varve-oss/regions/us-east1/subnetworks/s3-listing-study-us-east1 \
       --provisioning SPOT --boot-disk-gb 200
   done
   ```

   Add `--dry-run` to repeat the inspection pass.
4. After settlement, export each job's `swath_replay list_index` line and attach
   it with `benchmark.receipt --server-log` (`benchmark/docs/running.md`,
   *Export a receipt draft*).

## Budget

Retain failures; only a preempted job may be resubmitted once; poll at least
five minutes apart. The owner approved about $1 for this wave, with a hard cap
of **$3**. Any job still running at 20 minutes is stopped and retained as a
failure.

Pre-submit estimate: each job is priced at the observed create-to-terminal
duration of its counterpart, at the us-east1 N4 Spot rates the wave-1 receipt
records ($0.722752/hour for n4-highcpu-32, $0.361376/hour for n4-highcpu-16):

| Plan | Jobs | Host | Counterpart duration | Estimated compute |
| --- | --- | --- | --- | --- |
| blockchain-swath-go-16-5 | 2 | n4-highcpu-32 | 307.4 s (`swath.07cb7eabf980.s1`) | $0.1234 |
| blockchain-swath-go-8-4 | 1 | n4-highcpu-32 | 307.4 s (same) | $0.0617 |
| nara-swath-go-16-5 | 1 | n4-highcpu-32 | 189.4 s (`swath.773bf96d14ef.s1`) | $0.0380 |
| nara-swath-go-8-4 | 1 | n4-highcpu-32 | 189.4 s (same) | $0.0380 |
| nara-s7cmd-go-16-5 | 1 | n4-highcpu-32 | 753.5 s (`s7cmd.8b7670175058.s1`) | $0.1513 |
| nara-s7cmd-go-8-4 | 1 | n4-highcpu-32 | 753.5 s (same) | $0.1513 |
| fourcast-s5cmd-go-8-4 | 1 | n4-highcpu-16 | 576.0 s (`s5cmd.c6cfb2bf8dd8.s1`) | $0.0578 |
| **Total** | 8 | | | **$0.62** |

By `runDuration` instead of create-to-terminal the eight come to $0.53. The
estimate does not model fixture staging on a smaller host (the blockchain
counterpart staged on n4-highcpu-80) or a slower subject at a smaller replay,
so the blockchain rows may run longer. With one preemption resubmission of the
largest job ($0.15) and wave 2's $1 non-compute reserve, the planning estimate
is about $1.77, under the $3 cap. All eight jobs stopped at 20 minutes would
cost $1.81 of compute; at the provider deadlines (`maxRunDuration` 8,405 s for
blockchain, 3,005 s for the others) $7.78, which is why the 20-minute stop
applies. The longest counterpart, NARA s7cmd, took 12.6 minutes create to
terminal. These are estimates, not an invoice.

## Pre-submit record

Dry-run attempts (group `dry-run`; case IDs are minted from the frozen image
set and change if it changes):

| Plan | Attempts | Machine | `maxRunDuration` |
| --- | --- | --- | --- |
| blockchain-swath-go-16-5 | `swath.b9c1ed5ae8a2.s1`, `.s2` | n4-highcpu-32 | 8405s |
| blockchain-swath-go-8-4 | `swath.c1505ec66d66.s1` | n4-highcpu-32 | 8405s |
| nara-swath-go-16-5 | `swath.73401a67238f.s1` | n4-highcpu-32 | 3005s |
| nara-swath-go-8-4 | `swath.bc04b205a545.s1` | n4-highcpu-32 | 3005s |
| nara-s7cmd-go-16-5 | `s7cmd.3f553acb0aca.s1` | n4-highcpu-32 | 3005s |
| nara-s7cmd-go-8-4 | `s7cmd.5c9e1f4d4685.s1` | n4-highcpu-32 | 3005s |
| fourcast-s5cmd-go-8-4 | `s5cmd.37412f5e21c0.s1` | n4-highcpu-16 | 3005s |
