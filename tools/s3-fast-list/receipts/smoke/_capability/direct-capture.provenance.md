# Direct-capture provenance — s3-fast-list `list` mode

The standard `verify-listing.sh` path was **BLOCKED** for every smoke run (the
wrapper's `docker logs` capture is not binary-safe and corrupts this tool's
binary Parquet — see `HARNESS-INCOMPATIBILITY.txt`). Listing correctness
therefore rests on an **[OBS] manifest-diff** against faithful direct captures
(`docker run > file`), **not** a certified verifier PASS.

This record states exactly what the direct-capture payloads are bound to by
evidence, and — as required by the house evidence rules — what is **not
recorded** rather than reconstructing it. Nothing here is fabricated.

## What IS bound by evidence

| Payload | sha256 (matches `direct-capture.sha256`) | bytes | Parquet rows | manifest keys |
| --- | --- | --- | --- | --- |
| `list.full.direct.parquet` | `3425c398…` | 4,283,829 | 148,917 | 148,917 |
| `list.normals-hourly.direct.parquet` | `0fc70adb…` | 69,439 | 2,549 | 2,549 |
| `list.normals-monthly-1991-2020.direct.parquet` | `66b66b9a…` | 486,674 | 15,625 | 15,625 |
| `list.normals-annualseasonal-1981-2010-access.direct.parquet` | `aeb6493c…` | 307,391 | 9,839 | 9,839 |

- **Payload integrity**: each file's sha256 matches the value recorded in
  `direct-capture.sha256`, so the diffed bytes are frozen.
- **Valid parse**: each file parses as a valid Parquet (unlike the wrapper's
  corrupted `docker logs` payload); row counts read out via duckdb and equal the
  registry manifest counts above.
- **Byte-size cross-check**: `HARNESS-INCOMPATIBILITY.txt` independently records
  the full-bucket direct capture as 4,283,829 bytes and the hourly as 69,439
  bytes — both equal the on-disk sizes above.
- **File mtime**: all four captured 2026-07-17 ~12:08 UTC, i.e. after the wrapper
  smoke runs (12:01–12:06). The `_capability/debug-requestshape.stderr.txt`
  (fast-list v1.1.0, `normals-hourly/` scope) is from the same 12:08 window.
- **Subject / environment (from the wrapper receipts the direct runs were meant
  to replicate)**: image `s3-fast-list@sha256:6246ee51…` (arm64), auth
  `anonymous`, bucket `noaa-normals-pds`/`us-east-1`, box arm64 `gcp:us-east1-b`.
  Manifest sha256 `c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb`.

## What is NOT recorded (stated, not reconstructed)

- **The exact direct-run `docker run` command line** — the shell history file
  (`~/.bash_history`, last written 09:58 UTC) predates the 12:08 captures, so the
  direct command was not persisted. "Identical invocation to the wrapper argv" is
  the *intent* stated in the report; it is **not independently logged or proven**.
- **The direct runs' exit codes and per-run wall-clock** — no `run.meta` was
  emitted for the direct captures; only the wrapper runs have `run.meta`.
- **A binding of these payloads to the image digest at direct-run time** — the
  digest above is carried over from the wrapper receipts, not re-recorded at the
  direct run.
- **A verification transcript** — the manifest-diff was run out-of-band; its
  console output was not captured to a receipt.

## Net

The direct captures are cryptographically frozen, parse validly, and match the
manifest on key count and on the per-field diff reported in the receipts. That
supports the **[OBS] manifest-diff** claim. It does **not** amount to a certified
verifier verdict, and the "identical invocation" assertion is unproven — closing
that gap needs a binary-safe capture channel run through the standard verifier in
the benchmark phase (harness gap, routed to the orchestrator).
