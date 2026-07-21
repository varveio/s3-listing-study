#!/usr/bin/env bash
# tools/rclone/adapter/normalize.sh — output adapter for rclone (contract v2).
#
#   normalize.sh <mode> [prefix]
#
# Reads one rclone listing mode's raw output on stdin, emits contract v2:
#   key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class   (`-` where unexposed)
# one record per line. mtime is UTC YYYY-MM-DDTHH:MM:SSZ.
#
# Key-byte fidelity caveat: the lsjson modes decode via `jq -r ... @tsv`, which
# C-escapes the four bytes TAB, NEWLINE, CR and BACKSLASH in a key (\t \n \r \\).
# A key containing one of those would therefore be emitted with altered bytes,
# NOT raw. The NOAA smoke bucket contains no such keys (verified: all keys ASCII,
# none with those bytes), so output is byte-exact here; genuine weird-key fidelity
# is a deferred edge-case check (EDGE_BUCKET=none). A future edge bucket needs a
# raw-bytes decode path (e.g. `jq -rj` with explicit NUL framing) before this
# adapter can claim byte-exactness for arbitrary keys.
#
# Runs AFTER the measurement clock stops (verifier calls it) — never on the
# tool's timed path.
#
# Field exposure by mode (what rclone actually gives without a per-object HEAD):
#   * key    — always (from the listing).
#   * size   — from the listing (ListObjectsV2 Size).
#   * mtime  — LastModified, exposed only when the run used --use-server-modtime
#              (otherwise rclone HEADs every object; we never run it that way).
#              lsf mode omits mtime by construction (`-`).
#   * etag   — rclone's S3 listing path does NOT surface the raw S3 ETag (no lsf
#              format code, no lsjson field); `--hash md5` returns an MD5 that is
#              only equal to the ETag for single-part objects, so we do not claim
#              it. Always `-`.
#   * storage_class — lsjson exposes it as ".Tier" straight from the ListObjectsV2
#              response (no HEAD): "STANDARD", "GLACIER", … — same string source as
#              the manifest's StorageClass column, so we assert it. lsf mode does
#              not surface it (`-`); CommonPrefix directory rows carry none (`-`).
#
# The verifier asserts a field only where this adapter emits a non-`-` value.
#
# prefix ($2) is the scope the run used (verifier passes run.meta's prefix). A
# scoped run points rclone at bucket/<prefix>, so rclone prints keys RELATIVE to
# <prefix>; we prepend <prefix> to reconstruct the full bucket key. Empty for a
# full-bucket or root run.
set -euo pipefail
export LC_ALL=C
MODE="${1:?mode}"; PREFIX="${2:-}"

# Canonicalise an rclone RFC3339(Nano) timestamp to YYYY-MM-DDTHH:MM:SSZ.
# rclone emits the datetime as the leading 19 chars (YYYY-MM-DDTHH:MM:SS) under
# TZ=UTC (pinned by the wrapper); S3 LastModified is whole-second, so dropping
# any fractional part and re-stamping Z is exact, never a rounding.
# Done in awk (no per-row `date` fork).

case "$MODE" in
  recursive-fastlist|recursive-hierarchical|recursive-walk|listv1)
    # lsjson --files-only recursive: a JSON array of file objects.
    #   Path (relative to the run's prefix), Size, ModTime.
    jq -r '.[] | [.Path, (.Size|tostring), (.ModTime // ""), (.Tier // "")] | @tsv' \
      | awk -F'\t' -v OFS='\t' -v pfx="$PREFIX" '
          {
            key = pfx $1
            size = $2
            mt = "-"
            if ($3 != "" && length($3) >= 19) mt = substr($3,1,19) "Z"
            sc = ($4 == "" ? "-" : $4)
            print key, size, "-", mt, sc
          }'
    ;;
  delimiter-shallow)
    # lsjson non-recursive (single delimiter level): files AND directories
    # (CommonPrefixes, IsDir=true). A directory normalises to its key with a
    # trailing "/" and `-` for every value field (a CommonPrefix carries none);
    # a file carries size + mtime.
    jq -r '.[] | [.Path, (.Size|tostring), (.ModTime // ""), (.IsDir|tostring), (.Tier // "")] | @tsv' \
      | awk -F'\t' -v OFS='\t' -v pfx="$PREFIX" '
          {
            path = $1; size = $2; modt = $3; isdir = $4; tier = $5
            if (isdir == "true") {
              print pfx path "/", "-", "-", "-", "-"
            } else {
              mt = "-"
              if (modt != "" && length(modt) >= 19) mt = substr(modt,1,19) "Z"
              sc = (tier == "" ? "-" : tier)
              print pfx path, size, "-", mt, sc
            }
          }'
    ;;
  lsf)
    # lsf --format ps --separator ';' --files-only recursive: "path;size" lines.
    # No modtime requested (would force a per-object HEAD), so mtime is `-`.
    # A key could in principle contain ';'; split on the LAST ';' so the size
    # (a pure integer with no ';') is taken from the tail and the key keeps any
    # leading ';'. NOAA keys carry none, but this keeps the adapter honest.
    awk -F'\t' -v OFS='\t' -v pfx="$PREFIX" '
      {
        line = $0
        p = 0
        for (i = length(line); i >= 1; i--) { if (substr(line,i,1) == ";") { p = i; break } }
        if (p == 0) next
        key = substr(line, 1, p-1)
        size = substr(line, p+1)
        print pfx key, size, "-", "-", "-"
      }'
    ;;
  *)
    printf 'normalize.sh: unknown mode %s\n' "$MODE" >&2; exit 3 ;;
esac
