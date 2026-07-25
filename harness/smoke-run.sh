#!/usr/bin/env bash
# harness/smoke-run.sh — the shared smoke run wrapper.
#
# Owns `docker run` entirely: image, mounts, network, credential injection or
# starving, timeout, cleanup, measurement, receipt. A per-tool run.sh only
# prints the argv to execute inside the container; it never runs anything.
#
# Receipts produced outside this wrapper do not count (methodology § Run records (receipts)).
#
# Two rules this file exists to keep, both learned the hard way:
#   1. A receipt records what RAN. Not a plausible reconstruction of it.
#   2. A harness failure is never recorded as a tool result. Blaming a tool for
#      the harness's own error is a false accusation about someone else's work.
#
# Usage:
#   smoke-run.sh --tool NAME --mode MODE --image REF@sha256:... \
#                --run-script PATH --bucket B [--region R] [--prefix P] \
#                --auth anonymous|credentialed [--creds-profile NAME] \
#                --out DIR [--timeout 300] [--poll-ms 50] [--entrypoint E]
#
set -euo pipefail
export LC_ALL=C
WRAPPER_START="$(date +%s)"   # the 300s guardrail covers the WHOLE invocation

die()  { printf '\nsmoke-run: %s\n' "$*" >&2; exit 2; }   # 2 = harness error
say()  { printf 'smoke-run: %s\n' "$*" >&2; }

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${S3_STUDY_DATA:-$HOME/.s3-listing-study/data}"
TIMEOUT_CEILING=300        # brief § Guardrails: 300s per mode, no exceptions
PROC_ROOT=/proc
# The registry this run binds to. A constant here, never an argument and never an
# environment variable: a redirectable registry is how official-looking evidence
# gets produced from a registry nobody reviewed (the old SMOKE_REGISTRY hole).
# The fake-Docker regression harness rewrites it in a STAGED COPY of this file —
# the same file-level seam it already uses for SECURITY_STATE and PROC_ROOT.
# Editing the seam requires write access to a copy of this script, which is a
# strictly larger ask than setting a variable; nothing in the argv loop or the
# environment reaches it.
REGISTRY="$REPO_ROOT/data/registry.toml"

# shellcheck source=harness/runner-security-lib.sh
. "$REPO_ROOT/harness/runner-security-lib.sh"

# The wrapper's offline half: registry resolution, redaction, the credential
# value-shape scan, run.meta, and the receipt. This file keeps everything that
# runs against the host — Docker lifecycle, argv, platform selection, the
# timeout, the cleanup trap, the sampling loop — so a reviewer reads exactly
# what executes, in order.
#
# NOTHING here is on the measured clock. Every call below happens either BEFORE
# the container is created or AFTER it has exited; `wall` is Docker's own
# FinishedAt−StartedAt and memory comes from the container's cgroup, so no
# wrapper-side work can land inside the measured interval. That ordering is
# load-bearing, not incidental.
#
# The import path is pinned as hard as the registry is. `python3 -m` puts the
# INVOCATION DIRECTORY at sys.path[0], ahead of anything PYTHONPATH names, so
# running this wrapper from a directory that happens to contain
# `s3_listing_study/` would replace the redactor and the credential scanner with
# someone else's and still emit an official-looking receipt. `-P` (3.11+) drops
# that entry. An ambient PYTHONPATH is REPLACED, never appended to — appended, it
# precedes the standard library and can shadow `tomllib` itself. The --env
# denylist below already refuses PYTHONPATH for the container; the wrapper does
# not honour it for itself either.
receipt_py() {
  PYTHONPATH="$REPO_ROOT/src" python3 -P -m s3_listing_study.receipt "$@"
}
# Interpreter floor, checked once and by name. The packaged half reads the
# registry with `tomllib` and `-P` above is 3.11+ too; on 3.10 every call fails
# and the first one surfaces as "registry resolution failed for bucket '…'",
# which names the bucket and not the cause. `-I` so this probe answers about the
# interpreter, not about whatever is on the path around it. Absent and too old
# are separate messages: reporting a missing interpreter as "older than 3.11"
# would be a false statement about this runner.
command -v python3 >/dev/null 2>&1 \
  || die "python3 is not installed. The wrapper's offline half — redaction, the credential
        value-shape scan, run.meta and the receipt — is a Python package; there is no fallback path."
python3 -I -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "python3 is older than 3.11 ($(python3 -I -c 'import sys; print(sys.version.split()[0])' 2>/dev/null || echo unknown)).
        The wrapper's offline half reads the registry with tomllib (3.11+) and is run with -P (3.11+).
        Install a 3.11+ python3 on this runner; a receipt is not produced from a partial harness."

TOOL=""; MODE=""; IMAGE=""; RUN_SCRIPT=""; BUCKET=""; REGION=""; PREFIX=""
AUTH=""; CREDS_PROFILE=""; OUT=""; TIMEOUT=300; POLL_MS=50; ENTRYPOINT=""
TOOL_VERSION=""; PASS_ENV=()

while [ $# -gt 0 ]; do
  case "$1" in
    --tool) TOOL="$2"; shift 2 ;;   --mode) MODE="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;; --run-script) RUN_SCRIPT="$2"; shift 2 ;;
    --bucket) BUCKET="$2"; shift 2 ;; --region) REGION="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;; --auth) AUTH="$2"; shift 2 ;;
    --creds-profile) CREDS_PROFILE="$2"; shift 2 ;; --out) OUT="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;; --poll-ms) POLL_MS="$2"; shift 2 ;;
    --entrypoint) ENTRYPOINT="$2"; shift 2 ;;
    --tool-version) TOOL_VERSION="$2"; shift 2 ;;
    --env) PASS_ENV+=("$2"); shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done

