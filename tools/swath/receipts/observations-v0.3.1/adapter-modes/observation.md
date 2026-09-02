# Observation — eight adapter modes round-tripped against the v0.3.1 image

NOT A RECEIPT. This is the capsule-authoring adapter verification loop
("the adapter round-trips real output"), run as direct `docker run`
invocations on the study maintainer's workstation, not through the benchmark
worker: no attempt record, no secret-scan pipeline, no verifier verdict, no
reference manifest. It establishes that every mode `adapter/command.py`
declares parses and completes on the 0.3.1 image, and that
`adapter/normalize.py` reads each mode's native output into the five-field
contract with the same key set. It is not a completeness check: count and
cross-mode agreement leave a substituted key undetected.

Date (UTC)   : 2026-09-02T14:09:50Z
Image        : ghcr.io/varveio/swath@sha256:776e788200a1e70f30206897303a34e4faabd56c591e1c9562277677085c4f60 (index; linux/amd64 child, native)
Tool version : swath 0.3.1 | Built by Varve: https://varve.io | Source: https://github.com/varveio/swath | Commit: 7b9a5e2fba04 | Runtime: 25.0.4+7-LTS
Box          : x86_64, 8 cores, 15 GB, Linux 6.17.0-1022-gcp, Docker version 29.7.2, build a7dcaa6
Scope        : s3://noaa-normals-pds/normals-hourly/  (us-east-1, anonymous)
Argv source  : `adapter/command.py` `build_command(CommandRequest(mode, "noaa-normals-pds", "us-east-1", prefix="normals-hourly/", tool="swath", sink_dir="/sink", visible_memory_gb=2.0))`; no `concurrency` config, so the adapter rendered its declared default of 64
Environment  : `JAVA_TOOL_OPTIONS=-XX:MaxRAMPercentage=75` from `build_env`; `AWS_EC2_METADATA_DISABLED=true`, `HOME=/nonexistent`, `TZ=UTC`; container run `--cap-drop ALL --security-opt no-new-privileges:true`, `/sink` bind-mounted for the dataset modes
Normalizer   : `uv run python tools/swath/adapter/normalize.py <mode> normals-hourly/ --input <stdout>` or `--dataset <sink>`

The registered figure for this prefix is 2,549 keys (`docs/smoke-bucket.md`).
Every mode normalized to 2,549 rows of five fields with 2,549 distinct keys and
one identical key-set digest. Full digests are in `payload.hashes`; the two
retained stderr streams carry Swath's own `list_run_summary` counters.

| Mode | Argv after `list` prefix | Exit | Wall | Rows | Distinct keys | Key-set sha256 (16) | Normalized sha256 (16) | Raw |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `recursive-jsonl` | `-v --color never list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request --concurrency 64 --checkpoint none --format jsonl` | 0 | 4.5 s | 2549 | 2549 | `2830c2eed8fc980c` | `c9a2cdae568a4a62` | 668830 B |
| `recursive-parquet` | `-v --color never list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request --concurrency 64 --checkpoint none --format parquet -o /sink/listing` | 0 | 4.9 s | 2549 | 2549 | `2830c2eed8fc980c` | `c9a2cdae568a4a62` | 3 parts |
| `recursive-parquet-sorted` | `-v --color never list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request --concurrency 64 --checkpoint auto --format parquet -o /sink/listing --sort --tune sort.ignore-disk-check=on` | 0 | 4.5 s | 2549 | 2549 | `2830c2eed8fc980c` | `c9a2cdae568a4a62` | 1 parts |
| `recursive-table` | `-v --color never list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request --concurrency 64 --checkpoint none --format table` | 0 | 4.2 s | 2549 | 2549 | `2830c2eed8fc980c` | `6f043f68e62e277d` | 225312 B |
| `recursive-tsv` | `-v --color never list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request --concurrency 64 --checkpoint none --format tsv` | 0 | 4.0 s | 2549 | 2549 | `2830c2eed8fc980c` | `c9a2cdae568a4a62` | 327315 B |
| `recursive-tsv-dataset` | `-v --color never list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request --concurrency 64 --checkpoint none --format tsv --output-type dir -o /sink/listing --text-writers 3 --compression none` | 0 | 4.4 s | 2549 | 2549 | `2830c2eed8fc980c` | `c9a2cdae568a4a62` | 3 parts |
| `recursive-tsv-zstd` | `-v --color never list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request --concurrency 64 --checkpoint none --format tsv --output-type dir -o /sink/listing --text-writers 3 --compression zstd` | 0 | 4.0 s | 2549 | 2549 | `2830c2eed8fc980c` | `c9a2cdae568a4a62` | 3 parts |
| `seed-none` | `-v --color never list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request --concurrency 64 --checkpoint none --format tsv --tune seed.mode=none` | 0 | 2.4 s | 2549 | 2549 | `2830c2eed8fc980c` | `c9a2cdae568a4a62` | 327315 B |

Notes:

- `recursive-table` first normalized to zero rows: at 0.3.1 every text sink
  renders `last_modified` with a millisecond fraction (`2026-07-22T12:54:13.000Z`),
  and the table query did not strip it. The normalizer's table query was
  changed to strip fractions exactly as the TSV and JSONL queries already did;
  the row above is from the corrected normalizer over the same raw stream. The
  table column offsets (size 1-14, instant 17-40, key from 43) were unchanged
  because the 24-character instant fills the column the 0.2.0 formatter padded.
- The sorted run published `data/part-00000.parquet` (zero-based) with
  `manifest.json`, `symlink.txt`, `.swath-state.json`, `_swath_summary.json`
  and `_SUCCESS`; the direct Parquet and text dataset runs published three
  writer-named parts (`part-w0-00000.*` …) plus the same sidecars, the text
  datasets with `manifest.json`.
- Wall times are one sample each on a shared workstation and are not
  measurements of anything.
