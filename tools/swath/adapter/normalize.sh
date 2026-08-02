#!/usr/bin/env bash
# tools/swath/adapter/normalize.sh — swath native output -> the smoke contract.
#
# Usage: normalize.sh <mode> [prefix]
#   stdin  = the captured stdout payload for that mode
#   stdout = key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class, one row per object
#            ('-' for any field the mode does not expose)
#
# SUBJECT: swath v0.2.0 (commit cef8ec2).
#
# `prefix` ($2) is accepted and ignored: swath lists s3://bucket/prefix and
# returns WHOLE keys, so nothing needs reconstructing.
#
# Per-mode field coverage:
#   recursive-tsv / seed-none : key size etag mtime storage_class  (all five)
#   recursive-jsonl           : key size etag mtime storage_class  (all five)
#   recursive-table           : key size mtime                     (etag, sc -> '-')
#
# KEY-FIDELITY LIMIT — why this script refuses rather than repairs.
# swath's text sinks escape bytes < 0x20 and 0x7f as \xHH, and the escaper does
# NOT escape the backslash itself. So a key literally containing the four
# characters \x09 is byte-identical on the wire to a key containing a real TAB:
# the transform is not invertible. Escaping is also not disableable at v0.2.0
# (--raw-output carries no @Option; new runs always escape). Decoding \xHH would
# silently invent keys, so tsv/table REFUSE such a row instead. Only the JSONL
# and Parquet paths are faithful for control-char keys — JSON escaping is
# invertible because it escapes the backslash. See claims
# `text-sink-key-fidelity-ascii-only`, `jsonl-escaping-is-invertible`,
# `control-char-key-fidelity-untested`. Not exercised on the registered bucket,
# whose keys are ASCII.
#
# TIMESTAMPS — swath renders via DateTimeFormatter.ISO_INSTANT, which emits 0, 3,
# 6 or 9 fractional digits as needed. S3 LastModified is second-granularity so
# the practical output is already the contract's `...Z` form, but a sub-second
# endpoint would produce a value the verifier rejects outright as an adapter
# violation. The fractional part is therefore stripped unconditionally
# (claim `timestamp-precision-is-variable`).
set -euo pipefail
export LC_ALL=C

MODE="${1:?mode required}"
PREFIX="${2:-}"; : "$PREFIX"   # accepted, deliberately unused (see header)

die() { printf 'normalize.sh(swath/%s): %s\n' "$MODE" "$*" >&2; exit 3; }

# Refuse a \xHH control escape rather than decode it (not invertible, see header).
guard_esc='if (k ~ /\\x[0-9a-f][0-9a-f]/) {
    printf "normalize.sh: key carries a swath \\xHH control escape, which is not invertible (the backslash is not escaped). Re-run this scope with --format jsonl. Offending key: %s\n", k > "/dev/stderr"; exit 3 }'
# Contract v2 is TAB-delimited and cannot represent a key containing TAB/newline.
guard_sep='if (k ~ /\t/ || k ~ /\n/) {
    printf "normalize.sh: key contains a TAB or newline, unrepresentable in the 5-field contract: %s\n", k > "/dev/stderr"; exit 3 }'

case "$MODE" in

  # ---- TSV: header line, SIX columns, and NOT in contract order --------------
  # swath emits: key size last_modified etag storage_class row_type
  # contract is: key size etag           mtime            storage_class
  recursive-tsv|seed-none)
    awk -F'\t' -v OFS='\t' '
      NR==1 && $1=="key" && $2=="size" && $3=="last_modified" { next }   # header
      NF==0 { next }
      $6 != "" && $6 != "OBJECT" { next }                                # objects only
      {
        if (NF != 6) { printf "normalize.sh: expected 6 tsv columns, got %d on line %d\n", NF, NR > "/dev/stderr"; exit 3 }
        k=$1; sz=$2; mt=$3; et=$4; sc=$5
        '"$guard_esc"'
        '"$guard_sep"'
        sub(/\.[0-9]+Z$/, "Z", mt)
        if (sz=="") sz="-"; if (mt=="") mt="-"
        if (et=="") et="-"; if (sc=="") sc="-"
        gsub(/^"|"$/, "", et)          # defensive; swath already stores ETags unquoted
        print k, sz, et, mt, sc
      }' ;;

  # ---- JSONL: no header, nullable fields OMITTED (never null) ---------------
  # Key on field NAMES, never position. The TAB/newline guard runs inside jq on
  # the raw .key, BEFORE join("\t") — after the join a TAB has already become a
  # field separator and would slip through as a malformed 6-field row.
  # join("\t") is used rather than @tsv: @tsv escapes a backslash as \\ and would
  # corrupt any key containing one.
  recursive-jsonl)
    command -v jq >/dev/null || die "jq is required for mode recursive-jsonl"
    # Exit status is inspected rather than `|| die`d: a downstream reader that
    # closes early (`head`, an aborted verifier) delivers SIGPIPE, and reporting
    # that as a key-fidelity rejection would be actively misleading. 141 = 128+13.
    set +e
    jq -r '
      def dash: if . == null then "-" else . end;
      select((.row_type // "OBJECT") == "OBJECT")
      | (if (.key | test("[\t\n]")) then
           error("key contains a TAB or newline, unrepresentable in the 5-field contract: " + .key)
         else . end)
      | [ (.key // "-"),
          (if .size == null then "-" else (.size|tostring) end),
          (.etag          | dash),
          ((.last_modified | dash) | sub("\\.[0-9]+Z$"; "Z")),
          (.storage_class | dash) ]
      | join("\t")'
    jq_rc=$?
    set -e
    if [ "$jq_rc" -ne 0 ] && [ "$jq_rc" -ne 141 ]; then
      die "rejected (jq exited $jq_rc; its message is above)"
    fi ;;

  # ---- TABLE: no header, fixed-width, key LAST, no etag, no storage_class ---
  # size right-justified [1..14], "  ", time [17..40], "  ", key [43..]
  # The formatter appends unpadded when a value overflows its width, so the
  # separator spaces are asserted and the row refused if they are missing — a
  # silent mis-slice would fabricate keys.
  recursive-table)
    awk '
      length($0) < 43 { printf "normalize.sh: table line %d shorter than the 42-byte fixed prefix\n", NR > "/dev/stderr"; exit 3 }
      {
        if (substr($0,15,2) != "  " || substr($0,41,2) != "  ") {
          printf "normalize.sh: table column overflow on line %d (size>14ch or timestamp>24ch); offsets unreliable\n", NR > "/dev/stderr"; exit 3 }
        sz=substr($0,1,14); mt=substr($0,17,24); k=substr($0,43)
        gsub(/^ +| +$/, "", sz); gsub(/^ +| +$/, "", mt)
        '"$guard_esc"'
        '"$guard_sep"'
        sub(/\.[0-9]+Z$/, "Z", mt)
        if (sz=="" || sz=="PRE" || sz=="-") sz="-"   # PRE=common prefix, -=delete marker
        if (mt=="") mt="-"
        printf "%s\t%s\t-\t%s\t-\n", k, sz, mt        # table exposes no etag/storage_class
      }' ;;

  *)
    die "unknown mode (expected recursive-tsv|recursive-jsonl|recursive-table|seed-none; the parquet modes are directory sinks and are not stdout-capturable)" ;;
esac
