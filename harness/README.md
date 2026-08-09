# Harness

The active subject lifecycle is the stdlib-first Python package at
`src/s3_listing_study/attempt/`. It is the only implementation of process
execution, byte capture, timing, timeout cleanup, and finalization for new
attempts.

The first slice is deliberately local and small. It accepts a typed logical
listing request, resolves it through the selected in-image tool driver, starts
the resulting argv without a shell, captures stdout and stderr as bytes, and commits one
attempt directory containing:

```text
result.json
stdout.raw.gz
stderr.raw.gz
native/          only for a mode whose sink is a directory the tool writes itself
```

`result.json` is written atomically and last. Tool nonzero exits, signals, and
clean timeouts are recorded outcomes; they are not runner failures. The timer
uses `time.monotonic_ns()` around subject execution and capture only, before
credential-shape scanning of the complete opaque raw streams, deterministic
gzip, and result finalization. A flagged stream or scanner error is a harness
failure: the runner exits 2 and publishes none of the artifacts. A clean
scan is recorded in `result.json`.

`result.json` also carries a `resources` object — `peak_rss_kb`, `user_cpu_s`
and `system_cpu_s` from `getrusage(RUSAGE_CHILDREN)`, read immediately after the
reap that makes it valid, plus `peak_disk_delta_bytes` from a background poll of
filesystem usage against a pre-execution baseline. Both are the harness's own
measurement rather than anything a subject reports about itself. The RSS and CPU
figures are only the subject's because one process runs exactly one attempt, and
the disk figure is only the subject's because one attempt runs at a time on a
host; neither holds for a caller that reuses a process or runs attempts
concurrently. `resources` is additive within `schema_version: 1` rather than a
new version, so a reader must treat it as optional: attempts recorded before it
existed are schema 1 and do not carry it.

The runner opens and captures the subject's raw stdout/stderr byte streams
inside the derived image. Docker json-file logs, `docker logs`, Batch logs, and
other scheduler text streams are diagnostics only for new attempts; they are
not the listing-data channel and cannot stand in for `stdout.raw.gz` /
`stderr.raw.gz`.

The derived image fixes this zipapp entrypoint; schedulers append only the
logical request arguments and never replace it:

```sh
/opt/s3-listing-study/python/bin/python3 -I /opt/s3-listing-study/attempt.pyz \
  --output /output/attempt-id \
  --derived-image sha256:DERIVED_IMAGE_DIGEST \
  --tool aws-cli \
  --operation list \
  --mode s3api-v2-text \
  --bucket BUCKET \
  --region REGION \
  --prefix '' \
  --scope full
```

The scheduler passes only typed logical fields, including optional concurrency.
An explicit concurrency is accepted only by an adapter that declares support;
the current s4cmd adapter contract accepts `1..8` and defaults to `4`. All
eleven runnable subjects are registered; a tool is registered by
adding `tools/<tool>/build/image.json`. The selected tool's bundled `command.py`
resolves complete subject argv inside the image through the typed driver API;
there is no raw argv escape hatch. Adapters never execute or time the subject.

Tool-specific image packaging uses one shared recipe, documented in
[`derived-image/README.md`](derived-image/README.md). It runs the engine on a
pinned interpreter bound at build time, so a subject image is not required to
ship a Python of its own. Tool-specific subject digest, version, workdir, and
libc inputs remain capsule-owned and are selected through
`s3-listing-study build-derived-image --tool SLUG`, never free build arguments.
Each result records the validated canonical `adapter_bundle_sha256` as its sole
adapter identity, and the derived image's own digest as the image identity.

## Security boundary

Networked execution still requires the `s3-listing-study-v1` profile in
[`docs/operating/runner-security.md`](../docs/operating/runner-security.md).
The image scheduler must run `runner-security-check.sh` before starting a
networked subject. The in-image attempt engine provides defense in depth for
anonymous runs by replacing the ambient environment with a fixed, minimal
runtime environment and setting `AWS_EC2_METADATA_DISABLED=true`. The exact
child environment is recorded in `result.json`; credential/profile, endpoint,
proxy, trust-anchor, loader, Python, cloud-SDK, and arbitrary ambient variables
are not inherited. This does not replace the host identity, firewall, bridge,
digest-pin, and no-pull gate.

