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
group did it. Do not promote a step because a neighbouring step worked.

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
4. **Image-set JSON written** — schema 5, the exact eleven-tool roster, matching
   the built toolbox's manifest and recipe digests. Shape is in
   [`../README.md`](../README.md) § *Campaign image set*.
5. **Credential secret**, if any case signs: one
   `projects/<p>/secrets/<s>/versions/<v>` resource whose payload is the
   `KEY=VALUE` lines described in
   [`../../infra/terraform/modules/gcp/s3-listing-study/aws-credentials.tf`](../../infra/terraform/modules/gcp/s3-listing-study/aws-credentials.tf).
   Only a signing case's job carries it, and only the authenticated worker
   identity can read it.
6. **A `--suite` name.** It is the results-bucket path prefix, the job label a
   polling pass filters on, and the job-name prefix — constant for the life of
   a ledger, so it is chosen once, not per launch. `--group` is optional:
   leave it unset and `submit` mints `gYYYYMMDD-HHMMSS`, or name one yourself
   when you want a handle you chose rather than one you'll have to look up.

Keep `campaign.db` — it is authoritative controller state, not a cache, and it
is not interchangeable with the evidence in GCS. Back it up. What is inside it —
the tables, their keys, and every state they record — is in
[`model.md`](model.md).

## Submit

`VERIFIED: no`

```sh
uv run python benchmark/src/benchmark/campaign.py submit \
  --suite s3-listing-study \
  --plan benchmark/plans/buckets/noaa-ghcn-pds.yaml \
  --project my-project --location us-central1 \
  --results-bucket my-results --image-set /secure/images.json \
  --anonymous-worker-sa anonymous-worker@my-project.iam.gserviceaccount.com \
  --authenticated-worker-sa auth-worker@my-project.iam.gserviceaccount.com \
  --secret-resource projects/varve-oss/secrets/s3-listing-study-aws-credentials/versions/latest
```

`--group` names the launch instead of minting a timestamp. `--dry-run` renders
every attempt and slot the plan expands to and journals nothing at the
provider — use it first:

```sh
uv run python benchmark/src/benchmark/campaign.py submit \
  --suite s3-listing-study --plan benchmark/plans/buckets/noaa-ghcn-pds.yaml \
  --project my-project --location us-central1 \
  --results-bucket my-results --image-set /secure/images.json \
  --anonymous-worker-sa anonymous-worker@my-project.iam.gserviceaccount.com \
  --dry-run
```

prints one line per resolvable attempt (`attempt_id`, `job_name`, the frozen
Batch request), one line per slot the plan cannot yet identify (`slot
<group>/<n> <tool> <mode> awaiting <what>`), and a closing count — "N plan
row(s) expand to M attempt(s) and K slot(s)" — so a reviewer sees the true
shape of a launch before anything is created.

Two flags change what a repeated `submit` does with what it finds:

- **`--repeat`** submits a case that already has a successful attempt.
  Without it, `submit` refuses: re-measuring is `reps` in the plan, or this
  flag, never a silent extra run.
- **`--reuse-preparations`** binds an artifact an *earlier launch* already
  produced instead of refusing. Reuse within one launch is always free — every
  consumer of one preparation step shares it — but reuse across launches is a
  decision, because the digest cannot tell you the corpus moved
  ([`identity.md`](identity.md) § *What identity cannot cover*).

Defaults worth knowing: `--provisioning` is `SPOT`, so preemption is expected
rather than exceptional. `--network`/`--subnetwork` must be supplied together.

The controller **records intent before it creates a job** — a row is journaled
`SUBMITTING` inside the same transaction that allocates its ordinal, before the
provider is ever called. If the process dies between those two steps, the
ledger knows about a submission the provider may or may not have; `poll` and
`retry` are written to resolve that, which is why they consult recorded intent
rather than trusting the provider's view. `submit` also prints
`campaign: group <id>` on the way out — the handle `retry`, `cancel`, `status`,
`verify` and `prune` all take as their scope.

## Watch it

`VERIFIED: no`

```sh
# One pass, updates the ledger and exits.
uv run python benchmark/src/benchmark/campaign.py poll

# Block until every submission is terminal (default 30s between passes).
uv run python benchmark/src/benchmark/campaign.py poll --watch --interval 30

# Read the ledger only. No provider calls, no credentials needed.
uv run python benchmark/src/benchmark/campaign.py status --group g20260817-120000
```

