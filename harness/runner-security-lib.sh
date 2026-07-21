#!/usr/bin/env bash
# Shared, side-effect-free constants and Docker argv construction for the
# runner-security boundary. Network/firewall provisioning lives in the operator
# script; ordinary harness paths only consume and verify that state.

# These constants are consumed by scripts that source this library; a direct
# linter pass over the library cannot see those references.
# shellcheck disable=SC2034
SECURITY_PROFILE_ID="s3-listing-study-v1"
# shellcheck disable=SC2034
SECURITY_PROVIDER="local"
SECURITY_NETWORK="s3-listing-study-subjects"
# shellcheck disable=SC2034
SECURITY_BRIDGE="s3study0"
# shellcheck disable=SC2034
SECURITY_IPV4_SUBNET="172.30.0.0/24"
# shellcheck disable=SC2034
SECURITY_IPV4_GATEWAY="172.30.0.1"
# shellcheck disable=SC2034
SECURITY_POLICY_FILE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/security/policy.v1.env"
SECURITY_CHECK="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/runner-security-check.sh"

# Docker is a local control plane, but it can still hang while the daemon is
# unhealthy.  Every harness caller goes through these bounds.  The shorter
# cleanup bound keeps EXIT traps finite; callers retain a deterministic name for
# containers whose create/start call may have timed out.
SECURITY_DOCKER_CONTROL_TIMEOUT_S=30
SECURITY_DOCKER_CLEANUP_TIMEOUT_S=10
SECURITY_EVIDENCE_LOG_DRIVER=json-file
SECURITY_EVIDENCE_LOG_CONFIG='{"max-size":"-1"}'

security_append_docker_control_prefix() {
  local -n _security_docker_argv="$1"
  _security_docker_argv+=(timeout -k 2s "${SECURITY_DOCKER_CONTROL_TIMEOUT_S}s" docker)
}

security_docker_control() {
  timeout -k 2s "${SECURITY_DOCKER_CONTROL_TIMEOUT_S}s" docker "$@"
}

security_docker_cleanup() {
  timeout -k 2s "${SECURITY_DOCKER_CLEANUP_TIMEOUT_S}s" docker "$@"
}

# GNU timeout reports 124 when its deadline expires and 137 when the follow-up
# KILL fires (128 + SIGKILL).  Both mean the Docker client's final state is
# unknown and therefore require stable-name cleanup.
security_is_timeout_status() {
  case "$1" in 124|137) return 0 ;; *) return 1 ;; esac
}

security_docker_status() {
  if security_is_timeout_status "$1"; then
    printf 'timed out (Docker wrapper rc=%s)' "$1"
  else
    printf 'failed (Docker rc=%s)' "$1"
  fi
}

# Reconcile an uncertain stable name. `rm` may report "not found" after
# --rm already succeeded, so a failed remove is followed by a bounded inventory
# query. Success means either removal succeeded or absence was positively
# observed; daemon/client failure remains inconclusive.
security_confirm_container_absent() {
  local name="$1" remaining
  remaining="$(security_docker_cleanup container ls -a \
    --filter "name=^/${name}$" --format '{{.Names}}' 2>/dev/null)" || return 1
  [ -z "$remaining" ]
}

security_confirm_network_absent() {
  local name="$1" remaining
  remaining="$(security_docker_cleanup network ls \
    --filter "name=^${name}$" --format '{{.Name}}' 2>/dev/null)" || return 1
  [ -z "$remaining" ]
}

security_reconcile_container_absent() {
  security_docker_cleanup rm -f "$1" >/dev/null 2>&1 && return 0
  security_confirm_container_absent "$1"
}

security_reconcile_network_absent() {
  security_docker_cleanup network rm "$1" >/dev/null 2>&1 && return 0
  security_confirm_network_absent "$1"
}

# The readiness canary uses virtual-hosted HTTPS
# (<bucket>.s3.<region>.amazonaws.com). Keep its supported contract narrower
# than S3 as a whole: lowercase, dotless DNS labels avoid wildcard-certificate
# ambiguity and are sufficient for every registered campaign bucket.
security_validate_bucket() {
  printf '%s' "$1" | grep -Eq '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$'
}

security_validate_region() {
  printf '%s' "$1" | grep -Eq '^[a-z0-9][a-z0-9-]*$'
}

# security_append_network_args ARRAY_NAME
# Appends the complete common profile to an existing bash array. Keep this the
# single source for subject, reference, and security-probe container flags.
security_append_network_args() {
  local -n _security_network_argv="$1"
  _security_network_argv+=(
    --pull=never
    --network "$SECURITY_NETWORK"
    --cap-drop ALL
    --security-opt no-new-privileges:true
  )
}

# Evidentiary listing containers use Docker's local json-file logger with
# rotation disabled. Smoke later retrieves this output through `docker logs`;
# reference re-lists use the same explicit contract while consuming the attached
# stream directly.
security_append_evidence_log_args() {
  local -n _security_log_argv="$1"
  _security_log_argv+=(--log-driver=json-file --log-opt max-size=-1)
}

security_validate_evidence_log_config() {
  [ "$1" = "$SECURITY_EVIDENCE_LOG_DRIVER" ] || return 1
  [ "$2" = "$SECURITY_EVIDENCE_LOG_CONFIG" ]
}

# security_preflight BUCKET REGION
# Test-only path overrides are deliberately removed here so a smoke/verifier
# caller cannot weaken the production readiness check through inherited env.
security_preflight() {
  env -u S3_STUDY_SECURITY_STATE_FILE \
      -u S3_STUDY_SECURITY_INSTALLED_HELPER \
      -u S3_STUDY_SECURITY_RULES_V4 \
      -u S3_STUDY_SECURITY_RULES_V6 \
      -u S3_STUDY_SECURITY_ALLOW_UNPRIVILEGED_STATE \
      "$SECURITY_CHECK" --quiet --bucket "$1" --region "$2"
}
