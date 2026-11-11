# Confirmation blocks: Swath-Go revision, sort buffer and Java collector, blockchain (2026-11-11)

Varve maintains this study and builds Swath and Swath-Go. Diagnostic,
single fixture, single instrument; not a calibrated benchmark, not a
ranking, not a statement that either tool is fully tuned.

Thirty-six attempts: twelve cells by three seeded blocks, one ledger group
per cell and block (`sgc-b{1,2,3}-NN`; a submit takes one image set, so a
block cannot be one group). Blockchain sorted Parquet on the qualified Go
replay instrument, n4-highcpu-80, subject on 8 vCPUs unless the cell says
`/4`. Block 1 created 10:02 UTC, block 2 10:13, block 3 10:22; all terminal
by 10:31 UTC. **All 36 SUCCEEDED, exit 0, 143,008,674 rows, `TIMING_VALID`.**
No preemption, retry or cancellation.

**Harness availability.** `73fe22707cbac030c327ec21a08444bb5ef2c369` (Go-old, Java) and `e00dfb422b9d81dbe8775abb0f1c9d839e95f364` (Go-new): each an unmerged scratch revision, not published; the exact harness is carried by the recorded image digest (`benchmark-toolbox@sha256:1015b240…` and `@sha256:1e4319d4…` respectively).

- Go-old (`GO*`) and Java (`JG1`, `JPAR`): harness
  `73fe22707cbac030c327ec21a08444bb5ef2c369`, **unmerged branch
  `scratch/swath-go-on-go-replay`**, toolbox
  `benchmark-toolbox@sha256:1015b24063b4d59d2a7dbf8ad87059590a6ea1676d35e527b3fb6f43a0368d6f`,
  image set `743c2edaa23de88380219007cc93b7a12f29943765a210697689ce109ef48215`.
  Swath-Go `main-8c8f870` (tool build `1b24f0bc…`); Swath 0.3.2 (4 GiB heap,
  merge parallelism 8, `gc_collector` g1 or parallel).
- Go-new (`GN*`): harness `e00dfb422b9d81dbe8775abb0f1c9d839e95f364`,
  **unmerged branch `scratch/swath-go-next`**, toolbox
  `benchmark-toolbox@sha256:1e4319d4d3641eacfc7ad8582b9132b47fb9ed337ce2fd84ef806a3ddd80796a`,
  image set `287e474ffb8d3e7305bb95ca0a4483ab3a1b44f237e9eb982c1a09ed0964f95e`.
  Tool version `list-parser-cpu-c54179e`: source archive of varveio/swath
  `c54179eda8d67063f052bafec68db3a6b0022a2e`, the head of varveio/swath#458,
  archive SHA-256
  `b772587c07779bdf747a3fa9f48a0d0c18d7abd0b58eb0c8144de7084b101e1b`, tool
  build `a815f991f2476dfc268885b984b7c5c36a5252f8c2e48ce4c0a2c0d141506185`.
  That pull request (list scanner, density step and User-Agent caching) was
  squash-merged after this campaign as `0c856fc3c150d0c0a8e42211dd34de7349da717b`;
  these rows ran the pre-merge head, not the merge commit. The two commits
  have the same git tree (`2ff94031a3b7bb82933e952a631eb7ceadcb051a`), so the
  source is identical; the archive bytes and the stamped revision differ, and
  a capsule pinned to the merge commit is a different tool build. `e00dfb4` differs
  from `73fe227` only in the Swath-Go pin (Swath-Go build files and the
  toolbox Dockerfile), so the image sets differ only in the Swath-Go slice
  and the platform hash.
- Replay: Go replay `f8b70f0f…`, adopted blockchain fixture `c4a42eb4…`,
  list index on, deadlines 94/55/46 ms, release warm-up, replay on 64 vCPUs.
- Order: `schedule.json` (seed 20261111; per block
  `random.Random(seed).sample` of the twelve cells; cell definitions and plan
  paths). The seeded order fixes submission order; within a block the
  attempts ran concurrently on separate VMs, so a block is not same-host
  pairing.
