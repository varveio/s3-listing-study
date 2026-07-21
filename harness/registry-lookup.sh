#!/usr/bin/env bash
# harness/registry-lookup.sh — resolve one field for one bucket from the registry.
#
#   registry-lookup.sh <bucket> <field>
#   registry-lookup.sh --harness-image
#
# The registry (docs/smoke-bucket.md) is the single source for bucket facts.
# Nothing executable in this repo embeds a bucket name (owner's rule): callers
# pass the bucket as a parameter and resolve its facts through here, so a
# receipt cites what the registry says rather than what somebody retyped.
#
# Strict by construction: any ambiguity is a hard failure. A resolver that
# guesses would put a plausible wrong digest in a receipt, which is worse than
# no receipt at all.
#
# Fields: region | manifest | manifest_sha256 | snapshot_date | keys | shape
#
set -euo pipefail
export LC_ALL=C   # byte semantics everywhere; never locale collation

die() { printf 'registry-lookup: %s\n' "$*" >&2; exit 1; }

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REGISTRY="${SMOKE_REGISTRY:-$REPO_ROOT/docs/smoke-bucket.md}"
[ -r "$REGISTRY" ] || die "registry not readable: $REGISTRY"

# `--path` and `--digest` exist so callers can BIND a receipt to the registry
# that produced it. SMOKE_REGISTRY can silently point somewhere else; a receipt
# that cites neither path nor digest cannot prove which registry it came from,
# and a leftover test override would produce official-looking evidence from a
# registry nobody reviewed.
if [ "${1:-}" = "--path" ]; then printf '%s\n' "$REGISTRY"; exit 0; fi
if [ "${1:-}" = "--digest" ]; then sha256sum "$REGISTRY" | cut -d' ' -f1; exit 0; fi

