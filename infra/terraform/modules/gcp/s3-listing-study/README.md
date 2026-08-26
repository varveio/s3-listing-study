# `gcp/s3-listing-study`

The GCP infrastructure for running this study's benchmark campaigns: a results
bucket, an Artifact Registry repository for the single benchmark toolbox, the
least-privilege identity each Cloud Batch task runs as, the privilege bundle an
orchestrator needs, and a runner VM provisioned for toolbox construction,
optional authorized publication, campaign management, verification, and reports.

It creates **no Batch jobs**. Batch is serverless; a campaign's jobs are rendered
and submitted at runtime, one per scheduled run, each with a deterministic job ID so
submission is idempotent and a lost handle can be recomputed rather than
recovered.

| File | What it owns |
| --- | --- |
| `results-bucket.tf` | where attempt result markers and raw evidence land |
| `image-registry.tf` | Artifact Registry storage for the benchmark toolbox |
| `worker.tf` | the identity each Batch attempt task runs as |
| `orchestrator.tf` | the privilege bundle for driving a campaign |
| `runner.tf` | the VM for toolbox builds, campaign management, verification, and reports |
| `network.tf` | optional VPC, for estates without a default network |

## Why this is a module and not an environment

This repository publishes the study, not an estate. A running campaign needs
project IDs, principals, and Terraform state that belong to whoever runs it, and
none of that should be public. So the reusable half lives here and the specific
half lives in the operator's own infrastructure repository, which calls this
module and keeps its own state.

That split is also what makes the study reproducible: the infrastructure is
readable, reviewable, and applies in any project.

## Usage

```hcl
module "s3_listing_study" {
  source = "github.com/varveio/s3-listing-study//infra/terraform/modules/gcp/s3-listing-study?ref=v0.1.0"

  project     = "my-benchmark-project"
  region      = "us-east1"
  runner_zone = "us-east1-b"

  manager_members = [
    "serviceAccount:ci@my-benchmark-project.iam.gserviceaccount.com",
  ]
}
```

Pin `ref` to a tag or commit. An unpinned module source re-resolves on every
`init`, which is how an infrastructure change arrives unannounced.

## What the caller owns

**API enablement.** The module enables no services, because in a real estate
several roots share a project and each enabling the same APIs produces churn.
Enable these first:

```
storage.googleapis.com          artifactregistry.googleapis.com
batch.googleapis.com            compute.googleapis.com
logging.googleapis.com          monitoring.googleapis.com
iam.googleapis.com              cloudresourcemanager.googleapis.com
```

**Who else orchestrates.** `manager_members` is empty by default. The runner VM
receives the bundle automatically; add a principal here only for a CI service
account or a workstation identity that also drives campaigns.

**Quota.** A campaign's parallelism is bounded by the project's regional CPU
quota and by Cloud Batch's concurrent-job limits, neither of which this module
manages. Check both before a first large run — exceeding them shows up as jobs
queued indefinitely rather than as an error.

**Provisioning the runner.** Deliberately not automated, mirroring the estate's
other build hosts: the instance has no startup script. SSH in and install the
toolchain (Docker, `uv`, `git`, the study checkout) by hand, so a campaign's
working state belongs to a machine nobody rebuilds by accident.

## Design notes

**Public egress, no NAT.** The runner takes an ephemeral external IP and Batch
attaches one to its VMs by default; this module provisions no Cloud Router or
Cloud NAT. Outbound HTTPS to Google APIs and to the object stores under test is
all either needs. SSH ingress is restricted to IAP's forwarding range regardless
— though on a shared default VPC, GCP's own `default-allow-ssh` rule still opens
port 22 to the internet, and an estate that wants IAP-only SSH must delete that
rule itself. This module will not touch a shared network's pre-existing rules.

