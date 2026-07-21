#!/bin/bash
# Operator-run provisioning for the local Linux/Docker runner profile.
# This script intentionally supports only Docker's iptables firewall backend.
# It is never called by an ordinary run and must be run as root on a disposable
# runner, after all images needed by the campaign have been pulled/built.
set -euo pipefail
export LC_ALL=C
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
umask 022

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=harness/runner-security-lib.sh
. "$HERE/runner-security-lib.sh"

RUNNER_USER=""
PROBE_IMAGE=""
BUCKET=""
REGION=""
STATE_DIR="${S3_STUDY_SECURITY_STATE_DIR:-/etc/s3-listing-study}"
INSTALLED_HELPER="${S3_STUDY_SECURITY_INSTALLED_HELPER:-/usr/local/libexec/s3-study-firewall-state}"

die() { printf 'runner-security-provision: %s\n' "$*" >&2; exit 2; }
say() { printf 'runner-security-provision: %s\n' "$*" >&2; }

if [ "${S3_STUDY_SECURITY_TEST_MODE:-no}" != yes ]; then
  [ "$(id -u)" = 0 ] || die "run as root on the disposable runner"
fi

# Any root-authorized reprovision invocation is a security-state transition.
# Invalidate the exact old record before even parsing/validating new arguments;
# every subsequent failure leaves campaign execution fail-closed.
ready="$STATE_DIR/runner-ready.env"
if [ -e "$ready" ] || [ -L "$ready" ]; then
  [ ! -L "$ready" ] && [ -f "$ready" ] || die "refusing non-regular readiness path: $ready"
  rm -f -- "$ready"
fi

while [ "$#" -gt 0 ]; do
  case "$1" in
    --runner-user) RUNNER_USER="$2"; shift 2 ;;
    --probe-image) PROBE_IMAGE="$2"; shift 2 ;;
    --bucket) BUCKET="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done

printf '%s' "$RUNNER_USER" | grep -Eq '^[a-z_][a-z0-9_-]*[$]?$' || die "--runner-user is required and must be a local user name"
if [ "${S3_STUDY_SECURITY_TEST_MODE:-no}" != yes ]; then
  id "$RUNNER_USER" >/dev/null 2>&1 || die "runner user does not exist: $RUNNER_USER"
fi
case "$PROBE_IMAGE" in *@sha256:*) ;; *) die "--probe-image must be digest-pinned" ;; esac
printf '%s' "$PROBE_IMAGE" | grep -Eq '^[^[:space:][:cntrl:]]+@sha256:[0-9a-f]{64}$' || die "--probe-image contains invalid characters"
printf '%s' "${PROBE_IMAGE##*@sha256:}" | grep -Eq '^[0-9a-f]{64}$' || die "--probe-image digest must be 64 lowercase hex characters"
[ -n "$BUCKET" ] || die "--bucket is required for the public-S3 validation"
[ -n "$REGION" ] || die "--region is required for the public-S3 validation"
security_validate_bucket "$BUCKET" || die "bucket is not supported by the virtual-hosted HTTPS canary (use 3-63 lowercase letters/digits/hyphens, no dots)"
security_validate_region "$REGION" || die "invalid region"

for command_name in docker jq curl ip iptables iptables-restore iptables-save \
  ip6tables ip6tables-restore ip6tables-save install visudo sha256sum; do
  command -v "$command_name" >/dev/null 2>&1 || die "required command is missing: $command_name"
done
[ -x /usr/sbin/iptables-save ] && [ -x /usr/sbin/ip6tables-save ] \
  || die "firewall state helper requires /usr/sbin/iptables-save and /usr/sbin/ip6tables-save"
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet firewalld >/dev/null 2>&1; then
  die "firewalld is active and may rewrite iptables during a run; disable it on the dedicated runner"
fi

leaked="$(env | grep -E '^(AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN|SECURITY_TOKEN|PROFILE|ROLE_ARN|WEB_IDENTITY_TOKEN_FILE|SHARED_CREDENTIALS_FILE|CONFIG_FILE|CONTAINER_CREDENTIALS_(RELATIVE_URI|FULL_URI)|CONTAINER_AUTHORIZATION_TOKEN(_FILE)?)|GOOGLE_(APPLICATION_CREDENTIALS|OAUTH_ACCESS_TOKEN)|AZURE_CLIENT_(ID|SECRET|CERTIFICATE_PATH)|AZURE_TENANT_ID)=' | cut -d= -f1 | tr '\n' ' ' || true)"
[ -z "$leaked" ] || die "ambient credential variables are present: $leaked"

