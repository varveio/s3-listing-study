#!/usr/bin/env bash
# Cheap fail-closed gate consumed before every networked harness container.
# It never provisions or repairs the runner; see runner-security-provision.sh.
set -euo pipefail
export LC_ALL=C

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=harness/runner-security-lib.sh
. "$HERE/runner-security-lib.sh"

STATE_FILE="${S3_STUDY_SECURITY_STATE_FILE:-/etc/s3-listing-study/runner-ready.env}"
INSTALLED_HELPER="${S3_STUDY_SECURITY_INSTALLED_HELPER:-/usr/local/libexec/s3-study-firewall-state}"
RULES_V4="${S3_STUDY_SECURITY_RULES_V4:-/etc/s3-listing-study/runner-firewall.v1.iptables}"
RULES_V6="${S3_STUDY_SECURITY_RULES_V6:-/etc/s3-listing-study/runner-firewall.v1.ip6tables}"
QUIET=no
BUCKET=""
REGION=""

die() { printf 'runner-security-check: %s\n' "$*" >&2; exit 2; }
say() { [ "$QUIET" = yes ] || printf 'runner-security-check: %s\n' "$*" >&2; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --quiet) QUIET=yes; shift ;;
    --bucket) BUCKET="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -n "$BUCKET" ] || die "--bucket is required"
[ -n "$REGION" ] || die "--region is required"
security_validate_bucket "$BUCKET" || die "bucket is not supported by the virtual-hosted HTTPS canary (use 3-63 lowercase letters/digits/hyphens, no dots)"
security_validate_region "$REGION" || die "invalid region"

for command_name in docker jq sha256sum curl stat timeout; do
  command -v "$command_name" >/dev/null 2>&1 || die "required command is missing: $command_name"
done

[ -f "$STATE_FILE" ] && [ ! -L "$STATE_FILE" ] || die "missing regular readiness record: $STATE_FILE; provision the runner first"
if [ "${S3_STUDY_SECURITY_ALLOW_UNPRIVILEGED_STATE:-no}" != yes ]; then
  [ "$(stat -c %u "$STATE_FILE")" = 0 ] || die "readiness record is not root-owned: $STATE_FILE"
  state_mode="$(stat -c %a "$STATE_FILE")"
  case "$state_mode" in
    *[2367][0-7]|*[0-7][2367]) die "readiness record is group/world writable (mode $state_mode)" ;;
  esac
fi
grep -q $'\r' "$STATE_FILE" && die "readiness record contains CR bytes"
grep -qvE '^(version|profile_id|provider|host_id_sha256|boot_id_sha256|docker_id|docker_networks_sha256|network_name|network_id|bridge_name|ipv4_subnet|ipv4_gateway|mtu|ipv6|firewall_backend|policy_source_sha256|rules_v4_sha256|rules_v6_sha256|live_policy_sha256|helper_sha256|probe_image|provisioned_at)=[^[:cntrl:]]+$' "$STATE_FILE" \
  && die "readiness record contains an unknown, empty, or malformed field"

field() {
  local key="$1" count
  count="$(grep -c "^${key}=" "$STATE_FILE" || true)"
  [ "$count" = 1 ] || die "readiness record field '$key' occurs $count times"
  sed -n "s/^${key}=//p" "$STATE_FILE"
}

VERSION="$(field version)"
PROFILE="$(field profile_id)"
PROVIDER="$(field provider)"
HOST_ID="$(field host_id_sha256)"
BOOT_ID="$(field boot_id_sha256)"
DOCKER_ID="$(field docker_id)"
DOCKER_NETWORKS_SHA="$(field docker_networks_sha256)"
NETWORK="$(field network_name)"
NETWORK_ID="$(field network_id)"
BRIDGE="$(field bridge_name)"
SUBNET="$(field ipv4_subnet)"
GATEWAY="$(field ipv4_gateway)"
MTU="$(field mtu)"
IPV6="$(field ipv6)"
BACKEND="$(field firewall_backend)"
POLICY_SHA="$(field policy_source_sha256)"
RULES_V4_SHA="$(field rules_v4_sha256)"
RULES_V6_SHA="$(field rules_v6_sha256)"
LIVE_SHA="$(field live_policy_sha256)"
HELPER_SHA="$(field helper_sha256)"
PROBE_IMAGE="$(field probe_image)"
field provisioned_at >/dev/null

