# shellcheck shell=bash
# harness/scan-lib.sh — the single source for the credential value-shape scan.
#
# Sourced, never executed. Both harness/smoke-run.sh (payload/receipt hygiene)
# and harness/scan-tree.sh (pre-commit tree scan) pull the pattern from HERE, so
# the regex lives in exactly one place and cannot drift between the two.
#
# gitleaks is deliberately NOT used (owner's call, 2026-07-16): its entropy rules
# fire on S3 pagination cursors, so every paginating tool's --debug receipt trips
# it. This scan matches credential VALUES by shape — AKIA/ASIA key ids, hex
# signatures, long base64 assignments — not variable names. Two consequences:
#   * a receipt legitimately containing `-e AWS_SECRET_ACCESS_KEY=` with an EMPTY
#     value is the wrapper's credential starvation made visible, NOT a leak, and
#     the shape requirement (a value of real length must follow) keeps it clean;
#   * it does not fire on the ContinuationToken entropy that blocked real evidence.
#
# Validated against harness/tests/scan-fixtures/ (see harness/tests/scan-fixtures-run.sh).

# The pattern requires a credential-SHAPED VALUE, not merely "something after =".
# Match the shape (AKIA + 16, a hex signature, 20+ base64 chars) and the two
# historic false positives — the wrapper's own empty starving flag, and a `-` of
# the next argv element being read as a value — both disappear without losing
# teeth. See internal working notes § Harness mechanics (not published).
SCAN_SECRET_RE='AKIA[A-Z0-9]{16}|ASIA[A-Z0-9]{16}|X-Amz-Signature=[A-Fa-f0-9]{16,}|X-Amz-Credential=[A-Za-z0-9%/+-]{10,}|X-Amz-Security-Token=[A-Za-z0-9%/+=]{20,}|(AWS_SESSION_TOKEN|AWS_SECRET_ACCESS_KEY)=[A-Za-z0-9/+=]{16,}|aws_secret_access_key[[:space:]]*=[[:space:]]*[A-Za-z0-9/+=]{20,}|Authorization:[[:space:]]*(AWS4-HMAC-SHA256|Bearer|Basic)[[:space:]]'

# scan_secret_file <file> — classify ONE file into three outcomes, never conflated:
#   0 = clean          (grep found nothing)
#   1 = flagged        (grep matched a credential-shaped value)
#   2 = scanner error  (grep could not read/scan the file)
# grep exits 1 on no-match and 2 on error; treating rc=2 as "no match" turns a
# broken scan into a pass — that bug shipped in the first draft and must not return.
# `|| rc=$?` (not a bare call then `rc=$?`): under `set -e` a bare failing grep
# kills the caller before the next line, so a CLEAN file would abort the script.
scan_secret_file() {
  local f="$1" rc=0
  grep -aEi "$SCAN_SECRET_RE" "$f" >/dev/null 2>&1 || rc=$?
  case "$rc" in
    1) return 0 ;;
    0) return 1 ;;
    *) return 2 ;;
  esac
}
