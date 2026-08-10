#!/usr/bin/env bash
# Exercise the attempt harness end to end, locally, against a real bucket —
# the paths a campaign will take that smoke-campaign.sh never reaches.
#
# smoke-campaign.sh answers "does each tool list?"; every one of its runs
# completes well inside the default 300s timeout, so the supervision code that
# fires when a run does NOT complete has never run in a container. This script
# fires it deliberately, on one tool, and checks what the receipt says
# afterwards.
#
#   happy      generous timeout, small prefix  -> completed, counted
#   terminate  short timeout, grace honored    -> timed_out, SIGTERM was enough
#   hardkill   short timeout, zero grace       -> timed_out, escalated to SIGKILL
#
# Each case additionally recomputes the stdout hash from the stored gzip and
# compares it against the receipt, because a partially-written stream is
# exactly where a truncated capture would go unnoticed.
#
# With --destination, a fifth case runs the same attempt with an upload
# attached and checks what landed in GCS, including that re-uploading the same
# attempt to the same leaf is refused. Without it nothing here touches GCS at
# all, which is the mode the repo's own smoke campaign runs in.
#
# Usage:
#   harness/e2e-local.sh [--tool SLUG] [--bucket NAME] [--region NAME]
#                        [--prefix PREFIX] [--mode MODE]
#                        [--destination gs://BUCKET/PREFIX] [--keep]
#
# Prerequisites are smoke-campaign.sh's, minus credentials: the tool's derived
# image already built, the `s3-listing-study-subjects` bridge with its
# metadata-IP deny rule, and `sudo docker`. Anonymous only — this script never
# takes a credential, so it cannot leak one.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$HERE/.." && pwd)"
cd "$REPO_ROOT"

TOOL="aws-cli"
MODE="s3api-v2-text"
# Empty means "run everything locally and upload nothing", which is the default
# and the mode the repo's own smoke campaign uses.
DESTINATION=""
BUCKET="noaa-normals-pds"
REGION="us-east-1"
# The completing case lists this prefix; the timing-out cases list the whole
# bucket, which is far too big to finish inside their timeout.
PREFIX="normals-hourly/"
NETWORK="s3-listing-study-subjects"
KEEP=no

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tool) TOOL="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --bucket) BUCKET="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --destination) DESTINATION="$2"; shift 2 ;;
    --keep) KEEP=yes; shift ;;
    *) echo "e2e-local: unknown argument: $1" >&2; exit 2 ;;
  esac
done

command -v docker >/dev/null 2>&1 || { echo "e2e-local: docker not found" >&2; exit 2; }
sudo docker network inspect "$NETWORK" >/dev/null 2>&1 || {
  echo "e2e-local: docker network '$NETWORK' missing — see docs/operating/runner-security.md" >&2
  exit 2
}

tag="$(sudo docker images --filter "reference=s3-listing-study/$TOOL" \
  --format '{{.Repository}}:{{.Tag}}' | head -1)"
[ -n "$tag" ] || { echo "e2e-local: no derived image for '$TOOL' — build it first" >&2; exit 2; }
DIGEST="$(sudo docker inspect "$tag" --format '{{index .RepoDigests 0}}' | cut -d@ -f2)"

WORK="$(mktemp -d -t s3-study-e2e-XXXXXX)"
cleanup() { [ "$KEEP" = yes ] && echo "kept: $WORK" || rm -rf "$WORK"; }
trap cleanup EXIT

FAILURES=0
check() { # check <label> <actual> <expected>
  if [ "$2" = "$3" ]; then
    echo "    ok   $1 = $2"
  else
    echo "    FAIL $1 = $2 (expected $3)"
    FAILURES=$((FAILURES + 1))
  fi
}