- Retention: block 1 cells GN128@256, GN512@256, GO512@256 and JG1@256
  retained products (`retained-products.json`: manifest SHA-256, manifest part
  entries, GCS object MD5/CRC32C; 0 mismatches). Every other attempt keeps
  `result.json`, logs and the tool report only. Profiles off.

## Comparator and output

`compare.py.txt` (the comparator script `compare.py` verbatim, stored as text so the
repository linters skip it; SHA-256
`2a2e98b55e366888f3476f093fd71ff4f8107f76dac38fc47b430c83266b1f95`) over
local copies of the four retained products. `compare-output.txt` is the
output of the first run, whose DuckDB version was not recorded.
`compare-output-rerun.txt` is a re-run of the same script on 2026-11-11 under
DuckDB 1.5.5 (the version `uv.lock` pins), over fresh read-only downloads of
the retained products (integrity-checked by `gcloud storage cp`), one product
per invocation to fit the local disk, each deleted after its run; its four
lines equal the first run's. Per dataset: row count; an
order-insensitive fingerprint over the nine shared S3 columns (count,
`bit_xor` and sum of DuckDB's 64-bit row hash); strict unsigned-byte key order
within every part and across consecutive parts in manifest order. All four
have 143,008,674 rows, the same fingerprint as the Java product, and 0 order
violations.

Scope: this is a probabilistic fingerprint plus order checks on **four
block-1 products**, not a row-by-row comparison and not a check of all 36
attempts; `bit_xor` and sum of the same hash are not independent proofs.

## What this receipt supports

Configuration-specific diagnostic observations of wall clock, subject CPU
(direct rusage, both tools) and peak RSS for the cells in the table, and
paired Go/Java and Go-old/Go-new ratios computed from them. Report median
paired ratios and ratios of cell medians as separate statistics. The 128 MiB
sort-buffer cells met the schedule's empirical rule (largest per-block wall
ratio against 512 MiB 1.024 at 256 workers), not a one-sided uncertainty
bound below 1.05, and one fixture is no basis for a default change.

Cost: $5.0538 by `runDuration`, $6.0490 create-to-terminal.

## Record