`poll` takes no `--project`/`--location` — every non-terminal row already
carries its own `executor_env` and `location`, so a poll pass reads those from
the ledger rather than being told them again. One listing filtered by
`labels.suite=<suite>` covers every group in flight, so parallel launches need
no extra machinery. A settled preparation is resolved into whatever slots
awaited it in the same pass that noticed it settled.

`status` is the safe command: it opens `campaign.db` read-only and prints one
line per attempt (`attempt_id`, `state`, `purpose`, `group_id`, `job_name`) and
one line per pending slot (`slot <group>/<n>`, its state, `purpose`, and what
it is `awaiting`). Unfiltered it prints the whole file's history; `--group` and
`--case` narrow it. A group is not understood from its attempts alone while it
still owes a slot — that is why slots print alongside attempts rather than in
a separate command.

### The states

Two vocabularies share the `state` column: the controller's own relationship
with the provider, and the provider's lifecycle passed through as seen.
Full rationale is [`model.md`](model.md) § *The state column*.

| State | Terminal | Retryable | Meaning |
| --- | --- | --- | --- |
| `SUBMITTING` | no | no | Intent is durable; the provider has not been called yet. |
| `SUBMITTED` | no | no | Created — by this run, or found already matching recorded intent. |
| *(provider states)* | no | no | `QUEUED`, `SCHEDULED`, `RUNNING`, and the rest of Batch's own lifecycle. |
| `SUCCEEDED` | yes | no | The job ran cleanly. Not a verdict about the listing — that is `verify`'s question. |
| `FAILED` | yes | **yes** | Settled failure. |
| `NOT_CREATED` | yes | **yes** | The provider refused creation, or a job of that name exists and does not match recorded intent. |
| `CANCELLED` | yes | no | Set by `cancel`, or by the provider. One-way. |
| `ACCEPTED` | yes | no | Set by `accept-failure`. An absent measurement, never a passing one. |

A describe failure during `poll` prints to stderr and leaves the row alone
rather than inventing a state — the pass simply reports "not all terminal", so
`--watch` keeps going.

## When something settles badly

`VERIFIED: no`

Three commands, three different meanings. Choosing the wrong one is how a
campaign's evidence becomes ambiguous.

### `retry` — run it again under a fresh ordinal

```sh
uv run python benchmark/src/benchmark/campaign.py retry \
  --group g20260817-120000 \
  --project my-project --location us-central1 \
  --results-bucket my-results --image-set /secure/images.json \
  --anonymous-worker-sa anonymous-worker@my-project.iam.gserviceaccount.com
```

`--group` is required — `retry` scopes to one group and skips rows from
others rather than refusing outright. It sweeps every `FAILED` or
`NOT_CREATED` row in that group; there is no per-attempt form. A row whose
case declared `statistic: rate` is left alone and reported as such — its
failures are the finding, so retrying one would be resampling
([`model.md`](model.md) § *Sometimes the failures are the measurement*).

Each retry re-renders the frozen request from the recorded row with only the
new attempt's identities rewritten (`--job-name`, `--destination`,
`--attempt-id`), and refuses if that re-render disagrees with what was
recorded — a plan or image edited since submission cannot silently drift a
retry onto a different measurement; that is a new campaign, not a retry. The
new attempt gets its own ordinal, its own job name, and its own result prefix;
nothing is overwritten.

### `cancel` — stop one group now

```sh
uv run python benchmark/src/benchmark/campaign.py cancel --group g20260817-120000
```

`--group` is required — an unscoped cancel over an accumulating file is
refused rather than performed. Cancels every non-terminal attempt in that
group and marks each `CANCELLED`. There is no per-attempt form and no
confirmation prompt. `CANCELLED` is terminal and **not** retryable.

### `accept-failure` — declare one attempt's failure final

```sh
uv run python benchmark/src/benchmark/campaign.py accept-failure --attempt aws-cli.9f300cc4d2b1.s2
```

Moves one settled `FAILED` or `NOT_CREATED` attempt to `ACCEPTED`. It changes
no cloud state whatsoever — it is a bookkeeping declaration that you are not
going to retry this one, which takes it out of `retry`'s sweep. If the
attempt was a preparation, every slot waiting on it is recorded `ABANDONED` in
the same call — an absent measurement, propagated to whatever it would have
unblocked.

