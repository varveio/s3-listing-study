output "results_bucket" {
  description = "Results bucket name. Attempt evidence lands below campaigns/<campaign>/results/<bucket>/<tool>/<case>/run-<n>/submission-<n>/<attempt-uuid>/."
  value       = google_storage_bucket.results.name
}

output "results_bucket_url" {
  description = "Results bucket gs:// URL — workers create one UUID leaf per execution; benchmark verification and reporting resolve and bind those leaves."
  value       = "gs://${google_storage_bucket.results.name}"
}

output "image_repo_url" {
  description = "Docker repository root available for an explicitly authorized benchmark-toolbox publication. Campaign image sets use a final <image_repo_url>/benchmark-toolbox@sha256:<digest> URI; this module does not publish it."
  value       = "${var.region}-docker.pkg.dev/${var.project}/${google_artifact_registry_repository.images.repository_id}"
}

output "worker_sa_email" {
  description = "Email of the worker service account each Batch attempt task runs as. Goes into the job's allocationPolicy service account."
  value       = google_service_account.worker.email
}

output "worker_sa_unique_id" {
  description = "Numeric unique ID of the worker service account, distinct from its email — the value an external trust policy would pin."
  value       = google_service_account.worker.unique_id
}

output "runner_sa_email" {
  description = "Email of the runner VM's service account, or null when create_runner is false."
  value       = var.create_runner ? google_service_account.runner[0].email : null
}

output "runner_external_ip" {
  description = "Ephemeral external IP of the runner, or null when it has none or was not created. Egress path only — SSH ingress is IAP-restricted."
  value       = var.create_runner && var.runner_external_ip ? google_compute_instance.runner[0].network_interface[0].access_config[0].nat_ip : null
}

output "runner_ssh" {
  description = "Convenience command to reach the runner. Port 22 is open to the IAP forwarding range only, so the tunnel flag is required."
  value       = var.create_runner ? "gcloud compute ssh ${local.runner.name} --zone=${local.runner.zone} --project=${var.project} --tunnel-through-iap" : null
}

output "aws_credentials_secret_id" {
  description = "Secret ID holding AWS credentials for the authenticated stratum, or null when not created. The orchestrator passes this name — never the value — into an authenticated case's Batch task."
  value       = var.create_aws_credentials_secret ? google_secret_manager_secret.aws_credentials[0].secret_id : null
}

output "aws_credentials_secret_name" {
  description = "Fully qualified secret resource name, or null when not created. This is the form a Batch task's secret_variables entry takes, suffixed with /versions/latest."
  value       = var.create_aws_credentials_secret ? google_secret_manager_secret.aws_credentials[0].name : null
}

output "network_self_link" {
  description = "Self-link of the created VPC, or null when create_network is false. Pin it into the Batch job's allocationPolicy.network only in the custom-VPC case."
  value       = var.create_network ? google_compute_network.this[0].self_link : null
}

output "subnetwork_self_link" {
  description = "Self-link of the created subnet, or null when create_network is false."
  value       = var.create_network ? google_compute_subnetwork.this[0].self_link : null
}

output "authenticated_worker_sa_email" {
  description = "Identity for Batch tasks in the authenticated stratum — the only one that can read the AWS credentials secret. Null when create_aws_credentials_secret is false. The orchestrator submits an authenticated case with THIS service account and an anonymous case with worker_sa_email; that choice is what enforces the stratum."
  value       = var.create_aws_credentials_secret ? google_service_account.authenticated_worker[0].email : null
}
