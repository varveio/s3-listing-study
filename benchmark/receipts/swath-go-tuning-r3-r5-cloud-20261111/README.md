# Swath-Go and Swath setting exploration on the Go replay instrument (2026-11-11)

Varve maintains this study and builds Swath and Swath-Go. Diagnostic
exploration of each tool's settings, single repetitions, not a calibrated
benchmark or a tool ranking.

Fifteen attempts in three groups (scale-study ledger), created 08:15 UTC,
terminal by 08:23 UTC. All SUCCEEDED, exit 0, full row counts. Blockchain
rows are all `TIMING_VALID`; FourCast Go rows `INSUFFICIENT_EVIDENCE` (too
short), FourCast Java `TIMING_VALID`.

**Harness availability.** `08dee083bf78a408b2b992d769497fe51cde9d09`: unmerged scratch revision, not published; the exact harness is carried by the recorded image digest (`benchmark-toolbox@sha256:dab1c147…`).

- `sgt-r3-cpu`: blockchain sorted Parquet at 256 workers, subject on 8 and on
  4 vCPUs (Go: 8 encoders, 512 MiB sort buffer; Java: 4 GiB heap, merge
  parallelism 8).
- `sgt-r4-mem`: blockchain sorted Parquet at 256 workers, 8 vCPUs. Go: sort
  buffer 512 MiB vs 128 MiB with automatic encoders, and 512 MiB with 4
  encoders. Java: heap 4/6 GiB by merge parallelism 8/4 (the 4 GiB/8 case is
  the same case as r3's, run again).
- `sgt-r5-small`: FourCast sorted Parquet at 64 workers. Go sort buffer
  1 GiB (the 1/16 default in a 16 GiB container) vs 128 MiB; Java heap 4 vs
  8 GiB.
- Harness `08dee083bf78a408b2b992d769497fe51cde9d09`, **on the unmerged branch
  `scratch/swath-go-on-go-replay`** (plans
  `benchmark/plans/campaigns/swath-go-tuning/` at that commit; the tuning
  controls themselves are PR #36 commits merged into that branch). Toolbox
  `benchmark-toolbox@sha256:dab1c147072fa82d30b9fe1e187012760e355d3ffd127a83d8b04b07827b9ee5`,
  image set `2bfc3cb8c59c75eb6baeb7b963c853f8eace0b961550297721843628ad10e9b8`.
- Subjects: Swath-Go tool build `1b24f0bc…` (`main-8c8f870`), Swath 0.3.2
  `c28b5c8b…`; adapter bundles differ from the morning receipt because the
  tuning controls changed the adapters (hashes in `attempts.jsonl`).
- Replay: the qualified Go replay instrument as in
  `swath-go-on-go-replay-cloud-20261111`.

Readings this receipt supports, diagnostic and configuration-specific: the
same Java case ran 199.8 s and 240.9 s, so single repetitions here do not
resolve knob effects of that size; neither tool's explored settings are shown
flat, only unresolved at this precision. The smaller Go sort buffer lowered
peak RSS on both fixtures; equal FourCast walls are ungraded and do not meet a
non-inferiority rule.

Cost: $1.3785 by `runDuration`, $1.6936 create-to-terminal.

## Record

| group | attempt | tool | version | mode | c | settings | subject cpuset | machine | state | exit | rows | wall s | CPU s | max RSS GiB | cgroup peak GiB | timing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sgt-r3-cpu` | `swath-go.7d5e34a3d2ad.s1` | swath-go | main-8c8f870 | parquet-sorted | 256 | encoders=8, sort_buffer=512mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 110.0 | 776.9 | 2.05 | 6.73 | TV |
| `sgt-r3-cpu` | `swath-go.f7aaebdbd934.s1` | swath-go | main-8c8f870 | parquet-sorted | 256 | encoders=8, sort_buffer=512mib | 32-33,72-73 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 181.1 | 662.5 | 2.10 | 6.60 | TV |
| `sgt-r3-cpu` | `swath.43ab7c33ccf5.s1` | swath | 0.3.2 | parquet-sorted | 256 | jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 240.9 | 1374.4 | 1.85 | 7.85 | TV |
| `sgt-r3-cpu` | `swath.4328badeda0d.s1` | swath | 0.3.2 | parquet-sorted | 256 | jvm_max_heap=4g, sort_merge_parallelism=8 | 32-33,72-73 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 352.7 | 1193.0 | 1.94 | 7.89 | TV |
| `sgt-r4-mem` | `swath-go.a635be2b1eea.s1` | swath-go | main-8c8f870 | parquet-sorted | 256 | sort_buffer=512mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 104.6 | 731.9 | 2.01 | 6.76 | TV |
| `sgt-r4-mem` | `swath-go.70456c8205c4.s1` | swath-go | main-8c8f870 | parquet-sorted | 256 | sort_buffer=128mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 109.7 | 776.4 | 1.11 | 6.69 | TV |
| `sgt-r4-mem` | `swath-go.67b9c10b631d.s1` | swath-go | main-8c8f870 | parquet-sorted | 256 | encoders=4, sort_buffer=512mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 109.5 | 667.8 | 2.07 | 6.69 | TV |
| `sgt-r4-mem` | `swath.43ab7c33ccf5.s2` | swath | 0.3.2 | parquet-sorted | 256 | jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 199.8 | 1101.9 | 2.01 | 7.98 | TV |
| `sgt-r4-mem` | `swath.1bbe26879078.s1` | swath | 0.3.2 | parquet-sorted | 256 | jvm_max_heap=6g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 215.6 | 1149.9 | 1.99 | 8.00 | TV |
| `sgt-r4-mem` | `swath.1070280ec98c.s1` | swath | 0.3.2 | parquet-sorted | 256 | jvm_max_heap=4g, sort_merge_parallelism=4 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 225.6 | 1068.6 | 1.89 | 7.71 | TV |
| `sgt-r4-mem` | `swath.35b13c754842.s1` | swath | 0.3.2 | parquet-sorted | 256 | jvm_max_heap=6g, sort_merge_parallelism=4 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 214.4 | 979.4 | 2.26 | 7.90 | TV |
| `sgt-r5-small` | `swath-go.293c3d7e08ae.s1` | swath-go | main-8c8f870 | parquet-sorted | 64 | sort_buffer=128mib | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 4,081,170 | 9.7 | 16.6 | 0.43 | 0.54 | IE |
| `sgt-r5-small` | `swath-go.00747fc456eb.s1` | swath-go | main-8c8f870 | parquet-sorted | 64 | sort_buffer=1gib | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 4,081,170 | 9.7 | 19.3 | 1.04 | 1.13 | IE |
| `sgt-r5-small` | `swath.4b36bb978594.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 4,081,170 | 55.4 | 55.7 | 0.51 | 0.69 | TV |
| `sgt-r5-small` | `swath.ba0d6590b6f3.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=8g, sort_merge_parallelism=8 | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 4,081,170 | 56.8 | 55.0 | 0.48 | 0.59 | TV |

## Files

- `<group>.json`: one per ledger group, the deterministic `benchmark.receipt`
  export (`build_receipt`, byte-identical to the module's `receipt.json`,
  renamed per group). It carries the resolved case documents (config, machine,
  container memory, replay backend and allocation), the rendered Batch request
  (every runnable's container command line, `--cpuset-cpus`/`--memory` options
  and environment variables, boot disk, Spot provisioning), attempt states and
  timestamps, the `result.json` URI and SHA-256, both exit codes, wall clock,
  peak RSS, row count and the replay evidence summary.
- `attempts.jsonl`: one line per attempt with the `result.json` bindings the
  receipt does not copy: harness revision, toolbox image, image set,
  toolbox manifest/recipe, tool version, tool recipe/build-inputs/build hashes,
  adapter bundle hash, the ledger's platform and tool-slice hashes, executor
  settings (boot disk, retention), declared server and subject cpusets, the
  harness's replay timing assessment (per request shape: count, mean,
  deadline, overrun fraction; replay CPU samples; reasons), list-index meters
  where the Go replay ran, replay server memory before/after, subject user+system
  CPU seconds (rusage), cgroup memory peak and OOM deltas, evidence profile,
  product retention, native file manifest (SHA-256 of retained profiles and
  reports) and profile omissions.
- `server-lines.txt`: each Go-replay attempt's `swath_replay list_index` line, read from the job's Cloud Logging (`batch_task_logs`) with `gcloud logging read` on 2026-11-11 and attached to the group files through the `benchmark.receipt --server-log` path, which cross-checks it against the bound meters. Every attempt here reports `delivered: true` with no disagreement.
- `cost-observed.json`: each job's provider `runDuration` and
  create-to-terminal span, priced at the published us-east1 N4 Spot list rates
  used by the Go replay qualification receipts ($1.80688/h n4-highcpu-80,
  $0.722752/h n4-highcpu-32, $0.361376/h n4-highcpu-16; Google Spot VM pricing,
  us-east1 table retrieved 2026-11-10). Execution-based estimates at list
  rates, not an invoice; compute only.

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