| group | attempt | cell | tool | version | mode | c | settings | subject cpuset | machine | state | exit | rows | wall s | CPU s | max RSS GiB | cgroup peak GiB | timing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sgc-b1-01` | `swath-go.20d179d1bae1.s1` | GN128@64 | swath-go | list-parser-cpu-c54179e | parquet-sorted | 64 | sort_buffer=128mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 233.2 | 471.3 | 0.56 | 4.51 | TV |
| `sgc-b1-02` | `swath.fdd03b509775.s1` | JPAR@256 | swath | 0.3.2 | parquet-sorted | 256 | gc_collector=parallel, jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 210.6 | 1147.8 | 4.31 | 8.00 | TV |
| `sgc-b1-03` | `swath-go.c8001cbae2b1.s1` | GO512@64 | swath-go | main-8c8f870 | parquet-sorted | 64 | sort_buffer=512mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 239.3 | 576.4 | 1.33 | 5.19 | TV |
| `sgc-b1-04` | `swath-go.7d43994649c5.s1` | GN128@256 | swath-go | list-parser-cpu-c54179e | parquet-sorted | 256 | sort_buffer=128mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 92.4 | 585.5 | 1.09 | 6.68 | TV |
| `sgc-b1-05` | `swath-go.e25d0f83d0f2.s1` | GN512@256/4 | swath-go | list-parser-cpu-c54179e | parquet-sorted | 256 | sort_buffer=512mib | 32-33,72-73 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 162.8 | 590.2 | 2.12 | 6.66 | TV |
| `sgc-b1-06` | `swath.7c336967cef3.s1` | JG1@64 | swath | 0.3.2 | parquet-sorted | 64 | gc_collector=g1, jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 330.9 | 1051.2 | 1.52 | 7.90 | TV |
| `sgc-b1-07` | `swath.77058199c4c0.s1` | JPAR@64 | swath | 0.3.2 | parquet-sorted | 64 | gc_collector=parallel, jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 321.1 | 855.7 | 4.31 | 8.00 | TV |
| `sgc-b1-08` | `swath-go.6038850c33c1.s1` | GO512@256/4 | swath-go | main-8c8f870 | parquet-sorted | 256 | sort_buffer=512mib | 32-33,72-73 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 202.4 | 749.6 | 2.10 | 6.56 | TV |
| `sgc-b1-09` | `swath-go.22e636276e1e.s1` | GO512@256 | swath-go | main-8c8f870 | parquet-sorted | 256 | sort_buffer=512mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 107.7 | 775.0 | 2.03 | 6.69 | TV |
| `sgc-b1-10` | `swath.c07a24197a7b.s1` | JG1@256 | swath | 0.3.2 | parquet-sorted | 256 | gc_collector=g1, jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 233.0 | 1329.5 | 1.98 | 7.80 | TV |
| `sgc-b1-11` | `swath-go.6eab8d82a351.s1` | GN512@64 | swath-go | list-parser-cpu-c54179e | parquet-sorted | 64 | sort_buffer=512mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 231.1 | 453.5 | 1.32 | 4.94 | TV |
| `sgc-b1-12` | `swath-go.5f0e2acdc882.s1` | GN512@256 | swath-go | list-parser-cpu-c54179e | parquet-sorted | 256 | sort_buffer=512mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 95.9 | 621.3 | 2.07 | 6.72 | TV |
| `sgc-b2-01` | `swath-go.20d179d1bae1.s2` | GN128@64 | swath-go | list-parser-cpu-c54179e | parquet-sorted | 64 | sort_buffer=128mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 231.8 | 474.3 | 0.56 | 4.36 | TV |
| `sgc-b2-02` | `swath-go.e25d0f83d0f2.s2` | GN512@256/4 | swath-go | list-parser-cpu-c54179e | parquet-sorted | 256 | sort_buffer=512mib | 32-33,72-73 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 145.1 | 524.3 | 2.18 | 6.72 | TV |
| `sgc-b2-03` | `swath.fdd03b509775.s2` | JPAR@256 | swath | 0.3.2 | parquet-sorted | 256 | gc_collector=parallel, jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 197.6 | 1050.4 | 4.32 | 8.00 | TV |
| `sgc-b2-04` | `swath-go.7d43994649c5.s2` | GN128@256 | swath-go | list-parser-cpu-c54179e | parquet-sorted | 256 | sort_buffer=128mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 97.6 | 654.3 | 1.13 | 6.75 | TV |
| `sgc-b2-05` | `swath.77058199c4c0.s2` | JPAR@64 | swath | 0.3.2 | parquet-sorted | 64 | gc_collector=parallel, jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 327.1 | 952.9 | 4.31 | 8.00 | TV |
| `sgc-b2-06` | `swath.7c336967cef3.s2` | JG1@64 | swath | 0.3.2 | parquet-sorted | 64 | gc_collector=g1, jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 333.2 | 1059.8 | 1.40 | 7.77 | TV |
| `sgc-b2-07` | `swath-go.22e636276e1e.s2` | GO512@256 | swath-go | main-8c8f870 | parquet-sorted | 256 | sort_buffer=512mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 94.4 | 644.1 | 1.99 | 6.66 | TV |
| `sgc-b2-08` | `swath-go.6038850c33c1.s2` | GO512@256/4 | swath-go | main-8c8f870 | parquet-sorted | 256 | sort_buffer=512mib | 32-33,72-73 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 175.7 | 646.0 | 2.04 | 6.63 | TV |
| `sgc-b2-09` | `swath-go.6eab8d82a351.s2` | GN512@64 | swath-go | list-parser-cpu-c54179e | parquet-sorted | 64 | sort_buffer=512mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 233.7 | 505.9 | 1.37 | 5.05 | TV |
| `sgc-b2-10` | `swath.c07a24197a7b.s2` | JG1@256 | swath | 0.3.2 | parquet-sorted | 256 | gc_collector=g1, jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 210.2 | 1140.2 | 1.88 | 7.89 | TV |
| `sgc-b2-11` | `swath-go.c8001cbae2b1.s2` | GO512@64 | swath-go | main-8c8f870 | parquet-sorted | 64 | sort_buffer=512mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 238.0 | 616.8 | 1.37 | 5.20 | TV |
| `sgc-b2-12` | `swath-go.5f0e2acdc882.s2` | GN512@256 | swath-go | list-parser-cpu-c54179e | parquet-sorted | 256 | sort_buffer=512mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 95.3 | 619.7 | 2.09 | 6.71 | TV |
| `sgc-b3-01` | `swath-go.6038850c33c1.s3` | GO512@256/4 | swath-go | main-8c8f870 | parquet-sorted | 256 | sort_buffer=512mib | 32-33,72-73 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 210.6 | 771.0 | 2.21 | 6.62 | TV |
| `sgc-b3-02` | `swath.c07a24197a7b.s3` | JG1@256 | swath | 0.3.2 | parquet-sorted | 256 | gc_collector=g1, jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 202.5 | 1114.9 | 1.84 | 8.00 | TV |
| `sgc-b3-03` | `swath-go.5f0e2acdc882.s3` | GN512@256 | swath-go | list-parser-cpu-c54179e | parquet-sorted | 256 | sort_buffer=512mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 99.1 | 651.0 | 2.02 | 6.65 | TV |
| `sgc-b3-04` | `swath-go.22e636276e1e.s3` | GO512@256 | swath-go | main-8c8f870 | parquet-sorted | 256 | sort_buffer=512mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 107.5 | 759.5 | 2.04 | 6.74 | TV |
| `sgc-b3-05` | `swath-go.20d179d1bae1.s3` | GN128@64 | swath-go | list-parser-cpu-c54179e | parquet-sorted | 64 | sort_buffer=128mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 231.7 | 542.4 | 0.56 | 4.18 | TV |
| `sgc-b3-06` | `swath-go.c8001cbae2b1.s3` | GO512@64 | swath-go | main-8c8f870 | parquet-sorted | 64 | sort_buffer=512mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 233.5 | 520.0 | 1.40 | 5.11 | TV |
| `sgc-b3-07` | `swath-go.6eab8d82a351.s3` | GN512@64 | swath-go | list-parser-cpu-c54179e | parquet-sorted | 64 | sort_buffer=512mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 233.1 | 508.6 | 1.35 | 4.97 | TV |
| `sgc-b3-08` | `swath-go.7d43994649c5.s3` | GN128@256 | swath-go | list-parser-cpu-c54179e | parquet-sorted | 256 | sort_buffer=128mib | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 98.0 | 651.3 | 1.10 | 6.69 | TV |
| `sgc-b3-09` | `swath.7c336967cef3.s3` | JG1@64 | swath | 0.3.2 | parquet-sorted | 64 | gc_collector=g1, jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 346.8 | 1204.2 | 1.39 | 7.77 | TV |
| `sgc-b3-10` | `swath-go.e25d0f83d0f2.s3` | GN512@256/4 | swath-go | list-parser-cpu-c54179e | parquet-sorted | 256 | sort_buffer=512mib | 32-33,72-73 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 162.5 | 591.0 | 2.13 | 6.67 | TV |
| `sgc-b3-11` | `swath.77058199c4c0.s3` | JPAR@64 | swath | 0.3.2 | parquet-sorted | 64 | gc_collector=parallel, jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 337.1 | 1076.9 | 4.31 | 8.00 | TV |
| `sgc-b3-12` | `swath.fdd03b509775.s3` | JPAR@256 | swath | 0.3.2 | parquet-sorted | 256 | gc_collector=parallel, jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 212.4 | 1157.4 | 4.33 | 8.00 | TV |

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