The remaining files under `harness/` provision and test that host-side security
boundary or stage orchestrator workspaces. They do not run or time subjects.

### Authenticated attempts

`--auth authenticated` runs a signed request. The credential reaches the engine
as one ambient variable, `S3_STUDY_AWS_CREDENTIAL`, holding the same
`KEY=VALUE` lines the study's secret already uses; the name is deliberately not
an `AWS_*` one, so no SDK can pick it up on its own. Only
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_SESSION_TOKEN` are
accepted, the first two required, with no unknown or duplicate keys.

It fails closed in both directions. `--auth authenticated` with the variable
unset is an error, and the variable being *set* during `--auth anonymous` is
equally an error: an anonymous receipt must never come from a process that had
credential material in its environment. The child environment stays a fixed
allowlist, `AWS_EC2_METADATA_DISABLED=true` stays set even when authenticated
so the explicit static credential is the only one in play, and `result.json`
records the two credential values as `<REDACTED>` — names visible, values
never persisted. Callers must forward the variable by name (`docker run -e
NAME`), never as `-e NAME=value`, which would put the secret in argv.

## After the attempt

Two commands run against an already-finalized attempt directory, as separate
processes, after the container has exited. Neither can touch `elapsed_ns`, and
neither is part of the subject lifecycle:

- `s3-listing-study collect-attempt --attempt-dir DIR --tool SLUG` writes
  `collected.json` next to `result.json`, holding the row count obtained by
  running that tool's own `adapter/normalize.py` as an unmodified subprocess.
  `--convert-parquet` additionally writes `normalized.parquet`; it is off by
  default because it is the expensive part.
- `s3-listing-study upload-attempt --attempt-dir DIR --destination gs://…/`
  copies the directory to a caller-supplied destination prefix. Every object is
  written create-only (`if_generation_match=0`) and `result.json` goes last, so
  a retry cannot overwrite a recorded attempt and the presence of `result.json`
  at the destination means the upload finished. A partially failed upload
  therefore cannot be resumed into the same prefix by design; the orchestrator
  gives the retry a new prefix.

`smoke-campaign.sh` builds and smoke-runs every registered tool through both
steps. It allocates the next unused `attempt-N` per mode and never deletes
anything under `receipts/`.

The current ARM64 AWS CLI runner image and a local diagnostic attempt have
succeeded as implementation checks. They are non-evidentiary and
non-comparative: they prove this slice of the runner/image plumbing can produce
the minimal machine artifacts, not that any tool claim should be promoted or
any benchmark result should be published.

## Historical receipts and verification

Committed smoke receipts predate the attempt engine and remain immutable audit
evidence. Their `receipt.md`, `run.meta`, raw payload conventions, parsers, and
verifier tests are retained. Current tool pages describe those records as
wrapper-era receipts without presenting the deleted wrapper as an execution
path.

Each of those receipts names the wrapper that produced it, at the path the
wrapper then occupied under `harness/`. That path no longer resolves, and the
line is left standing anyway: a receipt records what actually ran, so editing it
to name something that did not produce it would be a rewrite of evidence. The
wrapper remains reachable in git history, which is where a reader who needs it
should look. Consequently a mode directory can hold two unrelated shapes at
once — wrapper-era `receipt.md` / `run.meta` / `verify.md` / `stderr.txt` files
describing one run, and one or more `attempt-N/` directories from the engine
describing others. They are separate runs on separate hosts, frequently on
different architectures and against different scopes; nothing merges or
supersedes across the two, and a reader must take the scope and date from the
record in hand rather than from the directory it shares.

`s3_listing_study.verify` continues to audit those receipt-bound streams against
their recorded registry and manifest. The offline union regression suite is
`harness/tests/run-regressions.sh`; the host security regression suite is
`harness/tests/runner-security-regressions.sh`.

Campaign scheduling, cloud execution, normalization, Parquet conversion,
comparison, upload, and aggregation are outside this first attempt-engine
slice. None may introduce a second subject lifecycle or timing implementation.
