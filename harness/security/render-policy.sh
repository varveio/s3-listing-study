#!/usr/bin/env bash
# Render the versioned deny policy into the exact iptables/ip6tables bodies the
# study chains must carry. Deterministic and side-effect-free: the readiness
# check renders it per run to compare against the live firewall, and the
# operator renders it once to install the rules. Same bytes both times.
set -euo pipefail
export LC_ALL=C

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=harness/runner-security-lib.sh
. "$HERE/../runner-security-lib.sh"

[ "$#" -eq 2 ] || { printf 'usage: %s V4_OUT V6_OUT\n' "$0" >&2; exit 2; }
v4_out="$1"; v6_out="$2"

die() { printf 'render-policy: %s\n' "$*" >&2; exit 2; }

policy_field() {
  local key="$1" count
  count="$(grep -c "^${key}=" "$SECURITY_POLICY_FILE" || true)"
  [ "$count" = 1 ] || die "policy field '$key' occurs $count times"
  sed -n "s/^${key}=//p" "$SECURITY_POLICY_FILE"
}
[ "$(policy_field profile_id)" = "$SECURITY_PROFILE_ID" ] || die "policy/profile mismatch"
[ "$(policy_field backend)" = iptables ] || die "policy backend mismatch"
IFS=, read -r -a deny4 <<<"$(policy_field ipv4_deny)"
IFS=, read -r -a deny6 <<<"$(policy_field ipv6_deny)"
reject4="$(policy_field reject_ipv4)"
reject6="$(policy_field reject_ipv6)"

# Docker networks outside the portable private ranges are denied too, and the
# set is read live rather than recorded: a network added after installation
# makes the rendered policy differ from the live one, which fails the run closed
# until the operator reinstalls. Removal is caught the same way.
networks_json() {
  local -a ids
  mapfile -t ids < <(security_docker_control network ls -q)
  [ "${#ids[@]}" -gt 0 ] || die "Docker reports no networks"
  security_docker_control network inspect "${ids[@]}"
}
NETWORKS_JSON="$(networks_json)" || die "cannot inspect Docker networks within the control-plane timeout"

foreign_subnets() { # <jq-family-filter>
  printf '%s' "$NETWORKS_JSON" | jq -r --arg study "$SECURITY_NETWORK" \
    ".[] | select(.Name != \$study) | .IPAM.Config[]? | .Subnet // empty | select($1)" | sort -u
}

render() { # <reject> <family-filter> <cidr...>
  local reject="$1" family_filter="$2"; shift 2
  printf '*filter\n-F S3STUDY_FWD\n-F S3STUDY_IN\n'
  local cidr
  for cidr in "$@"; do
    printf -- '-A S3STUDY_FWD -d %s -j REJECT --reject-with %s\n' "$cidr" "$reject"
  done
  while IFS= read -r cidr; do
    [ -n "$cidr" ] || continue
    printf -- '-A S3STUDY_FWD -d %s -j REJECT --reject-with %s\n' "$cidr" "$reject"
  done < <(foreign_subnets "$family_filter")
  printf -- '-A S3STUDY_FWD -j RETURN\n-A S3STUDY_IN -j REJECT --reject-with %s\nCOMMIT\n' "$reject"
}

render "$reject4" 'contains(":") | not' "${deny4[@]}" >"$v4_out"
render "$reject6" 'contains(":")' "${deny6[@]}" >"$v6_out"
