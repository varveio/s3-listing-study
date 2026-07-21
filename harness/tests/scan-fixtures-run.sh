#!/usr/bin/env bash
# harness/tests/scan-fixtures-run.sh — durable validation of the secret scan.
#
# The scan is single-sourced in harness/scan-lib.sh and used by both
# harness/smoke-run.sh (payload/receipt hygiene) and harness/scan-tree.sh
# (pre-commit). This runner proves, on every change, that:
#   * every fixture under scan-fixtures/clean/ stays CLEAN (no false positive —
#     including the wrapper's own `-e AWS_SECRET_ACCESS_KEY=` empty-value flag,
#     redacted text, and a paginating tool's ContinuationToken);
#   * every dirty fixture FLAGS (a real secret still blocks);
#   * scan-tree.sh's three-outcome exit codes are correct end-to-end.
#
# The dirty fixtures are stored base64-OBFUSCATED as scan-fixtures/dirty/*.b64 so
# no credential-shaped bytes ever sit in the repo (a whole-repo scan-tree.sh pass
# then needs NO name-based exclusion — the very bypass that removal closed). They
# are decoded into a mktemp dir HERE, at test time, and the scan is pointed there.
#
# Exit 0 = all expectations met; exit 1 = a fixture behaved wrong.
set -euo pipefail
export LC_ALL=C

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HARNESS="$(cd -- "$HERE/.." && pwd)"
# shellcheck source=harness/scan-lib.sh
. "$HARNESS/scan-lib.sh"
SCAN_TREE="$HARNESS/scan-tree.sh"
FIX="$HERE/scan-fixtures"

# Decode the obfuscated dirty fixtures into a throwaway dir; scan there.
DIRTY="$(mktemp -d)"
trap 'rm -rf "$DIRTY"' EXIT
for b in "$FIX"/dirty/*.b64; do
  base64 -d "$b" >"$DIRTY/$(basename "${b%.b64}")"
done

fail=0
pass() { printf 'ok   %s\n' "$*"; }
bad()  { printf 'FAIL %s\n' "$*"; fail=1; }

# --- per-file classification -------------------------------------------------
for f in "$FIX"/clean/*; do
  rc=0; scan_secret_file "$f" || rc=$?
  case "$rc" in
    0) pass "clean stays clean: ${f##*/}" ;;
    1) bad  "clean fixture FLAGGED (false positive): ${f##*/}" ;;
    *) bad  "scanner error on clean fixture: ${f##*/}" ;;
  esac
done
for f in "$DIRTY"/*; do
  rc=0; scan_secret_file "$f" || rc=$?
  case "$rc" in
    1) pass "dirty flags: ${f##*/}" ;;
    0) bad  "dirty fixture NOT flagged (false negative): ${f##*/}" ;;
    *) bad  "scanner error on dirty fixture: ${f##*/}" ;;
  esac
done

# --- the stored .b64 fixtures must themselves be clean on disk ----------------
# (no credential-shaped bytes committed; this is what lets scan-tree.sh drop the
# name-prune and still pass a whole-repo scan).
for b in "$FIX"/dirty/*.b64; do
  rc=0; scan_secret_file "$b" || rc=$?
  case "$rc" in
    0) pass "stored obfuscated fixture is clean on disk: ${b##*/}" ;;
    *) bad  "stored .b64 fixture is NOT clean on disk (obfuscation leaked): ${b##*/}" ;;
  esac
done

# --- scan-tree.sh end-to-end -------------------------------------------------
expect_tree() {  # <label> <dir> <want-exit>
  local label="$1" dir="$2" want="$3" rc=0
  "$SCAN_TREE" "$dir" >/dev/null 2>&1 || rc=$?
  if [ "$rc" -eq "$want" ]; then pass "$label -> exit $rc"; else bad "$label -> exit $rc (want $want)"; fi
}
expect_tree "scan-tree.sh clean tree" "$FIX/clean" 0
expect_tree "scan-tree.sh decoded-dirty tree" "$DIRTY" 1
expect_tree "scan-tree.sh stored-.b64 tree stays clean" "$FIX/dirty" 0
expect_tree "scan-tree.sh bad arg (scanner error)" "$FIX/no-such-dir-$$" 2

[ "$fail" -eq 0 ] && { printf 'ALL SCAN FIXTURES PASS\n'; exit 0; }
printf 'SCAN FIXTURE FAILURE\n'; exit 1
