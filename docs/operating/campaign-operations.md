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
concise progress line to stderr when controller or evidence state changes and
polls until `report_final` is true: the owned parent Workflow completed, every
case controller completed, and every possible provider effect settled. A closed
non-completed parent is an explicit error. A completed controller with an
unsettled provider is also an actionable error, not a final report: the command
names the affected job IDs, refuses publication, and leaves the deterministic
Batch resource names in the snapshot for investigation. It stores no durable
cursor, watcher state, or local database, so interruption only loses the current
observation call; rerunning reconstructs the same report from retained state. A
Temporal Worker must remain available for Workflow and Activity progress.

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
with a controller result. A terminal row separately records
`provider_settled`. Settlement is true only for an exact Batch resource observed
in provider-terminal `SUCCEEDED` or `FAILED`, or the explicit `NOT_CREATED`
outcome returned by a definitive create rejection. A controller failure by
itself has `provider_settled: false`; it can never turn absent GCS evidence into
an immutable negative result. Child failures are collected as data so siblings
remain observable, but they do not manufacture provider finality.

The Batch Activity has no total retry-attempt or schedule-to-close limit. Once a
deterministic job may exist, each retry adopts it and continues until Batch is
terminal. Each Activity attempt is still bounded by `controller_timeout_s`,
heartbeats every 30 seconds, and each Batch RPC has a 20-second timeout. A
definitive create rejection is the only error path that returns `NOT_CREATED`;
errors while creating ambiguously, adopting, or polling remain retryable. An
unexpected pre-existing or identity-mismatched deterministic job is followed to
provider terminal state and then recorded as a settled `BatchJobCollision`, not
failed early. A Temporal patch marker preserves the old Activity command options
when retained pre-change Workflow histories replay.

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

All case children remain concurrently runnable to preserve the campaign's
same-window methodology. Their sequential Workflow start commands may still
create a short synchronized Batch API burst; a launch rate limiter is deferred
because limiting active children would change the experiment.

Cancellation retains Temporal abandonment semantics: canceling the parent does
not cancel or settle a Batch job. The owned Workflow closes non-completed,
`controller_complete` and `report_final` remain false, and publication is
prohibited. Operators must let the controller recover or separately establish
provider settlement; cancellation is not a finalization procedure.

## Summary-only evidence reconciliation

Only provider-settled cases are harvested. During one `--wait` invocation,
each settled case's immutable evidence snapshot is cached so later polls do not
re-list or re-download it; the cache is discarded on exit. For each settled
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
| `pending` | Provider is not settled; GCS is not inspected, even if the controller is terminal. |
| `missing` | Provider is settled (including explicit `NOT_CREATED`) and the run prefix has zero UUID children. |
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

The schema-version-2 report is deterministic: manifest case order, sorted leaf
IDs, fixed aggregate keys, no observation timestamp, and canonical JSON when
published. It includes the frozen campaign-manifest SHA-256, the safe owned
Temporal-input digest, and separate controller, provider, evidence, and subject
counts. `NOT_CREATED` is counted separately from `SUCCEEDED`, `FAILED`, and
`unavailable`. Invalid and unsealed leaves carry stable reason codes rather than
raw exception text or object content. Finality and success are separate:

- `controller_complete`: the exact owned parent completed and every case
  controller is terminal.
- `provider_settled`: every case has an exact provider-terminal outcome or
  explicit proof that the create request produced no effect.
- `report_final`: both preceding conditions hold. Only this authorizes
  `--publish` or `--publish-report` and immutable negative-evidence states.
- `operational_success`: the report is final and every case has a controller
  success, provider `SUCCEEDED`, and exactly one valid recorded result. Subject
  failures and timeouts may still be valid recorded evidence; they do not by
  themselves make collection operationally unsuccessful.

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
