# ── Derived-image registry ────────────────────────────────────────────────────
#
# Holds one derived attempt image per registered tool: the subject image with the
# study's pinned interpreter and attempt engine layered in.
#
# Images arrive under the name the build derives from each tool's registration —
# <tool>:<subject version>-h<harness version>-<subject digest prefix> — so a tag
# states both versions it combines and cannot be mistaken for the upstream image
# it wraps.
#
# No cleanup policy. A campaign's results reference the exact derived digest they
# ran, and deleting those images would break the ability to re-run or audit a
# published number. Prune deliberately, once a campaign is retired.

resource "google_artifact_registry_repository" "images" {
  project       = var.project
  location      = var.region
  repository_id = local.name
  format        = "DOCKER"
  description   = "Derived attempt images: each subject tool plus the study's pinned attempt engine"
}