docker_networks_json() {
  local -a ids
  mapfile -t ids < <(docker network ls -q)
  [ "${#ids[@]}" -gt 0 ] || die "Docker reports no networks"
  docker network inspect "${ids[@]}"
}

backend="$(docker info --format '{{.FirewallBackend.Driver}}' 2>/dev/null)" || die "cannot determine Docker firewall backend"
[ "$backend" = iptables ] || die "Docker firewall backend is '$backend'; MVP supports iptables only and will not approximate nftables policy"

# local is an explicit non-cloud profile. A cloud VM needs its provider adapter,
# even when its service account/role was intentionally removed.
metadata_present=no
curl -fsS --noproxy '*' --connect-timeout 0.3 --max-time 0.7 \
  -H 'Metadata-Flavor: Google' http://169.254.169.254/computeMetadata/v1/instance/id \
  >/dev/null 2>&1 && metadata_present=gcp
curl -fsS --noproxy '*' --connect-timeout 0.3 --max-time 0.7 -X PUT \
  -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' http://169.254.169.254/latest/api/token \
  >/dev/null 2>&1 && metadata_present=aws
curl -fsS --noproxy '*' --connect-timeout 0.3 --max-time 0.7 \
  -H 'Metadata: true' 'http://169.254.169.254/metadata/instance?api-version=2021-02-01' \
  >/dev/null 2>&1 && metadata_present=azure
[ "$metadata_present" = no ] || die "provider '$metadata_present' metadata is present; use a future provider adapter, not local"

docker image inspect "$PROBE_IMAGE" >/dev/null 2>&1 \
  || die "probe image is not local. Pull/build it before provisioning; provisioning never resolves a mutable tag"

uplink="$(ip -4 route show default | awk 'NR==1 { for (i=1;i<=NF;i++) if ($i=="dev") { print $(i+1); exit } }')"
[ -n "$uplink" ] && [ -r "/sys/class/net/$uplink/mtu" ] || die "cannot determine default-route interface/MTU"
mtu="$(cat "/sys/class/net/$uplink/mtu")"
printf '%s' "$mtu" | grep -Eq '^[0-9]+$' && [ "$mtu" -ge 576 ] || die "invalid uplink MTU: $mtu"

if ! docker network inspect "$SECURITY_NETWORK" >/dev/null 2>&1; then
  collision="$(docker_networks_json 2>/dev/null | jq -r \
    --arg study "$SECURITY_NETWORK" --arg subnet "$SECURITY_IPV4_SUBNET" '.[] | select(.Name != $study) | . as $n | .IPAM.Config[]? | select(.Subnet == $subnet) | $n.Name' | head -1)"
  [ -z "$collision" ] || die "fixed study subnet $SECURITY_IPV4_SUBNET is already used by Docker network $collision"
  docker network create --driver bridge \
    --subnet "$SECURITY_IPV4_SUBNET" --gateway "$SECURITY_IPV4_GATEWAY" \
    --opt "com.docker.network.bridge.name=$SECURITY_BRIDGE" \
    --opt com.docker.network.bridge.enable_icc=false \
    --opt "com.docker.network.driver.mtu=$mtu" \
    --ipv6=false "$SECURITY_NETWORK" >/dev/null
  say "created $SECURITY_NETWORK ($SECURITY_BRIDGE, MTU $mtu)"
fi

network_json="$(docker network inspect "$SECURITY_NETWORK")"
printf '%s' "$network_json" | jq -e --arg bridge "$SECURITY_BRIDGE" \
  --arg subnet "$SECURITY_IPV4_SUBNET" --arg gateway "$SECURITY_IPV4_GATEWAY" --arg mtu "$mtu" '
  length == 1 and .[0].Driver == "bridge" and .[0].Internal == false and
  .[0].EnableIPv6 == false and
  .[0].Options["com.docker.network.bridge.name"] == $bridge and
  .[0].Options["com.docker.network.bridge.enable_icc"] == "false" and
  .[0].Options["com.docker.network.driver.mtu"] == $mtu and
  .[0].IPAM.Config[0].Subnet == $subnet and .[0].IPAM.Config[0].Gateway == $gateway
' >/dev/null || die "existing study network does not match the fixed profile; do not repair it while subjects are running"

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

