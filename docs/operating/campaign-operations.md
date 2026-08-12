# Campaign operations

The production campaign controller is a local SQLite state machine over exact,
deterministically named GCP Batch jobs. SQLite is operational state, not run
evidence: the frozen `campaign.json` and create-only attempt objects remain the
evidence chain. Keep the ledger for the lifetime of a campaign and back it up
like any other controller database.

New campaigns accept image-set schema 3 only. Submission freezes each plan and
the canonical campaign manifest before recording every case intent. It then
creates Batch jobs in waves of eight, separated by one second, without waiting
for those jobs to reach terminal provider states.
Every job has automatic retries disabled and carries the full attempt
fingerprint as `s3-study-attempt`. `--post-attempt-allowance-s` is a singleton;
repeating it is an error.

```sh
uv run s3-listing-study submit-campaign \
  --bucket noaa-ghcn-pds \
  --campaign 2026-08-11-first \
  --image-set image-set.json \
  --project study --location us-east1 \
  --results-bucket study-results \
  --anonymous-worker-sa worker@study.iam.gserviceaccount.com \
  --ledger campaign.sqlite3
```

Add `--wait` to stay attached until the report is final. Add
`--publish-report` with `--wait` to create `report.json`; publication is refused
without full provider settlement and uses `ifGenerationMatch=0`. A preexisting
byte-identical report is accepted, while different content is a hard error.
In `--dry-run` mode, `--publish-report` is accepted as part of command preview
without requiring `--wait`; no report or other external state is created.

For a detached campaign, observe or publish it separately:

```sh
uv run s3-listing-study report-campaign \
  --campaign 2026-08-11-first --results-bucket study-results \
  --ledger campaign.sqlite3 --wait --publish
```

Campaign report schema 3 separates four facts: controller completion, provider
settlement, report finality, and operational success. A final report with an
accepted failure is valid evidence and exits zero even though
`operational_success` is false. Evidence remains `pending` until the exact
provider effect settles. The reconciler lists only immediate execution UUID
children, validates each sealed `result.json` against the frozen manifest, and
selects no canonical result when current-submission evidence is duplicated.
Results from earlier submissions remain visible as historical leaves.
The public engine-neutral identity is `controller_input_sha256` plus
`engine.name`, `engine.execution_id`, `engine.run_id`, and `engine.status`;
SQLite-specific durability diagnostics do not alter the common case schema.

## Retry and accepted failure

A terminal provider failure, definitive `NOT_CREATED`, or settled name
collision leaves the case in `awaiting_retry`. Retry only the exact next
submission number. Submission numbers stop at 99 because the longest shared
Batch job-ID layout uses the provider's full 63-character limit:

```sh
uv run s3-listing-study retry-case \
  --campaign 2026-08-11-first --ledger campaign.sqlite3 \
  --job-id <original-s1-job-id> --submission 2
```

The retry keeps the artifact run prefix, changes the deterministic Batch name
to `-s2`, and updates both worker identity flags. The controller reserves the
case as active under an immediate SQLite transaction before any Batch call, so
finalization cannot race a retry into launching work after closure.

If the settled failures are accepted, close them explicitly:

```sh
uv run s3-listing-study finalize-campaign \
  --campaign 2026-08-11-first --ledger campaign.sqlite3
```

Finalization refuses pending, running, or provider-unsettled cases. It marks
only `awaiting_retry` cases as accepted failures; a finalized campaign is then
immutable and eligible for a final, operationally unsuccessful report.

## Provider ownership

Create rejection that proves no resource exists is recorded as `NOT_CREATED`.
An existing deterministic name is adopted only when its resource name, attempt
label, task groups, allocation policy, and logging policy match the frozen
request after removing narrowly defined provider materialization (task-group
names, the `batch-job-id` label, and only an added parent region). A mismatch is
a collision. Because that resource may still execute, collision does not count
as settled until the exact Batch job reaches `SUCCEEDED` or `FAILED`.

The local controller does not provide replay or a durable event-history
service. Its SQLite transaction durability and operator-managed database
recovery are the intentional orchestration-engine boundary; the frozen inputs,
Batch requests, result validation, report schema, retry rules, and publication
contract do not depend on that boundary.
