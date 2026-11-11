# Swath-Go at swath main `8c8f870` on the release Java replay instrument (2026-11-11)

Varve maintains this study and builds Swath and Swath-Go. Diagnostic rows,
not a calibrated benchmark or a tool ranking.

Twenty-one attempts in seven groups (scale-study ledger), created 06:40 to
06:43 UTC, all terminal by 06:53 UTC. All SUCCEEDED, exit 0, every row count
equal to the fixture's. FourCast Go sorted Parquet ran twice
(`swath-go.9c171401cb27.s1` in `swath-go-main-canary`, `.s2` in
`swath-go-main-fourcast`): the canary attempt was not reconciled before the
fixture group was submitted, so the second is a repeat of the same case, kept
as data.

**Harness availability.** `9ee636509151c82b8bf6f34dd12ab802cf44fa19` is published as tag `receipts/swath-go-main-8c8f870-20261111`.

- Harness: `9ee636509151c82b8bf6f34dd12ab802cf44fa19` (on
  `capsule/swath-go-v2`, PR #36), toolbox
  `benchmark-toolbox@sha256:c839fffb27891a1fc39ccd067f422b8057415de342f5ad793376e8d50d7e87f4`,
  image set `0f81d21f2eaf021f1c667e7b61e120c6e018149bc52cc26c09c6a4725c2fd238`,
  platform `ee2a4379ea42170de056d4b077a616f44c2a18b54f038aa7cfa453044858b4bb`.
- Swath-Go: tool version `main-8c8f870`, source archive of varveio/swath
  `8c8f870801c700f22413581f35683096782329a0` (main), archive SHA-256
  `8aa439ac53df3f8a704851f9c9e1eaee49b52f51a3c2dfa0d958160ceb7e5f4d`, tool
  build `1b24f0bc301ea7f43cd412c3805277a39faf41752787915a31ef4f9d07aed182`,
  adapter bundle `1ed564a98707fea5c7e883649f42c90f0865454076fc7aea36b93f38d5833dc5`.
  The same tool build ran in every later 2026-11-11 receipt that names
  `main-8c8f870`.
- Swath (Java) 0.3.2, rerun beside it on the same image (tool build
  `c28b5c8b…`, as in the 2026-11-07 receipt).
- Replay: the release Java replay instrument, unchanged from the 2026-11-07
  receipt (images, fixtures, deadlines, warm-up, machines, allocations). Go
  defaults at this pin include a sort buffer of 1/16 of the container memory
  limit (512 MiB in 8 GiB containers, 1 GiB in 16 GiB); the morning rows ran
  that default.
- Single repetition per cell (FourCast Go sorted twice). No content comparison
  beyond row counts. Products not retained.

Instrument note (from the timing assessments in `attempts.jsonl`): on the
Java replay server, Swath-Go rows show worker-page and structure-probe
overruns that Java's rows at the same concurrency do not (for example
FourCast Go sorted: worker_page overrun about 16 %, structure_probe about
25 %), and blockchain and NBM are `CAPACITY_FAILED` for both tools, as in the
release. Go/Java ratios from this receipt are not timing-valid and are not
conservative either way.

Cost: $1.2206 by `runDuration`, $1.5264 create-to-terminal.

## Record

