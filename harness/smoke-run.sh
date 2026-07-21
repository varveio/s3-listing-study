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
LOOKUP="$REPO_ROOT/harness/registry-lookup.sh"
DATA_DIR="${S3_STUDY_DATA:-$HOME/.s3-listing-study/data}"
INLINE_MAX=102400
TIMEOUT_CEILING=300        # brief § Guardrails: 300s per mode, no exceptions
PAYLOAD_CAP=$((64 * 1024 * 1024))   # brief § Stage C: 64 MiB per stream, then truncate
PROC_ROOT=/proc

# Single-source the credential value-shape scan (shared with harness/scan-tree.sh).
# shellcheck source=harness/scan-lib.sh
. "$REPO_ROOT/harness/scan-lib.sh"
# shellcheck source=harness/runner-security-lib.sh
. "$REPO_ROOT/harness/runner-security-lib.sh"

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
REG_PATH="$("$LOOKUP" --path)"
REG_DIGEST="$("$LOOKUP" --digest)"
REG_REGION="$("$LOOKUP" "$BUCKET" region)"
MANIFEST="$("$LOOKUP" "$BUCKET" manifest)"
MANIFEST_SHA="$("$LOOKUP" "$BUCKET" manifest_sha256)"
SNAPSHOT_DATE="$("$LOOKUP" "$BUCKET" snapshot_date)"
MANIFEST_KEYS="$("$LOOKUP" "$BUCKET" keys)"
SHAPE="$("$LOOKUP" "$BUCKET" shape)"

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
# validates the host-bound readiness record, bridge/firewall state, metadata
# denial, and public S3 path; it never provisions or repairs the runner.
security_preflight "$BUCKET" "$REGION" \
  || die "runner security preflight failed; no subject container was started"
SECURITY_STATE=/etc/s3-listing-study/runner-ready.env
security_state_field() { sed -n "s/^$1=//p" "$SECURITY_STATE"; }
SECURITY_PROFILE="$(security_state_field profile_id)"
SECURITY_PROVIDER_VALUE="$(security_state_field provider)"
SECURITY_MTU="$(security_state_field mtu)"
SECURITY_POLICY_SHA="$(security_state_field live_policy_sha256)"

