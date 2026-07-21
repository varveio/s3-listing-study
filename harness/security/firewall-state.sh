#!/bin/bash
# Installed root-owned by runner-security-provision.sh. It exposes only the
# canonicalized complete filter-table state so the unprivileged harness can
# detect a rule inserted before a study hook, without receiving a general
# privileged command surface. A dedicated runner has no unrelated policy that
# should drift.
set -euo pipefail
export LC_ALL=C
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

emit_filter() {
  local family="$1" save="$2"
  printf '[%s]\n' "$family"
  # iptables-save decorates otherwise identical rulesets with wall-clock
  # comments and mutable chain counters. Neither is policy state. Normalize
  # both so the readiness digest changes only when filter-table semantics or
  # rule order change.
  "$save" -t filter | sed -E \
    -e '/^#/d' \
    -e 's/^(:[^[:space:]]+[[:space:]]+[^[:space:]]+)[[:space:]]+\[[0-9]+:[0-9]+\]$/\1 [0:0]/'
}

emit_filter ipv4 /usr/sbin/iptables-save
emit_filter ipv6 /usr/sbin/ip6tables-save
