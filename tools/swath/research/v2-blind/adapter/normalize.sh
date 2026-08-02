#!/usr/bin/env bash
# tools/swath/adapter/normalize.sh — contract v2 adapter for swath v0.2.0.
#   normalize.sh <mode> [prefix]   stdin = captured stdout, stdout = 5-field TSV
#   key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class   ('-' where unexposed)
set -euo pipefail
export LC_ALL=C
MODE="${1:?mode}"; PREFIX="${2:-}"; : "$PREFIX"   # swath emits ABSOLUTE keys; prefix unused
die() { printf 'normalize.sh(swath/%s): %s\n' "$MODE" "$*" >&2; exit 3; }

# swath escapes C0 + DEL as \xHH in tsv/table and does NOT escape backslash,
# so the transform is NOT invertible: a key containing the literal chars \x09
# is byte-identical to one containing a real TAB. Refuse rather than guess.
guard_esc='if (k ~ /\\x[0-9a-f][0-9a-f]/) {
    printf "normalize.sh: key carries a swath \\xHH control escape, which is NOT invertible (ControlCharEscaper does not escape backslash). Re-run with --format jsonl. Offending: %s\n", k > "/dev/stderr"; exit 3 }'
guard_sep='if (k ~ /\t/ || k ~ /\n/) {
    printf "normalize.sh: key contains a TAB/newline, unrepresentable in contract v2: %s\n", k > "/dev/stderr"; exit 3 }'
# guard_sep is a backstop only in tsv/table: those formats escape C0 (TAB 0x09, LF 0x0a) and DEL
# as \xHH, so a RAW tab/newline can never reach it there — guard_esc is what actually refuses such
# keys, and it runs on fields that awk has already split, which is correct. In jsonl there is no
# escaping, so the equivalent check MUST happen inside jq on the raw .key (see that branch).

case "$MODE" in

  # ---- jsonl : RECOMMENDED. JSON escaping is invertible; no header; summary is
  #      kept off the data stream by construction. Fields are OMITTED when null,
  #      so key on names, never position.
  jsonl)
    command -v jq >/dev/null || die "jq is required for mode jsonl"
    # The TAB/newline refusal has to happen INSIDE jq, on the raw .key, BEFORE join("\t"):
    # once joined, a tab in a key IS a field separator and a newline IS a record break, so a
    # downstream `awk -F'\t' '{k=$1; …}'` only ever sees the truncated head and would emit a
    # malformed 6-field row (or a split record) with exit 0 — a silent contract violation.
    # `error` aborts the whole jq invocation with a nonzero exit and names the offending key.
    jq -r '
      def dash: if . == null then "-" else . end;
      def guard_sep:
        if . != null and (index("\t") != null or index("\n") != null) then
          error("key contains a TAB/newline, unrepresentable in contract v2: \(.)")
        else . end;
      [ ((.key | guard_sep) // "-"),
        (if .size == null then "-" else (.size|tostring) end),
        (.etag         | dash),
        ((.last_modified | dash) | sub("\\.[0-9]+Z$"; "Z")),
        (.storage_class| dash) ]
      | join("\t")' || die "jsonl rejected (jq exited nonzero; its message is above)"
    ;;

  # ---- tsv : header line + SIX columns in a DIFFERENT order than contract v2
  #      (key size last_modified etag storage_class row_type).
  tsv)
    awk -F'\t' -v OFS='\t' '
      NR==1 && $1=="key" && $2=="size" && $3=="last_modified" { next }   # drop header
      NF==0 { next }
      {
        if (NF != 6) { printf "normalize.sh: expected 6 tsv columns, got %d on line %d\n", NF, NR > "/dev/stderr"; exit 3 }
        k=$1; sz=$2; mt=$3; et=$4; sc=$5
        '"$guard_esc"'
        '"$guard_sep"'
        sub(/\.[0-9]+Z$/, "Z", mt)                       # ISO_INSTANT emits 0/3/6/9 frac digits
        if (sz=="") sz="-"; if (mt=="") mt="-"
        if (et=="") et="-"; if (sc=="") sc="-"
        gsub(/^"|"$/, "", et)                            # defensive: swath already strips ETag quotes
        print k, sz, et, mt, sc
      }' ;;

  # ---- table : no header, FIXED-WIDTH, key LAST, no etag / no storage_class.
  #      size[1..14] "  "[15..16] time[17..40] "  "[41..42] key[43..]
  table)
    awk '
      length($0) < 43 { printf "normalize.sh: table line %d shorter than the 42-byte fixed prefix\n", NR > "/dev/stderr"; exit 3 }
      {
        if (substr($0,15,2) != "  " || substr($0,41,2) != "  ") {
          printf "normalize.sh: table column overflow on line %d (size>14ch or timestamp>24ch) — offsets unreliable\n", NR > "/dev/stderr"; exit 3 }
        sz=substr($0,1,14); mt=substr($0,17,24); k=substr($0,43)
        gsub(/^ +| +$/, "", sz); gsub(/^ +| +$/, "", mt)
        '"$guard_esc"'
        '"$guard_sep"'
        sub(/\.[0-9]+Z$/, "Z", mt)
        if (sz=="" || sz=="PRE" || sz=="-") sz="-"       # PRE=common prefix, -=delete marker
        if (mt=="") mt="-"
        printf "%s\t%s\t-\t%s\t-\n", k, sz, mt           # table exposes NO etag, NO storage_class
      }' ;;

  *) die "unknown mode (expected tsv|jsonl|table; parquet is a directory sink and is not stdout-capturable)" ;;
esac