# Runs one attempt in the derived image and leaves the receipt in $WORK/<name>.
# Extra arguments after the case name go straight to the attempt runner.
run_case() {
  local name="$1"; shift
  local dir="$WORK/$name"
  mkdir -p "$dir"
  # The subject image runs as its own, varying, container uid.
  chmod 777 "$dir"
  local -a token_args=() sudo_args=()
  if [ -n "$DESTINATION" ]; then
    # Forwarded by name, never interpolated into a command line, so the token
    # never reaches argv or `ps` — the same discipline smoke-campaign.sh uses
    # for the AWS credential. Injection rather than the metadata server
    # because this bridge deliberately cannot reach 169.254.169.254.
    token_args=(-e S3_STUDY_GCS_TOKEN)
    sudo_args=(--preserve-env=S3_STUDY_GCS_TOKEN)
  fi
  sudo "${sudo_args[@]}" docker run --rm \
    --network "$NETWORK" \
    --cap-drop ALL --security-opt no-new-privileges:true \
    -v "$dir:/output" \
    -e S3_STUDY_ATTEMPT_OUT=/output \
    "${token_args[@]}" \
    "s3-listing-study/$TOOL@$DIGEST" \
    --output /output \
    --derived-image "$DIGEST" \
    --tool "$TOOL" \
    --harness-revision e2e-local \
    --operation list \
    --auth anonymous \
    --mode "$MODE" \
    --bucket "$BUCKET" \
    --region "$REGION" \
    --scope full \
    "$@" >/dev/null
  sudo chown -R "$(id -u):$(id -g)" "$dir"
  [ -f "$dir/result.json" ] || { echo "    FAIL no result.json written"; FAILURES=$((FAILURES + 1)); }
}

# Prints one `key=value` line per field the assertions below read, so the
# checks stay in bash rather than splitting across two languages.
facts() {
  python3 -c '
import gzip, hashlib, json, sys
from pathlib import Path

d = Path(sys.argv[1])
r = json.loads((d / "result.json").read_text())
out = {
    "status": r["outcome"]["status"],
    "timed_out": r["outcome"]["timed_out"],
    "signal": r["outcome"]["signal"],
    "exit_code": r["outcome"]["exit_code"],
    "state": r["outcome"]["cleanup"]["state"],
    "term_sent": r["outcome"]["cleanup"]["term_sent"],
    "kill_sent": r["outcome"]["cleanup"]["kill_sent"],
    "group_empty": r["outcome"]["cleanup"]["process_group_empty"],
    "escaped": len(r["outcome"]["cleanup"]["escaped_descendants"]),
    "secret_scan": r["secret_scan"]["status"],
    "attempt_id": r["attempt_id"],
}
# Every measurement the campaign exists to collect must be present and
# positive on any attempt that ran at all, timed out or not.
res = r["resources"]
out["rss_positive"] = res["rusage_children_max_child_peak_rss_kb"] > 0
out["cpu_positive"] = (
    res["rusage_children_user_cpu_s"] + res["rusage_children_system_cpu_s"]
) > 0
out["disk_present"] = res["whole_filesystem_peak_used_delta_bytes"] is not None
out["elapsed_positive"] = r["timing"]["elapsed_ns"] > 0
# A timeout must not overshoot its deadline by more than the grace it was
# given plus a second of reaping — that is the whole point of the deadline.
budget = r["timing"]["timeout_ns"] + r["timing"]["term_grace_ns"] + 1_000_000_000
out["within_deadline"] = r["timing"]["elapsed_ns"] <= budget
# Recompute from what was actually stored: a truncated capture would show up
# here and nowhere else.
s = r["streams"]["stdout"]
raw = gzip.decompress((d / s["path"]).read_bytes())
out["stdout_bytes_match"] = len(raw) == s["raw_bytes"]
out["stdout_hash_match"] = hashlib.sha256(raw).hexdigest() == s["raw_sha256"]
out["stdout_nonempty"] = s["raw_bytes"] > 0

out["summary_status"] = r["summary"]["status"]
out["row_count"] = r["summary"]["row_count"]

for k, v in out.items():
    print(f"{k}={v}")
' "$1"
}

fact() { grep -m1 "^$2=" "$1" | cut -d= -f2-; }

echo "e2e-local: $TOOL @ ${DIGEST:0:19}… bucket=$BUCKET"

echo
echo "== happy: completes well inside its timeout =="
run_case happy --prefix "$PREFIX" --timeout 120
facts "$WORK/happy" > "$WORK/happy.facts"
check "status"             "$(fact "$WORK/happy.facts" status)"             "completed"
check "timed_out"          "$(fact "$WORK/happy.facts" timed_out)"          "False"
check "exit_code"          "$(fact "$WORK/happy.facts" exit_code)"          "0"
check "cleanup.state"      "$(fact "$WORK/happy.facts" state)"              "not_needed"
check "escaped"            "$(fact "$WORK/happy.facts" escaped)"            "0"
check "summary.status"     "$(fact "$WORK/happy.facts" summary_status)"     "counted"
check "row_count"          "$(fact "$WORK/happy.facts" row_count)"          "2549"

