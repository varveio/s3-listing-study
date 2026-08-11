# Campaign operations

Production campaigns have no manager ledger. Temporal retains controller state;
the results bucket retains frozen inputs and create-only worker evidence. The
manager observer joins those two sources from scratch on every invocation.

## Start, wait, and resume

The normal production command freezes, starts, waits, reconciles, and prints the
final report:

```sh
uv run s3-listing-study submit-campaign <campaign inputs> --wait
```

Add `--publish-report` to create
`campaigns/<campaign>/report.json` with `ifGenerationMatch=0`. An exact existing
report is accepted; different content is refused. Omit `--wait` for asynchronous
submission. `--dry-run` remains local: it renders the frozen documents and jobs
without contacting GCS or Temporal, regardless of `--wait`.

Observation can be resumed from any manager with the same GCP ADC and the exact
non-secret Temporal address/Namespace frozen by submission:

```sh
uv run s3-listing-study report-campaign \
  --campaign <campaign-id> --results-bucket <results-bucket>

uv run s3-listing-study report-campaign \
  --campaign <campaign-id> --results-bucket <results-bucket> \
  --wait --publish
```

One-shot mode prints the current deterministic snapshot. `--wait` prints a
concise progress line to stderr when controller or evidence state changes,
polls until the owned parent Workflow is completed and every case controller is
terminal, then prints the final report. A closed non-completed parent is an
explicit error. It stores no durable cursor, cache, watcher state, or local
database, so interruption only loses the current observation call; rerunning
reconstructs the same report from retained state. A Temporal Worker must remain
available for Workflow and Activity progress.

If the exact owner exists but every case remains pending, the owner may have
been frozen just before the submitter crashed without sending the claim Signal.
Rerun the exact original `submit-campaign` command with the same frozen inputs
and Temporal address/Namespace. Its idempotent ownership path validates the
same retained Run and re-sends the matching claim. `report-campaign` is
deliberately read-only and cannot send that recovery Signal. Do not start a new
campaign or change any input: a mismatch is refused.

## Ownership and controller observation

The observer first reads the bounded, canonical, create-only
`inputs/temporal-owner.json`. It connects to that exact Temporal scope and Run
ID, then requires the retained Workflow's ID, Run ID, Namespace, type, Task
Queue, and digest memo to match. It never follows “latest run” and never starts
a replacement.

`CampaignWorkflow.progress` exposes one row per manifest job in frozen order:
pending before child start, running with the exact child Run ID, and terminal
with either a provider-terminal `BatchJobOutcome` state or a controller failure
type. Child failures are collected as data; one exhausted Activity or unexpected
child failure cannot fail-fast the parent and abandon observation of siblings.

For a nonterminal child, the observer uses the child's Temporal description and
its one pending `run_batch_job` Activity to report the current attempt and the
last heartbeat `{job_name, state}` when available. Attempt 2 or later is
`retrying`. It does not fetch complete Event Histories on every poll. Terminal
children need no history scan. These fields are operational diagnostics only:
provider state and heartbeat state do not classify the subject and are not run
evidence. Child descriptions are fetched concurrently with a fixed maximum of
16 in flight. A provider-terminal outcome is accepted only when its exact
resource name matches the frozen `temporal.json` case and its state is
`SUCCEEDED` or `FAILED`.

## Summary-only evidence reconciliation

Only controller-terminal cases are harvested. During one `--wait` invocation,
each terminal case's immutable evidence snapshot is cached so later polls do not
re-list or re-download it; the cache is discarded on exit. For each terminal
case, the observer takes the run prefix from frozen `campaign.json`, lists it
with `delimiter=/`, and examines only immediate execution-UUID children. It
GETs only each child's exact `result.json` commit marker; it never reads
`stdout.raw.gz`, `stderr.raw.gz`, or native output during routine reporting.

Discovery is also bounded: more than 256 immediate execution leaves below one
run prefix is refused as an anomaly before any result object is downloaded.
No canonical duplicate is chosen or retained.

The evidence state is:

| State | Meaning |
| --- | --- |
| `pending` | Controller is not terminal; GCS is not inspected. |
| `missing` | Controller is terminal and the run prefix has zero UUID children. |
| `recorded` | Exactly one canonical UUID has a bounded, valid, identity-matching `result.json`. |
| `duplicate` | More than one immediate execution child exists; all are surfaced and none is canonical. |
| `unsealed` | The sole canonical UUID child has no `result.json` commit marker. |
| `invalid` | The sole child name, JSON, size, schema, identity, or summary metrics are invalid. |

Leaf reason codes distinguish `invalid_attempt_id`, `missing_result_commit`,
`invalid_result_size`, `invalid_result_json`, `invalid_result_identity`,
`invalid_result_request`, `invalid_result_provenance`,
`invalid_result_secret_scan`, `invalid_result_outcome`, and
`invalid_result_metrics`. They never embed an exception message or object
content.

Strict identity checks bind the worker record to campaign, job, case and attempt
fingerprints, run ordinal, submission, declared resources, the exact logical
list request and target, tool/version, full derived/tool/shared-base image and
build provenance, harness revision, adapter bundle, exact artifact/result URIs,
and the worker's clean secret-scan structure. Outcome exit/signal/timeout facts
and row-count summary semantics must also agree. The result bytes are checked
using the worker's actual canonical JSON encoding, including its escaped
non-ASCII representation. A valid single result exposes subject outcome
(`completed`, `failed`, `timed_out`, `signaled`, or `harness_error`) separately
from controller/provider state, plus `elapsed_ns`, peak child RSS, and row
count. Missing, invalid, unsealed, and duplicate cases have no canonical
subject outcome or metrics.

Production submission accepts only image-set schema 3. The observer likewise
requires attempt-fingerprint version 3 and every schema-3 image registration
field, so the tool-image digest/URI and build-selection SHA-256 are always
bound; there is no weaker production compatibility mode for historical image
sets. Worker cleanup, outcome, and summary fields must have their exact typed,
derivable relationships, and execution leaves must be canonical UUIDv4 values.

Before querying Temporal, the observer generation-pins and validates both
`campaign.json` and `inputs/temporal.json`. The Temporal-input SHA-256 must equal
the owner digest; its manifest SHA-256 must equal the actual frozen manifest;
and campaign, safe Temporal scope, Workflow type, Task Queue, ordered job IDs,
and exact provider resource names must all agree. The cached bytes are reused
within one wait invocation and neither job bodies nor credential configuration
are emitted.

The schema-version-1 report is deterministic: manifest case order, sorted leaf
IDs, fixed aggregate keys, no observation timestamp, and canonical JSON when
published. It includes the frozen campaign-manifest SHA-256, the safe owned
Temporal-input digest, and separate controller, provider, evidence, and subject
counts. Invalid and unsealed leaves carry stable reason codes rather than raw
exception text or object content. `complete` means the parent Workflow is
completed and every controller is terminal; it does not erase or reinterpret
missing, duplicate, invalid, failed-provider, or failed-subject outcomes.

## Permissions and current integration boundary

The observer uses GCP Application Default Credentials. The Terraform
manager/orchestrator bundle grants Batch control-plane access, `actAs` on worker
identities, and results-bucket read/create access. Temporal credentials remain
environment/config inputs and never enter reports, frozen documents, or argv.

Focused tests use a fully orchestrated fake covering Workflow retry visibility,
single/missing/duplicate/invalid/unsealed GCS states, metric extraction, and the
submit-to-wait handoff. The manual local SDK test-server exercise is:

```sh
env -u TEMPORAL_API_KEY uv run python harness/tests/temporal-controller-e2e.py
```

It starts an isolated SDK test server, uses unique Workflow and job IDs, and
runs the real Worker, claim Signal, parent, child, retrying Activity, progress
query, exact owner/Run observation, terminal-missing reconciliation, stable
report recomputation, and create-only publication/collision path against an
in-memory GCS fake. On first use, the Temporal SDK lazily downloads its
test-server binary. A live Temporal Cloud plus GCS canary of the observer is
still required before the first production benchmark campaign.