# ------------------------------------------------------- owner's bucket rule
# Scans against EVERY registered bucket, not just this run's. A run.sh that
# hardcodes a *different* registered bucket still violates the rule and still
# produces a receipt claiming the bucket it was asked for.
# Enumerated from the registry's Bucket identity rows via registry-lookup, not
# scraped from heading text: headings are editorial, so a bucket registered under
# a reworded heading would resolve fine everywhere else while silently escaping
# this guard — leaving the README claiming an enforcement that does not happen.
while IFS= read -r b; do
  [ -n "$b" ] || continue
  if grep -qF -- "$b" "$RUN_SCRIPT"; then
    die "run script hardcodes the registered bucket name '$b': $RUN_SCRIPT
        Bucket, region and prefix are always parameters (owner's rule)."
  fi
done < <("$LOOKUP" --list-buckets)

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

# ------------------------------------------------------------------ redaction
redact() {
  sed -E \
    -e 's/(AKIA|ASIA)[A-Z0-9]{12,}/<REDACTED-AWS-KEY-ID>/g' \
    -e 's/([Xx]-[Aa]mz-[Ss]ignature=)[A-Fa-f0-9]+/\1<REDACTED>/g' \
    -e 's/([Xx]-[Aa]mz-[Cc]redential=)[^&[:space:]"]+/\1<REDACTED>/g' \
    -e 's/([Xx]-[Aa]mz-[Ss]ecurity-[Tt]oken=)[^&[:space:]"]+/\1<REDACTED>/g' \
    -e 's/([Aa]uthorization:[[:space:]]*).*/\1<REDACTED>/g' \
    -e 's/(AWS_SECRET_ACCESS_KEY=)[^[:space:]]+/\1<REDACTED>/g' \
    -e 's/(AWS_SESSION_TOKEN=)[^[:space:]]+/\1<REDACTED>/g' \
    -e 's/(Signature=)[A-Fa-f0-9]{32,}/\1<REDACTED>/g' \
    -e 's/(arn:aws[a-z-]*:[a-z0-9-]*:[a-z0-9-]*:)[0-9]{12}:/\1<REDACTED-ACCOUNT-ID>:/g' \
    -e 's/("?[Oo]wner"?[[:space:]]*[:=][[:space:]]*"?)[0-9]{12}("?)/\1<REDACTED-ACCOUNT-ID>\2/g'
}
# Account IDs are named explicitly by Stage C's redaction list, and the installed
# gitleaks default rules do NOT flag a bare 12-digit ARN account — so an
# `arn:aws:iam::123456789012:role/X` in debug output would otherwise be hashed
# and published. Deliberately narrow: matched only in ARN position or as an
# Owner field, because 12-digit numbers are also legitimate object sizes.

# gitleaks is DROPPED (owner's call, 2026-07-16). Not a shortcut — it blocked
# real evidence while protecting nothing:
#
#   Its `generic-api-key` rule fires on S3's ContinuationToken (entropy 5.62), so
#   EVERY multi-page `--debug` receipt was unstageable — for every tool, since
#   every tool paginates. That is precisely the evidence Stage C asks for
#   ("observe request behaviour where the tool makes it visible"). The aws-cli
#   pilot lost a mode to it, and correctly recorded BLOCKED rather than deleting
#   the scan.
#
#   A ContinuationToken is an opaque pagination cursor returned by S3 in an
#   ANONYMOUS, UNSIGNED response against a PUBLIC bucket. Replaying it re-lists
#   keys anyone can already list without it. It grants nothing.
#
# What remains is not "nothing": the scan below targets the credential-shaped
# VALUES the brief names. Measured, not assumed — clean against 888 KB of real
# debug output, and catches every finding in a fixture of real-shaped
# credentials. It does not fire on entropy, which is the whole difference.
#
# And the load-bearing control was never the scanner. CREDS=none, every run is
# credential-starved, and the mandatory runner boundary refuses execution unless
# its identity, firewall, metadata-denial, and public-S3 preflight passes. An
# unsigned listing carries no Authorization header: zero occurrences in that
# same 888 KB. There is nothing to leak in that recorded anonymous run.
#
# Re-enable gitleaks — with the pagination-cursor allowlist that was tested and
# works — before ever setting CREDS, where signatures and account IDs appear.
#
# Three outcomes must never be conflated: clean / leak / scanner-broke. grep
# returns 2 on error, and treating that as "no match" turns a broken scan into a
# pass. That bug shipped in this function's first draft; do not reintroduce it.
# The value-shape pattern and its three-outcome classifier live in
# harness/scan-lib.sh — single-sourced with harness/scan-tree.sh so the regex
# cannot drift between the two. Validated against harness/tests/scan-fixtures/.
# Three outcomes must never be conflated: clean / leak / scanner-broke. grep
# returns 2 on error, and treating that as "no match" turns a broken scan into a
# pass — that bug shipped in this function's first draft; do not reintroduce it.
scan_secrets() {
  local f="$1" rc=0
  scan_secret_file "$f" || rc=$?
  case "$rc" in
    0) return 0 ;;
    1) die "secret scan flagged $f — refusing to stage. Inspect before anything else." ;;
    *) die "secret scan errored on $f (rc=$rc). A scanner error is not a pass — refusing to stage." ;;
  esac
}

# Payload variant: on a flag, QUARANTINE the offending bytes to $OUT/quarantine/
# BEFORE dying, so the evidence is preserved for inspection rather than removed by
# the cleanup trap (which only clears $TMP and the container). $OUT/quarantine is
# outside $TMP, so the trap never touches it.
scan_secrets_or_quarantine() {  # <file> <label>
  local f="$1" label="$2" rc=0
  scan_secret_file "$f" || rc=$?
  case "$rc" in
    0) return 0 ;;
    1) mkdir -p "$OUT/quarantine"
       cp -- "$f" "$OUT/quarantine/$label"
       die "secret scan flagged the REDACTED $label stream — a credential-shaped value survived redaction.
        Quarantined to: $OUT/quarantine/$label
        Refusing to stage. Inspect the quarantined file before anything else." ;;
    *) die "secret scan errored on $label (rc=$rc). A scanner error is not a pass — refusing to stage." ;;
  esac
}

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

