variable "project" {
  description = "GCP project ID that owns the study's infrastructure. The ID, never the project number: service account emails are built from the ID, and a number would produce grants against a principal that does not exist while planning cleanly."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project))
    error_message = "project must be a GCP project ID, not a project number: 6-30 characters, starting with a letter."
  }
}

variable "region" {
  description = "Region for the results bucket, Artifact Registry repository, and any created subnet (e.g. us-east1). Keeping the runner, bucket, and Batch jobs together avoids cross-region reads during verification."
  type        = string
}

variable "name_prefix" {
  description = "Stem for every resource name in this module, so a second study estate can coexist in one project. Bounded by the service account ID limit of 30 characters: the longest derived name is <name_prefix>-auth-worker, twelve characters of suffix, so the stem may be at most 18."
  type        = string
  default     = "s3-listing-study"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,16}[a-z0-9]$", var.name_prefix))
    error_message = "name_prefix must be 3-18 characters of lowercase letters, digits, or hyphens, starting with a letter and ending alphanumeric. The 18-character cap leaves room for the longest suffix this module appends (-auth-worker) within GCP's 30-character service account ID limit."
  }
}

variable "manager_members" {
  description = "IAM principals (full member strings, e.g. serviceAccount:ci@proj.iam.gserviceaccount.com) granted the complete orchestrator bundle: manage Batch jobs, actAs worker identities, read/manage results, and optionally publish the toolbox. The runner VM gets this bundle automatically and does not belong here."
  type        = set(string)
  default     = []

  validation {
    condition     = alltrue([for m in var.manager_members : can(regex("^(serviceAccount:|user:|group:|domain:|principal://|principalSet://)", m))])
    error_message = "Each manager member must be a full IAM principal string (serviceAccount:, user:, group:, domain:, or a workload-identity principal:// or principalSet:// address)."
  }
}

# ── Network ───────────────────────────────────────────────────────────────────

variable "create_network" {
  description = "Create a minimal VPC and subnet. Leave false to use the project's default VPC; set true only where org policy suppressed default network creation."
  type        = bool
  default     = false
}

variable "subnet_cidr" {
  description = "IPv4 range for the created subnet. Ignored unless create_network is true. Needs only enough addresses for the campaign's peak concurrency plus the runner."
  type        = string
  default     = "10.10.0.0/20"
}

# ── Authenticated stratum ─────────────────────────────────────────────────────

variable "create_aws_credentials_secret" {
  description = "Create the authenticated listing stratum: a Secret Manager secret for AWS credentials, plus a SEPARATE worker service account that alone can read it. Batch metadata access is intentional, so the attached identity determines whether a task can obtain the secret and keeps recorded auth aligned with capability. Terraform creates the secret container only; add the value out of band, because a version's payload is stored in plain text in state. Leave false for an anonymous-only estate."
  type        = bool
  default     = false
}

# ── Runner ────────────────────────────────────────────────────────────────────

variable "create_runner" {
  description = "Create the campaign runner VM. Set false to orchestrate from elsewhere — a workstation or CI — in which case that principal belongs in manager_members instead."
  type        = bool
  default     = true
}

variable "runner_reads_aws_credentials" {
  description = "Grant the runner read access to the AWS credential secret for an explicitly authorized local diagnostic. Comparative authenticated runs use the separate authenticated Batch worker; leave this false when the runner only manages campaigns."
  type        = bool
  default     = false
}

variable "runner_zone" {
  description = "Zone for the runner VM. Must be inside var.region, and must offer the chosen machine type."
  type        = string
}

variable "runner_machine_type" {
  description = "Runner machine type. Sized for the multi-stage benchmark toolbox build, not for measurement — nothing timed runs on this machine."
  type        = string
  default     = "e2-standard-2"
}

variable "runner_image" {
  description = "Boot image for the runner. x86_64 avoids emulation while the toolbox wrapper builds its explicit linux/amd64 target for Batch."
  type        = string
  default     = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64"
}

variable "runner_disk_gb" {
  description = "Runner boot disk size for the multi-stage toolbox, downloaded tool closures, and Docker build cache."
  type        = number
  default     = 100
}

variable "runner_disk_type" {
  description = "Runner boot disk type. pd-balanced suits e2/n2; c4 and c4a families require hyperdisk-balanced instead."
  type        = string
  default     = "pd-balanced"
}

variable "runner_ssh_members" {
  description = "IAM principals (full member strings) allowed to SSH to the runner: OS Login as a normal user, plus the IAP tunnel the firewall restricts port 22 to. WHO may reach the machine is a deployment decision, so it arrives from the caller — the module only owns the fact that reaching it requires both grants. Use runner_ssh_sudo for principals that must also install the toolchain."
  type        = set(string)
  default     = []

  validation {
    condition     = alltrue([for m in var.runner_ssh_members : can(regex("^(serviceAccount:|user:|group:|domain:|principal://|principalSet://)", m))])
    error_message = "Each SSH member must be a full IAM principal string (serviceAccount:, user:, group:, domain:, or a workload-identity principal:// or principalSet:// address)."
  }
}

variable "runner_ssh_sudo" {
  description = "Grant runner_ssh_members roles/compute.osAdminLogin (root on the machine) instead of roles/compute.osLogin. True by default because the runner is provisioned by hand — installing Docker and the toolchain needs root."
  type        = bool
  default     = true
}

variable "runner_running" {
  description = "Keep the runner powered on. Set false to stop the VM between campaigns: compute billing ends, the boot disk and everything hand-installed on it survive, and flipping it back needs no reprovisioning. This is the everyday cost knob — prefer it to create_runner = false, which destroys the machine and the provisioning with it."
  type        = bool
  default     = true
}

variable "runner_external_ip" {
  description = "Give the runner an ephemeral external IP. True by default because that is how it reaches Docker Hub, the registry, and the pinned interpreter archive without a Cloud NAT — this module provisions no NAT. SSH ingress is restricted to the IAP range regardless. Set false only where the network already provides egress some other way."
  type        = bool
  default     = true
}
