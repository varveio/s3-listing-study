# Swath-Go `8c8f870` and Swath 0.3.2 on the qualified Go replay instrument (2026-11-11)

Varve maintains this study and builds Swath and Swath-Go. Diagnostic rows,
not a calibrated benchmark or a tool ranking.

Twelve attempts in three groups (`sgo-goreplay-{blockchain,nara,fourcast}`,
scale-study ledger), created 07:14 UTC, terminal by 07:22 UTC. All
SUCCEEDED, exit 0, full row counts.

**Harness availability.** `c25a6e7d90c9a9c0c87e10da121cffc24dc51db0` is published as tag `receipts/swath-go-on-go-replay-20261111`.

- Harness: `c25a6e7d90c9a9c0c87e10da121cffc24dc51db0`, **on the unmerged
  branch `scratch/swath-go-on-go-replay`**: a merge of PR #36 (`9ee6365`) and
  PR #37 (`89e6cd7`) plus the plans in
  `benchmark/plans/campaigns/swath-go-on-go-replay/` at that commit. Not a
  merged head of either PR. Toolbox
  `benchmark-toolbox@sha256:d5503f586eccc00fa519e409edb8f8f25dda3d22ca1bd9d31ee129efd81fb1dc`,
  image set `f5c1f40437e4f6ecb2923a7be9337f7988e16a50e8396ba5509c700eae91991c`,
  platform `a2007733ab865c165f4c6875d1e2248c7d2993cb07af19e0e66a0abb44cb1179`.
- Subjects: the same Swath-Go tool build (`main-8c8f870`, `1b24f0bc…`) and
  Swath 0.3.2 build (`c28b5c8b…`) as the morning receipt
  `swath-go-main-8c8f870-cloud-20261111`.
- Replay: Go replay
  `swath-replay@sha256:f8b70f0f1c337271007b6aa8b00bf87f68bdd8d361dbbb7a19401edb5f629f90`
  (varveio/swath `43ffab2f8c2c11041daba71273451bdf66190fc7`), adopted
  SeekParquet fixtures (`fixture_sha256` and `fixture_adopted_sha256` per case),
  list index on (64 MiB), server knobs and allocation as in the qualifying
  Go replay receipts (wave 3 blockchain, wave 4 NARA and FourCast), release
  deadlines and warm-up. Boot disk 100 GB (qualification used 200 GB).
- What changed against the morning receipt, all together: replay server
  implementation and image, fixture format, list index, server knobs, and the
  worker platform (host sampler, read-only host proc mount), which also gives
  new case ids. This is not an instrument-only change.
- Single repetition per cell. Products not retained.

Grades: all six blockchain rows are `TIMING_VALID` for both tools; NARA and
FourCast Go rows are `INSUFFICIENT_EVIDENCE` (runs too short for five replay
resource samples), their Java rows `TIMING_VALID`. Timing-valid single
repetitions support those rows only.

Cost: $0.8779 by `runDuration`, $1.0970 create-to-terminal.

## Record

| group | attempt | tool | version | mode | c | settings | subject cpuset | machine | state | exit | rows | wall s | CPU s | max RSS GiB | cgroup peak GiB | timing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sgo-goreplay-blockchain` | `swath-go.8536109cac23.s1` | swath-go | main-8c8f870 | tsv-zstd | 256 | - | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 113.1 | 870.4 | 2.29 | 5.58 | TV |
| `sgo-goreplay-blockchain` | `swath-go.194bbaaae3af.s1` | swath-go | main-8c8f870 | parquet-sorted | 256 | - | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 99.4 | 688.7 | 2.04 | 6.76 | TV |
| `sgo-goreplay-blockchain` | `swath-go.56e351e2c9cb.s1` | swath-go | main-8c8f870 | parquet-sorted | 64 | - | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 233.9 | 540.9 | 1.37 | 5.01 | TV |
| `sgo-goreplay-blockchain` | `swath.11628ad1fe9c.s1` | swath | 0.3.2 | parquet-sorted | 256 | jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 212.7 | 1172.4 | 1.86 | 8.00 | TV |
| `sgo-goreplay-blockchain` | `swath.10e8a304a85d.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 328.5 | 976.4 | 1.45 | 7.82 | TV |
| `sgo-goreplay-blockchain` | `swath.8439074f29a4.s1` | swath | 0.3.2 | tsv-zstd | 256 | jvm_max_heap=4g, part_rotation_interval=0, part_rotation_max_rows=0, text_part_size=1gb, text_writers=3, writeback_size=32mb | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 160.8 | 929.1 | 1.98 | 5.80 | TV |
| `sgo-goreplay-nara` | `swath-go.d627a0e76cd0.s1` | swath-go | main-8c8f870 | parquet-sorted | 64 | - | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 13,540,310 | 24.7 | 56.7 | 1.21 | 1.55 | IE |
| `sgo-goreplay-nara` | `swath-go.9abf47f16e7a.s1` | swath-go | main-8c8f870 | tsv | 64 | - | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 13,540,310 | 28.1 | 55.8 | 1.20 | 3.15 | IE |
| `sgo-goreplay-nara` | `swath.6b4de4e7fc46.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 13,540,310 | 77.5 | 106.9 | 0.67 | 1.25 | TV |
| `sgo-goreplay-fourcast` | `swath-go.a7eba8b1b911.s1` | swath-go | main-8c8f870 | parquet-sorted | 64 | - | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 4,081,170 | 9.9 | 19.7 | 0.87 | 0.96 | IE |
| `sgo-goreplay-fourcast` | `swath-go.88a58fd1d2fc.s1` | swath-go | main-8c8f870 | tsv | 64 | - | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 4,081,170 | 11.3 | 14.5 | 0.89 | 1.62 | IE |
| `sgo-goreplay-fourcast` | `swath.a0995929aac2.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 4,081,170 | 53.2 | 41.3 | 0.39 | 0.58 | TV |

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
