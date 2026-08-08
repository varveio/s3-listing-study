# Observation — four listing modes through the rewritten adapter

NOT A RECEIPT. The runner-security profile was not provisioned, so
harness/smoke-run.sh was not used and harness/verify-listing.sh could not
run (the reference manifest is absent from this box). Recorded as a direct
container observation.

HISTORICAL SUMMARY ONLY. The exact expanded per-mode commands and raw normalized
outputs were not retained, so the rows and hashes below are not independently
auditable and support no canonical runtime or cross-mode-agreement claim. Re-run
these modes under the wrapper before treating them as exercised coverage.

Date (UTC)   : 2026-08-02T21:10:41Z
Image        : ghcr.io/varveio/swath@sha256:ef1aca9ab473f133acceb5730ff88d52abaaa89e773801cdb62deff51f9909b0 (arm64 child, native)
Tool version : swath 0.2.0 (cef8ec24a74f)
Box          : aarch64, 8 cores, 31 GB, Linux 7.0.5-orbstack, Docker 29.4.0
Scope        : s3://noaa-normals-pds/normals-hourly/  (us-east-1, anonymous)
Argv source  : tools/swath/adapter/run.sh <mode> <bucket> <region> <prefix>
Normalizer   : tools/swath/adapter/normalize.sh <mode>

| Mode | Exit | Normalized rows | Fields/row | Raw sha256 |
| --- | --- | --- | --- | --- |
| `recursive-tsv` | 0 | 2549 | 5  | `0b5f5806f04ecd0b4a6c089e6639a8ce480fcde22ab097b51f652f7897bbca1d` |
| `recursive-jsonl` | 0 | 2549 | 5  | `34f0c8d04b114f4582b64535e27d91afb64e7c775cef92ea029fecbc000f69cc` |
| `recursive-table` | 0 | 2549 | 5  | `47c5dc5a1305fd89df05585ee66113329bfd01b7537d9967d021002a7458f27e` |
| `seed-none` | 0 | 2549 | 5  | `4cbcffb85bf9a153346a7a77855a3434a02dfaeeb876b86c209c1c2158e7b3dd` |
