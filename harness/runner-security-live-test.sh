#!/usr/bin/env bash
# Opt-in live validation of an already-provisioned production bridge/policy.
# It never changes firewall policy. Temporary control resources are namespaced
# to this PID and removed exactly by the cleanup trap.
set -euo pipefail
export LC_ALL=C

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=harness/runner-security-lib.sh
. "$HERE/runner-security-lib.sh"

PROBE_IMAGE=""
BUCKET=""
REGION=""
PRINT_PLAN=no

die() { printf 'runner-security-live-test: %s\n' "$*" >&2; exit 2; }
say() { printf 'runner-security-live-test: %s\n' "$*" >&2; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --probe-image) PROBE_IMAGE="$2"; shift 2 ;;
    --bucket) BUCKET="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --print-plan) PRINT_PLAN=yes; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done
case "$PROBE_IMAGE" in *@sha256:*) ;; *) die "--probe-image must be digest-pinned" ;; esac
printf '%s' "$PROBE_IMAGE" | grep -Eq '^[^[:space:][:cntrl:]]+@sha256:[0-9a-f]{64}$' || die "--probe-image contains invalid characters"
printf '%s' "${PROBE_IMAGE##*@sha256:}" | grep -Eq '^[0-9a-f]{64}$' || die "probe image digest must be 64 lowercase hex characters"
[ -n "$BUCKET" ] || die "--bucket is required"
[ -n "$REGION" ] || die "--region is required"
security_validate_bucket "$BUCKET" || die "bucket is not supported by the virtual-hosted HTTPS canary (use 3-63 lowercase letters/digits/hyphens, no dots)"
security_validate_region "$REGION" || die "invalid region"

if [ "$PRINT_PLAN" = yes ]; then
  printf '%s\n' \
    'positive: temporary control bridge -> temporary host HTTP service' \
    'negative: study bridge -> temporary host HTTP service' \
    'positive: temporary control bridge peer connectivity' \
    'negative: study same-bridge peer connectivity' \
    'negative: study bridge -> peer on another Docker network' \
    'negative: study bridge -> known link-local metadata HTTP port' \
    'positive: study bridge -> public S3 HTTPS LIST'
  exit 0
fi

for command_name in docker python3 mktemp; do
  command -v "$command_name" >/dev/null 2>&1 || die "required command is missing: $command_name"
done
security_docker_control image inspect "$PROBE_IMAGE" >/dev/null 2>&1 || die "probe image is not present locally"
security_docker_control network inspect "$SECURITY_NETWORK" >/dev/null 2>&1 || die "study network is missing"

suffix="${S3_STUDY_SECURITY_TEST_SUFFIX:-$$-$RANDOM}"
printf '%s' "$suffix" | grep -Eq '^[A-Za-z0-9-]+$' || die "invalid temporary-resource suffix"
control_net="s3study-validation-$suffix"
control_peer="s3study-control-peer-$suffix"
study_peer="s3study-study-peer-$suffix"
host_pid=""
work="$(mktemp -d)"
control_net_cleanup_required=no
control_peer_cleanup_required=no
study_peer_cleanup_required=no

cleanup() {
  local cleanup_failed=no
  if [ -n "$host_pid" ]; then
    kill -TERM "$host_pid" >/dev/null 2>&1 || true
    wait "$host_pid" >/dev/null 2>&1 || true
  fi
  if [ "$control_peer_cleanup_required" = yes ]; then security_reconcile_container_absent "$control_peer" || cleanup_failed=yes; fi
  if [ "$study_peer_cleanup_required" = yes ]; then security_reconcile_container_absent "$study_peer" || cleanup_failed=yes; fi
  if [ "$control_net_cleanup_required" = yes ]; then security_reconcile_network_absent "$control_net" || cleanup_failed=yes; fi
  rm -rf -- "$work"
  if [ "$cleanup_failed" = yes ]; then
    printf 'runner-security-live-test: bounded cleanup could not be confirmed; discard this runner\n' >&2
    trap - EXIT
    exit 2
  fi
}
trap cleanup EXIT

