# ── Campaign runner VM ────────────────────────────────────────────────────────
#
# One long-lived machine provisioned for everything outside a Batch task:
# toolbox builds and an explicitly authorized push, campaign management,
# verification, and reporting. Bulk artifacts stay in GCS unless verification
# or investigation requests them.
#
# It earns its place for three reasons beyond convenience:
#
#   * Architecture. Batch runs x86_64 and the toolbox wrapper explicitly builds
#     linux/amd64. An amd64 runner avoids emulation in that build path.
#   * Duration. A campaign outlives a laptop session. Its durable SQLite state,
#     polling, verification, and reporting benefit from a long-lived home.
#   * Egress. Routine reporting reads compact results, while a correctness check
#     may fetch large raw artifacts. Keeping the
#     runner in the bucket's region avoids billing for those requested reads.
#
# It is NOT a measurement host. Nothing timed ever runs here — subjects run in
# Batch tasks, one task per fresh VM. This machine's size and noise cannot reach any
# published number, which is why it is sized for Docker builds rather than for
# benchmark isolation.
#
# Deliberately NOT auto-provisioned, mirroring the estate's other build hosts: no
# startup script. SSH in and install the toolchain by hand. A campaign's working
# state then belongs to a machine nobody rebuilds by accident.

locals {
  runner = {
    name = "${local.name}-runner"
    # Every knob in one block, so cloning this module for a second stratum — an
    # arm64 runner, a different region — is copy-and-tweak rather than a hunt.
    zone         = var.runner_zone
    machine_type = var.runner_machine_type
    image        = var.runner_image
    disk_gb      = var.runner_disk_gb
    disk_type    = var.runner_disk_type
  }

  # The default VPC when this module did not create one. The runner and the Batch
  # tasks share it; both need only outbound HTTPS.
  network_name = var.create_network ? google_compute_network.this[0].name : "default"

  # Predictable rather than computed, so IAM for_each keys stay known at plan
  # time. Referencing google_service_account.runner[0].email here would make
  # every grant's key depend on an applied attribute, which Terraform refuses.
  runner_sa_member = "serviceAccount:${local.name}-runner@${var.project}.iam.gserviceaccount.com"
}

# ── Identity ──────────────────────────────────────────────────────────────────
# The runner holds the same orchestrator bundle as any other manager member, plus
# what a VM needs to report about itself. Granted here rather than by asking the
# caller to pass the runner's own address into manager_members, which would be a
# chicken-and-egg inside a single apply.

resource "google_service_account" "runner" {
  count        = var.create_runner ? 1 : 0
  project      = var.project
  account_id   = "${local.name}-runner"
  display_name = "s3-listing-study campaign runner"
  description  = "Identity for toolbox builds/publication, benchmark campaign management, verification, and reporting"
}

resource "google_project_iam_member" "runner" {
  for_each = var.create_runner ? toset(concat(local.manager_project_roles, [
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/osconfig.guestPolicyViewer",
  ])) : toset([])
  project = var.project
  role    = each.value
  member  = local.runner_sa_member

  depends_on = [google_service_account.runner]
}

resource "google_service_account_iam_member" "runner_actas" {
  count              = var.create_runner ? 1 : 0
  service_account_id = google_service_account.worker.name
  role               = "roles/iam.serviceAccountUser"
  member             = local.runner_sa_member

  depends_on = [google_service_account.runner]
}

resource "google_storage_bucket_iam_member" "runner_bucket" {
  count  = var.create_runner ? 1 : 0
  bucket = google_storage_bucket.results.name
  role   = "roles/storage.objectAdmin"
  member = local.runner_sa_member

  depends_on = [google_service_account.runner]
}

resource "google_artifact_registry_repository_iam_member" "runner_push" {
  count      = var.create_runner ? 1 : 0
  project    = var.project
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = local.runner_sa_member

  depends_on = [google_service_account.runner]
}

# ── SSH ───────────────────────────────────────────────────────────────────────
# Ingress only from Identity-Aware Proxy's fixed TCP-forwarding range, so the
# machine is reachable through `gcloud compute ssh --tunnel-through-iap` and not
# from the internet at large, even while it carries an external address for
# egress.
#
# On the default VPC this rule ADDS a path; it cannot remove one. GCP's
# auto-created `default-allow-ssh` opens port 22 to 0.0.0.0/0, and an estate that
# wants IAP-only SSH must delete that rule separately — this module will not
# touch a shared network's pre-existing rules.
#
# Reaching the machine takes two grants, and the firewall is only the first:
# osLogin (or osAdminLogin) to be a user on it, and iap.tunnelResourceAccessor to
# come through the tunnel this rule admits. Both are granted below to
# var.runner_ssh_members — WHO may connect is the caller's decision, so no
# principal is named here.

