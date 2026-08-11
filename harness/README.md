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
streams, native-row counting, deterministic gzip, result finalization, and GCS
upload happen only after that clock stops. A flagged
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
/usr/bin/python3 -I /opt/s3-listing-study/attempt.pyz \
  --output /output \
  --shared-base-digest sha256:SHARED_BASE_DIGEST \
  --shared-base-uri REGISTRY/shared-base@sha256:SHARED_BASE_DIGEST \
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

Image construction is deliberately separate from this execution lifecycle: one
published base, one capsule-owned tool payload, and one final image per tool.
The shared base contains no worker, so worker and adapter changes only
reassemble final images when the builder retains or restores its cache.

Build, publication, identity, and cache rules are documented once in
[`shared-image/README.md`](shared-image/README.md) and
[`derived-image/README.md`](derived-image/README.md).

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

The worker calls the selected adapter's `count_rows` function after
`elapsed_ns` has closed, then records that small summary directly in
`result.json`. This path counts the mode's native logical listing rows without
constructing or storing the verifier's five-field records. It deterministically
compresses the original raw streams and preserves native directory output
unchanged. Explicit correctness verification may later normalize those retained
artifacts on the manager; routine attempts do not. Each execution publishes one
authoritative attempt tree:

The nested `result.json.summary` uses schema version 2 for this count-only
contract. A completed attempt with no counting adapter records
`reason: adapter_not_configured`; a counting exception records
`error.code: row_count_failed`. Version 1 is the superseded worker-normalization
contract and retains its historical vocabulary in old result objects.

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
Batch retries to 0. Create-only upload preserves every leaf. The stateless
campaign reconciler surfaces more than one result under the same run prefix as an
anomaly; it must never select one as the canonical result or collapse them into
one attempt.

The routine manager reporting path visits only manifest-known run prefixes. It
lists each with GCS `delimiter=/` to discover immediate UUID
children without descending into their contents, then GET each child's exact
`result.json` and read its small worker-produced summary and metrics. Raw
listing artifacts remain in GCS as audit and verification evidence. The
manager fetches raw bytes
only for correctness verification or a specific investigation; it does not
download every listing, run routine manager-side collection over them, or write
a companion `collected.json` into an attempt.

`smoke-campaign.sh` remains the local all-tools diagnostic path. Production
campaigns are started with `s3-listing-study submit-campaign`. The manager
resolves cases, freezes the exact plans, campaign manifest, and compact Temporal
input create-only under `campaigns/<campaign>/`, and starts or obtains the
matching Temporal campaign Workflow by its stable campaign ID. One child
Workflow represents each
scheduled run; one long-running, heartbeating Activity submits or validates and
adopts its deterministic Batch job, then follows that job to a provider terminal
state. Its retry lifetime is unbounded once a provider effect may exist, while
each Activity attempt and Batch RPC remains bounded. A definitive create
rejection returns explicit `NOT_CREATED`; an Activity or child failure alone is
not provider settlement. Batch automatic retries remain disabled. Temporal
Event History is operational state, not run evidence. `report-campaign`
separately classifies provider settlement, sealed evidence, and subject outcome.

Temporal's UI, CLI, and API expose raw controller visibility; the stateless
`report-campaign` command joins that state to sealed GCS evidence without a
local ledger or watcher. Temporal Cloud hosts the durable service,
but an independently deployed `s3-listing-study-temporal-worker` process must
remain available to poll the campaign Task Queue. Stopping every Worker pauses
controller progress without changing the Batch job or the sealed GCS evidence
boundary; restarting a Worker resumes Activity control under the declared retry
and heartbeat policy.

### Production Temporal operator path

On the provisioned manager/runner, install the locked environment without
changing dependency resolution:

```sh
uv sync --locked
```

Provide the Temporal connection through the SDK's environment variables:
`TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_API_KEY`, and
`TEMPORAL_TLS=true`. Inject the API key from the operator's secret facility into
the process environment; never put its value in a command argument, shell
history, service definition, or log. The address and Namespace are non-secret,
but the submitter freezes both into `inputs/temporal.json`, so every submitting
Client and Worker must use the same scope.

Start and keep the Worker available in one supervised process:

```sh
uv run s3-listing-study-temporal-worker
```

From a second process with the same four Temporal environment variables and GCP
Application Default Credentials, submit the fully specified campaign:

```sh
uv run s3-listing-study submit-campaign \
  --path <plan.yaml> --campaign <new-campaign-id> --image-set <images.json> \
  --project <gcp-project> --location <batch-region> \
  --results-bucket <results-bucket> \
  --anonymous-worker-sa <worker-service-account> \
  --wait --publish-report
```

This normal path waits for both controller completion and provider settlement,
reconciles summary-only evidence, prints the final schema-versioned report, and
create-only publishes it. Omit `--wait` and
`--publish-report` for asynchronous submission. A later shell can resume without
local state:

```sh
uv run s3-listing-study report-campaign \
  --campaign <campaign-id> --results-bucket <results-bucket> --wait
```

Inspect the campaign in Temporal Cloud UI, or with the CLI under the same
Temporal environment:

```sh
temporal workflow describe --workflow-id <campaign-id>
```

The reusable Terraform module's
[`orchestrator.tf`](../infra/terraform/modules/gcp/s3-listing-study/orchestrator.tf)
is the manager permission bundle. The submitting ADC identity needs Batch job
create/get access, `actAs` on every selected worker identity, and results-bucket
read/create access; authenticated campaigns additionally select the dedicated
authenticated worker identity provisioned by that module.

Submission freezes plans, `campaign.json`, and the scope-bound `temporal.json`
before contacting Temporal. A create-only `inputs/temporal-owner.json` then
binds that frozen digest to one Workflow Run. The campaign Workflow starts in a
durable waiting state and fans out no children until the Client has frozen the
owner and sent the idempotent matching claim Signal. Retained owner and Event
History are operational recovery state, not study evidence. Worker availability
is still required: without a polling Worker the claimed Workflow and its
Activities do not progress.

If that exact owner exists but every case stays pending after Worker recovery,
rerun the exact original `submit-campaign` command with unchanged frozen inputs
and the same Temporal scope. The idempotent submission path validates the owned
Run and re-sends its claim Signal. `report-campaign` is read-only and cannot
perform this recovery.

The complete observer state model, identity checks, duplicate policy, and live
canary boundary are in
[`docs/operating/campaign-operations.md`](../docs/operating/campaign-operations.md).
Only `report_final: true` authorizes publication. Cancellation uses abandonment
and therefore never finalizes a still-running Batch effect.

Before a production canary, exercise the real local Temporal SDK server and
Worker path (claim Signal, child Workflow, Activity, progress query, and final
result) without GCP:

```sh
env -u TEMPORAL_API_KEY uv run python harness/tests/temporal-controller-e2e.py
```

The script starts an isolated SDK test server, uses unique local IDs, exercises
an Activity retry and the owner-bound observer through terminal missing-evidence
classification and create-only report collision, and does not read or print
connection configuration or secrets. The Temporal SDK lazily downloads its
test-server binary on first use.

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

Comparative presentation and correctness verification remain separate manager
work. Routine reconciliation now consumes worker summaries without introducing
a second subject lifecycle, timing implementation, or eager raw-artifact
download path.