security_confirm_network_absent "$control_net" \
  || die "temporary control-network name is occupied or absence cannot be confirmed: $control_net"
control_net_cleanup_required=yes
network_create_rc=0
security_docker_control network create --driver bridge "$control_net" >/dev/null || network_create_rc=$?
if [ "$network_create_rc" -ne 0 ]; then
  security_reconcile_network_absent "$control_net" \
    || die "control-network create $(security_docker_status "$network_create_rc") and cleanup/absence could not be confirmed; discard this runner"
  control_net_cleanup_required=no
  die "temporary control-network create $(security_docker_status "$network_create_rc")"
fi

# A temporary fixed-response host service proves the probe can recognize a
# reachable endpoint. It binds only the study bridge gateway: the disposable
# control bridge must reach that exact service, while the production bridge must
# be rejected on its INPUT path. It never exposes the host CWD.
if [ "${S3_STUDY_SECURITY_TEST_NO_LISTENER:-no}" = yes ]; then
  [ "${S3_STUDY_SECURITY_TEST_MODE:-no}" = yes ] || die "no-listener seam is test-only"
  host_port=18081
else
  python3 - "$work/host.port" "$SECURITY_IPV4_GATEWAY" <<'PY' &
import http.server
import pathlib
import signal
import sys

class FixedResponse(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"runner-security-positive-control\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return

def stop(_signum, _frame):
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
server = http.server.ThreadingHTTPServer((sys.argv[2], 0), FixedResponse)
pathlib.Path(sys.argv[1]).write_text(str(server.server_address[1]), encoding="ascii")
try:
    server.serve_forever()
finally:
    server.server_close()
PY
  host_pid=$!
  for _ in $(seq 1 50); do [ -s "$work/host.port" ] && break; sleep 0.05; done
  [ -s "$work/host.port" ] || die "temporary host service did not start"
  host_port="$(cat "$work/host.port")"
fi

probe_seq=0
probe_run() {
  probe_seq=$((probe_seq + 1))
  local probe_name="s3study-probe-$suffix-$probe_seq" rc=0
  security_docker_control run --rm --pull=never --name "$probe_name" \
    --cap-drop ALL --security-opt no-new-privileges:true "$@" || rc=$?
  if [ "$rc" -ne 0 ]; then
    if ! security_reconcile_container_absent "$probe_name"; then
      printf 'runner-security-live-test: probe %s and cleanup/absence could not be confirmed; discard this runner\n' "$(security_docker_status "$rc")" >&2
      return 125
    fi
  fi
  return "$rc"
}
wait_listener() {
  local container="$1"
  for _ in $(seq 1 50); do
    security_docker_control exec "$container" sh -c 'nc -z -w 1 127.0.0.1 18080' >/dev/null 2>&1 && return 0
    sleep 0.05
  done
  return 1
}

probe_run --network "$control_net" "$PROBE_IMAGE" sh -eu -c \
  'command -v nc >/dev/null; nc -z -w 2 "$1" "$2"' sh "$SECURITY_IPV4_GATEWAY" "$host_port" >/dev/null \
  || die "positive control failed: probe could not reach temporary host service on the disposable control bridge"
probe_rc=0
probe_run --network "$SECURITY_NETWORK" "$PROBE_IMAGE" sh -eu -c \
  'nc -z -w 1 "$1" "$2"' sh "$SECURITY_IPV4_GATEWAY" "$host_port" >/dev/null 2>&1 || probe_rc=$?
[ "$probe_rc" -ne 0 ] || die "study bridge reached a host service"
[ "$probe_rc" -eq 1 ] || die "host-denial probe was inconclusive: $(security_docker_status "$probe_rc")"

peer_rc=0
control_peer_cleanup_required=yes
security_docker_control run -d --pull=never --name "$control_peer" --network "$control_net" --cap-drop ALL \
  --security-opt no-new-privileges:true "$PROBE_IMAGE" sh -eu -c 'exec nc -lk -p 18080' >/dev/null || peer_rc=$?
[ "$peer_rc" -eq 0 ] || {
  security_reconcile_container_absent "$control_peer" \
    || die "control-peer creation $(security_docker_status "$peer_rc") and cleanup/absence could not be confirmed; discard this runner"
  control_peer_cleanup_required=no
  die "temporary control peer creation $(security_docker_status "$peer_rc")"
}
wait_listener "$control_peer" || die "temporary control peer did not start"
control_peer_ip="$(security_docker_control inspect -f "{{with index .NetworkSettings.Networks \"$control_net\"}}{{.IPAddress}}{{end}}" "$control_peer")"
probe_run --network "$control_net" "$PROBE_IMAGE" sh -eu -c \
  'nc -z -w 2 "$1" 18080' sh "$control_peer_ip" >/dev/null \
  || die "positive control failed: peer-connect probe is not capable of observing reachability"

peer_rc=0
study_peer_cleanup_required=yes
security_docker_control run -d --pull=never --name "$study_peer" --network "$SECURITY_NETWORK" --cap-drop ALL \
  --security-opt no-new-privileges:true "$PROBE_IMAGE" sh -eu -c 'exec nc -lk -p 18080' >/dev/null || peer_rc=$?
[ "$peer_rc" -eq 0 ] || {
  security_reconcile_container_absent "$study_peer" \
    || die "study-peer creation $(security_docker_status "$peer_rc") and cleanup/absence could not be confirmed; discard this runner"
  study_peer_cleanup_required=no
  die "temporary study peer creation $(security_docker_status "$peer_rc")"
}
wait_listener "$study_peer" || die "temporary study peer did not start"
study_peer_ip="$(security_docker_control inspect -f "{{with index .NetworkSettings.Networks \"$SECURITY_NETWORK\"}}{{.IPAddress}}{{end}}" "$study_peer")"
probe_rc=0
probe_run --network "$SECURITY_NETWORK" "$PROBE_IMAGE" sh -eu -c \
  'nc -z -w 1 "$1" 18080' sh "$study_peer_ip" >/dev/null 2>&1 || probe_rc=$?
[ "$probe_rc" -ne 0 ] || die "inter-container communication is enabled on the study bridge"
[ "$probe_rc" -eq 1 ] || die "same-bridge denial probe was inconclusive: $(security_docker_status "$probe_rc")"
probe_rc=0
probe_run --network "$SECURITY_NETWORK" "$PROBE_IMAGE" sh -eu -c \
  'nc -z -w 1 "$1" 18080' sh "$control_peer_ip" >/dev/null 2>&1 || probe_rc=$?
[ "$probe_rc" -ne 0 ] || die "study bridge reached a container on another Docker network"
[ "$probe_rc" -eq 1 ] || die "other-network denial probe was inconclusive: $(security_docker_status "$probe_rc")"
probe_rc=0
probe_run --network "$SECURITY_NETWORK" "$PROBE_IMAGE" sh -eu -c \
  'nc -z -w 1 169.254.169.254 80' >/dev/null 2>&1 || probe_rc=$?
[ "$probe_rc" -ne 0 ] || die "study bridge reached the metadata/link-local HTTP port"
[ "$probe_rc" -eq 1 ] || die "metadata-denial probe was inconclusive: $(security_docker_status "$probe_rc")"
probe_run --network "$SECURITY_NETWORK" "$PROBE_IMAGE" sh -eu -c '
  command -v wget >/dev/null
  wget -q -T 10 -O /dev/null "https://${1}.s3.${2}.amazonaws.com/?list-type=2&max-keys=1"
' sh "$BUCKET" "$REGION" >/dev/null || die "study bridge cannot reach public S3 HTTPS"

say "PASS: host, peer, other-network and metadata denied; public S3 retained"