work="$(mktemp -d)"
state_tmp=""
cleanup() {
  rm -rf -- "$work"
  [ -z "$state_tmp" ] || rm -f -- "$state_tmp"
}
trap cleanup EXIT
v4="$work/runner-firewall.v1.iptables"
v6="$work/runner-firewall.v1.ip6tables"
{
  printf '*filter\n-F S3STUDY_FWD\n-F S3STUDY_IN\n'
  for cidr in "${deny4[@]}"; do
    printf -- '-A S3STUDY_FWD -d %s -j REJECT --reject-with %s\n' "$cidr" "$reject4"
  done
  # Docker networks created outside the portable ranges are denied too. The
  # readiness hash catches later network topology changes only when policy is
  # reprovisioned, so campaign hosts must not create unrelated networks mid-run.
  docker_networks_json | jq -r \
    --arg study "$SECURITY_NETWORK" '.[] | select(.Name != $study) | .IPAM.Config[]? | .Subnet // empty | select(contains(":" ) | not)' \
    | sort -u | while IFS= read -r cidr; do
        [ -n "$cidr" ] && printf -- '-A S3STUDY_FWD -d %s -j REJECT --reject-with %s\n' "$cidr" "$reject4"
      done
  printf -- '-A S3STUDY_FWD -j RETURN\n-A S3STUDY_IN -j REJECT --reject-with %s\nCOMMIT\n' "$reject4"
} >"$v4"
{
  printf '*filter\n-F S3STUDY_FWD\n-F S3STUDY_IN\n'
  for cidr in "${deny6[@]}"; do
    printf -- '-A S3STUDY_FWD -d %s -j REJECT --reject-with %s\n' "$cidr" "$reject6"
  done
  docker_networks_json | jq -r \
    --arg study "$SECURITY_NETWORK" '.[] | select(.Name != $study) | .IPAM.Config[]? | .Subnet // empty | select(contains(":"))' \
    | sort -u | while IFS= read -r cidr; do
        [ -n "$cidr" ] && printf -- '-A S3STUDY_FWD -d %s -j REJECT --reject-with %s\n' "$cidr" "$reject6"
      done
  printf -- '-A S3STUDY_FWD -j RETURN\n-A S3STUDY_IN -j REJECT --reject-with %s\nCOMMIT\n' "$reject6"
} >"$v6"

# Remove every stale/duplicate jump into an owned chain, regardless of parent or
# position, then rebuild the chain bodies and insert direct bridge-interface
# hooks as rule 1 of INPUT and FORWARD. This path does not depend on Docker's
# placement of its DOCKER-USER jump.
remove_owned_hooks() {
  local tool="$1" save="$2" parent line
  while IFS= read -r parent; do
    [ -n "$parent" ] || continue
    while IFS= read -r line; do
      [ -n "$line" ] && "$tool" -w -D "$parent" "$line"
    done < <("$tool" -w -L "$parent" --line-numbers -n | awk '$2=="S3STUDY_IN" || $2=="S3STUDY_FWD" {print $1}' | sort -rn)
  done < <("$save" -t filter | awk '/^-A / && / -(j|g) S3STUDY_(IN|FWD)( |$)/ {print $2}' | sort -u)
}

iptables -w -N S3STUDY_FWD 2>/dev/null || true
iptables -w -N S3STUDY_IN 2>/dev/null || true
ip6tables -w -N S3STUDY_FWD 2>/dev/null || true
ip6tables -w -N S3STUDY_IN 2>/dev/null || true
remove_owned_hooks iptables iptables-save
remove_owned_hooks ip6tables ip6tables-save
iptables-restore --wait --noflush <"$v4"
ip6tables-restore --wait --noflush <"$v6"
iptables -w -I INPUT 1 -i "$SECURITY_BRIDGE" -j S3STUDY_IN
iptables -w -I FORWARD 1 -i "$SECURITY_BRIDGE" -j S3STUDY_FWD
ip6tables -w -I INPUT 1 -i "$SECURITY_BRIDGE" -j S3STUDY_IN
ip6tables -w -I FORWARD 1 -i "$SECURITY_BRIDGE" -j S3STUDY_FWD