for req in TOOL MODE IMAGE RUN_SCRIPT BUCKET AUTH OUT; do
  [ -n "${!req}" ] || die "--${req,,} is required"
done
[ -x "$RUN_SCRIPT" ] || die "run script not executable: $RUN_SCRIPT"
case "$IMAGE" in *@sha256:*) ;; *) die "image must be digest-pinned, got: $IMAGE" ;; esac
case "$AUTH" in anonymous|credentialed) ;; *) die "--auth must be anonymous|credentialed" ;; esac
printf '%s' "$TIMEOUT" | grep -Eq '^[0-9]+$' || die "--timeout must be an integer"
[ "$TIMEOUT" -ge 1 ] || die "--timeout must be >= 1"
# Capped at entry rather than trusted: the guardrail is "300s per mode, enforced
# by the wrapper", which a caller-supplied 9999 would quietly repeal.
[ "$TIMEOUT" -le "$TIMEOUT_CEILING" ] \
  || die "--timeout $TIMEOUT exceeds the ${TIMEOUT_CEILING}s guardrail (brief § Guardrails). Not negotiable per-run."
printf '%s' "$POLL_MS" | grep -Eq '^[0-9]+$' || die "--poll-ms must be an integer"
[ "$POLL_MS" -ge 1 ] && [ "$POLL_MS" -le 1000 ] \
  || die "--poll-ms must be 1..1000. An uncapped poll interval repeals the timeout:
        --timeout 1 --poll-ms 60000 checks once, sleeps a minute, and lets a hung tool
        run ~59s past a hard limit the wrapper claims to enforce."

# ----------------------------------------------------- --env passthrough guard
# --env is a narrow per-tool interface, not a general environment door.  Only
# needs demonstrated by the current adapters/research are accepted: RUST_LOG for
# s3-fast-list observability, and minio-mc's exact anonymous MC_HOST_s3 alias.
# Functional configuration is kept separate from observability in the record.
# A control character (byte < 0x20) in an --env value or a --tool-version forges a
# run.meta field: run.meta is line-oriented and its parsers take the FIRST match,
# so an embedded newline like $'RUST_LOG=x\nredaction_changed_bytes=no' plants an
# earlier forged field that the verifier's altered-evidence refusal then reads
# instead of the genuine value. Refuse anything carrying such a byte.
#
# The packaged half refuses the same byte class again on its own emission path
# (receipt/meta.py: reject_control). Deliberate defence in depth, not drift: this
# copy refuses at ARGV time, before any container is created, which the Python
# cannot do — it is only reached after the subject has already run.
reject_ctrl() {  # <label> <value>
  local label="$1" v="$2" s
  s="$(printf '%s' "$v" | LC_ALL=C tr -d '\000-\037')"
  [ "$s" = "$v" ] || die "$label contains a control character (byte < 0x20 — newline, CR or tab).
        run.meta is line-oriented, so an embedded newline forges a later field. Refused.
        Provide a single-line value."
}

OBS_ENV=(); FUNCTIONAL_ENV=()
for e in ${PASS_ENV+"${PASS_ENV[@]}"}; do
  case "$e" in
    *=*) ;;
    *) die "--env must be NAME=VALUE, got: '$e'" ;;
  esac
  reject_ctrl "--env value" "$e"
  name="${e%%=*}"
  printf '%s' "$name" | grep -Eq '^[A-Za-z_][A-Za-z0-9_]*$' \
    || die "--env name is not a valid environment variable name: '$name'"
  # Reject dangerous classes before checking the positive allowlist, so a future
  # allowlist edit cannot accidentally admit loader/path/credential redirection.
  if printf '%s' "$name" | grep -Eiq 'TOKEN|SECRET|PASSWORD|PASSPHRASE|CREDENTIAL|PRIVATE|APIKEY|API_KEY|ACCESS_KEY|AUTH|(^|_)PROXY$'; then
    die "--env '$name' matches a credential/proxy class (TOKEN|SECRET|PASSWORD|PASSPHRASE|CREDENTIAL|PRIVATE|APIKEY|API_KEY|ACCESS_KEY|AUTH|*_PROXY) — refused.
        --env is a per-tool allowlist. Credentials and traffic
        redirects never enter through here: the campaign is CREDS=none and such a variable would falsify auth=anonymous."
  fi
  uname_uc="${name^^}"
  case "$uname_uc" in
    AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|AWS_SECURITY_TOKEN|AWS_CONTAINER_*|\
    AWS_ROLE_ARN|AWS_SHARED_CREDENTIALS_FILE|AWS_CONFIG_FILE|AWS_WEB_IDENTITY_TOKEN_FILE|AWS_PROFILE|\
    AWS_EC2_METADATA_DISABLED|AWS_ENDPOINT_URL|AWS_ENDPOINT_URL_S3|AWS_CA_BUNDLE|\
    HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY|\
    SSL_CERT_FILE|SSL_CERT_DIR|CURL_CA_BUNDLE|REQUESTS_CA_BUNDLE|NODE_EXTRA_CA_CERTS|\
    PATH|HOME|TMPDIR|PYTHONPATH|PYTHONHOME|RUBYLIB|PERL5LIB|NODE_PATH|GEM_HOME|GEM_PATH|\
    LD_*|DYLD_*|BASH_ENV|ENV|CDPATH)
      die "--env '$name' is on the exact-name denylist — refused. It is either a credential source the
        wrapper deliberately starves (AWS_*_KEY/SECRET/TOKEN/PROFILE/ROLE_ARN/CREDENTIALS_FILE/CONFIG_FILE/
        WEB_IDENTITY_TOKEN_FILE/EC2_METADATA_DISABLED), a traffic redirect (AWS_ENDPOINT_URL[_S3],
        HTTP(S)_PROXY, ALL_PROXY, NO_PROXY), or a trust-anchor swap (AWS_CA_BUNDLE, SSL_CERT_FILE,
        SSL_CERT_DIR, CURL_CA_BUNDLE, REQUESTS_CA_BUNDLE, NODE_EXTRA_CA_CERTS), or a loader/path
        redirection (PATH/HOME/TMPDIR/*PATH/LD_*/DYLD_*/BASH_ENV/ENV/CDPATH)." ;;
  esac
  case "$TOOL:$name" in
    s3-fast-list:RUST_LOG)
      [ -n "${e#*=}" ] || die "--env RUST_LOG requires a non-empty filter"
      OBS_ENV+=("$e") ;;
    minio-mc:MC_HOST_s3)
      [ "${e#*=}" = 'https://s3.amazonaws.com' ] \
        || die "--env MC_HOST_s3 is functional endpoint/auth configuration and must be exactly https://s3.amazonaws.com for anonymous minio-mc smoke"
      FUNCTIONAL_ENV+=("$e") ;;
    *)
      die "--env '$name' is not allowlisted for tool '$TOOL'. Current demonstrated allowances are s3-fast-list:RUST_LOG (observability) and minio-mc:MC_HOST_s3=https://s3.amazonaws.com (functional anonymous alias)." ;;
  esac