**Both workers get bucket-level `objectCreator`, plus fixture-prefix read.**
`benchmark/src/benchmark/measure.py` uploads new artifacts into a fresh UUID
attempt leaf and never reads, overwrites, or deletes campaign objects. The only
read grant is a conditional `objectViewer` binding whose resource name must
start with `objects/fixtures/`; it lets the staging runnable download immutable,
content-addressed replay inputs without exposing any result tree. Plans use an
exact object URI, so staging does not require bucket listing permission. The
worker uploads `result.json` last as the completion marker. Campaign
verification and reporting use the separate orchestrator identity, which
retains the broader read/manage access those operations need.

**Batch metadata access is intentional.** The in-worker uploader obtains its
OAuth token from the VM metadata server. The subjects are cooperative software,
not treated as hostile; the primary anonymous identity can create result objects
and is otherwise limited to Artifact Registry reads and Batch/log reporting. Each
attempt has a fresh VM with one task in an otherwise disposable benchmark
project. This module does not claim a local metadata-denial sandbox. The runtime
job renderer sets `maxRetryCount: 0`; duplicate execution is
still detected through multiple worker UUIDs beneath one `run-<n>` prefix.

**The anonymous worker holds no credentials for the object stores under test.**
The benchmark lists public buckets anonymously. "Anonymous" describes the S3
authentication stratum, not the absence of a cloud identity — the task still runs
as this service account, which is what bounds what a subject image could reach.

**The stratum is an identity, not a flag.** `create_aws_credentials_secret` also
creates a *second* worker service account, and only it can read the credentials
secret. An anonymous case therefore cannot obtain the credential even if its job
spec asks for one — Batch fails the task at environment preparation, before the
container starts. This makes "this attempt was anonymous" a fact rather than an
intention, so a mis-submitted
case fails loudly instead of quietly producing an authenticated measurement
labelled anonymous. The submitter chooses by setting the job's
`allocationPolicy.serviceAccount` to `worker_sa_email` or
`authenticated_worker_sa_email`.

Credential transport, Batch job construction, and the distinction between
anonymous and authenticated cases are documented in
[`benchmark/README.md`](../../../../../benchmark/README.md). The AWS-side policy
remains an operator-owned control outside this GCP module.

**The runner is not a measurement host.** Nothing timed runs on it. Subjects run
in Batch tasks, one task per fresh VM, so the runner's size and noise cannot reach
any published number — which is why it is sized for Docker builds and why its
`machine_type` is in `ignore_changes`.

**Result collection follows the benchmark evidence model.** Each worker execution
publishes one authoritative tree under
`campaigns/<campaign>/results/<bucket>/<tool>/<case>/run-<n>/submission-<n>/<attempt-uuid>/`,
with raw artifacts such as `stdout.log.gz`, `stderr.log.gz`, and `native/**`
first, then `<attempt-uuid>/result.json` last. The campaign model owns the run ordinal;
the current `reps: 1` policy yields `run-1`, while higher ordinals are reserved
for separately scheduled runs rather than an implemented append-later command.
`benchmark/src/benchmark/report.py` and `benchmark/src/benchmark/verify.py` resolve the UUID leaf, bind the
result to recorded campaign intent, and refuse zero or multiple completed leaves.
Raw listings are read for requested correctness verification or investigation;
no Terraform resource performs that collection.

**Grants are additive (`google_*_iam_member`), never authoritative.** An
authoritative binding on the bucket would clobber the workers' `objectCreator` on
the same resource.

## Inputs and outputs

See [`variables.tf`](variables.tf) and [`outputs.tf`](outputs.tf); every variable
and output carries its own description.

The four an orchestrator needs are `results_bucket_url` (artifact destination),
`image_repo_url` (repository root for an explicitly authorized toolbox publication), `worker_sa_email` (the job's `allocationPolicy`
service account), and `runner_ssh` (how to get onto the runner).

The module does not build or publish the toolbox. Follow
[`benchmark/README.md`](../../../../../benchmark/README.md) and
`.github/workflows/benchmark-toolbox.yml` for the clean-revision build and smoke;
campaigns consume only the resulting digest-pinned toolbox URI.
