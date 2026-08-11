#!/usr/bin/env bash
# Build every registered tool's derived image and run one representative
# smoke attempt against the registered smoke bucket, ADDING a new attempt
# directory next to whatever that mode already holds.
#
# This script never deletes anything under receipts/. A mode directory may
# already hold legacy single-run receipt files and earlier attempt-N
# directories; both are evidence a claim may cite, and
# docs/operating/tool-structure.md forbids removing them to tidy a rerun.
# Each run allocates the next unused attempt-N, so a rerun accumulates rather
# than overwrites — the same rule upload-attempt enforces at the destination
# with if_generation_match=0.
#
# Usage:
#   harness/smoke-campaign.sh \
#     --shared-base-image REGISTRY/...@sha256:<digest> \
#     [--tool SLUG]... [--credential-file PATH] [--jobs N] [--keep-going]
#
# --shared-base-image is the immutable shared runtime image used to build every
# selected tool. The same URI and exact digest suffix are recorded by each
# worker attempt.
#
# Every attempt normalizes and counts locally after its subject measurement;
# the small summary is sealed into result.json. GCS upload is a separate step
# (`s3-listing-study upload-attempt`) — this script never uploads anything.
#
# With no --tool, runs every tool listed in TOOL_SMOKE_PLAN below that has a
# build/image.json registration. --tool may repeat to run a subset — pass it
# once to run exactly one tool. --jobs N (default 1) runs up to N tools'
# build+smoke concurrently; each tool writes to its own receipts directory, so
# concurrent tools never touch the same files. Note that --jobs N>1 does make
# each attempt's `resources.whole_filesystem_peak_used_delta_bytes` unusable: that figure comes
# from polling whole-filesystem usage, so concurrent attempts sharing a disk
# are counted into each other. Smoke does not measure, so this is acceptable
# here; a campaign that cares about the figure runs one attempt per host.
#
# --credential-file PATH points at a file whose first two lines are the AWS
# access key ID and secret access key (no KEY= prefix, no quoting) — the same
# shape the study's Secret Manager payload's two credential lines take.
# Required only for tools whose plan entry says "authenticated"; omit it and
# those tools are skipped with a clear message instead of failing the run.
# The credential value is exported into this script's own environment and
# forwarded by name only (`docker run -e NAME`, never `-e NAME=value`), so it
# never appears in any process's argv, in `ps` output, or in sudo's log.
#
# Prerequisites this script does NOT set up (see docs/operating/
# runner-security.md § Operator procedure): the `s3-listing-study-subjects`
# Docker bridge network, and a DOCKER-USER iptables rule denying that bridge's
# subnet 169.254.169.254 so subject containers cannot reach cloud metadata.
# Create the network and rule once per host before running this script. It
# also needs `sudo docker` (and, for the authenticated cases, a sudoers policy
# that permits --preserve-env for one named variable) plus membership of the
# `docker` group for the build step's `sg docker`.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$HERE/.." && pwd)"
cd "$REPO_ROOT"

BUCKET="noaa-normals-pds"
REGION="us-east-1"
PREFIX="normals-hourly/"
NETWORK="s3-listing-study-subjects"

# Stamped into every result.json so a receipt names the harness that produced
# it. `-dirty` is recorded honestly rather than suppressed: a smoke run from an
# uncommitted tree is a real thing to do, and a receipt claiming a clean commit
# it was not built from would be worse than one that says so.
HARNESS_REVISION="$(git rev-parse --short=12 HEAD)"
git diff --quiet HEAD -- . || HARNESS_REVISION="$HARNESS_REVISION-dirty"

# slug:mode:auth:prefix — one representative smoke case per registered tool.
# prefix is empty to mean "use the global $PREFIX default"; only a tool whose
# mode cannot take one (pS3 has no --prefix flag at all) sets it explicitly
# empty here. Add a line here (and a build/image.json registration) to bring
# a new tool under this script; nothing else in this file is tool-specific.
TOOL_SMOKE_PLAN=(
  "aws-cli:s3api-v2-text:anonymous:"
  "swath:recursive-tsv:anonymous:"
  "s5cmd:recursive:anonymous:"
  "rclone:recursive-hierarchical:anonymous:"
  "minio-mc:recursive:anonymous:"
  "s7cmd:recursive-tsv:anonymous:"
  "s3-fast-list:list:anonymous:"
  "ps3:list:authenticated:EMPTY"
  "s3kor:list:authenticated:"
  "s4cmd:recursive:authenticated:"
  "s3p:ls:authenticated:"
)

