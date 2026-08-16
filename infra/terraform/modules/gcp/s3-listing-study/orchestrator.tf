# ── Orchestrator privilege bundle ─────────────────────────────────────────────
#
# The complete privilege set for driving a campaign: submit and monitor Batch
# jobs, actAs the worker service account, read and write the results bucket, and
# optionally publish the single benchmark toolbox.
#
# Which roles make up the bundle is intrinsic to this concern and lives here; WHO
# holds it is a deployment choice, so members arrive through var.manager_members.
# Every member receives the whole bundle, so no principal is ever left
# half-granted and failing partway through a campaign.
#
# The runner VM in runner.tf holds this same bundle and is granted separately —
# its address is derived rather than passed in, because a module cannot ask its
# caller for the address of a service account the module itself creates.
#
# Additive google_*_iam_member throughout, never authoritative _iam_binding or
# _iam_policy: an authoritative grant on the bucket would clobber the worker's
# objectCreator on the same resource.

locals {
  manager_project_roles = [
    "roles/batch.jobsEditor", # submit, list, and cancel a campaign's jobs
    "roles/logging.viewer",   # read task logs when diagnosing an infra failure
  ]

  manager_project_grants = {
    for pair in setproduct(tolist(var.manager_members), local.manager_project_roles) :
    "${pair[0]}|${pair[1]}" => { member = pair[0], role = pair[1] }
  }
}

resource "google_project_iam_member" "manager" {
  for_each = local.manager_project_grants
  project  = var.project
  role     = each.value.role
  member   = each.value.member
}

resource "google_service_account_iam_member" "manager_actas" {
  for_each           = var.manager_members
  service_account_id = google_service_account.worker.name
  role               = "roles/iam.serviceAccountUser"
  member             = each.value
}

resource "google_storage_bucket_iam_member" "manager_bucket" {
  for_each = var.manager_members
  bucket   = google_storage_bucket.results.name
  role     = "roles/storage.objectAdmin"
  member   = each.value
}

resource "google_artifact_registry_repository_iam_member" "manager_push" {
  for_each   = var.manager_members
  project    = var.project
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = each.value
}