| group | attempt | tool | version | mode | c | settings | subject cpuset | machine | state | exit | rows | wall s | CPU s | max RSS GiB | cgroup peak GiB | timing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `swath-go-main-canary` | `swath-go.9c171401cb27.s1` | swath-go | main-8c8f870 | parquet-sorted | 64 | - | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 4,081,170 | 11.4 | 19.6 | 1.10 | 1.15 | IE |
| `swath-go-main-aws-public-blockchain` | `swath-go.8e1576dce667.s1` | swath-go | main-8c8f870 | tsv-zstd | 256 | - | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 144.6 | 1054.4 | 2.29 | 5.60 | CF |
| `swath-go-main-aws-public-blockchain` | `swath-go.f166d7158121.s1` | swath-go | main-8c8f870 | parquet-sorted | 256 | - | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 117.2 | 792.3 | 2.04 | 6.82 | CF |
| `swath-go-main-aws-public-blockchain` | `swath-go.d124b68ad0bb.s1` | swath-go | main-8c8f870 | parquet-sorted | 64 | - | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 241.4 | 692.3 | 1.38 | 5.34 | CF |
| `swath-go-main-aws-public-blockchain` | `swath.cfcdc7a2bd18.s1` | swath | 0.3.2 | parquet-sorted | 256 | jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 242.9 | 1354.5 | 1.92 | 7.87 | CF |
| `swath-go-main-aws-public-blockchain` | `swath.ad6b344d2e75.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 338.3 | 1094.9 | 1.41 | 7.78 | CF |
| `swath-go-main-aws-public-blockchain` | `swath.8da458f41379.s1` | swath | 0.3.2 | tsv-zstd | 256 | jvm_max_heap=4g, part_rotation_interval=0, part_rotation_max_rows=0, text_part_size=1gb, text_writers=3, writeback_size=32mb | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 167.8 | 962.3 | 2.27 | 6.38 | CF |
| `swath-go-main-idc-open-data` | `swath-go.c026de7403b0.s1` | swath-go | main-8c8f870 | parquet-sorted | 64 | - | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 56,311,145 | 68.6 | 336.8 | 2.68 | 5.19 | NA |
| `swath-go-main-idc-open-data` | `swath.10f9d021cec6.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 56,311,145 | 139.7 | 497.0 | 0.98 | 6.20 | NA |
| `swath-go-main-nara-1950-census` | `swath-go.ba249212329c.s1` | swath-go | main-8c8f870 | parquet-sorted | 64 | - | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 13,540,310 | 26.1 | 47.7 | 1.21 | 1.55 | IE |
| `swath-go-main-nara-1950-census` | `swath-go.0202c990a7bb.s1` | swath-go | main-8c8f870 | tsv | 64 | - | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 13,540,310 | 31.0 | 40.4 | 1.26 | 2.80 | IE |
| `swath-go-main-nara-1950-census` | `swath.7eb9aafd6be8.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 13,540,310 | 78.3 | 98.8 | 0.73 | 1.28 | CF |
| `swath-go-main-noaa-nbm-grib2-pds` | `swath-go.0c9597650041.s1` | swath-go | main-8c8f870 | parquet-sorted | 64 | - | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 66,405,936 | 103.8 | 218.5 | 1.19 | 2.73 | CF |
| `swath-go-main-noaa-nbm-grib2-pds` | `swath-go.74204f852189.s1` | swath-go | main-8c8f870 | tsv | 64 | - | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 66,405,936 | 110.3 | 192.1 | 1.20 | 8.00 | CF |
| `swath-go-main-noaa-nbm-grib2-pds` | `swath.9ba34a96c2bb.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 66,405,936 | 156.8 | 325.5 | 1.10 | 4.21 | CF |
| `swath-go-main-real-changesets` | `swath-go.6357dd2c6236.s1` | swath-go | main-8c8f870 | parquet-sorted | 64 | - | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 13,868,442 | 33.8 | 42.6 | 1.14 | 1.45 | IE |
| `swath-go-main-real-changesets` | `swath-go.59afc39d3f16.s1` | swath-go | main-8c8f870 | tsv | 64 | - | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 13,868,442 | 35.0 | 34.6 | 1.13 | 2.06 | IE |
| `swath-go-main-real-changesets` | `swath.82de0fc2103d.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 13,868,442 | 63.7 | 78.4 | 0.63 | 1.27 | TV |
| `swath-go-main-fourcast` | `swath-go.9c171401cb27.s2` | swath-go | main-8c8f870 | parquet-sorted | 64 | - | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 4,081,170 | 11.7 | 14.0 | 1.01 | 1.10 | IE |
| `swath-go-main-fourcast` | `swath-go.6e76631186a4.s1` | swath-go | main-8c8f870 | tsv | 64 | - | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 4,081,170 | 13.8 | 18.0 | 0.98 | 1.53 | IE |
| `swath-go-main-fourcast` | `swath.5e684e7a293f.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 4,081,170 | 50.9 | 40.7 | 0.38 | 0.54 | TV |

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
