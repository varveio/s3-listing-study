#!/usr/bin/env bash
# Tier-3 stub for harness/runner-security-lib.sh.
#
# The real library gates every container on a provisioned runner: it shells out
# to runner-security-check.sh, which wants a firewall state file, an installed
# helper, and a live Docker daemon. Offline that check refuses, the verifier dies
# rc=3 inside the re-list branch, and the FAIL / DRIFT / ERROR verdicts are
# unreachable — which is why zero committed receipts exercise them.
#
# What this stub changes and what it does NOT:
#   * security_preflight returns 0 — the runner boundary is not what tier 3 is
#     testing. It has its own suite (harness/tests/runner-security-regressions.sh).
#   * every docker path still goes through `docker`, resolved on PATH, so the
#     tier-3 fake docker sees the exact argv the production library builds:
#     the `timeout -k 2s 30s` wrapper, --pull=never, the network profile, the
#     evidence log driver. Nothing about the re-list command line is faked.
#   * the status strings are the production ones verbatim, because they land in
#     the DRIFT_NOTE of an ERROR verify.md and the test asserts on that text.
#
# It is loaded by copying it over runner-security-lib.sh in a STAGED harness
# tree. harness/ itself is never touched: the shipped verifier is the reference
# side of the differential and is copied in byte-identical.

# shellcheck disable=SC2034
SECURITY_NETWORK="s3-listing-study-subjects"
SECURITY_DOCKER_CONTROL_TIMEOUT_S=30
SECURITY_DOCKER_CLEANUP_TIMEOUT_S=10

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

security_confirm_container_absent() {
  local name="$1" remaining
  remaining="$(security_docker_cleanup container ls -a \
    --filter "name=^/${name}$" --format '{{.Names}}' 2>/dev/null)" || return 1
  [ -z "$remaining" ]
}

security_reconcile_container_absent() {
  security_docker_cleanup rm -f "$1" >/dev/null 2>&1 && return 0
  security_confirm_container_absent "$1"
}

security_append_network_args() {
  local -n _security_network_argv="$1"
  _security_network_argv+=(
    --pull=never
    --network "$SECURITY_NETWORK"
    --cap-drop ALL
    --security-opt no-new-privileges:true
  )
}

security_append_evidence_log_args() {
  local -n _security_log_argv="$1"
  _security_log_argv+=(--log-driver=json-file --log-opt max-size=-1)
}

# The one substantive stub. Preflight is a property of the RUNNER, not of the
# implementation under test, and no port of the verifier can change its answer.
security_preflight() {
  security_validate_bucket "$1" || return 1
  security_validate_region "$2" || return 1
  return 0
}

security_validate_bucket() {
  printf '%s' "$1" | grep -Eq '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$'
}

security_validate_region() {
  printf '%s' "$1" | grep -Eq '^[a-z0-9][a-z0-9-]*$'
}
