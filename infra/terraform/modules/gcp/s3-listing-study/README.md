# `gcp/s3-listing-study`

The GCP infrastructure for running this study's benchmark campaigns: a results
bucket, an Artifact Registry repository for the derived attempt images, the
least-privilege identity each Cloud Batch task runs as, the privilege bundle an
orchestrator needs, and a runner VM provisioned to build and push images and to
host campaign submission and the required summary-only reconciler.

It creates **no Batch jobs**. Batch is serverless; a campaign's jobs are rendered
and submitted at runtime, one per scheduled run, each with a deterministic job ID so
submission is idempotent and a lost handle can be recomputed rather than
recovered.

| File | What it owns |
| --- | --- |
| `results-bucket.tf` | where campaign plans, compact results, and raw audit artifacts land |
| `image-registry.tf` | Artifact Registry for the derived attempt images |
| `worker.tf` | the identity each Batch attempt task runs as |
| `orchestrator.tf` | the privilege bundle for driving a campaign |
| `runner.tf` | the VM for builds, submission, and the required summary reconciler |
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

**The worker gets `objectCreator`, not `objectAdmin`.** The study's rule is that
an attempt is never overwritten and a retry is always a new attempt. Overwriting
an existing GCS object requires delete as well as create, so withholding delete
makes that rule an IAM property rather than a convention the uploader is trusted
to honour. Bucket versioning is on as well, so a violation is recoverable rather
than silent.

**The worker cannot read the bucket.** Reading results back is the orchestrator's
job. A worker writes its own attempt and has no reason to see any other.

**Batch metadata access is intentional.** The in-worker uploader obtains its
OAuth token from the VM metadata server. The subjects are cooperative software,
not treated as hostile; the attached identity is nevertheless limited to new
result-object creation, Artifact Registry reads, and Batch/log reporting. Each
attempt has a fresh VM with one task in an otherwise disposable benchmark
project. The strict metadata-denial bridge remains a local-Docker profile for
direct runs on the more-privileged runner and is not a Batch prerequisite. The
runtime job renderer sets `maxRetryCount: 0`; duplicate execution is
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

The IAM policy that credential should carry — scoped to list other people's
buckets, denied everything of the operator's own, and with no account or
organization ID hardcoded — is in
[`docs/operating/runner-security.md`](../../../../../docs/operating/runner-security.md)
§ *The authenticated stratum's AWS credential*.

**The runner is not a measurement host.** Nothing timed runs on it. Subjects run
in Batch tasks, one task per fresh VM, so the runner's size and noise cannot reach
any published number — which is why it is sized for Docker builds and why its
`machine_type` is in `ignore_changes`.

**Required routine collection is summary-only.** Each worker execution
publishes one authoritative tree under
`campaigns/<campaign>/<bucket>/<tool>/<case>/run-<n>/<attempt-uuid>/`, with raw
artifacts first and `result.json` last. The campaign model owns the run ordinal;
the current `reps: 1` policy yields `run-1`, while higher ordinals are reserved
for separately scheduled runs rather than an implemented append-later command.
For every manifest-known run prefix the required manager reconciler must use a
delimiter listing to discover only immediate UUID children, then GET each exact
`result.json`. It must read raw listings only for requested correctness
verification or investigation. More than one UUID child under one run must be
surfaced as duplicate execution and none may be silently selected.

**Grants are additive (`google_*_iam_member`), never authoritative.** An
authoritative binding on the bucket would clobber the worker's `objectCreator` on
the same resource.

## Inputs and outputs

See [`variables.tf`](variables.tf) and [`outputs.tf`](outputs.tf); every variable
and output carries its own description.

The four an orchestrator needs are `results_bucket_url` (artifact destination),
`image_repo_url` (push target), `worker_sa_email` (the job's `allocationPolicy`
service account), and `runner_ssh` (how to get onto the runner).