resource "google_compute_firewall" "runner_ssh" {
  count     = var.create_runner ? 1 : 0
  project   = var.project
  name      = "${local.runner.name}-ssh"
  network   = local.network_name
  direction = "INGRESS"

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["35.235.240.0/20"]
  target_tags   = [local.runner.name]
}

# Both grants are bound to the runner INSTANCE, never to the project. At project
# scope, adding one person here would hand them root SSH on every other VM in a
# shared project — and this module's own README anticipates estates where several
# roots share one.
resource "google_compute_instance_iam_member" "runner_ssh_login" {
  for_each      = var.create_runner ? var.runner_ssh_members : toset([])
  project       = var.project
  zone          = local.runner.zone
  instance_name = google_compute_instance.runner[0].name
  role          = var.runner_ssh_sudo ? "roles/compute.osAdminLogin" : "roles/compute.osLogin"
  member        = each.value
}

resource "google_iap_tunnel_instance_iam_member" "runner_ssh_tunnel" {
  for_each = var.create_runner ? var.runner_ssh_members : toset([])
  project  = var.project
  zone     = local.runner.zone
  instance = google_compute_instance.runner[0].name
  role     = "roles/iap.tunnelResourceAccessor"
  member   = each.value
}

# ── Instance ──────────────────────────────────────────────────────────────────
# The external address exists so the machine can reach Docker Hub, the registry,
# and the pinned interpreter archive without a Cloud NAT. Set
# runner_external_ip = false where the network already provides egress, and the
# VM stays reachable through IAP alone.

resource "google_compute_instance" "runner" {
  count                      = var.create_runner ? 1 : 0
  project                    = var.project
  name                       = local.runner.name
  zone                       = local.runner.zone
  machine_type               = local.runner.machine_type
  tags                       = [local.runner.name]
  key_revocation_action_type = "NONE"
  allow_stopping_for_update  = true
  desired_status             = var.runner_running ? "RUNNING" : "TERMINATED"

  boot_disk {
    auto_delete = true
    initialize_params {
      # A supplied snapshot restores the hand-provisioned runner disk during a
      # deliberate replacement; otherwise the runner starts from its image.
      image    = var.runner_boot_snapshot == null ? local.runner.image : null
      snapshot = var.runner_boot_snapshot
      # Sized for the multi-stage toolbox build, downloaded tool closures, and
      # reusable Docker build cache.
      size = local.runner.disk_gb
      type = local.runner.disk_type
    }
  }

  network_interface {
    network    = local.network_name
    subnetwork = var.create_network ? google_compute_subnetwork.this[0].name : null

    dynamic "access_config" {
      for_each = var.runner_external_ip ? [1] : []
      content {
        # Ephemeral external IP.
      }
    }
  }

  service_account {
    email = google_service_account.runner[0].email
    # Scopes are the legacy authorization layer; the IAM roles above are what
    # actually bound this identity. cloud-platform defers the decision to IAM
    # rather than duplicating it in a second, coarser system.
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  scheduling {
    on_host_maintenance = "MIGRATE"
    automatic_restart   = true
    preemptible         = false
  }

  shielded_instance_config {
    enable_integrity_monitoring = true
    enable_secure_boot          = false
    enable_vtpm                 = true
  }

  metadata = {
    enable-osconfig = "TRUE"
    enable-oslogin  = "TRUE"
    # No startup-script: provision by hand after SSH (see the header).
    # SSH keys are managed through OS Login, not Terraform.
  }

  lifecycle {
    # ssh-keys: OS Login and gcloud write here; Terraform must not fight them.
    #
    # boot image: the family reference resolves to whatever image is current, so
    # leaving it live would replace this machine — and destroy the toolchain
    # installed on it by hand — the first time Canonical publishes a new build.
    # Rebuild deliberately by tainting, having decided what to reinstall.
    #
    # machine_type is deliberately NOT ignored: var.runner_machine_type is the
    # documented way to resize, and allow_stopping_for_update makes it a
    # stop-resize-start rather than a replacement.
    ignore_changes = [
      metadata["ssh-keys"],
      boot_disk[0].initialize_params[0].image,
      boot_disk[0].initialize_params[0].snapshot,
    ]
  }

  depends_on = [
    google_project_iam_member.runner,
    google_artifact_registry_repository_iam_member.runner_push,
  ]
}