done

# A caller-supplied --tool-version is written verbatim into line-oriented run.meta
# and the receipt; a control character in it forges a field exactly as an --env
# value would.
[ -z "$TOOL_VERSION" ] || reject_ctrl "--tool-version" "$TOOL_VERSION"
[ -z "$ENTRYPOINT" ] || reject_ctrl "--entrypoint" "$ENTRYPOINT"

# --------------------------------------------------------- credentialed mode
# Deliberately unimplemented rather than approximately implemented. The obvious
# spelling — mount ~/.aws read-only — exposes every profile, SSO cache and
# source_profile chain on the box to a third-party binary, when the brief calls
# for a list-only identity scoped to the registered buckets. A tool that ignores
# AWS_PROFILE reaches all of it. CREDS=none today, so this path has no user;
# building it wrong now to have it ready would be strictly worse than this.
if [ "$AUTH" = credentialed ]; then
  die "credentialed mode is not implemented (requested profile: '${CREDS_PROFILE:-none}').
        Mounting \$HOME/.aws would expose every profile and SSO cache on this box to the
        subject tool, not the list-only scoped identity the brief requires. Implement a
        minimal materialised credential bundle (single profile, no source_profile chain)
        before enabling. Campaign is CREDS=none; no mode needs this yet."
fi

# ---------------------------------------------------------------- registry
# Resolved once, here, by the same loader the verifier uses — so a receipt and
# the verdict stamped into it cannot disagree about which registry produced
# them. Read into shell variables rather than re-read later: the registry file
# could be edited during a 300s run, and a receipt citing a digest that was
# never the one validated against the manifest would be a plausible fiction.
reg_rc=0
REG_KV="$(receipt_py registry --registry "$REGISTRY" --bucket "$BUCKET")" || reg_rc=$?
[ "$reg_rc" -eq 0 ] || die "registry resolution failed for bucket '$BUCKET' (exit $reg_rc)"
REG_PATH=""; REG_DIGEST=""; REG_REGION=""; MANIFEST=""
MANIFEST_SHA=""; SNAPSHOT_DATE=""; MANIFEST_KEYS=""
while IFS='=' read -r k v; do
  case "$k" in
    path) REG_PATH="$v" ;;              digest) REG_DIGEST="$v" ;;
    region) REG_REGION="$v" ;;          manifest) MANIFEST="$v" ;;
    manifest_sha256) MANIFEST_SHA="$v" ;; snapshot_date) SNAPSHOT_DATE="$v" ;;
    keys) MANIFEST_KEYS="$v" ;;
    *) die "registry resolution emitted an unexpected field '$k'" ;;
  esac
done <<<"$REG_KV"
for reg_var in REG_PATH REG_DIGEST REG_REGION MANIFEST MANIFEST_SHA SNAPSHOT_DATE MANIFEST_KEYS; do
  [ -n "${!reg_var}" ] || die "registry resolution returned no $reg_var for bucket '$BUCKET'"
done
# The shape is multi-line, so it is asked for by name rather than framed into
# the key=value stream above.
shape_rc=0
SHAPE="$(receipt_py registry --registry "$REGISTRY" --bucket "$BUCKET" --field shape)" || shape_rc=$?
[ "$shape_rc" -eq 0 ] || die "registry shape resolution failed for bucket '$BUCKET' (exit $shape_rc)"

if [ -n "$REGION" ] && [ "$REGION" != "$REG_REGION" ]; then
  die "region '$REGION' contradicts the registry ('$REG_REGION') for bucket '$BUCKET'"
fi
REGION="$REG_REGION"

[ -r "$MANIFEST" ] || die "manifest not readable: $MANIFEST"
actual_sha="$(sha256sum "$MANIFEST" | cut -d' ' -f1)"
[ "$actual_sha" = "$MANIFEST_SHA" ] \
  || die "manifest digest mismatch — registry $MANIFEST_SHA, file $actual_sha.
        Registry and data directory disagree; the orchestrator re-baselines. Not a tool finding."

# Fail closed before inspecting/pulling or executing the subject image. This
# validates the ambient credential environment, metadata denial, bridge and
# live firewall state, and the public S3 path; it never provisions or repairs
# the runner.
security_preflight "$BUCKET" "$REGION" \
  || die "runner security preflight failed; no subject container was started"
