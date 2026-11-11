# CPU profiles: Swath-Go `8c8f870` and Swath 0.3.2, blockchain sorted Parquet at 256 workers (2026-11-11)

Varve maintains this study and builds Swath and Swath-Go. Profiled runs;
their walls are diagnostic and not comparable with unprofiled rows.

Two attempts in group `sgt-r2-profile` (scale-study ledger), created 08:31
UTC, terminal 08:36 UTC, both SUCCEEDED, exit 0, 143,008,674 rows,
`TIMING_VALID`, 8 subject vCPUs, Go replay instrument as in
`swath-go-on-go-replay-cloud-20261111`.

**Harness availability.** `a4b9cc46993b6cf6dd905edc932207a6f7e5940c`: unmerged scratch revision, not published; the exact harness is carried by the recorded image digest (`benchmark-toolbox@sha256:e82b0c14…`).

- Harness `a4b9cc46993b6cf6dd905edc932207a6f7e5940c`, **on the unmerged branch
  `scratch/swath-go-on-go-replay`** (a merge of PR #36's profile-retention
  commits into that branch). Toolbox
  `benchmark-toolbox@sha256:e82b0c140145c6a2e672436e26aef8755764471528c3d1aecdf9a70c41c613c2`,
  image set `b17a0783aab3d94696fcfc8a2d85a7e5df49a8cf8dd3618cc4ee9289c1734e7b`.
- Swath-Go `main-8c8f870` (tool build `1b24f0bc…`) with `cpu_profile: on`;
  Swath 0.3.2 (4 GiB heap, merge parallelism 8) with `gc_log: on` and
  `jfr: on`.
- Profiles retained beside the result, bound by SHA-256 in each
  `result.json`'s native manifest (copied in `attempts.jsonl`), no profile
  omissions:
  - `swath-go.a0b2455731f9.s1/native/swath-go-cpu.pprof`
    `2ace37806def028399514dfb2e11549b00b8b5de8c5c06b30a56c3d10f5caa68`;
  - `swath.383a82eab611.s1/native/swath.jfr`
    `db5f4dcb46a8bd113067e68e0c20ba17670592a3d96f235d6ee8ce6856aa9c7f` and
    `swath-gc.log`
    `c4333e6a234ccb8923044b1156a13b99d8013bcebbbd0aa1f9f54001053b9291`.

Scope of any profile reading: Go CPU is the profile plus direct rusage;
Java CPU from JFR samples is an estimate, not the direct rusage the
confirmation receipt reports, and the two pipelines differ, so a CPU ratio
from this pair is not an equal-work comparison.

Cost: $0.2125 by `runDuration`, $0.2650 create-to-terminal.

## Record

| group | attempt | tool | version | mode | c | settings | subject cpuset | machine | state | exit | rows | wall s | CPU s | max RSS GiB | cgroup peak GiB | timing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sgt-r2-profile` | `swath-go.a0b2455731f9.s1` | swath-go | main-8c8f870 | parquet-sorted | 256 | cpu_profile=on | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 101.2 | 713.7 | 2.04 | 6.73 | TV |
| `sgt-r2-profile` | `swath.383a82eab611.s1` | swath | 0.3.2 | parquet-sorted | 256 | gc_log=on, jfr=on, jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 222.0 | 1255.6 | 1.87 | 7.98 | TV |

## Files

- `<group>.json`: one per ledger group, the `benchmark.receipt` export
  (`build_receipt`) after the public projection below. It carries the resolved case documents (config, machine,
  container memory, replay backend and allocation), the rendered Batch request
  (every runnable's container command line, `--cpuset-cpus`/`--memory` options
  and environment variables, boot disk, Spot provisioning), attempt states and
  timestamps, the `result.json` SHA-256, both exit codes, wall clock,
  peak RSS, row count and the replay evidence summary.
- `attempts.jsonl`: one line per attempt with the `result.json` bindings the
  receipt does not copy: harness revision, toolbox image, image set,
  toolbox manifest/recipe, tool version, tool recipe/build-inputs/build hashes,
  adapter bundle hash, the ledger's platform and tool-slice hashes, executor
  settings (boot disk, retention), declared server and subject cpusets, the
  harness's replay timing assessment (per request shape: count, mean,
  deadline, overrun fraction; replay CPU samples; reasons), the list-index summary
  where the Go replay ran (meters, plus the attached server line), replay server memory before/after, subject user+system
  CPU seconds (rusage), cgroup memory peak and OOM deltas, evidence profile,
  product retention, native file manifest (SHA-256 of retained profiles and
  reports) and profile omissions.
- `server-lines.txt`: each Go-replay attempt's `swath_replay list_index` line, read from the job's Cloud Logging (`batch_task_logs`) with `gcloud logging read` on 2026-11-11 and attached to the group files and `attempts.jsonl` through the `benchmark.receipt --server-log` path, which cross-checks it against the bound meters. Every attempt here reports `delivered: true` with no disagreement.
- `cost-observed.json`: each job's provider `runDuration` and
  create-to-terminal span, priced at the published us-east1 N4 Spot list rates
  used by the Go replay qualification receipts ($1.80688/h n4-highcpu-80,
  $0.722752/h n4-highcpu-32, $0.361376/h n4-highcpu-16; Google Spot VM pricing,
  us-east1 table retrieved 2026-11-10). Execution-based estimates at list
  rates, not an invoice; compute only.

Public projection: the ledger and the raw export carry the study's estate,
so every JSON file here is a projection of it, made after export and checked
with the release exporter's forbidden-pattern list
(`benchmark.public_export.FORBIDDEN_PATTERNS`) plus cloud project, network and
secret resource paths, the results bucket and Batch job UIDs, all with no hit. Dropped
wherever they occur: service accounts, secret resources, project, network,
subnetwork and zone, Batch job names and UIDs (including the worker's
job argument), result-store prefixes and URIs, and the executor
environment apart from boot disk, provisioning and product retention.
Substituted in every string: the results bucket becomes `<result-store>/` or
`<fixture-store>/`, the study's registry path `<study-registry>/` (digests
kept). Everything else is verbatim, including the `result.json` SHA-256 that
binds each attempt to its original evidence. Case ids are carried from the
ledger; they are not recomputable from the projected case documents.

Export: `benchmark.receipt` and `benchmark.report` (for the timing
assessment and declared cpusets) imported from study commit
`73fe22707cbac030c327ec21a08444bb5ef2c369` (an unmerged scratch revision, not
published), read-only against the scale-study ledger and the results bucket.
Its `receipt.py`, `report.py`, `replay.py`, `verify.py` and `ledger.py` are
byte-identical to PR #37's head `89e6cd7`, so that head reproduces the
export. That commit is used because it contains both this
branch's capsule and PR #37's Go replay contract: the exporter on this branch
alone refuses Go replay documents (`fixture_adopted_sha256`,
`server_implementation` are unknown to it). Provider durations come from the
Batch job list read on 2026-11-11.

Grades in the table are the harness's replay timing states: TV
`TIMING_VALID`, IE `INSUFFICIENT_EVIDENCE`, CF `CAPACITY_FAILED`, NA
`NOT_APPLICABLE` (no latency model), `-` no replay. `TIMING_VALID` means the
replay instrument met its own evidence rule for that attempt; it does not make
a row calibrated, a ranking, or a tuned comparison. RSS and cgroup peak are
GiB (2^30 bytes); RSS is the subject's `max_rss_kb`, cgroup peak is the
subject container's `memory.peak` (includes page cache). CPU s is the
subject's user+system rusage.