CREDENTIAL_FILE=""
SHARED_BASE_IMAGE=""
SHARED_BASE_DIGEST=""
KEEP_GOING=no
JOBS=1
declare -a ONLY_TOOLS=()

usage() {
  echo "usage: harness/smoke-campaign.sh --shared-base-image REGISTRY/...@sha256:<digest> [--tool SLUG]... [--credential-file PATH] [--jobs N] [--keep-going]"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tool) ONLY_TOOLS+=("$2"); shift 2 ;;
    --credential-file) CREDENTIAL_FILE="$2"; shift 2 ;;
    --shared-base-image)
      [ "$#" -ge 2 ] || { echo "smoke-campaign: --shared-base-image requires a value" >&2; exit 2; }
      SHARED_BASE_IMAGE="$2"
      shift 2
      ;;
    --jobs) JOBS="$2"; shift 2 ;;
    --keep-going) KEEP_GOING=yes; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "smoke-campaign: unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$JOBS" in ''|*[!0-9]*|0) echo "smoke-campaign: --jobs must be a positive integer" >&2; exit 2 ;; esac
if [ -z "$SHARED_BASE_IMAGE" ]; then
  echo "smoke-campaign: --shared-base-image is required" >&2
  exit 2
fi
if [[ ! "$SHARED_BASE_IMAGE" =~ ^[a-z0-9]+([._-][a-z0-9]+)*/([a-z0-9]+([._-][a-z0-9]+)*/)*[a-z0-9]+([._-][a-z0-9]+)*@sha256:[0-9a-f]{64}$ ]]; then
  echo "smoke-campaign: --shared-base-image must be REGISTRY/...@sha256:<64 lowercase hex digits>" >&2
  exit 2
fi
SHARED_BASE_DIGEST="${SHARED_BASE_IMAGE##*@}"

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
  local tool="$1" mode="$2" auth="$3" prefix_field="${4:-}"
  local prefix="$PREFIX"
  [ -z "$prefix_field" ] || { [ "$prefix_field" = EMPTY ] && prefix="" || prefix="$prefix_field"; }

  if [ ! -f "tools/$tool/build/image.json" ]; then
    echo "SKIP (not registered — no tools/$tool/build/image.json)"
    return 0
  fi
  if [ "$auth" = authenticated ] && [ -z "$CREDENTIAL_FILE" ]; then
    echo "SKIP (authenticated case needs --credential-file)"
    return 0
  fi

  local tag digest repo_tags
  tag="$(
    uv run python - "$tool" <<'PY'
import sys
from pathlib import Path

from s3_listing_study.common.build_selection import derived_image_tag, load_registered_selection

