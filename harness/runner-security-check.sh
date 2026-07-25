#!/usr/bin/env bash
# Cheap fail-closed gate consumed before every networked harness container.
#
# It enforces one property, per run: this run is anonymous by construction.
# Nothing in the environment carries a credential, no instance-metadata service
# answers on this host or from the subject bridge, the bridge still denies
# private networks, and it still reaches public S3. It verifies those facts
# live; it never provisions or repairs the runner, and it does not attest to a
# recorded snapshot of one. Setup is an operator procedure:
# docs/operating/runner-security.md § Operator procedure.
set -euo pipefail
export LC_ALL=C

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=harness/runner-security-lib.sh
. "$HERE/runner-security-lib.sh"

CONFIG_FILE="${S3_STUDY_SECURITY_STATE_FILE:-/etc/s3-listing-study/runner.env}"
INSTALLED_HELPER="${S3_STUDY_SECURITY_INSTALLED_HELPER:-/usr/local/libexec/s3-study-firewall-state}"
UNPRIVILEGED="${S3_STUDY_SECURITY_ALLOW_UNPRIVILEGED_STATE:-no}"
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

for command_name in docker jq curl stat timeout; do
  command -v "$command_name" >/dev/null 2>&1 || die "required command is missing: $command_name"
done

# ---------------------------------------------------------------- no credentials
# A receipt that says auth=anonymous is a claim about this environment, so the
# scan runs first and covers the variables an SDK would silently pick up.
leaked="$(env | grep -E '^(AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN|SECURITY_TOKEN|PROFILE|ROLE_ARN|WEB_IDENTITY_TOKEN_FILE|SHARED_CREDENTIALS_FILE|CONFIG_FILE|CONTAINER_CREDENTIALS_(RELATIVE_URI|FULL_URI)|CONTAINER_AUTHORIZATION_TOKEN(_FILE)?)|GOOGLE_(APPLICATION_CREDENTIALS|OAUTH_ACCESS_TOKEN)|AZURE_CLIENT_(ID|SECRET|CERTIFICATE_PATH)|AZURE_TENANT_ID)=' | cut -d= -f1 | tr '\n' ' ' || true)"
[ -z "$leaked" ] || die "ambient credential variables are present: $leaked"

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

# ------------------------------------------------------------------ probe image
# The only site-specific input: which trusted image carries the canary. It is
# operator configuration, not an attestation — every security property below is
# re-derived live — but it stays root-owned so an unprivileged process cannot
# swap the canary for an image that always exits 0.
[ -f "$CONFIG_FILE" ] && [ ! -L "$CONFIG_FILE" ] || die "missing regular runner config: $CONFIG_FILE; set the runner up first"
if [ "$UNPRIVILEGED" != yes ]; then
  [ "$(stat -c %u "$CONFIG_FILE")" = 0 ] || die "runner config is not root-owned: $CONFIG_FILE"
  config_mode="$(stat -c %a "$CONFIG_FILE")"
  case "$config_mode" in
    *[2367][0-7]|*[0-7][2367]) die "runner config is group/world writable (mode $config_mode)" ;;
  esac
fi
grep -qvE '^probe_image=[^[:space:][:cntrl:]]+@sha256:[0-9a-f]{64}$' "$CONFIG_FILE" \
  && die "runner config carries an unknown, empty, or non-digest-pinned line: $CONFIG_FILE"
[ "$(grep -c '^probe_image=' "$CONFIG_FILE" || true)" = 1 ] \
  || die "runner config must hold exactly one probe_image= line: $CONFIG_FILE"
PROBE_IMAGE="$(sed -n 's/^probe_image=//p' "$CONFIG_FILE")"

# ----------------------------------------------------------------- study bridge
# The firewall hooks are keyed on the bridge interface name, so the bridge the
# subject will actually attach to has to be the one the policy covers.
BACKEND="$(security_docker_control info --format '{{.FirewallBackend.Driver}}' 2>/dev/null)" \
  || die "cannot determine Docker firewall backend within the control-plane timeout"
[ "$BACKEND" = iptables ] || die "Docker firewall backend is '$BACKEND'; this profile validates iptables policy only"
network_json="$(security_docker_control network inspect "$SECURITY_NETWORK" 2>/dev/null)" \
  || die "study network is missing: $SECURITY_NETWORK"
MTU="$(printf '%s' "$network_json" | jq -r '.[0].Options["com.docker.network.driver.mtu"] // empty')"
printf '%s' "$MTU" | grep -Eq '^[0-9]+$' && [ "$MTU" -ge 576 ] || die "study bridge has no usable MTU option: $MTU"
printf '%s' "$network_json" | jq -e \
  --arg bridge "$SECURITY_BRIDGE" --arg subnet "$SECURITY_IPV4_SUBNET" \
  --arg gateway "$SECURITY_IPV4_GATEWAY" '
    length == 1 and
    .[0].Driver == "bridge" and
    .[0].Internal == false and .[0].EnableIPv6 == false and
    .[0].Options["com.docker.network.bridge.name"] == $bridge and
    .[0].Options["com.docker.network.bridge.enable_icc"] == "false" and
    .[0].IPAM.Config[0].Subnet == $subnet and
    .[0].IPAM.Config[0].Gateway == $gateway
  ' >/dev/null || die "study bridge configuration does not match the fixed profile"

# ------------------------------------------------------- private-network denial
# Rendered from the versioned policy in this repository and from the Docker
# networks that exist right now, then compared with the live filter table. The
# comparison is structural, not a digest of a provisioned snapshot: it proves
# the deny is in force during this run.
[ -f "$INSTALLED_HELPER" ] && [ ! -L "$INSTALLED_HELPER" ] || die "installed firewall-state helper is missing: $INSTALLED_HELPER"
if [ "$UNPRIVILEGED" != yes ]; then
  [ "$(stat -c %u "$INSTALLED_HELPER")" = 0 ] || die "installed firewall-state helper is not root-owned"
fi
work="$(mktemp -d)"; trap 'rm -rf -- "$work"' EXIT
"$HERE/security/render-policy.sh" "$work/rules4" "$work/rules6" \
  || die "cannot render the expected firewall policy from $SECURITY_POLICY_FILE"
if [ "$UNPRIVILEGED" = yes ]; then
  live_policy="$($INSTALLED_HELPER)" || die "cannot inspect live firewall state"
else
  live_policy="$(sudo -n "$INSTALLED_HELPER")" || die "cannot inspect live firewall state through the installed read-only helper"
fi
printf '%s\n' "$live_policy" >"$work/live"
"$HERE/security/validate-firewall-state.sh" "$work/live" "$work/rules4" "$work/rules6" >/dev/null \
  || die "the live firewall does not carry this policy's deny on a canonical first/unique hook path; reinstall the rules"

# ------------------------------------------------------------------- the canary
security_docker_control image inspect "$PROBE_IMAGE" >/dev/null 2>&1 || die "digest-pinned probe image is not present locally: $PROBE_IMAGE (setup, not a run, pulls/builds images)"
PROBE_CMD=()
security_append_docker_control_prefix PROBE_CMD
PROBE_NAME="s3study-preflight-$$-$RANDOM"
PROBE_CMD+=(run --rm --name "$PROBE_NAME")
security_append_network_args PROBE_CMD
# The probe asserts its own capabilities before it asserts policy: an image
# without `nc`/`wget` fails the gate rather than passing it by silence.
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

say "ready: profile=$SECURITY_PROFILE_ID provider=$SECURITY_PROVIDER network=$SECURITY_NETWORK mtu=$MTU"