# Grace is generous enough that a well-behaved subject exits on SIGTERM alone.
echo
echo "== terminate: deadline hit, SIGTERM is enough =="
run_case terminate --timeout 6 --term-grace 2
facts "$WORK/terminate" > "$WORK/terminate.facts"
check "status"             "$(fact "$WORK/terminate.facts" status)"         "timed_out"
check "timed_out"          "$(fact "$WORK/terminate.facts" timed_out)"      "True"
check "term_sent"          "$(fact "$WORK/terminate.facts" term_sent)"      "True"
check "group_empty"        "$(fact "$WORK/terminate.facts" group_empty)"    "True"
check "escaped"            "$(fact "$WORK/terminate.facts" escaped)"        "0"
check "partial stdout"     "$(fact "$WORK/terminate.facts" stdout_nonempty)" "True"
# A partial listing is not a measurement: the worker must decline to count it
# rather than report a row count that undercounts the bucket.
check "row_count declined" "$(fact "$WORK/terminate.facts" row_count)"      "None"

# Zero grace leaves no room to exit voluntarily, so the group must be killed.
echo
echo "== hardkill: deadline hit, escalates to SIGKILL =="
run_case hardkill --timeout 4 --term-grace 0
facts "$WORK/hardkill" > "$WORK/hardkill.facts"
check "status"             "$(fact "$WORK/hardkill.facts" status)"          "timed_out"
check "cleanup.state"      "$(fact "$WORK/hardkill.facts" state)"           "killed"
check "kill_sent"          "$(fact "$WORK/hardkill.facts" kill_sent)"       "True"
check "group_empty"        "$(fact "$WORK/hardkill.facts" group_empty)"     "True"
check "escaped"            "$(fact "$WORK/hardkill.facts" escaped)"         "0"

echo
echo "== every case: metrics captured, streams intact, deadline respected =="
for case_name in happy terminate hardkill; do
  echo "  $case_name"
  for field in rss_positive cpu_positive disk_present elapsed_positive \
               within_deadline stdout_bytes_match stdout_hash_match; do
    check "$field" "$(fact "$WORK/$case_name.facts" "$field")" "True"
  done
  check "secret_scan" "$(fact "$WORK/$case_name.facts" secret_scan)" "clean"
done

if [ -n "$DESTINATION" ]; then
  echo
  echo "== upload: a completed attempt publishes itself to $DESTINATION =="
  # Minted here and forwarded by name. Short-lived by construction, so an
  # e2e run cannot leave a durable credential behind in the environment.
  S3_STUDY_GCS_TOKEN="$(gcloud auth print-access-token)"
  export S3_STUDY_GCS_TOKEN
  run_case upload --prefix "$PREFIX" --timeout 120 --destination "$DESTINATION"
  facts "$WORK/upload" > "$WORK/upload.facts"
  check "status" "$(fact "$WORK/upload.facts" status)" "completed"

  # The worker names its own run leaf, so the destination layout is only
  # knowable from the receipt the run produced.
  leaf="$(fact "$WORK/upload.facts" attempt_id)"
  # stdout, stderr, and the worker-owned result (which already holds row_count).
  landed="$(gcloud storage ls "$DESTINATION/$leaf/" 2>/dev/null | wc -l)"
  check "objects at destination" "$landed" "3"

  check "worker row_count" "$(fact "$WORK/upload.facts" row_count)" "2549"

  # The create-only precondition is the whole of "an attempt is never
  # overwritten". Re-uploading the same directory to the same leaf must be
  # refused, not silently accepted as a second generation.
  #
  echo "  re-upload to the same leaf must be refused"
  if uv run s3-listing-study upload-attempt \
       --attempt-dir "$WORK/upload" --destination "$DESTINATION/$leaf" >/dev/null 2>&1; then
    echo "    FAIL re-upload succeeded; create-only did not hold"
    FAILURES=$((FAILURES + 1))
  else
    echo "    ok   re-upload refused on the objects that already existed"
  fi
  unset S3_STUDY_GCS_TOKEN

  final="$(gcloud storage ls "$DESTINATION/$leaf/" 2>/dev/null | wc -l)"
  check "objects after refused re-upload" "$final" "3"

  echo
  echo "  destination layout:"
  gcloud storage ls -r "$DESTINATION/$leaf/" 2>/dev/null | sed 's/^/    /'
fi

echo
echo "== no container outlived its attempt =="
leftover="$(sudo docker ps -aq --filter "ancestor=s3-listing-study/$TOOL@$DIGEST" | wc -l)"
check "leftover containers" "$leftover" "0"

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "e2e-local: PASS"
else
  echo "e2e-local: FAIL ($FAILURES check(s))"
  exit 1
fi