# The receipt cites the boundary the preflight just proved: the profile
# constants this build enforces, the MTU of the bridge the subject attaches to,
# and the digest of the versioned deny policy found in force — not a hash of one
# box's firewall dump, which no reader could re-derive.
SECURITY_PROFILE="$SECURITY_PROFILE_ID"
SECURITY_PROVIDER_VALUE="$SECURITY_PROVIDER"
SECURITY_MTU="$(security_docker_control network inspect "$SECURITY_NETWORK" \
  -f '{{index .Options "com.docker.network.driver.mtu"}}')" \
  || die "cannot read the study bridge MTU $(security_docker_status $?)"
SECURITY_POLICY_SHA="$(sha256sum "$SECURITY_POLICY_FILE" | cut -d' ' -f1)"

# ------------------------------------------------------- owner's bucket rule
# Scans against EVERY registered bucket, not just this run's. A run.sh that
# hardcodes a *different* registered bucket still violates the rule and still
# produces a receipt claiming the bucket it was asked for.
# Enumerated from the registry's own bucket table, not scraped from heading
# text: headings are editorial, so a bucket registered under a reworded heading
# would resolve fine everywhere else while silently escaping this guard —
# leaving the README claiming an enforcement that does not happen.
buckets_rc=0
REG_BUCKETS="$(receipt_py registry --registry "$REGISTRY" --list-buckets)" || buckets_rc=$?
[ "$buckets_rc" -eq 0 ] || die "could not enumerate registered buckets (exit $buckets_rc)"
while IFS= read -r b; do
  [ -n "$b" ] || continue
  if grep -qF -- "$b" "$RUN_SCRIPT"; then
    die "run script hardcodes the registered bucket name '$b': $RUN_SCRIPT
        Bucket, region and prefix are always parameters (owner's rule)."
  fi
done <<<"$REG_BUCKETS"

# ------------------------------------------------------------------ box spec
ARCH="$(uname -m)"
CORES="$(nproc)"
RAM_GB="$(awk '/^MemTotal:/ {printf "%.0f", $2/1048576}' /proc/meminfo)"
HOST_KERNEL="$(uname -r)"
RUNNER_LOC="unknown"
if z="$(curl -s -m 2 -H 'Metadata-Flavor: Google' \
        http://metadata.google.internal/computeMetadata/v1/instance/zone 2>/dev/null)"; then
  [ -n "$z" ] && RUNNER_LOC="gcp:${z##*/}"
fi
if [ "$RUNNER_LOC" = unknown ]; then
  if t="$(curl -s -m 2 -X PUT http://169.254.169.254/latest/api/token \
          -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null)" && [ -n "$t" ]; then
    az="$(curl -s -m 2 -H "X-aws-ec2-metadata-token: $t" \
          http://169.254.169.254/latest/meta-data/placement/availability-zone 2>/dev/null || true)"
    [ -n "$az" ] && RUNNER_LOC="aws:$az"
  fi
fi

# ------------------------- anonymous credential starvation, checked per run
# The mandatory runner-security preflight above owns identity/network readiness.
# This additional check binds the wrapper's anonymous claim to its own current
# environment before the subject command is assembled.
if [ "$AUTH" = anonymous ]; then
  leaked="$(env | grep -E '^AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN|PROFILE|ROLE_ARN|WEB_IDENTITY_TOKEN_FILE|CONTAINER_CREDENTIALS_(RELATIVE_URI|FULL_URI))=' | cut -d= -f1 | tr '\n' ' ' || true)"
  [ -z "$leaked" ] || die "anonymous run, but the wrapper's own environment carries AWS credential variables: $leaked
        A receipt claiming auth=anonymous from this environment would be an assertion, not a fact."
  [ ! -e "$HOME/.aws" ] || say "note: \$HOME/.aws exists but is NOT mounted; container cannot read it"
fi

# ------------------------------------------------------------------- argv
# Process substitution would hide run.sh's exit status: `mapfile < <(prog)`
# reports mapfile's success, so a script that prints half its argv and dies
# still gets executed. Capture, check the producer, verify NUL framing, decode.
TMP="$(mktemp -d)"
CONTAINER_NAME="s3study-smoke-${TMP##*/}"
CONTAINER_NAME="${CONTAINER_NAME//./-}"
container_create_attempted=no
cleanup() {
  local cleanup_failed=no
  if [ "$container_create_attempted" = yes ]; then
    if ! security_reconcile_container_absent "$CONTAINER_NAME"; then
      say "WARNING: bounded cleanup could not confirm removal of '$CONTAINER_NAME'; discard this runner before any further execution."
      cleanup_failed=yes
    fi
  fi
  rm -rf -- "$TMP"
  if [ "$cleanup_failed" = yes ]; then
    trap - EXIT
    exit 2
  fi
}
trap cleanup EXIT

docker_control_die() { # <operation> <rc>
  if security_is_timeout_status "$2"; then
    die "$1 timed out (Docker wrapper rc=$2) — cleanup will target '$CONTAINER_NAME'; if cleanup cannot be confirmed, discard this runner"
  fi
  die "$1 failed (Docker rc=$2) — harness error, no tool result produced"
}
# Status captured explicitly: inside `if ! cmd; then ... $?`, $? is the exit of
# the NEGATION (always 0), so a script dying with 17 reports "failed (exit 0)".
rs_rc=0
"$RUN_SCRIPT" "$MODE" "$BUCKET" "$REGION" ${PREFIX:+"$PREFIX"} >"$TMP/argv.bin" 2>"$TMP/argv.err" || rs_rc=$?
[ "$rs_rc" -eq 0 ] || die "run script failed (exit $rs_rc) for mode '$MODE': $(head -3 "$TMP/argv.err")"
[ -s "$TMP/argv.bin" ] || die "run script produced no argv for mode '$MODE'"
# The contract is NUL-terminated records. An unterminated trailing fragment means
# a truncated write, and accepting it would execute a half-formed argument.
[ "$(tail -c1 "$TMP/argv.bin" | od -An -tu1 | tr -d ' \n')" = "0" ] \
  || die "run script's argv is not NUL-terminated — truncated output, refusing to execute mode '$MODE'"
mapfile -d '' -t ARGV <"$TMP/argv.bin"
[ "${#ARGV[@]}" -gt 0 ] || die "run script produced no argv elements for mode '$MODE'"

# NOTE: ARGV is appended to the image's ENTRYPOINT. For amazon/aws-cli the
# entrypoint IS `aws`, so correct argv starts `s3api …`, not `aws s3api …`.

# Credentials must never reach a receipt via argv (they are committed verbatim).
for a in "${ARGV[@]}"; do
  case "$a" in
    *AKIA*|*ASIA*|*aws_secret_access_key*|*AWS_SECRET_ACCESS_KEY*|*X-Amz-Signature*|*X-Amz-Credential*)
      die "run.sh emitted argv that looks like it carries a credential. Refusing: invocations are committed verbatim." ;;
  esac