root = Path.cwd().resolve(strict=True)
print(derived_image_tag(load_registered_selection(root, sys.argv[1])))
PY
  )" || { echo "could not resolve the registered image tag for $tool"; return 1; }
  case "$tag" in
    "s3-listing-study/$tool:"*) ;;
    *) echo "registered image tag for $tool has an unexpected namespace"; return 1 ;;
  esac

  echo "-- build --"
  sg docker -c "uv run s3-listing-study build-derived-image --tool '$tool' --shared-base-image '$SHARED_BASE_IMAGE' --tag '$tag'"

  digest="$(sudo docker image inspect "$tag" --format '{{.Id}}')" || {
    echo "could not inspect the image built as $tag"
    return 1
  }
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    echo "built image for $tool has no sha256 image ID"
    return 1
  }
  repo_tags="$(sudo docker image inspect "$tag" --format '{{json .RepoTags}}')" || {
    echo "could not inspect tags for the image built as $tag"
    return 1
  }
  if ! printf '%s' "$repo_tags" | python3 -c \
    'import json, sys; tags = json.load(sys.stdin); raise SystemExit(0 if isinstance(tags, list) and sys.argv[1] in tags else 1)' \
    "$tag"; then
    echo "built image does not carry the exact requested tag $tag"
    return 1
  fi

  echo "-- smoke ($mode, $auth) --"
  local mode_dir="tools/$tool/receipts/smoke/$mode"
  mkdir -p "$mode_dir"

  # Allocate the next unused attempt-N; never remove what is already here. The
  # engine writes each artifact with an exclusive create, so a collision would
  # fail the run rather than overwrite a recorded attempt — this loop keeps us
  # from ever reaching that, without a destructive rm.
  local n=1
  while [ -e "$mode_dir/attempt-$n" ]; do n=$((n + 1)); done
  local attempt_dir="$mode_dir/attempt-$n"
  local abs_attempt_dir="$REPO_ROOT/$attempt_dir"
  mkdir -p "$abs_attempt_dir"
  # Only the new attempt directory is opened up, and only for the length of the
  # run: subject images run as their own, varying, container uid. The mode
  # directory holding prior evidence keeps its normal permissions.
  chmod 777 "$abs_attempt_dir"

  local -a env_args=() sudo_args=()
  if [ "$auth" = authenticated ]; then
    # Exported, never interpolated into a command line. `-e NAME` tells docker
    # to forward the value from its own environment; sudo resets that
    # environment, so the one variable is named on --preserve-env. If a sudoers
    # policy refuses that, docker forwards nothing, the engine refuses to run an
    # authenticated attempt without a credential, and the run fails closed
    # instead of quietly falling back to an unsigned request.
    local aws_access_key aws_secret_key
    aws_access_key="$(sed -n '1p' "$CREDENTIAL_FILE")"
    aws_secret_key="$(sed -n '2p' "$CREDENTIAL_FILE")"
    S3_STUDY_AWS_CREDENTIAL="AWS_ACCESS_KEY_ID=$aws_access_key
AWS_SECRET_ACCESS_KEY=$aws_secret_key"
    export S3_STUDY_AWS_CREDENTIAL
    env_args=(-e S3_STUDY_AWS_CREDENTIAL)
    sudo_args=(--preserve-env=S3_STUDY_AWS_CREDENTIAL)
  fi

  local tool_version
  tool_version="$(python3 -c "
import json, sys
print(json.load(open(sys.argv[1]))['tool_version'])" "tools/$tool/build/image.json")"

  local run_status=0
  sudo "${sudo_args[@]}" docker run --rm \
    --network "$NETWORK" \
    --cap-drop ALL --security-opt no-new-privileges:true \
    -v "$abs_attempt_dir:/output" \
    -e S3_STUDY_ATTEMPT_OUT=/output \
    "${env_args[@]}" \
    "$digest" \
    --output /output \
    --derived-image "$digest" \
    --shared-base-uri "$SHARED_BASE_IMAGE" \
    --shared-base-digest "$SHARED_BASE_DIGEST" \
    --tool "$tool" \
    --tool-version "$tool_version" \
    --harness-revision "$HARNESS_REVISION" \
    --operation list \
    --auth "$auth" \
    --mode "$mode" \
    --bucket "$BUCKET" \
    --region "$REGION" \
    --prefix "$prefix" \
    --scope full || run_status=$?
  unset S3_STUDY_AWS_CREDENTIAL

  sudo chown -R "$(id -u):$(id -g)" "$abs_attempt_dir"
  chmod 755 "$abs_attempt_dir"

  if [ "$run_status" -ne 0 ] || [ ! -f "$attempt_dir/result.json" ]; then
    echo "smoke FAILED (exit $run_status) — see $attempt_dir"
    return 1
  fi
  echo "wrote $attempt_dir"

  python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
print(f\"outcome={d['outcome']['status']} secret_scan={d['secret_scan']['status']} \"
      f\"summary={d['summary']['status']} row_count={d['summary']['row_count']}\")
print(f\"resources={d.get('resources')}\")
" "$attempt_dir/result.json"
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
  local entry="$1" tool mode auth prefix_field
  IFS=: read -r tool mode auth prefix_field <<<"$entry"
  echo "== $tool: starting =="
  {
    run_one_tool "$tool" "$mode" "$auth" "$prefix_field"
  } >"$LOG_DIR/$tool.log" 2>&1 &
  pids["$tool"]=$!
}

reap_one() {
  local finished_pid="" wait_status=0
  wait -n -p finished_pid || wait_status=$?
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
    IFS=: read -r tool mode auth prefix_field <<<"$entry"
    echo "== $tool =="
    if run_one_tool "$tool" "$mode" "$auth" "$prefix_field"; then
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