# ----------------------------- redact (full) → scan (full) → truncate → hash
# Brief § Stage C contract: redact-then-scan-then-hash. Redaction runs over the
# WHOLE raw stream first (a legitimate object key shaped like a credential is
# scrubbed rather than falsely flagged), then the secret scan runs over the FULL
# REDACTED stream — every byte, BEFORE truncation — so a credential the redaction
# did not catch is still flagged even when it sits beyond the 64 MiB cap, instead
# of being silently dropped with the truncated tail. On a flag the offending
# redacted stream is QUARANTINED to $OUT/quarantine/ before dying (the cleanup
# trap clears only $TMP and the container, never the quarantine dir). Anonymous
# runs sign nothing, so redaction is a no-op in the normal case and any change is
# surfaced loudly.
declare -A TRUNCATED DROPPED PAYLOAD_NOTE PAYLOAD_PATH PAYLOAD_SHA
REDACTION_CHANGED=no
for s in stdout stderr; do
  redact <"$TMP/$s.raw" >"$TMP/$s.txt"
  if ! cmp -s "$TMP/$s.raw" "$TMP/$s.txt"; then
    REDACTION_CHANGED=yes
    say "WARNING: redaction altered $s bytes. Either a real secret was present, or the
        scrubber corrupted legitimate data. The verifier's verdict on this payload is
        NOT trustworthy until the orchestrator reviews it."
  fi
  scan_secrets_or_quarantine "$TMP/$s.txt" "$s"
done

# Truncate the (already redacted+scanned) stream at 64 MiB, keeping the head. A run
# that emits gigabytes of repeated retry noise is evidence of the retrying, not
# worth publishing byte-for-byte, and an unbounded capture can fill the disk
# mid-study. Truncation is recorded loudly: the verifier refuses a completeness
# verdict on a truncated stream. The full redacted stream was already scanned, so
# the dropped tail cannot hide a secret.
for s in stdout stderr; do
  TRUNCATED[$s]=no; DROPPED[$s]=0
  txt_size="$(stat -c %s "$TMP/$s.txt")"
  if [ "$txt_size" -gt "$PAYLOAD_CAP" ]; then
    head -c "$PAYLOAD_CAP" "$TMP/$s.txt" >"$TMP/$s.capped"
    mv "$TMP/$s.capped" "$TMP/$s.txt"
    TRUNCATED[$s]=yes
    DROPPED[$s]=$(( txt_size - PAYLOAD_CAP ))
    say "[$TOOL/$MODE] $s exceeded ${PAYLOAD_CAP}-byte cap — TRUNCATED, dropped ${DROPPED[$s]} bytes (head kept)."
  fi
done

# Hash and place the redacted+truncated stream.
for s in stdout stderr; do
  size="$(stat -c %s "$TMP/$s.txt")"
  sha="$(sha256sum "$TMP/$s.txt" | cut -d' ' -f1)"
  # Name carries auth mode: anonymous and credentialed runs of the same mode are
  # both required, and <mode>.<stream>.txt alone lets the second silently
  # overwrite bytes the first receipt already cited by hash.
  if [ "$size" -le "$INLINE_MAX" ]; then
    cp "$TMP/$s.txt" "$OUT/$s.txt"
    # Inline payloads travel with run.meta. Record only the sibling filename
    # and declare its base below; embedding the caller's relative --out spelling
    # made old paths depend on an undeclared invocation working directory.
    PAYLOAD_PATH[$s]="$s.txt"
    PAYLOAD_NOTE[$s]="inline — \`$s.txt\` (${size} bytes, sha256 \`$sha\`)"
  else
    ext_dir="$DATA_DIR/receipts/$TOOL"; mkdir -p "$ext_dir"
    ext_dir="$(cd -- "$ext_dir" && pwd)"
    # Scope belongs in the identity: Stage C runs the same mode full-bucket AND
    # against each designated prefix, so mode+auth+stream alone makes the second
    # >100KB payload collide with the first and abort a legitimate run.
    scope_tag="full"
    [ -n "$PREFIX" ] && scope_tag="$(printf '%s' "$PREFIX" | tr -c 'A-Za-z0-9._-' '_')"
    ext="$ext_dir/${MODE}.${BUCKET}.${scope_tag}.${AUTH}.${s}.txt"
    [ -e "$ext" ] && [ "$(sha256sum "$ext" | cut -d' ' -f1)" != "$sha" ] \
      && die "external payload $ext already exists with different content — refusing to clobber evidence another receipt may cite."
    cp "$TMP/$s.txt" "$ext"
    # Re-hash at the destination: the receipt cites bytes at a path, so the
    # bytes at that path are what must be verified, not the ones we copied from.
    dest_sha="$(sha256sum "$ext" | cut -d' ' -f1)"
    [ "$dest_sha" = "$sha" ] || die "payload hash changed during placement — refusing to cite it"
    PAYLOAD_PATH[$s]="$ext"
    PAYLOAD_NOTE[$s]="external — \`$ext\` (${size} bytes, sha256 \`$dest_sha\`) — redacted and scanned before hashing; published as a release asset at publication"
  fi
  PAYLOAD_SHA[$s]="$sha"
