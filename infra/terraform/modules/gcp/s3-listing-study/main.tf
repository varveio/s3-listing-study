# ── s3-listing-study: GCP infrastructure ──────────────────────────────────────
#
# Everything a benchmark campaign for this study needs in one GCP project:
#
#   results-bucket.tf   campaigns/<campaign> metadata and attempt trees
#   image-registry.tf   Artifact Registry for the benchmark toolbox
#   worker.tf           the identity each Cloud Batch attempt task runs as
#   orchestrator.tf     the privilege bundle for driving a campaign
#   runner.tf           the VM that builds, pushes, submits, and reads summaries
#   network.tf          optional VPC, for estates without a default network
#
# It creates no Batch jobs. Batch is serverless: a campaign's jobs are rendered
# and submitted at runtime, one per attempt, each with a deterministic job ID so
# submission is idempotent and a lost handle can be recomputed rather than
# recovered.
#
# The module carries no operator's project IDs, bucket names, or principals —
# everything specific to one estate arrives through variables, so the study's
# infrastructure can be read, reviewed, and reproduced by anyone.

locals {
  # One name stem for every resource, so a second study estate can coexist in the
  # same project without collisions.
  name = var.name_prefix
}
