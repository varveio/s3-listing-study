#!/usr/bin/env bash
# Build every registered tool's derived image and run one representative
# smoke attempt against the registered smoke bucket, replacing that tool's
# committed receipt.
#
# Usage:
#   harness/smoke-campaign.sh [--tool SLUG]... [--credential-file PATH] \
#                              [--jobs N] [--keep-going]
#
# With no --tool, runs every tool listed in TOOL_SMOKE_PLAN below that has a
# build/image.json registration. --tool may repeat to run a subset — pass it
# once to run exactly one tool. --jobs N (default 1) runs up to N tools'
# build+smoke concurrently; each tool writes to its own receipts directory, so
# concurrent tools never touch the same files.
#
# --credential-file PATH points at a file whose first two lines are the AWS
# access key ID and secret access key (no KEY= prefix, no quoting) — the same
# shape the study's Secret Manager payload's two credential lines take.
# Required only for tools whose plan entry says "authenticated"; omit it and
# those tools are skipped with a clear message instead of failing the run.
#
# Prerequisites this script does NOT set up (see docs/operating/
# runner-security.md and this session's notes handoff for why): the
# `s3-listing-study-subjects` Docker bridge network, and a DOCKER-USER
# iptables rule denying that bridge's subnet 169.254.169.254 so subject
# containers cannot reach cloud metadata. Create the network and rule once
# per host before running this script.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$HERE/.." && pwd)"
cd "$REPO_ROOT"

BUCKET="noaa-normals-pds"
REGION="us-east-1"
PREFIX="normals-hourly/"
NETWORK="s3-listing-study-subjects"

# slug:mode:auth — one representative smoke case per registered tool. Add a
# line here (and a build/image.json registration) to bring a new tool under
# this script; nothing else in this file is tool-specific.
TOOL_SMOKE_PLAN=(
  "aws-cli:s3api-v2-text:anonymous"
  "swath:recursive-tsv:anonymous"
  "s5cmd:recursive:anonymous"
  "rclone:recursive-hierarchical:anonymous"
  "minio-mc:recursive:anonymous"
  "s7cmd:recursive:anonymous"
  "s3-fast-list:recursive:anonymous"
  "ps3:list:authenticated"
  "s3kor:list:authenticated"
  "s4cmd:recursive:authenticated"
  "s3p:ls:authenticated"
)

CREDENTIAL_FILE=""
KEEP_GOING=no
JOBS=1
declare -a ONLY_TOOLS=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tool) ONLY_TOOLS+=("$2"); shift 2 ;;
    --credential-file) CREDENTIAL_FILE="$2"; shift 2 ;;
    --jobs) JOBS="$2"; shift 2 ;;
    --keep-going) KEEP_GOING=yes; shift ;;
    *) echo "smoke-campaign: unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$JOBS" in ''|*[!0-9]*|0) echo "smoke-campaign: --jobs must be a positive integer" >&2; exit 2 ;; esac

command -v docker >/dev/null 2>&1 || { echo "smoke-campaign: docker not found" >&2; exit 2; }
sudo docker network inspect "$NETWORK" >/dev/null 2>&1 || {
  echo "smoke-campaign: Docker network '$NETWORK' does not exist." >&2
  echo "Create it and the metadata-block rule first (see docs/operating/runner-security.md" >&2
  echo "and this session's notes handoff for the exact commands)." >&2
  exit 2
}

wanted() {
  local slug="$1"
  [ "${#ONLY_TOOLS[@]}" -eq 0 ] && return 0
  local want
  for want in "${ONLY_TOOLS[@]}"; do
    [ "$want" = "$slug" ] && return 0
  done
  return 1
}

LOG_DIR="$(mktemp -d)"
trap 'rm -rf -- "$LOG_DIR"' EXIT