done

peak_rss_mb="unavailable"; cg_peak_mb="unavailable"
[ -n "$peak_rss_kb" ] && peak_rss_mb="$(awk -v k="$peak_rss_kb" 'BEGIN{printf "%.1f", k/1024}')"
[ -n "$cg_peak_b" ] && cg_peak_mb="$(awk -v b="$cg_peak_b" 'BEGIN{printf "%.1f", b/1048576}')"

# --------------------------------------------------------------- tool version
# Caller-supplied --tool-version wins; otherwise best-effort auto-detect by
# running the image with its own entrypoint and `--version` (5s cap, failure
# tolerated, no network). A version is NEVER fabricated: if detection fails the
# receipt keeps a TODO and the final summary announces it loudly (below).
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

# ------------------------------------------------------------- run metadata
# Machine-readable, and the binding the verifier validates against. Without it,
# the verifier takes bucket/mode/scope/inputs as independent arguments and will
# happily check one mode's output against another mode's scope and stamp the
# verdict into a third mode's receipt.
meta_field() { # <key> <value>; the only run.meta emission path
  reject_ctrl "run.meta field '$1'" "$2"
  printf '%s=%s\n' "$1" "$2"
}
{
  meta_field tool "$TOOL"; meta_field mode "$MODE"; meta_field auth "$AUTH"
  meta_field bucket "$BUCKET"; meta_field region "$REGION"; meta_field prefix "$PREFIX"
  meta_field image "$IMAGE"; meta_field image_arch "$IMG_ARCH"; meta_field host_arch "$HOST_DOCKER_ARCH"
  meta_field entrypoint "${ENTRYPOINT:-none}"; meta_field measured_process "$main_comm"
  meta_field security_profile "$SECURITY_PROFILE"; meta_field security_provider "$SECURITY_PROVIDER_VALUE"
  meta_field docker_network "$SECURITY_NETWORK"; meta_field network_mtu "$SECURITY_MTU"
  meta_field firewall_policy_sha256 "$SECURITY_POLICY_SHA"
  meta_field container_cap_drop ALL; meta_field container_no_new_privileges true
  meta_field docker_pull_policy never; meta_field docker_control_timeout_s "$SECURITY_DOCKER_CONTROL_TIMEOUT_S"
  meta_field docker_cleanup_timeout_s "$SECURITY_DOCKER_CLEANUP_TIMEOUT_S"
  meta_field docker_log_driver "$DOCKER_LOG_DRIVER"; meta_field docker_log_config_sha256 "$DOCKER_LOG_CONFIG_SHA"
  meta_field docker_log_option_keys_base64 "$DOCKER_LOG_OPTION_KEYS_B64"
  meta_field exit_code "$rc"; meta_field timed_out "$timed_out"; meta_field wall_clock_s "$wall"
  meta_field registry_path "$REG_PATH"; meta_field registry_sha256 "$REG_DIGEST"
  meta_field manifest "$MANIFEST"; meta_field manifest_sha256 "$MANIFEST_SHA"; meta_field snapshot_date "$SNAPSHOT_DATE"
  # Relative stream paths are resolved from the directory containing this
  # run.meta. External payloads remain absolute until the release-asset index
  # gives them content-addressed public identities.
  meta_field payload_path_base run-meta-directory
  meta_field stdout_path "${PAYLOAD_PATH[stdout]}"; meta_field stdout_sha256 "${PAYLOAD_SHA[stdout]}"
  meta_field stderr_path "${PAYLOAD_PATH[stderr]}"; meta_field stderr_sha256 "${PAYLOAD_SHA[stderr]}"
  meta_field stdout_truncated "${TRUNCATED[stdout]}"; meta_field stdout_dropped_bytes "${DROPPED[stdout]}"
  meta_field stderr_truncated "${TRUNCATED[stderr]}"; meta_field stderr_dropped_bytes "${DROPPED[stderr]}"
  meta_field passed_env "$PASSED_ENV_NOTE"; meta_field observability_env "$OBS_ENV_NOTE"
  meta_field functional_env "$FUNCTIONAL_ENV_NOTE"
  meta_field tool_version "${TOOL_VERSION:-unavailable}"; meta_field tool_version_source "$TOOL_VERSION_SRC"
  meta_field redaction_changed_bytes "$REDACTION_CHANGED"
  meta_field peak_rss_kb "${peak_rss_kb:-unavailable}"; meta_field cgroup_peak_bytes "${cg_peak_b:-unavailable}"
  meta_field rss_samples "$rss_samples"; meta_field cgroup_samples "$cg_samples"; meta_field poll_ms "$POLL_MS"
  meta_field utc_start "$UTC_START"; meta_field runner_location "$RUNNER_LOC"
} >"$TMP/run.meta"

