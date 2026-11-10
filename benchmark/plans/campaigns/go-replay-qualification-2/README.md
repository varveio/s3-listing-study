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
same cells, at the same allocations, deliver their treatment. A same-wave index-off control separates the index
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
12,500 rows, latency deadlines, declared warm-up, the delivered-treatment grader
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
     --project varve-oss --location us-east1 --zone us-east1-b \
     --results-bucket s3-listing-study-results-29c02004 --image-set "$STATE/images.json" \
     --anonymous-worker-sa s3-listing-study-worker@varve-oss.iam.gserviceaccount.com \
     --network projects/varve-oss/global/networks/s3-listing-study \
     --subnetwork projects/varve-oss/regions/us-east1/subnetworks/s3-listing-study-us-east1 \
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