done

# --------------------------------------------------------- platform / emulation
# Inferring "native" from host arch alone records an amd64 image running under
# qemu on this arm64 box as "native arm64" — defeating the exact field that is
# supposed to stop emulation silently reaching the benchmark phase.
IMG_ARCH="$(security_docker_control image inspect -f '{{.Architecture}}' "$IMAGE" 2>/dev/null || echo unknown)"
if [ "$IMG_ARCH" = unknown ]; then
  die "digest-pinned subject image is not present locally: $IMAGE.
        Build/pull every image before runner provisioning; campaign execution never pulls."
fi
case "$ARCH" in
  aarch64|arm64) HOST_DOCKER_ARCH=arm64 ;;
  x86_64|amd64)  HOST_DOCKER_ARCH=amd64 ;;
  *)             HOST_DOCKER_ARCH="$ARCH" ;;
esac
if [ "$IMG_ARCH" = unknown ]; then
  EMULATED="unknown — could not read image architecture"
elif [ "$IMG_ARCH" = "$HOST_DOCKER_ARCH" ]; then
  EMULATED="no — image $IMG_ARCH on host $HOST_DOCKER_ARCH"
else
  EMULATED="**yes** — image $IMG_ARCH on host $HOST_DOCKER_ARCH (qemu). Smoke only; must not carry into the benchmark."
fi

# ------------------------------------------------------- build ONE argv array
# The receipt's invocation is serialized from the SAME array that is executed.
# An earlier draft reconstructed a plausible command afterwards and recorded
# `--rm` while actually running `-d`: a receipt is the thing someone rebuilds the
# run from, so a receipt describing a command that never ran is worse than none.
DOCKER_CMD=()
security_append_docker_control_prefix DOCKER_CMD
DOCKER_CMD+=(create --name "$CONTAINER_NAME")
security_append_evidence_log_args DOCKER_CMD
security_append_network_args DOCKER_CMD
DOCKER_CMD+=( -e AWS_EC2_METADATA_DISABLED=true )
# TZ pinned so timestamps are unambiguous. `aws s3 ls` prints LOCAL time with no
# offset marker — same object reads 14:41:50 or 07:41:50 depending purely on the
# container's TZ, with nothing in the output to say which. Measured. Any mtime
# comparison is meaningless without this, and reproducibility wants it regardless.
DOCKER_CMD+=( -e TZ=UTC )
# Neutralise credential variables the IMAGE may define. Checking only the host's
# environment misses `ENV AWS_ACCESS_KEY_ID=...` baked into a subject image: the
# tool would sign happily while the receipt asserts auth=anonymous — the receipt
# lying about the one property the wrapper exists to guarantee. `-e VAR=` sets it
# empty in the container, overriding the image's ENV.
#
# Empty is NOT the same as unset, and docker run cannot unset. `-e AWS_PROFILE=`
# makes aws-cli look for a profile *named* the empty string and die with "The
# config profile () could not be found" — a fix that breaks every run is worse
# than the hole it closes. So: credential VALUES are emptied (harmless), and
# credential SOURCES are pointed at a path that cannot exist (neutralising a
# credentials file baked into an image, without breaking config parsing).
# Profile-name variables are deliberately left alone — with every source dead,
# a baked profile name resolves to nothing.
for v in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN \
         AWS_CONTAINER_CREDENTIALS_RELATIVE_URI AWS_CONTAINER_CREDENTIALS_FULL_URI \
         AWS_CONTAINER_AUTHORIZATION_TOKEN AWS_ROLE_ARN; do
  DOCKER_CMD+=( -e "$v=" )
done
for v in AWS_SHARED_CREDENTIALS_FILE AWS_CONFIG_FILE AWS_WEB_IDENTITY_TOKEN_FILE \
         AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE; do
  DOCKER_CMD+=( -e "$v=/nonexistent-by-harness" )
done
# Per-tool passthrough (--env), classified and validated above.
# Added AFTER the credential-neutralising block so a passthrough var is never one
# of the names that block emptied — the guard already forbids that overlap.
for e in ${PASS_ENV[@]+"${PASS_ENV[@]}"}; do
  DOCKER_CMD+=( -e "$e" )