# --list-buckets: every registered bucket, resolved from the `Bucket` IDENTITY
# ROW of each section — never from heading text. Headings carry editorial
# prefixes ("Primary:", "Optional edge-case fixture:") that are prose and may be
# reworded; a caller scraping headings silently misses a bucket that is
# registered perfectly well, which for the hardcoded-bucket guard means a
# violating run.sh sails through a check the README claims is enforced.
if [ "${1:-}" = "--list-buckets" ]; then
  awk '/^## /{n++} n{print n "\t" $0}' "$REGISTRY" \
    | awk -F'\t' '{print $1}' | sort -un | while read -r n; do
        body="$(awk '/^## /{c++} c{print c "\t" $0}' "$REGISTRY" \
                | awk -F'\t' -v n="$n" '$1==n{sub(/^[0-9]+\t/,""); print}')"
        printf '%s\n' "$body" | awk '
          /^\|/ { line=$0; sub(/^\|[[:space:]]*/,"",line)
                  i=index(line,"|"); if(!i) next
                  lab=substr(line,1,i-1); val=substr(line,i+1)
                  gsub(/^[[:space:]]+|[[:space:]]+$/,"",lab)
                  if (tolower(lab)=="bucket") {
                    if (match(val, /`[^`]+`/)) print substr(val, RSTART+1, RLENGTH-2)
                    exit } }'
      done | grep -v '^$' | sort -u
  exit 0
fi

# Pull the value cell out of a two-column markdown row: | Label | value |
# Returns nothing (not an error) when the row is absent; callers decide.
#
# Requires EXACTLY ONE matching row. Taking the first match would contradict
# this script's whole contract — README promises all ambiguity is fatal, and a
# duplicated `Manifest sha256` row silently resolving to whichever came first is
# precisely the ambiguity that matters.
row_value() {
  local section="$1" label="$2" out n
  out="$(printf '%s\n' "$section" | awk -v want="$label" '
    /^\|/ {
      line = $0
      sub(/^\|[[:space:]]*/, "", line)
      idx = index(line, "|")
      if (idx == 0) next
      label = substr(line, 1, idx - 1)
      value = substr(line, idx + 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", label)
      sub(/[[:space:]]*\|[[:space:]]*$/, "", value)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (tolower(label) == tolower(want)) print value
    }')"
  [ -z "$out" ] && return 0
  n="$(printf '%s\n' "$out" | wc -l)"
  [ "$n" -eq 1 ] || die "row '$label' appears $n times in this section — registry is ambiguous, refusing to guess"
  printf '%s\n' "$out"
}

# First backtick-quoted token in a cell — the registry writes every machine-
# relevant value in backticks precisely so this is unambiguous.
#
# Pure bash on purpose. The obvious `grep -o ... | head -1` spelling exits 1 on
# no-match, and under `set -o pipefail` that kills the script *silently* at the
# assignment — no die(), no message, just rc=1. In a resolver whose whole
# contract is to fail loudly, a silent exit is the worst available bug.
first_code() {
  [[ "$1" =~ \`([^\`]*)\` ]] && printf '%s\n' "${BASH_REMATCH[1]}"
  return 0
}

# --- harness client image -------------------------------------------------
if [ "${1:-}" = "--harness-image" ]; then
  section="$(awk '/^## Harness client/{f=1;next} /^## /{f=0} f' "$REGISTRY")"
  [ -n "$section" ] || die "no '## Harness client' section in $REGISTRY"
  image="$(first_code "$(row_value "$section" "Image")")"
  [ -n "$image" ] || die "no Image row under '## Harness client'"
  case "$image" in
    *@sha256:*) ;;
    *) die "harness image is not digest-pinned: $image" ;;
  esac
  printf '%s\n' "$image"
  exit 0
fi

[ $# -eq 2 ] || die "usage: registry-lookup.sh <bucket> <field> | --harness-image"
BUCKET="$1"; FIELD="$2"
[ -n "$BUCKET" ] || die "empty bucket"

# --- locate the bucket's section -----------------------------------------
# Match on the section's Bucket row, not on the heading text: headings carry
# editorial prefixes ("Primary:", "Optional edge-case fixture:") that are prose
# and may change; the Bucket row is the identity.
sections="$(awk '/^## /{n++} n{print n "\t" $0}' "$REGISTRY")"
match_n=""
for n in $(printf '%s\n' "$sections" | cut -f1 | sort -un); do
  body="$(printf '%s\n' "$sections" | awk -F'\t' -v n="$n" '$1==n{sub(/^[0-9]+\t/,""); print}')"
  b="$(first_code "$(row_value "$body" "Bucket")")"
  if [ "$b" = "$BUCKET" ]; then
    [ -z "$match_n" ] || die "bucket '$BUCKET' matches more than one section — registry is ambiguous"
    match_n="$n"
  fi
done
[ -n "$match_n" ] || die "bucket '$BUCKET' is not registered in $REGISTRY (register it before running anything against it)"
SECTION="$(printf '%s\n' "$sections" | awk -F'\t' -v n="$match_n" '$1==n{sub(/^[0-9]+\t/,""); print}')"

need() {
  local v; v="$(row_value "$SECTION" "$1")"
  [ -n "$v" ] || die "bucket '$BUCKET' has no '$1' row in $REGISTRY"
  printf '%s\n' "$v"
}

case "$FIELD" in
  region)
    v="$(first_code "$(need "Region")")"
    [ -n "$v" ] || die "Region for '$BUCKET' is not backtick-quoted in the registry"
    printf '%s\n' "$v" ;;
  manifest)
    v="$(first_code "$(need "Manifest")")"
    [ -n "$v" ] || die "Manifest path for '$BUCKET' is not backtick-quoted in the registry"
    # Expand a leading ~ ourselves: the registry is prose for humans, and the
    # shell never sees this string before we do.
    # shellcheck disable=SC2088  # matching a LITERAL "~/" read from the file, not expanding one
    case "$v" in "~/"*) v="$HOME/${v#\~/}" ;; esac
    printf '%s\n' "$v" ;;
  manifest_sha256)
    v="$(first_code "$(need "Manifest sha256")")"
    printf '%s' "$v" | grep -Eq '^[0-9a-f]{64}$' \
      || die "Manifest sha256 for '$BUCKET' is not a 64-hex digest: '$v'"
    printf '%s\n' "$v" ;;
  snapshot_date)
    v="$(need "Snapshot date")"
    [[ "$v" =~ ([0-9]{4}-[0-9]{2}-[0-9]{2}) ]] \
      || die "Snapshot date for '$BUCKET' has no YYYY-MM-DD: '$v'"
    printf '%s\n' "${BASH_REMATCH[1]}" ;;
  keys)
    v="$(need "Keys")"
    [[ "$v" =~ ([0-9][0-9,]*) ]] || die "Keys for '$BUCKET' has no number: '$v'"
    printf '%s\n' "${BASH_REMATCH[1]//,/}" ;;
  shape)
    # The measured-shape summary the receipt must carry. Prose by design — it is
    # for a human reading the receipt, not for parsing. Absent or empty is fatal:
    # the receipt is required to carry it, and a receipt silently missing its
    # bucket shape looks complete while omitting the context that makes its
    # numbers mean anything.
    v="$(printf '%s\n' "$SECTION" | awk '/^### Measured shape/{f=1;next} /^### /{f=0} f' \
         | sed '/^[[:space:]]*$/d')"
    [ -n "$v" ] || die "bucket '$BUCKET' has no non-empty '### Measured shape' section"
    printf '%s\n' "$v"
    ;;
  *) die "unknown field '$FIELD' (region|manifest|manifest_sha256|snapshot_date|keys|shape)" ;;
esac
