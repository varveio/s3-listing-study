# ── Batch worker identity ─────────────────────────────────────────────────────
#
# The bounded identity each cooperative Cloud Batch attempt uses. Metadata
# access is intentional: the in-worker uploader obtains this identity's token
# there. Its narrow grants limit mistakes without treating the subject as
# hostile software.
#
# The benchmark lists public buckets anonymously, so this identity holds no
# credentials for any object store under test. "Anonymous" describes the S3
# authentication stratum, not the absence of a cloud identity — the task still
# runs as this service account, which is what authorizes its GCS upload.

resource "google_service_account" "worker" {
  project      = var.project
  account_id   = "${local.name}-worker"
  display_name = "s3-listing-study attempt worker"
  description  = "Identity Cloud Batch attempt tasks run as"
}

resource "google_project_iam_member" "worker" {
  for_each = toset([
    "roles/batch.agentReporter", # the Batch agent reports task state
    "roles/logging.logWriter",   # container stdout/stderr -> Cloud Logging
  ])
  project = var.project
  role    = each.value
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_artifact_registry_repository_iam_member" "worker_pull" {
  project    = var.project
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.worker.email}"
}

# objectCreator, deliberately, rather than objectAdmin.
#
# Overwriting an existing GCS object requires delete as well as create, so
# withholding delete makes "an attempt is never overwritten" an IAM property
# rather than a convention the uploader is trusted to honour. Every worker
# execution mints a new attempt UUID, so a duplicate execution has a new leaf.
#
# There is no viewer role here on purpose: reading results back is the
# orchestrator's job. A worker writes its own attempt and has no reason to see
# any other.
resource "google_storage_bucket_iam_member" "worker_write" {
  bucket = google_storage_bucket.results.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.worker.email}"
}