done
PASSED_ENV_NOTE="none"
[ "${#PASS_ENV[@]}" -gt 0 ] && PASSED_ENV_NOTE="$(printf '%s ' "${PASS_ENV[@]}")" && PASSED_ENV_NOTE="${PASSED_ENV_NOTE% }"
OBS_ENV_NOTE="none"
[ "${#OBS_ENV[@]}" -gt 0 ] && OBS_ENV_NOTE="$(printf '%s ' "${OBS_ENV[@]}")" && OBS_ENV_NOTE="${OBS_ENV_NOTE% }"
FUNCTIONAL_ENV_NOTE="none"
[ "${#FUNCTIONAL_ENV[@]}" -gt 0 ] && FUNCTIONAL_ENV_NOTE="$(printf '%s ' "${FUNCTIONAL_ENV[@]}")" && FUNCTIONAL_ENV_NOTE="${FUNCTIONAL_ENV_NOTE% }"
ENV_NOTE="AWS_EC2_METADATA_DISABLED=true; credential values emptied and credential file sources pointed at a nonexistent path in-container, overriding any baked into the image; no mounted profile or config"
[ -n "$ENTRYPOINT" ] && DOCKER_CMD+=( --entrypoint "$ENTRYPOINT" )
DOCKER_CMD+=( "$IMAGE" "${ARGV[@]}" )
DOCKER_START_CMD=()
security_append_docker_control_prefix DOCKER_START_CMD
DOCKER_START_CMD+=(start "$CONTAINER_NAME")

serialize() { local out=""; for a in "$@"; do out+="$(printf '%q' "$a") "; done; printf '%s' "${out% }"; }
INVOCATION="$(serialize "${DOCKER_CMD[@]}")"$'\n'"$(serialize "${DOCKER_START_CMD[@]}")"

# ---------------------------------------------------------------------- run
# Refuse to overwrite evidence. Re-running a mode into a populated directory
# silently replaces stdout/stderr/run.meta/receipt.md while leaving the previous
# verify.md in place — an internally mixed receipt tree that looks complete and
# whose verdict belongs to bytes that no longer exist.
if [ -e "$OUT" ] && [ -n "$(ls -A "$OUT" 2>/dev/null)" ]; then
  die "receipt directory is not empty: $OUT
        Refusing to overwrite evidence. Remove it deliberately, or use a fresh directory."
fi
mkdir -p "$OUT"
UTC_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
say "[$TOOL/$MODE] starting (auth=$AUTH, timeout=${TIMEOUT}s, $EMULATED)"

container_create_attempted=yes
create_rc=0
cid="$("${DOCKER_CMD[@]}")" || create_rc=$?
[ "$create_rc" -eq 0 ] || docker_control_die "docker create" "$create_rc"
printf '%s' "$cid" | grep -Eq '^[0-9a-f]{12,64}$' \
  || die "docker create returned a malformed container id; cleanup will target '$CONTAINER_NAME'"

# Verify the explicit, non-rotating local log contract before trusting later
# `docker logs` output. Capture effective state rather than assuming create argv
# won over daemon behavior.
inspect_rc=0
DOCKER_LOG_DRIVER="$(security_docker_control inspect -f '{{.HostConfig.LogConfig.Type}}' "$CONTAINER_NAME" 2>/dev/null)" || inspect_rc=$?
[ "$inspect_rc" -eq 0 ] || docker_control_die "Docker log-driver inspection" "$inspect_rc"
inspect_rc=0
DOCKER_LOG_CONFIG="$(security_docker_control inspect -f '{{json .HostConfig.LogConfig.Config}}' "$CONTAINER_NAME" 2>/dev/null)" || inspect_rc=$?
[ "$inspect_rc" -eq 0 ] || docker_control_die "Docker log-configuration inspection" "$inspect_rc"
[ -n "$DOCKER_LOG_DRIVER" ] && [ -n "$DOCKER_LOG_CONFIG" ] \
  || die "Docker returned incomplete effective log configuration; refusing to start the subject"
# Log options can contain credentials. Keep raw values temporary, canonicalize
# in memory, and reject everything except the exact unlimited json-file config.
DOCKER_LOG_CONFIG_CANON="$(printf '%s' "$DOCKER_LOG_CONFIG" | jq -cS \
  'if type == "object" then . else error("log config is not an object") end' 2>/dev/null)" \
  || die "Docker returned malformed effective log configuration; refusing to start the subject"
security_validate_evidence_log_config "$DOCKER_LOG_DRIVER" "$DOCKER_LOG_CONFIG_CANON" \
  || die "effective Docker log configuration is not exactly json-file with max-size=-1; refusing incomplete or remotely logged evidence"
DOCKER_LOG_CONFIG_SHA="$(printf '%s' "$DOCKER_LOG_CONFIG_CANON" | sha256sum | cut -d' ' -f1)"
DOCKER_LOG_OPTION_KEYS_B64="$(printf '%s' "$DOCKER_LOG_CONFIG_CANON" | jq -r 'keys[] | @base64' | paste -sd, -)" \
  || die "could not derive safe Docker log-option key identities"
[ -n "$DOCKER_LOG_OPTION_KEYS_B64" ] || DOCKER_LOG_OPTION_KEYS_B64=none
unset DOCKER_LOG_CONFIG DOCKER_LOG_CONFIG_CANON

start_rc=0
"${DOCKER_START_CMD[@]}" >/dev/null || start_rc=$?
[ "$start_rc" -eq 0 ] || docker_control_die "docker start" "$start_rc"

inspect_rc=0
pid="$(security_docker_control inspect -f '{{.State.Pid}}' "$cid" 2>/dev/null)" || inspect_rc=$?
[ "$inspect_rc" -eq 0 ] || docker_control_die "container PID inspection" "$inspect_rc"
[ -n "$pid" ] || pid=0
main_comm="unknown"
[ "$pid" != 0 ] && main_comm="$(cat "$PROC_ROOT/$pid/comm" 2>/dev/null || echo unknown)"

# Discover the cgroup from the process itself rather than guessing driver layout.
cgpath=""
if [ "$pid" != 0 ] && [ -r "/proc/$pid/cgroup" ]; then
  rel="$(awk -F: '$1=="0"{print $3}' "/proc/$pid/cgroup" 2>/dev/null || true)"
  [ -n "$rel" ] && [ -d "/sys/fs/cgroup$rel" ] && cgpath="/sys/fs/cgroup$rel"
