# ── Results bucket ────────────────────────────────────────────────────────────
#
# Landing zone for everything a campaign produces under campaigns/<campaign>/:
# frozen plan/manifest objects and authoritative
# <bucket>/<tool>/<case>/run-<n>/<attempt-uuid>/** trees containing result
# summaries and raw audit evidence.
#
# Versioning is on because the study's rule is that an attempt is never
# overwritten and a retry is always a new attempt. Versioning does not enforce
# that — the worker's objectCreator grant in worker.tf does — it makes a
# violation recoverable instead of silent.
#
# prevent_destroy guards recorded evidence: force_destroy = false alone still
# lets `tofu destroy` drop an emptied bucket.

resource "random_id" "results_suffix" {
  byte_length = 4
}

resource "google_storage_bucket" "results" {
  name     = "${local.name}-results-${random_id.results_suffix.hex}"
  project  = var.project
  location = var.region

  force_destroy               = false
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}
