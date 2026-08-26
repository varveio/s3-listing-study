# ── AWS credentials for the authenticated stratum ─────────────────────────────
#
# Most of the study lists public buckets anonymously and needs nothing here. Some
# subjects were never exercised at all without credentials, and some questions —
# versioned listings, requester-pays, a private corpus — cannot be asked
# anonymously. Those attempts run in an authenticated stratum, and this is where
# the credential for it lives.
#
# Deliberately a static key in Secret Manager rather than cross-cloud keyless
# federation. Federation is the right answer for a service that runs
# continuously; a benchmark campaign is a bounded, supervised run against a
# handful of buckets, and the workload-identity plumbing would cost more to build
# and to explain than it saves. Prefer short-lived STS credentials in the payload
# where the account allows it, and treat whatever is here as rotatable.
#
# ── The stratum is an IDENTITY, not a flag ────────────────────────────────────
#
# A separate service account, not a separate argument to the same one. Every
# Batch task can reach the VM metadata server, so any task running as an identity
# that holds secretAccessor can mint a token and read the secret whatever it was
# submitted to do — the secret's name is derivable, so non-enumerability is not a
# control. Separate identities make the recorded authentication stratum match
# what the task can obtain and prevent a mis-submitted anonymous case from
# silently running authenticated.
#
# So the anonymous worker in worker.tf CANNOT read this secret, and this identity
# exists only to run the authenticated cases. Submitting a case in the
# authenticated stratum means submitting its job with THIS service account; the
# orchestrator's flag chooses the identity, and IAM does the enforcing.
#
# ── The payload ───────────────────────────────────────────────────────────────
#
# ONE secret, whose payload is KEY=VALUE lines — the format the tools' own
# environment already uses:
#
#   AWS_ACCESS_KEY_ID=AKIA...
#   AWS_SECRET_ACCESS_KEY=wJal...
#   AWS_SESSION_TOKEN=...            # only for short-lived credentials
#
# One secret rather than three is one grant and one rotation. Lines rather than
# JSON because a human writes this by hand: no quoting, no jq, and a mistake is
# visible. Lines rather than a positional delimiter because the session token is
# optional and a secret access key may itself contain '/' and '+'.
#
# TERRAFORM CREATES THE CONTAINER, NEVER THE VALUE. No google_secret_manager_
# secret_version resource appears here on purpose: a secret version's payload is
# stored in plain text in state, so writing the key through Terraform would put
# it in the state bucket and in every plan output. Add it out of band:
#
#   gcloud secrets versions add <secret id> --project=<project> --data-file=- <<'EOF'
#   AWS_ACCESS_KEY_ID=...
#   AWS_SECRET_ACCESS_KEY=...
#   EOF

resource "google_secret_manager_secret" "aws_credentials" {
  count     = var.create_aws_credentials_secret ? 1 : 0
  project   = var.project
  secret_id = "${local.name}-aws-credentials"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  labels = {
    purpose = "authenticated-listing-stratum"
  }
}

# ── The authenticated worker ──────────────────────────────────────────────────
# Identical to the anonymous worker in every respect except that it, and only it,
# can read the credential. Same least-privilege shape: pull its image, report
# task state, write its own attempt, read nothing back.

resource "google_service_account" "authenticated_worker" {
  count        = var.create_aws_credentials_secret ? 1 : 0
  project      = var.project
  account_id   = "${local.name}-auth-worker"
  display_name = "s3-listing-study authenticated attempt worker"
  description  = "Identity Batch tasks run as for cases in the authenticated stratum"
}

resource "google_project_iam_member" "authenticated_worker" {
  for_each = var.create_aws_credentials_secret ? toset([
    "roles/batch.agentReporter",
    "roles/logging.logWriter",
  ]) : toset([])
  project = var.project
  role    = each.value
  member  = "serviceAccount:${google_service_account.authenticated_worker[0].email}"
}

resource "google_artifact_registry_repository_iam_member" "authenticated_worker_pull" {
  count      = var.create_aws_credentials_secret ? 1 : 0
  project    = var.project
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.authenticated_worker[0].email}"
}

resource "google_storage_bucket_iam_member" "authenticated_worker_write" {
  count  = var.create_aws_credentials_secret ? 1 : 0
  bucket = google_storage_bucket.results.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.authenticated_worker[0].email}"
}

resource "google_storage_bucket_iam_member" "authenticated_worker_fixture_read" {
  count  = var.create_aws_credentials_secret ? 1 : 0
  bucket = google_storage_bucket.results.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.authenticated_worker[0].email}"

  condition {
    title       = "replay-fixtures-only"
    description = "Read only immutable staged replay fixtures"
    expression  = "resource.type == \"storage.googleapis.com/Object\" && resource.name.startsWith(\"projects/_/buckets/${google_storage_bucket.results.name}/objects/fixtures/\")"
  }
}

# The grant that defines the stratum. On the secret, not at project scope, and on
# this identity alone.
resource "google_secret_manager_secret_iam_member" "authenticated_worker_access" {
  count     = var.create_aws_credentials_secret ? 1 : 0
  project   = var.project
  secret_id = google_secret_manager_secret.aws_credentials[0].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.authenticated_worker[0].email}"
}

# The orchestrator must be able to launch jobs as this identity, exactly as it
# can for the anonymous worker.
resource "google_service_account_iam_member" "manager_actas_authenticated" {
  for_each           = var.create_aws_credentials_secret ? var.manager_members : toset([])
  service_account_id = google_service_account.authenticated_worker[0].name
  role               = "roles/iam.serviceAccountUser"
  member             = each.value
}

resource "google_service_account_iam_member" "runner_actas_authenticated" {
  count              = var.create_aws_credentials_secret && var.create_runner ? 1 : 0
  service_account_id = google_service_account.authenticated_worker[0].name
  role               = "roles/iam.serviceAccountUser"
  member             = local.runner_sa_member

  depends_on = [google_service_account.runner]
}

# The runner may read the credential for an explicitly authorized local
# diagnostic. This grant does not provide a metadata-denial sandbox; the
# operator is responsible for the local execution boundary. Comparative runs
# use the separate authenticated Batch identity above.
#
# Off by default: a deployment that only ever submits jobs has no reason to widen
# the runner, and the authenticated worker above remains the identity that reads
# this secret in the normal path.
resource "google_secret_manager_secret_iam_member" "runner_access" {
  count     = var.create_aws_credentials_secret && var.create_runner && var.runner_reads_aws_credentials ? 1 : 0
  project   = var.project
  secret_id = google_secret_manager_secret.aws_credentials[0].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.runner_sa_member

  depends_on = [google_service_account.runner]
}