[ "$VERSION" = 1 ] || die "unsupported readiness-record version: $VERSION"
[ "$PROFILE" = "$SECURITY_PROFILE_ID" ] || die "security profile mismatch: $PROFILE"
[ "$PROVIDER" = "$SECURITY_PROVIDER" ] || die "provider mismatch: $PROVIDER (this build supports local only)"
[ "$NETWORK" = "$SECURITY_NETWORK" ] || die "network mismatch: $NETWORK"
[ "$BRIDGE" = "$SECURITY_BRIDGE" ] || die "bridge mismatch: $BRIDGE"
[ "$SUBNET" = "$SECURITY_IPV4_SUBNET" ] || die "subnet mismatch: $SUBNET"
[ "$GATEWAY" = "$SECURITY_IPV4_GATEWAY" ] || die "gateway mismatch: $GATEWAY"
[ "$IPV6" = false ] || die "study bridge must be IPv4-only"
[ "$BACKEND" = iptables ] || die "unsupported firewall backend: $BACKEND (MVP supports iptables only)"
printf '%s' "$MTU" | grep -Eq '^[0-9]+$' && [ "$MTU" -ge 576 ] || die "invalid recorded MTU: $MTU"
case "$PROBE_IMAGE" in *@sha256:*) ;; *) die "probe image is not digest-pinned: $PROBE_IMAGE" ;; esac
printf '%s' "$PROBE_IMAGE" | grep -Eq '^[^[:space:][:cntrl:]]+@sha256:[0-9a-f]{64}$' || die "probe image contains invalid characters"
probe_digest="${PROBE_IMAGE##*@sha256:}"
printf '%s' "$probe_digest" | grep -Eq '^[0-9a-f]{64}$' || die "probe image digest is malformed: $PROBE_IMAGE"

[ -r /etc/machine-id ] || die "cannot read /etc/machine-id to bind readiness to this host"
[ -r /proc/sys/kernel/random/boot_id ] || die "cannot read the kernel boot ID"
current_host_id="$(sha256sum /etc/machine-id | cut -d' ' -f1)" \
  || die "cannot hash /etc/machine-id"
boot_id_value="$(cat /proc/sys/kernel/random/boot_id)" \
  || die "cannot read the kernel boot ID"
current_boot_id="$(printf '%s' "$boot_id_value" | sha256sum | cut -d' ' -f1)" \
  || die "cannot hash the kernel boot ID"
current_docker_id="$(security_docker_control info --format '{{.ID}}' 2>/dev/null)" || die "cannot inspect Docker daemon identity within the control-plane timeout"
current_backend="$(security_docker_control info --format '{{.FirewallBackend.Driver}}' 2>/dev/null)" || die "cannot determine Docker firewall backend within the control-plane timeout"
[ -n "$current_host_id" ] && [ "$current_host_id" = "$HOST_ID" ] || die "readiness record belongs to a different host"
[ "$current_boot_id" = "$BOOT_ID" ] || die "readiness record predates this boot; re-provision/revalidate the live firewall"
[ "$current_docker_id" = "$DOCKER_ID" ] || die "readiness record belongs to a different Docker daemon"
[ "$current_backend" = "$BACKEND" ] || die "Docker firewall backend changed: recorded=$BACKEND live=$current_backend"
mapfile -t current_network_ids < <(security_docker_control network ls -q)
[ "${#current_network_ids[@]}" -gt 0 ] || die "Docker reports no networks"
current_networks_sha="$(security_docker_control network inspect "${current_network_ids[@]}" | jq -cS '[.[] | {Id,Name,Driver,Internal,EnableIPv6,IPAM:.IPAM.Config}] | sort_by(.Id)' | sha256sum | cut -d' ' -f1)"
[ "$current_networks_sha" = "$DOCKER_NETWORKS_SHA" ] || die "Docker network topology changed since provisioning; re-provision the private-network deny policy"

policy_now="$(sha256sum "$SECURITY_POLICY_FILE" | cut -d' ' -f1)"
[ "$policy_now" = "$POLICY_SHA" ] || die "repo firewall policy changed since provisioning"
for rendered in "$RULES_V4" "$RULES_V6"; do
  [ -f "$rendered" ] && [ ! -L "$rendered" ] || die "installed rendered firewall policy is missing: $rendered"
  if [ "${S3_STUDY_SECURITY_ALLOW_UNPRIVILEGED_STATE:-no}" != yes ]; then
    [ "$(stat -c %u "$rendered")" = 0 ] || die "installed rendered firewall policy is not root-owned: $rendered"
  fi
done
[ "$(sha256sum "$RULES_V4" | cut -d' ' -f1)" = "$RULES_V4_SHA" ] || die "installed IPv4 policy changed"
[ "$(sha256sum "$RULES_V6" | cut -d' ' -f1)" = "$RULES_V6_SHA" ] || die "installed IPv6 policy changed"
[ -f "$INSTALLED_HELPER" ] && [ ! -L "$INSTALLED_HELPER" ] || die "installed firewall-state helper is missing"
if [ "${S3_STUDY_SECURITY_ALLOW_UNPRIVILEGED_STATE:-no}" != yes ]; then
  [ "$(stat -c %u "$INSTALLED_HELPER")" = 0 ] || die "installed firewall-state helper is not root-owned"
