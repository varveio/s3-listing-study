#!/usr/bin/env bash
# harness/tests/adapters/normalize.sh — test-fixture normalize adapter (contract v2).
#
# Reads a tool's raw output on stdin, emits contract v2 on stdout:
#   key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class   (`-` where unexposed)
#
#   normalize.sh <mode> [prefix]
#
# Modes drive the harness regression suite, not a real tool:
#   listobjects — aws-cli `Contents[].[Key,Size,ETag,LastModified,StorageClass]`
#                 text: strip ETag quotes, rewrite `+00:00` -> `Z`. Full fidelity.
#   keysonly    — keys only; every non-key field is `-` (keys-only policy check).
#   badadapter  — deliberately WRONG: corrupts the size on row 1 and the mtime on
#                 row 2 (drives the FAIL-with-fields=2 regression).
#   passthrough — input is already contract-v2 TSV; emit unchanged (synthetic
#                 union shards). Validates 5 fields.
#   rootlisting — like passthrough, but a DISTINCT mode string: stands in for the
#                 delimiter/root listing a union remainder shard uses (a different
#                 request shape, hence a different mode, exempt from mode binding).
set -euo pipefail
export LC_ALL=C
MODE="${1:?mode}"; PREFIX="${2:-}"   # prefix accepted per contract (unused here: keys are absolute)
: "$PREFIX"

case "$MODE" in
  listobjects)
    awk -F'\t' -v OFS='\t' 'NF>=5 { gsub(/"/,"",$3); sub(/\+00:00$/,"Z",$4); print $1,$2,$3,$4,$5 }' ;;
  keysonly)
    awk -F'\t' -v OFS='\t' 'NF>=1 { print $1,"-","-","-","-" }' ;;
  badadapter)
    awk -F'\t' -v OFS='\t' '
      NF>=5 { gsub(/"/,"",$3); sub(/\+00:00$/,"Z",$4)
              if (NR==1) $2=$2+1            # wrong size on the first row
              if (NR==2) $4="1999-01-01T00:00:00Z"   # wrong mtime on the second row
              print $1,$2,$3,$4,$5 }' ;;
  passthrough|rootlisting)
    cat ;;
  *) printf 'normalize.sh: unknown mode %s\n' "$MODE" >&2; exit 3 ;;
esac
