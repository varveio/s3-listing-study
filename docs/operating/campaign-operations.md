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

## Retry or accept settled failures

A newly started campaign remains open when a case reaches a settled operational
failure: Batch `FAILED`, an explicit `NOT_CREATED`, or a settled job-identity
collision. The case phase becomes `awaiting_retry`; other cases continue and no
final report can be published while that decision is outstanding. List the exact
original job IDs and current submissions from a one-shot report:

```sh
uv run s3-listing-study report-campaign \
  --campaign <campaign-id> --results-bucket <results-bucket> \
  | jq -r '.cases[] | select(.controller.phase == "awaiting_retry") |
      [.job_id, .current_submission, .controller.terminal.provider_state] | @tsv'
```

Retry one case without editing or regenerating a plan:

```sh
uv run s3-listing-study retry-case \
  --campaign <campaign-id> --results-bucket <results-bucket> \
  --job-id <original-job-id-ending-in-s1> --submission <current-plus-one>
```

The command is bound to the exact frozen GCS owner and Temporal Run. The
submission must be exactly the current value plus one. Temporal gives the Update
the deterministic ID `retry-<job-id>-s<N>`, so retrying the same command after a
client timeout returns the same accepted result instead of launching another
submission. The Workflow derives the `-s<N>` Batch ID and rewrites only the
worker's `--job-id` and `--submission-number`; case identity, attempt
fingerprint, resources, image, and stable result prefix remain frozen.

If a settled failure is accepted rather than retried, close the campaign
explicitly:

```sh
uv run s3-listing-study finalize-campaign \
  --campaign <campaign-id> --results-bucket <results-bucket>
```

Finalization refuses active cases, converts all `awaiting_retry` cases to
terminal, and lets the parent complete. It does not claim success: the final
report still records the provider failure and `operational_success: false`.
Both commands refuse a mismatched owner, a closed parent, and campaigns created
before targeted retries were introduced. A published or otherwise closed
historical campaign is immutable; rerun its failed cases in a new campaign.

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

Each case has two explicit Activity boundaries. `ensure_batch_job` idempotently
creates or validates and adopts the deterministic Batch resource. Once it
returns a durable handle, `wait_for_batch_job` GETs that exact resource every ten
seconds until Batch reports `SUCCEEDED` or `FAILED`. The wait has no total
retry-attempt or schedule-to-close limit; after a Worker restart Temporal reruns
the wait from its recorded input rather than trying to create again. Each wait
attempt is bounded by `controller_timeout_s`, heartbeats every 30 seconds, and
each Batch RPC has a 20-second timeout.

Create remains idempotent because an `ensure_batch_job` Activity attempt can
itself fail after the provider accepted the request but before Temporal recorded
the return. Its retry validates and adopts the same deterministic resource. A
definitive create rejection is the only path that returns `NOT_CREATED`; errors
while creating ambiguously, adopting, or polling remain retryable. An unexpected
pre-existing or identity-mismatched deterministic job is followed to provider
terminal state and then recorded as a settled `BatchJobCollision`, not failed
early. Patch markers retain the legacy combined `run_batch_job` command only for
replay of pre-change histories.

For a nonterminal child, the observer uses the child's Temporal description and
its pending ensure, wait, or legacy Activity to report the current attempt and
the last heartbeat `{job_name, state}` when available. Attempt 2 or later is
`retrying`. It does not fetch complete Event Histories on every poll. Terminal
children need no history scan. These fields are operational diagnostics only:
provider state and heartbeat state do not classify the subject and are not run
evidence. Child descriptions are fetched concurrently with a fixed maximum of
16 in flight. A provider-terminal outcome is accepted only when its exact
resource name matches the frozen `temporal.json` case and its state is
`SUCCEEDED` or `FAILED`.

All case children remain concurrently runnable to preserve the campaign's
same-window methodology. New campaigns start children in deterministic waves of
eight with a one-second gap, smoothing the create-API burst without imposing an
active-job concurrency limit.

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
Every leaf is strictly validated. A fully valid sealed result from an earlier
submission of the same logical case is surfaced as `historical` with its job ID
and submission number, but does not compete with the current submission's
canonical result. A malformed or merely identity-mismatched leaf is never
treated as history. More than one current-submission leaf remains a duplicate;
no canonical duplicate is chosen or retained.

The evidence state is:

| State | Meaning |
| --- | --- |
| `pending` | Provider is not settled; GCS is not inspected, even if the controller is terminal. |
| `missing` | Provider is settled (including explicit `NOT_CREATED`) and the run prefix has zero UUID children. |
| `recorded` | Exactly one current-submission UUID has a bounded, valid, identity-matching `result.json`; valid earlier submissions may also be surfaced as history. |
| `duplicate` | More than one current-submission execution child exists; all leaves are surfaced and no current result is canonical. |
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

The retained GCS control and evidence layout is:

```text
campaigns/<campaign>/
  campaign.json
  inputs/temporal.json
  inputs/temporal-owner.json
  plans/<frozen-plan>.yaml
  results/<bucket>/<tool>/<case>/run-<n>/<attempt-uuid>/result.json
  report.json                         # only after report_final=true
```

`campaign.json` is the ordered human-readable case manifest. Temporal Event
History is the durable live state; the JSON file is frozen input, not a mutable
attempt ledger.

The schema-version-3 report is deterministic: manifest case order, sorted leaf
IDs, fixed aggregate keys, no observation timestamp, and canonical JSON when
published. It includes the frozen campaign-manifest SHA-256, the safe owned
controller-input digest, the engine identity and status, and separate controller,
provider, evidence, and subject counts. `NOT_CREATED` is counted separately from
`SUCCEEDED`, `FAILED`, and `unavailable`. Invalid and unsealed leaves carry stable
reason codes rather than raw exception text or object content. Finality and success
are separate:

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
runs the real Worker, claim Signal, parent, child, ensure retry, settled failure,
`retry_case` Workflow Update, second submission, progress query, exact owner/Run
observation, terminal-missing reconciliation, stable report recomputation, and
create-only publication/collision path against an in-memory GCS fake. On first
use, the Temporal SDK lazily downloads its test-server binary.

## Durable local Temporal service

For a durable single-machine controller, run the Temporal development server
with an explicit SQLite file outside the repository:

```sh
temporal server start-dev \
  --ip 127.0.0.1 --port 7233 \
  --db-filename /absolute/path/to/s3-study-temporal.db
```

Set `TEMPORAL_ADDRESS=127.0.0.1:7233`, `TEMPORAL_NAMESPACE=default`, and
`TEMPORAL_TLS=false` for the Worker and manager. The database file retains
Workflow Event Histories across service restarts; the Worker is still a
separate supervised process and GCS/Batch remain external. A Docker deployment
is equivalent when the server's persistence path is on a mounted host volume.
The SDK test-server exercise above is intentionally ephemeral and is not this
durable operating profile.