# ------------------------------------------------------------------ receipt
# Escape every dynamic receipt value through one renderer. HTML entities are
# safe in Markdown table cells and cannot terminate code spans, create a new
# cell, or inject HTML. Controls are refused, not normalized away.
html_escape() {
  sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' \
      -e 's/|/\&#124;/g' -e 's/`/\&#96;/g'
}
md_safe_inline() { # <label> <value>
  reject_ctrl "receipt field '$1'" "$2"
  printf '%s' "$2" | html_escape
}
md_safe_block() { # <label> <value>; LF is layout, every other control is forbidden
  local label="$1" value="$2" stripped
  stripped="$(printf '%s' "$value" | LC_ALL=C tr -d '\000-\011\013-\037')"
  [ "$stripped" = "$value" ] || die "receipt block '$label' contains a forbidden control character"
  printf '%s' "$value" | html_escape
}
receipt_safe_var() {
  local var="$1" safe
  safe="$(md_safe_inline "$var" "${!var}")"
  printf -v "$var" '%s' "$safe"
}
for receipt_var in TOOL MODE UTC_START rc wall AUTH ENV_NOTE OBS_ENV_NOTE FUNCTIONAL_ENV_NOTE \
  TOOL_VERSION TOOL_VERSION_SRC IMAGE IMG_ARCH ENTRYPOINT EMULATED main_comm SECURITY_PROFILE \
  SECURITY_PROVIDER_VALUE SECURITY_NETWORK SECURITY_MTU SECURITY_POLICY_SHA DOCKER_LOG_DRIVER \
  DOCKER_LOG_CONFIG_SHA DOCKER_LOG_OPTION_KEYS_B64 ARCH CORES RAM_GB HOST_KERNEL RUNNER_LOC \
  BUCKET REGION PREFIX REG_PATH REG_DIGEST MANIFEST MANIFEST_SHA SNAPSHOT_DATE MANIFEST_KEYS \
  peak_rss_mb cg_peak_mb rss_samples cg_samples POLL_MS REDACTION_CHANGED; do
  receipt_safe_var "$receipt_var"
