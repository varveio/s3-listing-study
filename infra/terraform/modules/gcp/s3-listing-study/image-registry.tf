# ── Benchmark toolbox registry ────────────────────────────────────────────────
#
# Holds the single self-contained benchmark toolbox built from the repository's
# eleven capsule recipes. This module creates storage and grants; it does not
# build or publish an image.
#
# A separately authorized publication should use a repository path such as
# <image_repo_url>/benchmark-toolbox and campaigns consume only its immutable
# @sha256 URI. No cleanup policy: prune deliberately after dependent campaigns
# have been retired.

resource "google_artifact_registry_repository" "images" {
  project       = var.project
  location      = var.region
  repository_id = local.name
  format        = "DOCKER"
  description   = "Immutable self-contained benchmark toolbox images"
}