Use it when a case genuinely cannot run. It is not a way to make a red
campaign look green: `verify` and `report` still see the accepted state, and
an accepted failure is an absent measurement, not a passing one.

## Verify

`VERIFIED: no`

```sh
uv run python benchmark/src/benchmark/verify.py --state campaign.db --group g20260817-120000
```

`verify` is a standalone CLI, not a `campaign.py` subcommand, bound to one
group at a time via `--group` (required) and the same `--state` ledger. It
reads that group from the recorded rows — never a re-resolved plan
([`model.md`](model.md) § *What verify binds against*). It
compares every `purpose = 'measurement'` attempt within a stratum — one
target bucket, one `(product, fields)` — against the others in that stratum. A
`PASS` means the subjects **agree**, not that any one of them is correct:
there is no sealed manifest, and agreement is what stands in for control over
a corpus that keeps growing. Read
[`../README.md`](../README.md) § *Agreement is not ground truth* before
quoting a verdict.

A `statistic: rate` case is reported as successes over attempts and takes no
part in cross-tool agreement — its finding is the rate itself, printed
alongside the group's verdict rather than folded into it.

Exit code is worst-wins across the whole group:

| Code | Verdict | Meaning |
| --- | --- | --- |
| `0` | `PASS` / `UNCOMPARED` | Every stratum agrees, or has only one subject to agree with. |
| `1` | `FAIL` | A field mismatch beyond `mtime`. |
| `2` | `DRIFT` | The only mismatches are `mtime` — deliberately still nonzero. |
| `9` | `INCOMPLETE` | The group owes a subject — a `BLOCKED` slot, or a comparison `verify` refused. |

A refusal — a missing result marker, an identity mismatch between recorded
intent and the object it was found under, a failed or timed-out subject, a
`normalize.py` error, or a `NULL` field in a normalized row — becomes a gap in
the printed report and drives the group to `INCOMPLETE` rather than being
guessed past; each gap's own reason and finer-grained code are in the printed
detail, not in the process's exit status. Pass `--no-write` to compare without
writing `verify.json` back under each compared attempt's own result prefix.

## Report

`VERIFIED: no`

```sh
uv run python benchmark/src/benchmark/report.py --state campaign.db --group g20260817-120000
```

Omit `--group` to report the whole ledger. Prints a Markdown summary of every
attempt's state and evidence binding, and exits nonzero if the scope still
owes a `BLOCKED` slot, any row is non-terminal, any row's state is outside
`SUCCEEDED`/`CANCELLED`/`ACCEPTED` (a settled `FAILED`/`NOT_CREATED` must be
retried or accepted first), or any `SUCCEEDED` row's evidence is not bound —
a report that exits `0` is a report whose inputs agree with the ledger.

## Prune

`VERIFIED: no`

```sh
uv run python benchmark/src/benchmark/campaign.py prune --group g20260817-120000
```

Deletes the evidence objects of every attempt in that group that settled
*unsuccessfully* — `FAILED`, `NOT_CREATED`, `CANCELLED`, `ACCEPTED` — leaving
every row. `--group` is required, for the same reason `cancel`'s is: an
unscoped delete over a file that accumulates every group ever launched is the
one mistake with no undo. A `SUCCEEDED` attempt's evidence is never a target.

## Things that will bite

`VERIFIED: no` — inferred from the code paths, not from a real run.

- **A dirty checkout cannot build an attestable image.** Commit first.
- **A tag where a digest belongs is rejected.** Campaigns consume `@sha256:` only.
- **`poll` and `cancel` need no `--project`/`--location`** — they read each
  row's own `executor_env` and `location` — but `submit` and `retry` do, and a
  copy-pasted `--project`/`--location` aimed at the wrong estate creates a
  stray job there rather than failing closed.
- **SPOT is the default.** Preemption is a normal outcome; plan retries for it.
- **`cancel` is one group and one-way.**
- **`retry` on a `statistic: rate` case is a no-op**, by design — its failures
  are data, not an owed retry.
- **Losing `campaign.db` loses the binding**, not the evidence — but without it
  the evidence in GCS cannot be tied to intent, and `verify`/`report` will
  refuse it.
