# Swath-Go at the provisional pin `7b05605`: release replay and live S3 receipt (2026-11-07)

Varve maintains this study and builds Swath and Swath-Go. These are
diagnostic rows from the capsule's earlier pin, not a calibrated benchmark or a
tool ranking. The capsule has since moved to a later pin whose command line and
products differ; these rows describe the `7b05605` bundle only.

Twenty-three attempts in eight ledger groups (scale-study ledger, suite
`scale-study`), submitted 2026-11-07 11:05 to 11:45 UTC on us-east1 Spot.
Twenty-one SUCCEEDED with exit 0 and the fixture's (or, live, the bucket's)
row count. **Two were CANCELLED by the operator** (`swath-go-live-cogs-1`,
live `s3://sentinel-cogs` at 64 and 256 workers, cancelled 13:27 UTC): no
result marker exists, the Batch jobs were deleted by the cancel, and nothing
about their runtime behaviour is recorded or claimed.

**Harness availability.** `2479097ea6f428ffe4480efe0f41079f74cf638c` is published as tag `receipts/swath-go-pin-7b05605-20261107`.

- Harness: `2479097ea6f428ffe4480efe0f41079f74cf638c` (on
  `capsule/swath-go-v2`, PR #36), toolbox
  `benchmark-toolbox@sha256:78bd9762ea63bdf353fb4261ca035539fe006c8050043128425d368ede224112`,
  image set `c4ef060d2a54a42dee7e451b452c2d6acb0a08f0e65488bf39e2130860b18750`,
  platform `ac1531ad2ad180658a761aeae62c82c51103a94bd1981ea511917ef0e62fbf35`.
- Swath-Go: tool version `provisional-7b05605`, source archive of
  varveio/swath `7b05605444766de4e7a736db0bc410409af47429` (head of
  varveio/swath#255 at the time; not on swath `main`), archive SHA-256
  `ffa2a7493cfad80fcc680eddbe2389449375c429aa379bbcdf4046c1c5cdeb69`, tool
  build `15ae352315a2f4ffcbf7a8853cae1b3d326c7131fd9d9f5ab0ed448c0c08785d`,
  adapter bundle `3a21555fc322cd0122988a67fed3247bcebe276df24b3a43fd32f97bc21f9e2f`,
  tool slice `271283d9fcf1e41800d6a4a01ec3abb8f46b0c323fb7d880db04a42c765bba70`.
- Swath (Java) 0.3.2: `ghcr.io/varveio/swath` image
  `sha256:0bbc96c10b4b63d184cce76734679dd4a2f54a1c81c7c94c1000f7114eab8e43`,
  tool build `c28b5c8bb5ca95cdca56191fe18ad2f147689a642e9a9b7dd66ef960fe7725d7`,
  adapter bundle `f657a7c498f0…` (full hash in `attempts.jsonl`).
- Replay: the release Java replay instrument, unchanged: images
  `ghcr.io/varveio/swath-replay@sha256:3955f242f25294d3ebc6d63d3cf23e2a2694f7f1c9a5334dc6ec1054b0a69097`
  (FourCast, IDC, NARA, NBM, changesets) and
  `swath-replay@sha256:5797514c7599c795197e53f4e55651829fca7c1630c351242407690bff9881ad`
  (blockchain), release fixtures, deadlines, warm-up and allocations (per-case
  documents in the group files). IDC has no latency model (NA).
- Live S3: `swath-go-live-rc-1` listed `s3://real-changesets` at 64 workers on
  n4-highcpu-32 with products retained (manifest and part hashes in
  `retained-products.json`); 200 GB boot disk for the live groups, 100 GB for
  replay.
- Single repetition per cell. No content comparison beyond row counts.

Cost (`cost-observed.json`): $1.2582 by `runDuration`, $1.6233
create-to-terminal for the 21 jobs the provider still lists. The two cancelled
jobs have no provider record; their ledger recorded-to-settled intervals
(2.37 h and 1.70 h on n4-highcpu-32) bound them above at $2.9474 at the same
list rate.

`retained-products.json`: manifest SHA-256, manifest part entries and GCS
object MD5/CRC32C for the retained live product (3 parts, 0 mismatches
between manifest and objects).

## Record

| group | attempt | tool | version | mode | c | settings | subject cpuset | machine | state | exit | rows | wall s | CPU s | max RSS GiB | cgroup peak GiB | timing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `swath-go-nara-1950-census-1` | `swath-go.1e68489008c5.s1` | swath-go | provisional-7b05605 | parquet-sorted | 64 | - | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 13,540,310 | 26.8 | 45.1 | 0.46 | 1.98 | IE |
| `swath-go-nara-1950-census-1` | `swath-go.dd9186c83853.s1` | swath-go | provisional-7b05605 | tsv | 64 | - | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 13,540,310 | 35.8 | 47.8 | 0.41 | 2.33 | IE |
| `swath-go-nara-1950-census-1` | `swath.2acc474986d3.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 13,540,310 | 78.9 | 111.5 | 0.62 | 1.29 | CF |
| `swath-go-noaa-nws-fourcastnetgfs-pds-1` | `swath-go.80accc514a76.s1` | swath-go | provisional-7b05605 | parquet-sorted | 64 | - | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 4,081,170 | 12.2 | 16.7 | 0.41 | 0.67 | IE |
| `swath-go-noaa-nws-fourcastnetgfs-pds-1` | `swath-go.f773dff17176.s1` | swath-go | provisional-7b05605 | tsv | 64 | - | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 4,081,170 | 15.1 | 16.6 | 0.39 | 0.96 | IE |
| `swath-go-noaa-nws-fourcastnetgfs-pds-1` | `swath.66fe952ba987.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 4,081,170 | 47.6 | 47.9 | 0.37 | 0.55 | IE |
| `swath-go-real-changesets-1` | `swath-go.27479f9f6ea6.s1` | swath-go | provisional-7b05605 | parquet-sorted | 64 | - | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 13,868,442 | 33.8 | 37.3 | 0.39 | 1.27 | IE |
| `swath-go-real-changesets-1` | `swath-go.284df7e8a6ed.s1` | swath-go | provisional-7b05605 | tsv | 64 | - | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 13,868,442 | 39.9 | 32.8 | 0.33 | 1.38 | IE |
| `swath-go-real-changesets-1` | `swath.cebbe320f3ad.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 13,868,442 | 63.1 | 87.7 | 0.68 | 1.32 | TV |
| `swath-go-noaa-nbm-grib2-pds-1` | `swath-go.6a71f007adb7.s1` | swath-go | provisional-7b05605 | parquet-sorted | 64 | - | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 66,405,936 | 105.3 | 233.8 | 0.48 | 3.12 | CF |
| `swath-go-noaa-nbm-grib2-pds-1` | `swath-go.d28e1fcb1334.s1` | swath-go | provisional-7b05605 | tsv | 64 | - | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 66,405,936 | 112.4 | 208.5 | 0.37 | 8.00 | CF |
| `swath-go-noaa-nbm-grib2-pds-1` | `swath.eac26552f657.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 10-13,26-29 | hc-32 | SUCCEEDED | 0 | 66,405,936 | 159.0 | 354.3 | 1.12 | 4.24 | CF |
| `swath-go-idc-open-data-1` | `swath-go.e6212e6f7aff.s1` | swath-go | provisional-7b05605 | parquet-sorted | 64 | - | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 56,311,145 | 81.8 | 263.6 | 0.59 | 7.57 | NA |
| `swath-go-idc-open-data-1` | `swath.cb395f3e542c.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 4-6,12-14 | hc-16 | SUCCEEDED | 0 | 56,311,145 | 125.1 | 428.8 | 1.06 | 6.28 | NA |
| `swath-go-aws-public-blockchain-1` | `swath-go.770e8457adf2.s1` | swath-go | provisional-7b05605 | tsv-zstd | 256 | - | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 283.8 | 716.1 | 1.04 | 8.02 | CF |
| `swath-go-aws-public-blockchain-1` | `swath-go.12e06554532f.s1` | swath-go | provisional-7b05605 | parquet-sorted | 256 | - | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 170.1 | 707.4 | 1.12 | 8.03 | CF |
| `swath-go-aws-public-blockchain-1` | `swath-go.ac47460bdf7b.s1` | swath-go | provisional-7b05605 | parquet-sorted | 64 | - | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 257.7 | 547.0 | 0.57 | 8.01 | CF |
| `swath-go-aws-public-blockchain-1` | `swath.8096eafd2244.s1` | swath | 0.3.2 | parquet-sorted | 256 | jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 217.9 | 1206.3 | 1.93 | 7.90 | CF |
| `swath-go-aws-public-blockchain-1` | `swath.9cd087042de8.s1` | swath | 0.3.2 | parquet-sorted | 64 | jvm_max_heap=4g, sort_merge_parallelism=8 | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 324.3 | 972.3 | 1.55 | 7.92 | CF |
| `swath-go-aws-public-blockchain-1` | `swath.6ee941ec8c3d.s1` | swath | 0.3.2 | tsv-zstd | 256 | jvm_max_heap=4g, part_rotation_interval=0, part_rotation_max_rows=0, text_part_size=1gb, text_writers=3, writeback_size=32mb | 32-35,72-75 | hc-80 | SUCCEEDED | 0 | 143,008,674 | 153.8 | 857.3 | 1.99 | 5.88 | CF |
| `swath-go-live-cogs-1` | `swath-go.03a72c2faf2e.s1` | swath-go | - | parquet-sorted | 64 | - | - | hc-32 | CANCELLED | - | - | - | - | - | - | - |
| `swath-go-live-cogs-1` | `swath-go.4753553039c7.s1` | swath-go | - | parquet-sorted | 256 | - | - | hc-32 | CANCELLED | - | - | - | - | - | - | - |
| `swath-go-live-rc-1` | `swath-go.a821996ad2bd.s1` | swath-go | provisional-7b05605 | parquet-sorted | 64 | - | - | hc-32 | SUCCEEDED | 0 | 13,868,442 | 34.0 | 43.1 | 0.39 | 1.30 | - |

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
