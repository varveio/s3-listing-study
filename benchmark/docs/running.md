# Running a campaign

The operator's runbook: what to have ready, how to submit, how to watch, what
to do when a job settles badly, and how to turn finished attempts into a report.

For *what a campaign is* — and why a plan carries no campaign ID, image digest,
or date — read [`../plans/README.md`](../plans/README.md) first. For what a case
row is and how to add one, the same file's *"A layer and a row"* and *"Cases are
an ordered union"* sections are authoritative. This page does not restate either.

## Status of this procedure: `VERIFIED: no`

**No campaign has ever been run in this repository.** Every step below was
derived from reading `campaign.py`, not from executing it against GCP Batch.
That makes this an unverified procedure in exactly the sense
[`../../AGENTS.md`](../../AGENTS.md) means it: source reading is not a receipt.

Each section carries its own marker. Promote a marker to `VERIFIED: yes` only in
the commit where a real run exercised that path, and say in the message which
campaign did it. Do not promote a step because a neighbouring step worked.

| Step | Exercised against real Batch? |
| --- | --- |
| Toolbox build + eleven-tool smoke | **yes** — the `benchmark-toolbox` workflow, local Docker |
| `submit` | no |
| `poll` / `status` | no |
| `retry` / `cancel` / `accept-failure` | no |
| `verify` / `report` | no |

## Before you submit

`VERIFIED: no`

The submit command assumes all of this already exists. It is not a checklist the
tool enforces for you; a missing item surfaces as a provider error mid-campaign.

1. **Infrastructure applied.** Project, region, network/subnetwork, results
   bucket, and both worker service accounts. See
   [`../../infra/terraform/modules/gcp/s3-listing-study/README.md`](../../infra/terraform/modules/gcp/s3-listing-study/README.md).
   Both worker identities hold `roles/storage.objectCreator` and nothing wider.
2. **Toolbox built and smoked** at the exact revision you intend to attest:

   ```sh
   uv run python benchmark/src/benchmark/build_image.py \
     --harness-revision "$(git rev-parse HEAD)" \
     --tag benchmark-toolbox:local
   ```

   The build refuses a dirty checkout and refuses a revision that is not `HEAD`.
   Commit first; there is no `--force`.
3. **Image published and pinned.** Push through an explicitly authorized registry
   operation, then record the *immutable* `@sha256:` URI. A tag is rejected.
4. **Image-set JSON written** — schema 4, the exact eleven-tool roster, matching
   the built toolbox's manifest and recipe digests. Shape is in
   [`../README.md`](../README.md) § *Campaign image set*.
5. **Secrets file**, if any case is `authenticated`. Each entry must be a full
   `projects/<p>/secrets/<s>/versions/<v>` resource path.
6. **A campaign ID you have not used before.** Job IDs are derived from it; a
   reused ID against a changed plan is what `COLLISION` exists to catch.

Keep `campaign.db` — it is authoritative controller state, not a cache, and it
is not interchangeable with the evidence in GCS. Back it up.

## Submit

`VERIFIED: no`

```sh
python benchmark/src/benchmark/campaign.py submit \
  --project my-project --location us-central1 \
  --campaign-id 2026-08-16-canary \
  --plan benchmark/plans/buckets/noaa-ghcn-pds.yaml \
  --results-bucket my-results --image-set /secure/images.json \
  --anonymous-worker-sa anonymous-worker@my-project.iam.gserviceaccount.com \
  --authenticated-worker-sa auth-worker@my-project.iam.gserviceaccount.com \
  --secrets /secure/secrets.yaml
```

`--dry-run` renders and records nothing at the provider — use it first.

Defaults worth knowing: `--provisioning` is `SPOT`, so preemption is expected
rather than exceptional. `--network`/`--subnetwork` must be supplied together.

The controller **records intent before it creates a job**. If the process dies
between those two steps, the ledger knows about a submission the provider may or
may not have; `poll` and `retry` are written to resolve that, which is why they
consult recorded intent rather than trusting the provider's view.

## Watch it

`VERIFIED: no`

```sh
# One pass, updates the ledger and exits.
python benchmark/src/benchmark/campaign.py poll --project my-project --location us-central1

# Block until every submission is terminal (default 30s between passes).
python benchmark/src/benchmark/campaign.py poll --project my-project --location us-central1 \
  --watch --interval 30

# Read the ledger only. No provider calls, no credentials needed.
python benchmark/src/benchmark/campaign.py status
```

`status` is the safe command: it opens `campaign.db` read-only and prints
`job_id`, `state`, `tool`. Reach for it whenever you want to know where things
stand without touching GCP.

`poll` reports only the latest submission per case unless you pass `--all`,
which includes superseded retry generations.

### The states

| State | Terminal | Meaning |
| --- | --- | --- |
| *(provider states)* | no | Batch's own lifecycle, written through as seen |
| `SUCCEEDED` | yes | The job finished cleanly. Not a verdict about the listing. |
| `FAILED` | yes | Settled failure. Retryable. |
| `NOT_CREATED` | yes | The provider refused creation (bad request, permissions, precondition). Retryable. |
| `COLLISION` | yes | A job of that ID exists but does not match recorded intent. Retryable. |
| `CANCELLED` | yes | Set by `cancel`. **Not** retryable. |
| `ACCEPTED_*` | yes | You declared the failure final. Not retryable. |

