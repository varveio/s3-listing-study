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

# objectAdmin: the orchestration change on the other branch has the worker
# read and manage its own attempt's objects (not just create them), so the
# narrower objectCreator grant no longer covers what the worker does.
resource "google_storage_bucket_iam_member" "worker_write" {
  bucket = google_storage_bucket.results.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.worker.email}"
}
