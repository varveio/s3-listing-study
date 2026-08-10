# Harness

The active subject lifecycle is the stdlib-first Python package at
`src/s3_listing_study/worker/`. It is the only implementation of process
execution, byte capture, timing, timeout cleanup, and finalization for new
attempts.

The worker accepts a typed logical listing request, resolves it through the
selected in-image tool driver, starts the resulting argv without a shell,
captures stdout and stderr as bytes, and commits one attempt directory containing:

```text
result.json
stdout.raw.gz
stderr.raw.gz
native/          only for a mode whose sink is a directory the tool writes itself
```

`result.json` is written atomically and last. Tool nonzero exits, signals, and
clean timeouts are recorded outcomes; they are not runner failures. Inside the
worker container, the subject is a supervised subprocess tree, not another
container. The timer uses `time.monotonic_ns()` from immediately before launch
through reap of that tree. Credential-shape scanning of the complete opaque raw
streams, deterministic gzip, worker-side normalization and row counting, result
finalization, and GCS upload happen only after that clock stops. A flagged
stream or scanner error is a harness failure: the runner exits 2 and publishes
none of the artifacts. A clean scan is recorded in `result.json`.

In `schema_version: 2`, the result records UTC subject start/end timestamps and
observed worker facts (logical CPUs, host memory, and readable cgroup-v2
location/limit). Its `resources` object records
`rusage_children_max_child_peak_rss_kb`, `rusage_children_user_cpu_s`,
`rusage_children_system_cpu_s`, `whole_filesystem_peak_used_delta_bytes` plus its
sample path/interval, and `cgroup_v2_memory` snapshots before/after the subject
with OOM/OOM-kill deltas. The RSS field is the largest single child's peak, not
aggregate tree memory; cgroup memory is the whole container/task signal when
available. The filesystem delta is attributable only when one attempt uses the
host filesystem at a time.

The runner opens and captures the subject's raw stdout/stderr byte streams
inside the derived image. Docker json-file logs, `docker logs`, Batch logs, and
other scheduler text streams are diagnostics only for new attempts; they are
not the listing-data channel and cannot stand in for `stdout.raw.gz` /
`stderr.raw.gz`.

The derived image fixes this zipapp entrypoint; schedulers append only the
logical request arguments and never replace it. There is no public
`--attempt-id`: each worker-container execution mints its own UUID:

```sh
/opt/s3-listing-study/python/bin/python3 -I /opt/s3-listing-study/attempt.pyz \
  --output /output \
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

Networked execution uses the scheduler-specific profile in
[`docs/operating/runner-security.md`](../docs/operating/runner-security.md).
GCP Batch is cooperative: its metadata endpoint stays reachable because this
worker's uploader needs the task service-account token. The identity can create
but not read, replace, or delete results. Local Docker on the more-privileged
manager/runner host retains the `s3-listing-study-v1` bridge, firewall, and
metadata-denial gate; `runner-security-check.sh` applies there, not to Batch.
The gate is required before a local invocation can claim the strict evidence
profile. `smoke-campaign.sh` and `e2e-local.sh` are diagnostic paths that only
inspect the bridge; their outputs are not strict-profile evidence.

In both profiles the attempt engine replaces the subject child's ambient
environment with a fixed, minimal runtime environment and sets
`AWS_EC2_METADATA_DISABLED=true`. That setting prevents the S3 client subprocess
from discovering an AWS instance credential; it does not and must not prevent
the surrounding Batch worker from reading its GCP token for upload. The exact
child environment is recorded in `result.json`.

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

## After subject timing

The worker normalizes enough of the captured listing to compute the row count
after `elapsed_ns` has closed, then records that small summary directly in
`result.json`. It deterministically compresses the raw streams. Each execution
publishes one authoritative attempt tree:

```text
campaigns/<campaign>/results/<bucket>/<tool>/<case>/run-<n>/<attempt-id>/
```

Raw artifacts upload first and `result.json` uploads last, so the result record
is also the commit marker for that execution. Every object is written create-only
(`ifGenerationMatch=0`).

The campaign model owns the `run-<n>` ordinal (`run-1` under the current
`reps: 1` policy; higher ordinals are reserved for separately scheduled runs,
not an implemented append-later command). Each worker-container execution mints
the `attempt_id` UUID beneath that run prefix. The UUID is per execution, not the
scheduled run or Batch job: one job may theoretically produce more than one
UUID leaf if the task is duplicated despite the campaign setting automatic
Batch retries to 0. Create-only upload preserves every leaf. The required
reconciler must surface more than one result under the same run prefix as an
anomaly; it must never select one as the canonical result or collapse them into
one attempt.

The required routine manager reporting path must visit only manifest-known run
prefixes. It must list each with GCS `delimiter=/` to discover immediate UUID
children without descending into their contents, then GET each child's exact
`result.json` and read its small worker-produced summary and metrics. Raw
listing artifacts remain in GCS as audit and verification evidence. The
manager fetches raw bytes
only for correctness verification or a specific investigation; it does not
download every listing, run routine manager-side collection over them, or write
a companion `collected.json` into an attempt.

`smoke-campaign.sh` remains the local all-tools diagnostic path. Production
campaign modeling under `s3_listing_study.manager.campaign` resolves cases,
freezes image and attempt manifests, assigns Batch job IDs, and records mutable
submission state plus its append-only transition history in the local ledger.
The mutable row answers current state; events retain submission/retry history
after Batch ages jobs out. The ledger is operational and is not run evidence.

All eleven subjects have run at smoke on amd64 through this engine, four with a
scoped credential. Those runs are non-comparative and carry no verifier verdict:
they prove the runner/image path can produce the machine artifacts, not that a
tool claim should be promoted or a benchmark result published.

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

`s3_listing_study.manager.verify` continues to audit those receipt-bound streams against
their recorded registry and manifest. The offline union regression suite is
`harness/tests/run-regressions.sh`; the host security regression suite is
`harness/tests/runner-security-regressions.sh`.

Provider submission/reconciliation and comparative reporting remain separate
manager integration work. They must consume worker summaries for routine
operation and must not introduce a second subject lifecycle, timing
implementation, or eager raw-artifact download path.
