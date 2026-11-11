# Content equality: Swath-Go `8c8f870` vs Swath 0.3.2, FourCast sorted Parquet (2026-11-11)

Varve maintains this study and builds Swath and Swath-Go. A diagnostic
content check, not a benchmark.

Two attempts in group `sgo-eq-fourcast` (scale-study ledger), created 07:52
UTC, terminal 07:54 UTC, both SUCCEEDED with exit 0 and 4,081,170 rows, run
with products retained. They are second attempts (`.s2`) of the
`sgo-goreplay-fourcast` cases, repeated with retention on.

- Harness `c25a6e7d90c9a9c0c87e10da121cffc24dc51db0` on the unmerged branch
  `scratch/swath-go-on-go-replay` (see `swath-go-on-go-replay-cloud-20261111`),
  same toolbox `d5503f58…`, same Go replay instrument and FourCast fixture.
- `retained-products.json`: listing URIs, manifest SHA-256, manifest part
  entries (Swath-Go: SeekParquet part SHA-256; Swath: part MD5) and GCS object
  MD5/CRC32C. Both manifests agree with their objects (0 mismatches).

## Comparator and output

The comparison was a direct DuckDB session over local copies of the two
retained datasets, not a committed script, on the nine S3 columns both tools
write (`key, size, last_modified, etag, storage_class, owner_id,
owner_display_name, checksum_algorithm, checksum_type`). The pinned
`go/bench/diffds.py` could not be used: it reads SeekParquet/swath-dataset
manifests only, and Swath 0.3.2's manifest has no `schema_id`. DuckDB version
was not recorded. Output as recorded by the operator:

- row count: 4,081,170 in both;
- multiset difference (`EXCEPT ALL`) in each direction: 0 and 0 rows;
- positional comparison (row n against row n, `IS DISTINCT FROM` per column):
  0 mismatches;
- Swath-Go key order strictly ascending by unsigned bytes: 0 violations;
- Swath-only columns: `version_id` all NULL, `is_delete_marker` never true,
  `row_type` `OBJECT` on every row.

Scope: exact equality of the nine shared columns, including microsecond
timestamps, on this fixture and these two products. It says nothing about
other fixtures, concurrency or outputs.

Cost: $0.0128 by `runDuration`, $0.0232 create-to-terminal.

## Record

| group | attempt | tool | version | mode | c | settings | subject cpuset | machine | state | exit | rows | wall s | CPU s | max RSS GiB | cgroup peak GiB | timing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sgo-eq-fourcast` | `swath-go.a7eba8b1b911.s2` | swath-go | main-8c8f870 | parquet-sorted | 64 | - | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 4,081,170 | 9.7 | 19.5 | 0.97 | 1.05 | IE |
| `sgo-eq-fourcast` | `swath.a0995929aac2.s2` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 4,081,170 | 55.0 | 56.1 | 0.34 | 0.52 | TV |

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
- `cost-observed.json`: each job's provider `runDuration` and
  create-to-terminal span, priced at the published us-east1 N4 Spot list rates
  used by the Go replay qualification receipts ($1.80688/h n4-highcpu-80,
  $0.722752/h n4-highcpu-32, $0.361376/h n4-highcpu-16; Google Spot VM pricing,
  us-east1 table retrieved 2026-11-10). Execution-based estimates at list
  rates, not an invoice; compute only.

Export: `benchmark.receipt` and `benchmark.report` (for the timing
assessment and declared cpusets) imported from study commit
`73fe22707cbac030c327ec21a08444bb5ef2c369` on the unmerged branch
`scratch/swath-go-on-go-replay`, read-only against the scale-study ledger and
the results bucket. That commit is used because it contains both this
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