fi
if [ -z "$cgpath" ]; then
  for p in "/sys/fs/cgroup/system.slice/docker-$cid.scope" "/sys/fs/cgroup/docker/$cid"; do
    [ -d "$p" ] && { cgpath="$p"; break; }
  done
fi

# A measurement that was never taken is reported as unavailable, never as 0.
# "peak_rss: 0.0 MB (sampled)" is a fabricated number, and it looks like a
# finding about the tool.
rss_samples=0; cg_samples=0
peak_rss_kb=""; cg_peak_b=""; timed_out=0
poll_s="$(awk -v ms="$POLL_MS" 'BEGIN{printf "%.3f", ms/1000}')"
deadline=$(( WRAPPER_START + TIMEOUT ))

while [ "$pid" != 0 ] && [ -d "/proc/$pid" ]; do
  v="$(awk '/^VmHWM:/{print $2}' "/proc/$pid/status" 2>/dev/null || true)"
  if [ -n "$v" ]; then
    rss_samples=$((rss_samples + 1))
    { [ -z "$peak_rss_kb" ] || [ "$v" -gt "$peak_rss_kb" ]; } && peak_rss_kb="$v"
  fi
  if [ -n "$cgpath" ]; then
    c="$(cat "$cgpath/memory.peak" 2>/dev/null || true)"
    if [ -n "$c" ]; then
      cg_samples=$((cg_samples + 1))
      { [ -z "$cg_peak_b" ] || [ "$c" -gt "$cg_peak_b" ]; } && cg_peak_b="$c"
    fi
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    timed_out=1
    say "[$TOOL/$MODE] TIMEOUT at ${TIMEOUT}s — killing. A timeout is a recorded result, not a retry."
    kill_rc=0
    security_docker_cleanup kill "$cid" >/dev/null 2>&1 || kill_rc=$?
    [ "$kill_rc" -eq 0 ] || docker_control_die "docker kill after subject timeout" "$kill_rc"
    break
  fi
  sleep "$poll_s"
done

# Docker control-plane failures are HARNESS errors. Recording `docker wait`
# failing as the tool's exit code (-1) manufactures evidence against the tool.
wait_rc=0
rc="$(security_docker_control wait "$cid" 2>/dev/null)" || wait_rc=$?
[ "$wait_rc" -eq 0 ] || docker_control_die "docker wait" "$wait_rc"
printf '%s' "$rc" | grep -Eq '^-?[0-9]+$' || die "docker wait returned '$rc' — harness error, not a tool exit code"
logs_rc=0
security_docker_control logs "$cid" >"$TMP/stdout.raw" 2>"$TMP/stderr.raw" || logs_rc=$?
[ "$logs_rc" -eq 0 ] || docker_control_die "docker logs" "$logs_rc"

inspect_rc=0
started="$(security_docker_control inspect -f '{{.State.StartedAt}}' "$cid")" || inspect_rc=$?
[ "$inspect_rc" -eq 0 ] || docker_control_die "Docker StartedAt inspection" "$inspect_rc"
inspect_rc=0
finished="$(security_docker_control inspect -f '{{.State.FinishedAt}}' "$cid")" || inspect_rc=$?
[ "$inspect_rc" -eq 0 ] || docker_control_die "Docker FinishedAt inspection" "$inspect_rc"
wall="$(awk -v a="$(date -d "$started" +%s.%N)" -v b="$(date -d "$finished" +%s.%N)" \
        'BEGIN{d=b-a; if(d<0)d=0; printf "%.3f", d}')"

# For the closing summary line only; the receipt derives its own MB from the
# raw kB/byte figures passed below. A measurement that was never taken stays
# `unavailable` here too — "0.0 MB" is a fabricated number that looks like a
# finding about the tool.
peak_rss_mb="unavailable"; cg_peak_mb="unavailable"
[ -n "$peak_rss_kb" ] && peak_rss_mb="$(awk -v k="$peak_rss_kb" 'BEGIN{printf "%.1f", k/1024}')"
[ -n "$cg_peak_b" ] && cg_peak_mb="$(awk -v b="$cg_peak_b" 'BEGIN{printf "%.1f", b/1048576}')"

# --------------------------------------------------------------- tool version
# Caller-supplied --tool-version wins; otherwise best-effort auto-detect by
# running the image with its own entrypoint and `--version` (5s cap, failure
# tolerated, no network). A version is NEVER fabricated: if detection fails the
# receipt keeps a TODO and the final summary announces it loudly (below).
#
# This probe now runs BEFORE the payload redaction/scan rather than after it: the
# offline half is one call and takes the version as an argument, so it cannot be
# ordered around it. The captured raw payload therefore sits unscanned in $TMP
# across one extra `docker run --version`. $TMP is a private `mktemp -d`, the
# probe is `--pull=never --network none` and reads nothing from it, and nothing is
# placed in $OUT or hashed until the scan has passed — so the scan is later, not
# weaker. No measurement is affected: the clock stopped at FinishedAt.
TOOL_VERSION_SRC="caller-supplied"
if [ -z "$TOOL_VERSION" ]; then
  TOOL_VERSION_SRC="auto-detected (image --version)"
  VERSION_CONTAINER_NAME="s3study-version-${TMP##*/}"
  VERSION_CONTAINER_NAME="${VERSION_CONTAINER_NAME//./-}"
  version_rc=0
  version_raw="$(timeout -k 2s 5s docker run --rm --pull=never --network none \
      --name "$VERSION_CONTAINER_NAME" ${ENTRYPOINT:+--entrypoint "$ENTRYPOINT"} \
      "$IMAGE" --version 2>/dev/null)" || version_rc=$?
  if [ "$version_rc" -ne 0 ]; then
    security_reconcile_container_absent "$VERSION_CONTAINER_NAME" \
      || die "offline version probe $(security_docker_status "$version_rc") and bounded cleanup/absence could not be confirmed; discard this runner"
    TOOL_VERSION_SRC="unavailable — --version $(security_docker_status "$version_rc")"
  fi
  [ "$version_rc" -eq 0 ] && TOOL_VERSION="$(printf '%s\n' "$version_raw" | head -1)"
  if [ "$version_rc" -eq 0 ] && [ -z "$TOOL_VERSION" ]; then
    TOOL_VERSION_SRC="unavailable — --version returned no version"
  fi
