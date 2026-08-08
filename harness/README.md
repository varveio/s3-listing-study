# Harness

The active subject lifecycle is the stdlib-first Python package at
`src/s3_listing_study/attempt/`. It is the only implementation of process
execution, byte capture, timing, timeout cleanup, and finalization for new
attempts.

The first slice is deliberately local and small. It accepts a typed logical
listing request, resolves it through the selected in-image tool driver, starts
the resulting argv without a shell, captures stdout and stderr as bytes, and commits one
attempt directory containing exactly:

```text
result.json
stdout.raw.gz
stderr.raw.gz
```

`result.json` is written atomically and last. Tool nonzero exits, signals, and
clean timeouts are recorded outcomes; they are not runner failures. The timer
uses `time.monotonic_ns()` around subject execution and capture only, before
credential-shape scanning of the complete opaque raw streams, deterministic
gzip, and result finalization. A flagged stream or scanner error is a harness
failure: the runner exits 2 and publishes none of the three artifacts. A clean
scan is recorded in `result.json`.

The runner opens and captures the subject's raw stdout/stderr byte streams
inside the derived image. Docker json-file logs, `docker logs`, Batch logs, and
other scheduler text streams are diagnostics only for new attempts; they are
not the listing-data channel and cannot stand in for `stdout.raw.gz` /
`stderr.raw.gz`.

The derived image fixes this zipapp entrypoint; schedulers append only the
logical request arguments and never replace it:

```sh
/usr/bin/python3 -I /opt/s3-listing-study/attempt.pyz \
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
the current s4cmd adapter contract accepts `1..8` and defaults to `4`. AWS CLI
is the only current derived-image registration. The selected tool's bundled
`command.py` resolves complete subject argv inside the image through the typed
driver API; there is no raw argv escape hatch. Adapters never execute or time
the subject. Tool-specific image
packaging uses one shared recipe; its current compatible-interpreter constraint
is documented in
[`derived-image/README.md`](derived-image/README.md). Tool-specific subject
digest and workdir inputs remain capsule-owned and are selected through
`s3-listing-study build-derived-image --tool SLUG --tag TAG`, never free build
arguments. Each result records the validated canonical
`adapter_bundle_sha256` as its sole adapter identity.

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

`s3_listing_study.verify` continues to audit those receipt-bound streams against
their recorded registry and manifest. The offline union regression suite is
`harness/tests/run-regressions.sh`; the host security regression suite is
`harness/tests/runner-security-regressions.sh`.

Campaign scheduling, cloud execution, normalization, Parquet conversion,
comparison, upload, and aggregation are outside this first attempt-engine
slice. None may introduce a second subject lifecycle or timing implementation.
