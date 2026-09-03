# Observation — eight adapter modes round-tripped against the v0.3.2 image

NOT A RECEIPT. The capsule-authoring adapter verification loop ("the adapter round-trips real
output"), run as direct `docker run` invocations on the study maintainer's workstation, not through
the benchmark worker: no attempt record, no secret-scan pipeline, no verifier verdict, no reference
manifest. It establishes that every mode `adapter/command.py` declares parses and completes on the
0.3.2 image, and that `adapter/normalize.py` reads each mode's native output into the five-field
contract with the same key set. It is not a completeness check: count and cross-mode agreement
leave a substituted key undetected. The 0.3.1 observation of 2026-09-02 is kept beside this one.

Date (UTC)   : 2026-09-03T09:10:38Z
Image        : ghcr.io/varveio/swath@sha256:0bbc96c10b4b63d184cce76734679dd4a2f54a1c81c7c94c1000f7114eab8e43 (index; linux/amd64 child, native)
Tool version : swath 0.3.2 | Built by Varve: https://varve.io | Source: https://github.com/varveio/swath | Commit: acf0d509f238 | Runtime: 25.0.4+7-LTS
Box          : x86_64, 8 cores, Linux 6.17.0-1022-gcp, Docker version 29.7.2, build a7dcaa6
Scope        : s3://noaa-normals-pds/normals-hourly/  (us-east-1, anonymous)
Argv source  : `adapter/command.py` `build_command(CommandRequest(mode, "noaa-normals-pds", "us-east-1", prefix="normals-hourly/", tool="swath", sink_dir="/sink", visible_memory_gb=2.0))`; no `concurrency` config, so the adapter rendered its declared default of 64
Environment  : `build_env` (`JAVA_TOOL_OPTIONS`), `AWS_EC2_METADATA_DISABLED=true`, `HOME=/nonexistent`, `TZ=UTC`; container run `--cap-drop ALL --security-opt no-new-privileges:true --user <uid>:<gid>`, `/sink` bind-mounted for the dataset modes, the image's Java invoked as `--entrypoint /opt/java/openjdk/bin/java -jar /opt/swath/swath.jar` (the toolbox's registered executable)
Normalizer   : `uv run python tools/swath/adapter/normalize.py <mode> normals-hourly/ --input <stdout>` or `--dataset <sink>/listing`
Digest recipe: key-set sha256 = sha256 of the sorted distinct keys joined by LF with a trailing LF; normalized sha256 = sha256 of the normalizer's stdout bytes; `raw` = sha256 of the subject's stdout for stream modes, the part list for dataset modes (recorded in `payload.hashes`)

The registered figure for this prefix is 2,549 keys (`docs/smoke-bucket.md`).
Every mode normalized to 2,549 rows with 2,549 distinct keys and one identical key-set digest.

| Mode | Argv after `list` prefix | Exit | Wall | Rows | Distinct keys | Key-set sha256 (16) | Normalized sha256 (16) | Raw |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `recursive-jsonl` | `-v --color never list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request --concurrency 64 --checkpoint none --format jsonl` | 0 | 4.1 s | 2549 | 2549 | `e71289eb3c4e0dc0` | `5f5b99c3aeaaed08` | 668830 B |
| `recursive-parquet` | `-v --color never list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request --concurrency 64 --checkpoint none --format parquet -o /sink/listing --report /sink/listing/_swath_summary.json` | 0 | 4.4 s | 2549 | 2549 | `e71289eb3c4e0dc0` | `e5bda5d3380636aa` | 3 parts |
| `recursive-parquet-sorted` | `-v --color never list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request --concurrency 64 --checkpoint auto --format parquet -o /sink/listing --report /sink/listing/_swath_summary.json --sort --tune sort.ignore-disk-check=on` | 0 | 4.7 s | 2549 | 2549 | `e71289eb3c4e0dc0` | `9a51af67862b4b52` | 1 parts |
| `recursive-table` | `-v --color never list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request --concurrency 64 --checkpoint none --format table` | 0 | 4.1 s | 2549 | 2549 | `e71289eb3c4e0dc0` | `1c1923ada5a58d9c` | 225312 B |
| `recursive-tsv` | `-v --color never list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request --concurrency 64 --checkpoint none --format tsv` | 0 | 4.0 s | 2549 | 2549 | `e71289eb3c4e0dc0` | `538a027aad712ee5` | 327315 B |
| `recursive-tsv-dataset` | `-v --color never list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request --concurrency 64 --checkpoint none --format tsv --output-type dir -o /sink/listing --report /sink/listing/_swath_summary.json --text-writers 3 --compression none` | 0 | 4.2 s | 2549 | 2549 | `e71289eb3c4e0dc0` | `2dab22c90c56af13` | 3 parts |
| `recursive-tsv-zstd` | `-v --color never list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request --concurrency 64 --checkpoint none --format tsv --output-type dir -o /sink/listing --report /sink/listing/_swath_summary.json --text-writers 3 --compression zstd` | 0 | 4.2 s | 2549 | 2549 | `e71289eb3c4e0dc0` | `fd691e105c797edd` | 3 parts |
| `seed-none` | `-v --color never list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request --concurrency 64 --checkpoint none --format tsv --tune seed.mode=none` | 0 | 2.6 s | 2549 | 2549 | `e71289eb3c4e0dc0` | `9a51af67862b4b52` | 327315 B |

Notes:

- Wall times are one sample each on a shared workstation and are not measurements of anything.
- The two retained stderr streams carry Swath's own `list_run_summary` counters, which at 0.3.2
  include the `channel_receive` client-cost span.