fi

# ------------------- redact (full) → scan (full) → truncate → hash → receipt
# The container has exited; the measured clock stopped at FinishedAt. Everything
# from here on is offline, and is done by the packaged half of this wrapper
# (src/s3_listing_study/receipt/), which owns the data-shaped work:
#
#   * redaction over the WHOLE raw stream first, so a legitimate object key
#     shaped like a credential is scrubbed rather than falsely flagged;
#   * the credential value-shape scan over the FULL REDACTED stream — every
#     byte, BEFORE truncation — so a credential redaction did not catch is still
#     flagged when it sits beyond the 64 MiB cap, instead of being silently
#     dropped with the truncated tail. On a flag the offending stream is
#     QUARANTINED to $OUT/quarantine/ before dying: the cleanup trap clears only
#     $TMP and the container, never the quarantine dir. Three outcomes, never
#     conflated — clean / flagged / scanner-broke;
#   * truncation at 64 MiB keeping the head, recorded loudly;
#   * hashing LAST, because the hash freezes the bytes and redaction after it
#     would redact nothing;
#   * run.meta and receipt.md, every field refused if it carries a control byte,
#     scanned in $TMP and placed only after passing.
#
# Every value below is what THIS wrapper measured or decided, passed explicitly:
# a receipt records what ran, never a plausible reconstruction of it.
finish_rc=0
receipt_py finish \
  --tmp="$TMP" --out="$OUT" --data-dir="$DATA_DIR" \
  --tool="$TOOL" --mode="$MODE" --auth="$AUTH" \
  --bucket="$BUCKET" --region="$REGION" --prefix="$PREFIX" \
  --image="$IMAGE" --image-arch="$IMG_ARCH" --host-arch="$HOST_DOCKER_ARCH" \
  --entrypoint="$ENTRYPOINT" --measured-process="$main_comm" --emulated="$EMULATED" \
  --security-profile="$SECURITY_PROFILE" --security-provider="$SECURITY_PROVIDER_VALUE" \
  --docker-network="$SECURITY_NETWORK" --network-mtu="$SECURITY_MTU" \
  --firewall-policy-sha256="$SECURITY_POLICY_SHA" \
  --container-cap-drop=ALL --container-no-new-privileges=true --docker-pull-policy=never \
  --docker-control-timeout-s="$SECURITY_DOCKER_CONTROL_TIMEOUT_S" \
  --docker-cleanup-timeout-s="$SECURITY_DOCKER_CLEANUP_TIMEOUT_S" \
  --docker-log-driver="$DOCKER_LOG_DRIVER" \
  --docker-log-config-sha256="$DOCKER_LOG_CONFIG_SHA" \
  --docker-log-option-keys-base64="$DOCKER_LOG_OPTION_KEYS_B64" \
  --exit-code="$rc" --timed-out="$timed_out" --wall-clock-s="$wall" --timeout="$TIMEOUT" \
  --registry-path="$REG_PATH" --registry-sha256="$REG_DIGEST" \
  --manifest="$MANIFEST" --manifest-sha256="$MANIFEST_SHA" \
  --snapshot-date="$SNAPSHOT_DATE" --manifest-keys="$MANIFEST_KEYS" --shape="$SHAPE" \
  --passed-env="$PASSED_ENV_NOTE" --observability-env="$OBS_ENV_NOTE" \
  --functional-env="$FUNCTIONAL_ENV_NOTE" --env-note="$ENV_NOTE" \
  --tool-version="$TOOL_VERSION" --tool-version-source="$TOOL_VERSION_SRC" \
  --peak-rss-kb="${peak_rss_kb:-unavailable}" --cgroup-peak-bytes="${cg_peak_b:-unavailable}" \
  --rss-samples="$rss_samples" --cgroup-samples="$cg_samples" --poll-ms="$POLL_MS" \
  --utc-start="$UTC_START" --runner-location="$RUNNER_LOC" \
  --arch="$ARCH" --cores="$CORES" --ram-gb="$RAM_GB" --host-kernel="$HOST_KERNEL" \
  --invocation="$INVOCATION" || finish_rc=$?
# It has already said why, in the wrapper's own voice. Re-wording it here would
# only bury the reason under a second, vaguer one.
[ "$finish_rc" -eq 0 ] || exit "$finish_rc"

# TODO discipline: a mandatory receipt field left as TODO is a defective receipt.
# The agent greps receipts for TODO, but the wrapper must ALSO announce it — a
# field can never be silently absent-but-looking-filled. Auto-filled fields
# (tool version) drop their TODO on success; whatever remains is surfaced loudly.
todo_lines="$(grep -nF 'TODO' "$OUT/receipt.md" || true)"
if [ -n "$todo_lines" ]; then
  say "WARNING: $OUT/receipt.md carries UNFILLED TODO field(s) — a mandatory field left as TODO is a defective receipt. Fill these before the checkpoint:"
  while IFS= read -r l; do say "  TODO → $l"; done <<<"$todo_lines"
fi

say "[$TOOL/$MODE] exit=$rc wall=${wall}s peak_rss=${peak_rss_mb}MB cgroup_peak=${cg_peak_mb}MB → $OUT/receipt.md"
exit 0
