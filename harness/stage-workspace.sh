#!/usr/bin/env bash
# Orchestrator-side: publish a blind research workspace by rename-only generation.
# Usage: stage-workspace.sh <tool>
# Re-run at EVERY dispatch — staged copies go stale when the harness changes.
set -euo pipefail

TOOL="${1:?usage: stage-workspace.sh <tool>}"
case "$TOOL" in
  aws-cli|s5cmd|s7cmd|rclone|minio-mc|s3-fast-list|swath|s3p|s3kor|s4cmd|ps3) ;;
  *) echo "REFUSING: unknown runnable subject: $TOOL" >&2; exit 2 ;;
esac

REPO="$(cd -P -- "$(dirname -- "$0")/.." && pwd)"
[ -d "$REPO/.git" ] || { echo "repo not found" >&2; exit 1; }
SHA="$(git -C "$REPO" rev-parse HEAD)"
DIRTY="$(git -C "$REPO" status --porcelain -- harness docs/operating/tool-research-brief.md docs/smoke-bucket.md)"
if [ -n "$DIRTY" ]; then
  echo "REFUSING: harness/brief/registry have uncommitted changes — stage from a committed state:" >&2
  echo "$DIRTY" >&2
  exit 1
fi

STAGING_ROOT_RAW="${S3_STUDY_SOURCES:-$HOME/.s3-listing-study/sources}"
[ -n "$STAGING_ROOT_RAW" ] || { echo "REFUSING: staging root is empty" >&2; exit 2; }
STAGING_ROOT="$(realpath -m -- "$STAGING_ROOT_RAW")"
HOME_REAL="$(realpath -m -- "$HOME")"
case "$STAGING_ROOT" in
  /|"$HOME_REAL"|"$REPO")
    echo "REFUSING: unsafe staging root resolves to $STAGING_ROOT" >&2; exit 2 ;;
  "$REPO"/*)
    echo "REFUSING: staging root is inside the repository: $STAGING_ROOT" >&2; exit 2 ;;
esac

mkdir -p -- "$STAGING_ROOT"
cd -P -- "$STAGING_ROOT"
STAGING_ROOT="$(pwd -P)"
case "$STAGING_ROOT" in
  /|"$HOME_REAL"|"$REPO"|"$REPO"/*)
    echo "REFUSING: canonical staging root is broad or repository-owned: $STAGING_ROOT" >&2; exit 2 ;;
esac

for command_name in flock mktemp mv cp awk grep git realpath; do
  command -v "$command_name" >/dev/null 2>&1 \
    || { echo "REFUSING: required command is missing: $command_name" >&2; exit 2; }
done
mv --no-copy --no-target-directory --version >/dev/null 2>&1 \
  || { echo "REFUSING: GNU mv with --no-copy and --no-target-directory is required" >&2; exit 2; }

# Lock the directory inode itself; opening a lock pathname could follow a
# hostile symlink. After cd -P, every mutable path is a fixed single component
# beneath this same filesystem directory.
exec 9< .
flock -x 9

STABLE="${TOOL}-work"
GENERATION=""
RETIRED=""
report_failure() {
  local rc=$?
  [ -z "$GENERATION" ] || printf 'stage-workspace: unpublished generation retained at %s/%s\n' "$(pwd -P)" "$GENERATION" >&2
  [ -z "$RETIRED" ] || printf 'stage-workspace: retired generation retained at %s/%s\n' "$(pwd -P)" "$RETIRED" >&2
  printf 'stage-workspace: failed; no generation was recursively deleted. Inspect or dispose of the staging filesystem.\n' >&2
  return "$rc"
}
trap report_failure ERR

GENERATION="$(mktemp -d -- ".${TOOL}-work.new.XXXXXX")"
mkdir -p -- "$GENERATION/docs"
cp -r -- "$REPO/harness" "$GENERATION/harness"
cp -- "$REPO/docs/smoke-bucket.md" "$GENERATION/docs/smoke-bucket.md"

# Extract Part 2 of the brief as BRIEF.md (Part 1 is orchestrator-only).
awk '/^## Part 2 — the agent prompt$/{found=1} found{print}' \
  "$REPO/docs/operating/tool-research-brief.md" >"$GENERATION/BRIEF.md"
[ -s "$GENERATION/BRIEF.md" ] \
  || { echo "BRIEF.md extraction produced nothing" >&2; false; }

# Contamination check: no OTHER study subject may be named in what the blind
# agent reads. The registry's aws-cli harness-client reference is accepted.
SUBJECTS="aws-cli s3-fast-list s5cmd s7cmd s3ls-rs rclone minio-mc s4cmd s3p s3kor ps3 swath s3-inventory pure-storage"
scan_rc=0
for subject in $SUBJECTS; do
  [ "$subject" = "$TOOL" ] && continue
  if hit="$(grep -rn -F -i -- "$subject" "$GENERATION/BRIEF.md" 2>/dev/null)"; [ -n "$hit" ]; then
    echo "CONTAMINATION in BRIEF.md: subject '$subject' named:" >&2
    echo "$hit" >&2
    scan_rc=1
  fi
  if [ "$subject" != aws-cli ]; then
    if hit="$(grep -rn -F -i -- "$subject" "$GENERATION/docs/smoke-bucket.md" 2>/dev/null)"; [ -n "$hit" ]; then
      echo "CONTAMINATION in smoke-bucket.md: subject '$subject' named:" >&2
      echo "$hit" >&2
      scan_rc=1
    fi
  fi
done
[ "$scan_rc" -eq 0 ] || false

{
  echo "Staged: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "Source repo commit: $SHA"
  echo "Contents: harness/ docs/smoke-bucket.md BRIEF.md"
  echo "Rule: re-staged at every dispatch; if the harness changes mid-dispatch, the dispatch is stale."
} >"$GENERATION/PROVENANCE.txt"

# Fully validate the unpublished generation before changing the stable name.
[ -d "$GENERATION/harness" ]
[ -s "$GENERATION/docs/smoke-bucket.md" ]
[ -s "$GENERATION/BRIEF.md" ]
[ -s "$GENERATION/PROVENANCE.txt" ]

if [ -e "$STABLE" ] || [ -L "$STABLE" ]; then
  RETIRED="$(mktemp -d -- ".${TOOL}-work.retired.XXXXXX")"
  mv --no-copy --no-target-directory -- "$STABLE" "$RETIRED/workspace"
  printf 'stage-workspace: prior stable entry retained at %s/%s/workspace\n' "$(pwd -P)" "$RETIRED" >&2
fi

# Same-directory, same-filesystem publication. There is a short stable-name gap
# between retirement and this rename; generations remain recoverable on failure.
mv --no-copy --no-target-directory -- "$GENERATION" "$STABLE"
GENERATION=""
printf 'stage-workspace: published %s/%s from %s\n' "$(pwd -P)" "$STABLE" "$SHA"
