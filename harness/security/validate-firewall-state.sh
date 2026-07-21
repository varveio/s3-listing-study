#!/usr/bin/env bash
# Validate the effective entry path and exact owned chain bodies in a canonical
# firewall-state capture. Hashing the capture is additional drift detection;
# this structural check is the safety proof.
set -euo pipefail
export LC_ALL=C

[ "$#" -eq 3 ] || { printf 'usage: %s LIVE_STATE V4_RULESET V6_RULESET\n' "$0" >&2; exit 2; }
live="$1"; rules4="$2"; rules6="$3"
work="$(mktemp -d)"; trap 'rm -rf -- "$work"' EXIT

awk -v v4="$work/live4" -v v6="$work/live6" '
  /^\[ipv4\]$/ { out=v4; next }
  /^\[ipv6\]$/ { out=v6; next }
  out != "" { print >out }
' "$live"
[ -s "$work/live4" ] && [ -s "$work/live6" ] || { echo 'firewall capture lacks both families' >&2; exit 1; }

validate_family() {
  local live_family="$1" expected="$2" family="$3"
  local input_hook='-A INPUT -i s3study0 -j S3STUDY_IN'
  local forward_hook='-A FORWARD -i s3study0 -j S3STUDY_FWD'

  [ "$(grep -c '^:S3STUDY_IN ' "$live_family" || true)" = 1 ] || { echo "$family S3STUDY_IN declaration is not unique" >&2; return 1; }
  [ "$(grep -c '^:S3STUDY_FWD ' "$live_family" || true)" = 1 ] || { echo "$family S3STUDY_FWD declaration is not unique" >&2; return 1; }
  [ "$(grep -cFx -- "$input_hook" "$live_family" || true)" = 1 ] || { echo "$family INPUT hook is missing or duplicated" >&2; return 1; }
  [ "$(grep -cFx -- "$forward_hook" "$live_family" || true)" = 1 ] || { echo "$family FORWARD hook is missing or duplicated" >&2; return 1; }
  [ "$(grep -m1 '^-A INPUT ' "$live_family")" = "$input_hook" ] || { echo "$family INPUT hook is not rule 1" >&2; return 1; }
  [ "$(grep -m1 '^-A FORWARD ' "$live_family")" = "$forward_hook" ] || { echo "$family FORWARD hook is not rule 1" >&2; return 1; }
  [ "$(grep -Ec -- '^-A [^ ]+ .* -(j|g) S3STUDY_(IN|FWD)( |$)' "$live_family" || true)" = 2 ] \
    || { echo "$family has a stale or unexpected owned-chain hook" >&2; return 1; }
  grep '^-A S3STUDY_\(IN\|FWD\) ' "$expected" >"$work/expected.$family"
  grep '^-A S3STUDY_\(IN\|FWD\) ' "$live_family" >"$work/live.$family"
  cmp -s "$work/expected.$family" "$work/live.$family" \
    || { echo "$family owned chain body differs from the rendered policy" >&2; return 1; }
}

validate_family "$work/live4" "$rules4" ipv4
validate_family "$work/live6" "$rules6" ipv6