# Runs entirely inside its own log-redirected subshell so parallel instances
# never interleave output; exit status alone is the caller's signal.
run_one_tool() {
  local tool="$1" mode="$2" auth="$3"

  if [ ! -f "tools/$tool/build/image.json" ]; then
    echo "SKIP (not registered — no tools/$tool/build/image.json)"
    return 0
  fi
  if [ "$auth" = authenticated ] && [ -z "$CREDENTIAL_FILE" ]; then
    echo "SKIP (authenticated case needs --credential-file)"
    return 0
  fi

  echo "-- build --"
  sg docker -c "uv run s3-listing-study build-derived-image --tool $tool"

  local tag digest
  tag="$(sudo docker images --filter "reference=s3-listing-study/$tool" \
    --format '{{.Repository}}:{{.Tag}}' | head -1)"
  [ -n "$tag" ] || { echo "could not find a built image for $tool"; return 1; }
  digest="$(sudo docker inspect "$tag" --format '{{index .RepoDigests 0}}' | cut -d@ -f2)"
  [ -n "$digest" ] || { echo "could not resolve built image digest for $tool"; return 1; }

  echo "-- smoke ($mode, $auth) --"
  local outdir="tools/$tool/receipts/smoke/$mode"
  rm -rf "$outdir"
  mkdir -p "$outdir"
  chmod 777 "$outdir"  # subject images run as their own, varying, container uid
  local abs_outdir="$REPO_ROOT/$outdir"

  local -a env_args=()
  local cred=""
  if [ "$auth" = authenticated ]; then
    cred="AWS_ACCESS_KEY_ID=$(sed -n '1p' "$CREDENTIAL_FILE")
AWS_SECRET_ACCESS_KEY=$(sed -n '2p' "$CREDENTIAL_FILE")"
    env_args=(-e "S3_STUDY_AWS_CREDENTIAL=$cred")
  fi

  local run_status=0
  sudo docker run --rm \
    --network "$NETWORK" \
    --cap-drop ALL --security-opt no-new-privileges:true \
    -v "$abs_outdir:/output" \
    -e S3_STUDY_ATTEMPT_OUT=/output \
    "${env_args[@]}" \
    "s3-listing-study/$tool@$digest" \
    --output /output/attempt-1 \
    --derived-image "$digest" \
    --tool "$tool" \
    --operation list \
    --auth "$auth" \
    --mode "$mode" \
    --bucket "$BUCKET" \
    --region "$REGION" \
    --prefix "$PREFIX" \
    --scope full || run_status=$?
  unset cred

  sudo chown -R "$(id -u):$(id -g)" "$outdir"

  if [ "$run_status" -ne 0 ] || [ ! -f "$outdir/attempt-1/result.json" ]; then
    echo "smoke FAILED (exit $run_status)"
    return 1
  fi

  python3 -c "
import json
d = json.load(open('$outdir/attempt-1/result.json'))
print(f\"outcome={d['outcome']['status']} secret_scan={d['secret_scan']['status']}\")
"
}

declare -a queued=()
for entry in "${TOOL_SMOKE_PLAN[@]}"; do
  IFS=: read -r tool _mode _auth <<<"$entry"
  wanted "$tool" || continue
  queued+=("$entry")
done

declare -A pids=()
failures=()
running=0

launch() {
  local entry="$1" tool mode auth
  IFS=: read -r tool mode auth <<<"$entry"
  echo "== $tool: starting =="
  {
    run_one_tool "$tool" "$mode" "$auth"
  } >"$LOG_DIR/$tool.log" 2>&1 &
  pids["$tool"]=$!
}

reap_one() {
  local finished_pid="" wait_status=0
  wait -n -p finished_pid
  wait_status=$?
  local finished=""
  local tool
  for tool in "${!pids[@]}"; do
    if [ "${pids[$tool]}" = "$finished_pid" ]; then
      finished="$tool"
      break
    fi
  done
  [ -n "$finished" ] || return 0  # reaped something outside our own job set
  unset 'pids[$finished]'
  running=$((running - 1))
  if [ "$wait_status" -eq 0 ]; then
    echo "== $finished: done =="
    sed 's/^/   /' "$LOG_DIR/$finished.log"
  else
    echo "== $finished: FAILED =="
    sed 's/^/   /' "$LOG_DIR/$finished.log"
    failures+=("$finished")
  fi
}

if [ "$JOBS" -eq 1 ]; then
  for entry in "${queued[@]}"; do
    IFS=: read -r tool mode auth <<<"$entry"
    echo "== $tool =="
    if run_one_tool "$tool" "$mode" "$auth"; then
      :
    else
      failures+=("$tool")
      [ "$KEEP_GOING" = yes ] || { echo "smoke-campaign: FAILED: $tool" >&2; exit 1; }
    fi
  done
else
  for entry in "${queued[@]}"; do
    while [ "$running" -ge "$JOBS" ]; do
      reap_one
    done
    launch "$entry"
    running=$((running + 1))
  done
  while [ "$running" -gt 0 ]; do
    reap_one
  done
fi

if [ "${#failures[@]}" -gt 0 ]; then
  echo "smoke-campaign: FAILED: ${failures[*]}" >&2
  exit 1
fi
echo "smoke-campaign: all requested tools done"