fi
[ "$(sha256sum "$INSTALLED_HELPER" | cut -d' ' -f1)" = "$HELPER_SHA" ] || die "installed firewall-state helper changed"

network_json="$(security_docker_control network inspect "$NETWORK" 2>/dev/null)" || die "study network is missing: $NETWORK"
printf '%s' "$network_json" | jq -e \
  --arg id "$NETWORK_ID" --arg bridge "$BRIDGE" --arg subnet "$SUBNET" \
  --arg gateway "$GATEWAY" --arg mtu "$MTU" '
    length == 1 and
    .[0].Id == $id and .[0].Driver == "bridge" and
    .[0].Internal == false and .[0].EnableIPv6 == false and
    .[0].Options["com.docker.network.bridge.name"] == $bridge and
    .[0].Options["com.docker.network.bridge.enable_icc"] == "false" and
    .[0].Options["com.docker.network.driver.mtu"] == $mtu and
    .[0].IPAM.Config[0].Subnet == $subnet and
    .[0].IPAM.Config[0].Gateway == $gateway
  ' >/dev/null || die "study bridge configuration no longer matches readiness"

if [ "${S3_STUDY_SECURITY_ALLOW_UNPRIVILEGED_STATE:-no}" = yes ]; then
  live_policy="$($INSTALLED_HELPER)" || die "cannot inspect live firewall state"
else
  live_policy="$(sudo -n "$INSTALLED_HELPER")" || die "cannot inspect live firewall state through the installed read-only helper"
fi
[ "$(printf '%s\n' "$live_policy" | sha256sum | cut -d' ' -f1)" = "$LIVE_SHA" ] \
  || die "live study firewall state differs from the provisioned policy"
live_tmp="$(mktemp)"; trap 'rm -f -- "$live_tmp"' EXIT
printf '%s\n' "$live_policy" >"$live_tmp"
"$HERE/security/validate-firewall-state.sh" "$live_tmp" "$RULES_V4" "$RULES_V6" >/dev/null \
  || die "live firewall has no canonical first/unique hook path or exact owned chain body"

# The local adapter is intentionally not a cloud identity detector. It refuses
# recognizable GCP/AWS/Azure metadata presence; provider-specific adapters must
# later prove identity attachment through their control planes.
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
[ "$metadata_present" = no ] || die "provider '$metadata_present' metadata is present; local profile refuses cloud runners"

leaked="$(env | grep -E '^(AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN|SECURITY_TOKEN|PROFILE|ROLE_ARN|WEB_IDENTITY_TOKEN_FILE|SHARED_CREDENTIALS_FILE|CONFIG_FILE|CONTAINER_CREDENTIALS_(RELATIVE_URI|FULL_URI)|CONTAINER_AUTHORIZATION_TOKEN(_FILE)?)|GOOGLE_(APPLICATION_CREDENTIALS|OAUTH_ACCESS_TOKEN)|AZURE_CLIENT_(ID|SECRET|CERTIFICATE_PATH)|AZURE_TENANT_ID)=' | cut -d= -f1 | tr '\n' ' ' || true)"
[ -z "$leaked" ] || die "ambient credential variables are present: $leaked"

security_docker_control image inspect "$PROBE_IMAGE" >/dev/null 2>&1 || die "digest-pinned probe image is not present locally: $PROBE_IMAGE (provisioning, not a run, pulls/builds images)"
PROBE_CMD=()
security_append_docker_control_prefix PROBE_CMD
PROBE_NAME="s3study-preflight-$$-$RANDOM"
PROBE_CMD+=(run --rm --name "$PROBE_NAME")
security_append_network_args PROBE_CMD
# Provisioning's live positive controls prove this exact pinned probe image
# supports `nc -z`; this per-run use is the policy canary, not capability discovery.
PROBE_CMD+=("$PROBE_IMAGE" sh -eu -c '
  command -v nc >/dev/null
  command -v wget >/dev/null
  if nc -z -w 1 169.254.169.254 80 >/dev/null 2>&1; then
    echo "metadata/link-local port unexpectedly reachable" >&2
    exit 41
  fi
  wget -q -T 10 -O /dev/null "https://${1}.s3.${2}.amazonaws.com/?list-type=2&max-keys=1"
' sh "$BUCKET" "$REGION")
probe_rc=0
"${PROBE_CMD[@]}" >/dev/null 2>&1 || probe_rc=$?
if [ "$probe_rc" -ne 0 ]; then
  security_reconcile_container_absent "$PROBE_NAME" \
    || die "subject-boundary probe $(security_docker_status "$probe_rc") and bounded cleanup/absence could not be confirmed; discard this runner"
  die "subject-boundary probe $(security_docker_status "$probe_rc"): metadata denial and public S3 reachability are both required"
fi

say "ready: profile=$PROFILE provider=$PROVIDER network=$NETWORK mtu=$MTU policy=$LIVE_SHA"
