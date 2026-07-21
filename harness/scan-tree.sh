#!/usr/bin/env bash
# harness/scan-tree.sh <dir> — pre-commit credential value-shape scan over a tree.
#
# Same value-shaped scan the wrapper applies to payloads and receipts, exported
# so an agent can run it over the tree it is about to commit (brief Stage C step
# 6). Single-sourced: the pattern lives in harness/scan-lib.sh and both this
# script and harness/smoke-run.sh read it from there — no duplicated regex to
# drift.
#
# Three outcomes, never conflated (the whole point of the scan):
#   exit 0  — clean: no file flagged
#   exit 1  — FLAGGED: at least one file carries a credential-shaped value
#   exit 2  — SCANNER ERROR: the scan itself could not run (a bad arg, an
#             unreadable file). A scanner error is NOT a pass and NOT a leak.
#
# Recursively scans regular files; skips only .git. There is NO name-based
# exclusion: a `-name scan-fixtures -prune` would let anyone bypass the scan by
# naming a directory `scan-fixtures`. The scanner's own dirty test corpus does not
# need excluding because it carries NO credential-shaped bytes on disk — the dirty
# fixtures are stored base64-OBFUSCATED as `*.b64` and decoded into a mktemp dir
# only at test time (harness/tests/scan-fixtures-run.sh). So a whole-repo scan
# stays clean with no name-prune, and the pattern's teeth are still proven.
# quarantine-and-flag on a hit — this only reports; deleting evidence is never
# this script's job.
set -euo pipefail
export LC_ALL=C

err() { printf 'scan-tree: %s\n' "$*" >&2; exit 2; }   # 2 = scanner error

[ $# -eq 1 ] || err "usage: scan-tree.sh <dir>"
DIR="$1"
[ -d "$DIR" ] || err "not a directory: $DIR"

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=harness/scan-lib.sh
. "$HERE/scan-lib.sh"

flagged=0; scanned=0
# find runs in a plain pipeline via a temp file, NOT process substitution: a
# `done < <(find …)` reports the exit status of the WHILE loop, so a traversal
# failure (an unreadable directory printing `Permission denied`) would go
# unobserved and the scan would exit 0 "clean" over a tree it could not read. A
# traversal failure is never a clean pass — capture find's status AND its stderr,
# and any sign of trouble is a SCANNER ERROR (exit 2).
FINDOUT="$(mktemp)"; FINDERR="$(mktemp)"
trap 'rm -f "$FINDOUT" "$FINDERR"' EXIT
# NUL-delimited so filenames with spaces/newlines are safe. -prune only .git.
find "$DIR" -name .git -prune -o -type f -print0 >"$FINDOUT" 2>"$FINDERR" \
  || err "find failed traversing $DIR (exit $?): $(head -2 "$FINDERR") — a traversal failure is not a clean pass"
[ -s "$FINDERR" ] \
  && err "find emitted diagnostics traversing $DIR (unreadable path?): $(head -2 "$FINDERR") — a traversal failure is not a clean pass"
while IFS= read -r -d '' f; do
  scanned=$((scanned + 1))
  rc=0
  scan_secret_file "$f" || rc=$?
  case "$rc" in
    0) ;;
    1) printf 'scan-tree: FLAGGED %s\n' "$f" >&2; flagged=$((flagged + 1)) ;;
    *) err "scanner error on $f — a scan that cannot read a file is not a pass" ;;
  esac
done <"$FINDOUT"

if [ "$flagged" -gt 0 ]; then
  printf 'scan-tree: %d of %d file(s) FLAGGED — quarantine and inspect, never delete evidence.\n' \
    "$flagged" "$scanned" >&2
  exit 1
fi
printf 'scan-tree: clean — %d file(s) scanned, 0 flagged.\n' "$scanned" >&2
exit 0