A describe failure during `poll` prints to stderr and leaves the row alone
rather than inventing a state — the pass simply reports "not all terminal", so
`--watch` keeps going.

`SUCCEEDED` means the job ran, nothing more. Whether the listing is any good is
`verify`'s question.

## When something settles badly

`VERIFIED: no`

Three commands, three different meanings. Choosing the wrong one is how a
campaign's evidence becomes ambiguous.

### `retry` — run it again under a fresh identity

```sh
python benchmark/src/benchmark/campaign.py retry \
  --project ... --location ... --campaign-id ... --plan ... \
  --results-bucket ... --image-set ... --anonymous-worker-sa ... \
  [--job-id <one submission>]
```

Only `FAILED`, `NOT_CREATED`, and `COLLISION` are retryable. Without `--job-id`
it sweeps every retryable latest submission; with one, it refuses loudly if that
submission is not retryable.

Before resubmitting anything it **inspects the evidence already in GCS**:

- `COMPLETE` (one leaf, result marker present) — refuses. There is a recorded
  outcome; re-running would produce a second answer to a settled question.
- `AMBIGUOUS` (several leaves, or leaves it cannot reconcile) — refuses, and
  will not be argued out of it. Resolve by hand.
- `ABSENT` / `INCOMPLETE` — proceeds.

It then re-checks that the plan case, fingerprint, bucket, region, image set,
image URI, provider parent, and results-bucket root all still match recorded
intent. **A retry cannot silently drift onto a different plan or a newer image.**
If you changed either, that is a new campaign, not a retry.

The new submission gets a new job ID and its own artifact prefix. Nothing is
overwritten, and no job from a retired controller is ever adopted.

### `cancel` — stop everything now

```sh
python benchmark/src/benchmark/campaign.py cancel --project ... --location ...
```

Cancels *every* non-terminal submission in the ledger and marks them
`CANCELLED`. There is no per-job form and no confirmation prompt. `CANCELLED` is
terminal and **not** retryable, so this ends the campaign — reach for it when you
want the campaign stopped, not when you want one job restarted.

### `accept-failure` — declare a failure final

```sh
python benchmark/src/benchmark/campaign.py accept-failure --job-id <id>
```

Moves one settled failure to `ACCEPTED_FAILED` / `ACCEPTED_NOT_CREATED` /
`ACCEPTED_COLLISION`. It changes no cloud state whatsoever — it is a bookkeeping
declaration that you are not going to retry this one, which takes it out of
`retry`'s sweep and lets the campaign reach a terminal whole.

Use it when a case genuinely cannot run, and say why in the campaign's notes. It
is not a way to make a red campaign look green: `report` still sees the accepted
state, and an accepted failure is an absent measurement, not a passing one.

## Verify

`VERIFIED: no`

```sh
python benchmark/src/benchmark/campaign.py verify \
  --plan benchmark/plans/buckets/noaa-ghcn-pds.yaml \
  --reference-case s3api-v2-text
```

Verification compares one completed attempt against another completed attempt —
the reference case — field by field. It is **not** a check against a sealed
manifest, and a `PASS` does not prove either listing is complete. Read
[`../README.md`](../README.md) § *Agreement is not ground truth* before quoting
a verdict.

Exit codes, where refusal dominates:

| Code | Verdict |
| --- | --- |
| `0` | `PASS` — exposed fields agree |
| `2` | `DRIFT` — the only mismatches are `mtime`; deliberately still nonzero |
| `1` | `FAIL` — anything else, including every refusal |

Refusals are `FAIL`, on purpose: ambiguous attempt leaves, a missing result
marker, a binding or artifact-hash mismatch, a failed/timed-out/unclean subject,
a normalizer error, or a `NULL` field in a normalized row. The verifier declines
to turn incomplete evidence into a comparison rather than guessing.

## Report

`VERIFIED: no`

```sh
python benchmark/src/benchmark/report.py --state campaign.db
```

Binds each result back to recorded controller intent and renders the analysis.
It refuses unbound or inconsistent results, so a report that renders is a report
whose inputs agree with the ledger.

## Things that will bite

`VERIFIED: no` — inferred from the code paths, not from a real run.

- **A dirty checkout cannot build an attestable image.** Commit first.
- **A tag where a digest belongs is rejected.** Campaigns consume `@sha256:` only.
- **`--project`/`--location` must match what the ledger recorded.** Every
  provider-touching command re-checks this before doing anything, so a
  copy-pasted command aimed at the wrong project fails closed rather than
  creating a stray job.
- **SPOT is the default.** Preemption is a normal outcome; plan retries for it.
- **`cancel` is campaign-wide and one-way.**
- **Losing `campaign.db` loses the binding**, not the evidence — but without it
  the evidence in GCS cannot be tied to intent, and `report` will refuse it.