install -d -o root -g root -m 0755 "$STATE_DIR" /usr/local/libexec
install -o root -g root -m 0755 "$HERE/security/firewall-state.sh" "$INSTALLED_HELPER"
install -o root -g root -m 0644 "$v4" "$STATE_DIR/runner-firewall.v1.iptables"
install -o root -g root -m 0644 "$v6" "$STATE_DIR/runner-firewall.v1.ip6tables"

preflight_live="$work/firewall.before-live"
"$INSTALLED_HELPER" >"$preflight_live"
"$HERE/security/validate-firewall-state.sh" "$preflight_live" \
  "$STATE_DIR/runner-firewall.v1.iptables" "$STATE_DIR/runner-firewall.v1.ip6tables" \
  || die "provisioned firewall does not have canonical first/unique hooks and exact owned chain bodies"

sudoers_tmp="$work/sudoers"
printf '%s ALL=(root) NOPASSWD: %s ""\n' "$RUNNER_USER" "$INSTALLED_HELPER" >"$sudoers_tmp"
visudo -cf "$sudoers_tmp" >/dev/null
install -o root -g root -m 0440 "$sudoers_tmp" /etc/sudoers.d/s3-listing-study-runner-security

# Full live canaries run before readiness is minted. They create only namespaced
# temporary containers/networks and never toggle this production policy.
"$HERE/runner-security-live-test.sh" --probe-image "$PROBE_IMAGE" --bucket "$BUCKET" --region "$REGION"

live_policy="$($INSTALLED_HELPER)"
printf '%s\n' "$live_policy" >"$work/firewall.final"
"$HERE/security/validate-firewall-state.sh" "$work/firewall.final" \
  "$STATE_DIR/runner-firewall.v1.iptables" "$STATE_DIR/runner-firewall.v1.ip6tables" \
  || die "live validation left the firewall outside the canonical policy"
live_sha="$(printf '%s\n' "$live_policy" | sha256sum | cut -d' ' -f1)"
network_id="$(printf '%s' "$network_json" | jq -r '.[0].Id')"
state_tmp="$(mktemp "$STATE_DIR/.runner-ready.env.XXXXXX")"
{
  printf 'version=1\nprofile_id=%s\nprovider=%s\n' "$SECURITY_PROFILE_ID" "$SECURITY_PROVIDER"
  printf 'host_id_sha256=%s\n' "$(sha256sum /etc/machine-id | cut -d' ' -f1)"
  printf 'boot_id_sha256=%s\n' "$(printf '%s' "$(cat /proc/sys/kernel/random/boot_id)" | sha256sum | cut -d' ' -f1)"
  printf 'docker_id=%s\n' "$(docker info --format '{{.ID}}')"
  printf 'docker_networks_sha256=%s\n' "$(docker_networks_json | jq -cS '[.[] | {Id,Name,Driver,Internal,EnableIPv6,IPAM:.IPAM.Config}] | sort_by(.Id)' | sha256sum | cut -d' ' -f1)"
  printf 'network_name=%s\nnetwork_id=%s\nbridge_name=%s\n' "$SECURITY_NETWORK" "$network_id" "$SECURITY_BRIDGE"
  printf 'ipv4_subnet=%s\nipv4_gateway=%s\nmtu=%s\nipv6=false\n' "$SECURITY_IPV4_SUBNET" "$SECURITY_IPV4_GATEWAY" "$mtu"
  printf 'firewall_backend=%s\n' "$backend"
  printf 'policy_source_sha256=%s\n' "$(sha256sum "$SECURITY_POLICY_FILE" | cut -d' ' -f1)"
  printf 'rules_v4_sha256=%s\nrules_v6_sha256=%s\n' \
    "$(sha256sum "$STATE_DIR/runner-firewall.v1.iptables" | cut -d' ' -f1)" \
    "$(sha256sum "$STATE_DIR/runner-firewall.v1.ip6tables" | cut -d' ' -f1)"
  printf 'live_policy_sha256=%s\n' "$live_sha"
  printf 'helper_sha256=%s\n' "$(sha256sum "$INSTALLED_HELPER" | cut -d' ' -f1)"
  printf 'probe_image=%s\nprovisioned_at=%s\n' "$PROBE_IMAGE" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$state_tmp"
chown root:root "$state_tmp"
chmod 0644 "$state_tmp"
mv -f "$state_tmp" "$STATE_DIR/runner-ready.env"
state_tmp=""

say "ready: $SECURITY_PROFILE_ID provider=local network=$SECURITY_NETWORK MTU=$mtu policy=$live_sha"
