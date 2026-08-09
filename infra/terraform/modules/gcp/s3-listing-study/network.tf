# ── Network substrate (fallback only) ─────────────────────────────────────────
#
# Batch VMs and the runner need outbound HTTPS to Google APIs and to the object
# stores under test, and nothing else. Enabling the compute API normally
# auto-creates a `default` VPC whose implied allow-all-egress rule already
# suffices, so create_network defaults to false.
#
# Set it true only where org policy (compute.skipDefaultNetworkCreation)
# suppressed that default network. No egress firewall rule is needed either way:
# every VPC carries an implied allow-all egress. A custom VPC only lacks the
# default network's convenience *ingress* rules, which are irrelevant to
# egress-only workers.

resource "google_compute_network" "this" {
  count                   = var.create_network ? 1 : 0
  project                 = var.project
  name                    = local.name
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "this" {
  count         = var.create_network ? 1 : 0
  project       = var.project
  name          = "${local.name}-${var.region}"
  region        = var.region
  network       = google_compute_network.this[0].id
  ip_cidr_range = var.subnet_cidr

  # Without this, a VM with no external address cannot reach Artifact Registry,
  # GCS, or the Batch API at all, and this module provisions no Cloud NAT by
  # design. That combination is reachable through documented options —
  # create_network = true with runner_external_ip = false — and it fails in a
  # confusing way: IAP SSH still works, so it reads as a broken toolchain rather
  # than as a machine with no route to Google.
  private_ip_google_access = true
}
