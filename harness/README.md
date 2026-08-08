# Harness

The active subject lifecycle is the stdlib-first Python package at
`src/s3_listing_study/attempt/`. It is the only implementation of process
execution, byte capture, timing, timeout cleanup, and finalization for new
attempts.

The first slice is deliberately local and small. It accepts a direct argv,
starts it without a shell, captures stdout and stderr as bytes, and commits one
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

Run the package directly when it is already installed in a derived image:

```sh
python -m s3_listing_study.attempt \
  --output /output/attempt-id \
  --tool aws-cli \
  --mode s3api-v2-text \
  --bucket BUCKET \
  --region REGION \
  --prefix '' \
  --scope full \
  --command-prefix /usr/local/bin/aws \
  -- s3api list-objects-v2 --bucket BUCKET --region REGION --no-sign-request \
  --query 'Contents[].[Key,Size,ETag,LastModified,StorageClass]' --output text
```

The scheduler obtains the arguments after `--` from the tool's NUL-delimited
`tools/<tool>/adapter/run.sh`. Adapters compile friendly parameters into argv;
they never execute or time the subject. The AWS CLI derived-image packaging
contract and its current self-contained-payload blocker are documented in
`images/aws-cli/README.md`.

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
verifier tests are retained. References in tool pages to
`harness/smoke-run.sh` describe the historical command that produced those
records; the shell runner itself is retired and is not an active execution
path.

`s3_listing_study.verify` continues to audit those receipt-bound streams against
their recorded registry and manifest. The offline union regression suite is
`harness/tests/run-regressions.sh`; the host security regression suite is
`harness/tests/runner-security-regressions.sh`.

Campaign scheduling, cloud execution, normalization, Parquet conversion,
comparison, upload, and aggregation are outside this first attempt-engine
slice. None may introduce a second subject lifecycle or timing implementation.