done
PAYLOAD_NOTE[stdout]="$(md_safe_inline payload_stdout "${PAYLOAD_NOTE[stdout]}")"
PAYLOAD_NOTE[stderr]="$(md_safe_inline payload_stderr "${PAYLOAD_NOTE[stderr]}")"
INVOCATION_SAFE="$(md_safe_block invocation "$INVOCATION")"
SHAPE_SAFE="$(md_safe_block shape "$SHAPE")"
{
  printf '# Smoke receipt — `%s` / mode `%s`\n\n' "$TOOL" "$MODE"
  printf 'Produced by `harness/smoke-run.sh`. Not a benchmark: this run makes no\n'
  printf 'comparative claim and its duration is a fact about this run only.\n\n'

  printf '## Result\n\n| | |\n| --- | --- |\n'
  printf '| Date (UTC) | %s |\n' "$UTC_START"
  printf '| Exit code | `%s`%s |\n' "$rc" "$([ "$timed_out" = 1 ] && printf ' — **killed at the %ss timeout**' "$TIMEOUT")"
  printf '| Wall-clock | %ss (container lifetime, StartedAt→FinishedAt) |\n' "$wall"
  printf '| Auth mode | `%s` — %s |\n' "$AUTH" "$ENV_NOTE"
  printf '| Observability env (--env) | %s |\n' "$([ "$OBS_ENV_NOTE" = none ] && printf 'none' || printf '`%s` — recorded verbatim' "$OBS_ENV_NOTE")"
  printf '| Functional env (--env) | %s |\n' "$([ "$FUNCTIONAL_ENV_NOTE" = none ] && printf 'none' || printf '`%s` — validated tool configuration, recorded verbatim' "$FUNCTIONAL_ENV_NOTE")"
  printf '| Verifier verdict | _(filled in by `harness/verify-listing.sh`)_ |\n'
  if [ -n "$TOOL_VERSION" ]; then
    printf '| Tool version | `%s` — %s |\n' "$TOOL_VERSION" "$TOOL_VERSION_SRC"
  else
    printf '| Tool version | _(TODO: %s — agent records from the tool manually)_ |\n' "$TOOL_VERSION_SRC"
  fi
  printf '\n## Invocation\n\n<pre><code>%s</code></pre>\n\n' "$INVOCATION_SAFE"
  printf 'Serialized from the same argv array that was executed — not reconstructed.\n'
  printf 'Container is created under a stable wrapper-owned name, then started detached, so the wrapper can sample memory and read the\n'
  printf 'cgroup while the process lives; it is removed by the wrapper afterwards.\n'

  printf '\n## Subject\n\n| | |\n| --- | --- |\n'
  printf '| Image | `%s` |\n' "$IMAGE"
  printf '| Image arch | `%s` |\n' "$IMG_ARCH"
  printf '| Entrypoint override | %s |\n' "${ENTRYPOINT:-none}"
  printf '| Emulated | %s |\n' "$EMULATED"
  printf '| Measured process | `%s` (container main process) |\n' "$main_comm"

  printf '\n## Security boundary\n\n| | |\n| --- | --- |\n'
  printf '| Profile | `%s` |\n' "$SECURITY_PROFILE"
  printf '| Provider adapter | `%s` |\n' "$SECURITY_PROVIDER_VALUE"
  printf '| Docker network | `%s` (user-defined bridge, MTU %s) |\n' "$SECURITY_NETWORK" "$SECURITY_MTU"
  printf '| Firewall policy | sha256 `%s` |\n' "$SECURITY_POLICY_SHA"
  printf '| Container hardening | `--pull=never`; `--cap-drop ALL`; `--security-opt no-new-privileges:true` |\n'
  printf '| Docker control bounds | %ss ordinary calls; %ss cleanup calls |\n' "$SECURITY_DOCKER_CONTROL_TIMEOUT_S" "$SECURITY_DOCKER_CLEANUP_TIMEOUT_S"
  printf '| Docker logging | driver `%s`; canonical config sha256 `%s`; option keys (base64) `%s` |\n' \
    "$DOCKER_LOG_DRIVER" "$DOCKER_LOG_CONFIG_SHA" "$DOCKER_LOG_OPTION_KEYS_B64"

  printf '\n## Box\n\n| | |\n| --- | --- |\n'
  printf '| Arch | `%s` |\n' "$ARCH"
  printf '| Cores | %s |\n' "$CORES"
  printf '| RAM | %s GB |\n' "$RAM_GB"
  printf '| Kernel | `%s` |\n' "$HOST_KERNEL"
  printf '| Runner location | `%s` |\n' "$RUNNER_LOC"
  printf '\n> Runner location is recorded because RTT sets the ratio of network\n'
  printf '> time to CPU time in a listing run: a runner outside the bucket region\n'
  printf '> can mask per-page CPU cost that would be significant in-region. For an\n'
  printf '> RTT-bound tool it does **not** bias serial-vs-parallel comparison — to\n'
  printf '> first order that ratio is the concurrency factor — but client CPU,\n'
  printf '> output back-pressure, and throttling can pull real ratios below it.\n'
  printf '> Recorded so a reader can judge; irrelevant at smoke scale, which\n'
  printf '> produces no comparative numbers.\n'

  printf '\n## Bucket\n\n| | |\n| --- | --- |\n'
  printf '| Bucket | `%s` |\n' "$BUCKET"
  printf '| Region | `%s` |\n' "$REGION"
  # NOT ${PREFIX:+`x`}${PREFIX:-y}: `:-` substitutes the VALUE when set, so a
  # set prefix rendered twice (`normals-hourly/`normals-hourly/). Both the
  # aws-cli and s5cmd agents routed this identically; receipts are wrapper
  # output and were correctly left un-patched by hand.
  if [ -n "$PREFIX" ]; then
    printf '| Prefix scope | `%s` |\n' "$PREFIX"
  else
    printf '| Prefix scope | full bucket |\n'
  fi
  printf '| Registry | `%s` (sha256 `%s`) |\n' "$REG_PATH" "$REG_DIGEST"
  printf '| Manifest | `%s` |\n' "$MANIFEST"
  printf '| Manifest sha256 | `%s` — verified against the file before this run |\n' "$MANIFEST_SHA"
  printf '| Snapshot date | %s |\n' "$SNAPSHOT_DATE"
  printf '| Manifest keys | %s |\n' "$MANIFEST_KEYS"
  printf '\n### Measured shape (from the registry)\n\n<pre>%s</pre>\n' "$SHAPE_SAFE"

  printf '\n## Memory\n\n| | | |\n| --- | --- | --- |\n'
  printf '| `peak_rss` | %s MB | `VmHWM` of the container'"'"'s main process, %s successful samples. **Main process only** — a multi-process fan-out mode'"'"'s children are not included. |\n' "$peak_rss_mb" "$rss_samples"
  printf '| `cgroup_peak_mem` | %s MB | cgroup v2 `memory.peak`, whole container tree, %s successful samples. **Page cache and kernel/socket memory included. Never present this as RSS.** |\n' "$cg_peak_mb" "$cg_samples"
  printf '\n**Both numbers are sampled**, polled every %s ms. Each is a\n' "$POLL_MS"
  printf 'kernel-maintained high-water mark, so a poll returns the true peak as of\n'
  printf 'that read; the unmeasured window is between the last poll and process\n'
  printf 'exit. The container cgroup is destroyed at exit, so neither can be read\n'
  printf 'post-mortem. `unavailable` means the value was never successfully read —\n'
  printf 'it is not zero, and it is not a finding about the tool.\n\n'
  printf '**Neither number bounds the other, and neither is a sanity check on the\n'
  printf 'other.** `VmHWM` counts pages resident in the main process, including\n'
  printf 'shared/file-backed pages that may be charged to a **different** cgroup;\n'
  printf '`memory.peak` counts memory charged to **this** cgroup and excludes pages\n'
  printf 'charged elsewhere. `peak_rss` > `cgroup_peak_mem` is normal where the\n'
  printf 'image is already hot in page cache.\n'

  printf '\n## API call count\n\n'
  printf '_(TODO: agent fills in where the tool exposes a counter; otherwise\n'
  printf '"not exposed" — request-shape capture defers to the replay-server phase.)_\n'

  printf '\n## Raw output\n\n'
  printf -- '- stdout: %s\n' "${PAYLOAD_NOTE[stdout]}"
  printf -- '- stderr: %s\n' "${PAYLOAD_NOTE[stderr]}"
  printf -- '- Redaction altered bytes: **%s**\n' "$REDACTION_CHANGED"
  for s in stdout stderr; do
    [ "${TRUNCATED[$s]}" = yes ] || continue
    printf -- '- **%s TRUNCATED at the %d-byte (64 MiB) cap — %d bytes dropped (head kept).**\n' \
      "$s" "$PAYLOAD_CAP" "${DROPPED[$s]}"
  done
  { [ "${TRUNCATED[stdout]}" = yes ] || [ "${TRUNCATED[stderr]}" = yes ]; } && \
    printf -- '\n> **Truncation warning.** A capped stream is incomplete by construction. The\n> verifier refuses a completeness verdict on any mode whose *verified* payload was\n> truncated (a cut-off listing cannot prove it listed everything); truncation of\n> stderr alone does not block verifying a complete stdout listing.\n'
  printf '\nRedacted and secret-scanned **before** hashing: the hash freezes the bytes,\n'
  printf 'so redaction after it would redact nothing. Machine-readable binding for the\n'
  printf 'verifier is in `run.meta`.\n'
} >"$TMP/receipt.md"

# Scan in TMP, place only after passing. Writing the receipt into $OUT and then
# scanning it detects a leak but does not prevent staging it: die() removes the
# container and TMP, not the secret-bearing file already sitting in the receipt
# directory. Scan-then-place, never place-then-scan — the same ordering rule as
# redact-before-hash.
scan_secrets "$TMP/receipt.md"
scan_secrets "$TMP/run.meta"
mv "$TMP/receipt.md" "$OUT/receipt.md"
mv "$TMP/run.meta" "$OUT/run.meta"

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
