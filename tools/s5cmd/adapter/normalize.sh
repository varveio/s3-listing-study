#!/usr/bin/env bash
# tools/s5cmd/adapter/normalize.sh <mode> [prefix]
#
# Reads one s5cmd listing mode's RAW output on stdin, emits contract-v2 TSV on
# stdout, one line per object:
#     key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class
# `-` for any field the mode does not expose. mtime is YYYY-MM-DDTHH:MM:SSZ (UTC).
# Runs AFTER the measurement clock stops (never inside a timed window).
#
# KEY-BYTE FIDELITY — scope. The text branches split on whitespace and rejoin
# fields with a single space, so a key containing runs of spaces, tabs, or an
# embedded newline is NOT reproduced byte-for-byte; the JSON branch's `jq @tsv`
# escapes tab/newline/backslash. This is exact for the NOAA smoke keyspace (keys
# are `[A-Za-z0-9._/-]`, no whitespace) and every committed PASS exercises only
# such keys. Full weird-key/unicode fidelity is DEFERRED with the edge-case
# fixture (EDGE_BUCKET=none); when that bucket exists, `--json` + a byte-safe JSON
# path (not `@tsv`) is the route to assert space/tab/newline keys. The text
# adapters are honest for the current corpus, not a general weird-key parser.
#
# KEY RECONSTRUCTION. s5cmd text `ls` prints paths RELATIVE to the query prefix
# (storage/url/url.go:170 Relative -> parseBatch, verified in Stage A). The
# verifier passes the run's prefix as $2 so full keys can be rebuilt:
#     full_key = <prefix> + <relative>
# For a full-bucket run the prefix is empty and the relative path already is the
# full key. JSON output instead prints the ABSOLUTE s3://bucket/key URL, so the
# JSON path strips the s3://bucket/ scheme+bucket and does NOT prepend prefix.
#
# TIMEZONE. s5cmd text prints ModTime as "2006/01/02 15:04:05" with no offset
# (command/ls.go:248). Containers run TZ=UTC pinned, and the SDK parses S3
# LastModified as UTC, so this timestamp is UTC by construction; we append `Z`.
# JSON already emits RFC3339 `...Z`.
set -euo pipefail
export LC_ALL=C

mode="${1:?mode required}"
prefix="${2:-}"

case "$mode" in
  recursive|listv1|allversions)
    # Text, batch/recursive. Columns (with -e -s): date time SC etag size relkey
    # [versionID]. allversions appends a trailing versionID token (e.g. "null"
    # on a non-versioned bucket) which we drop. Key = fields 6..(end, minus the
    # trailing version token for allversions). DIR lines cannot occur in a
    # recursive listing (Delimiter=""), but are skipped defensively.
    awk -v OFS='\t' -v pfx="$prefix" -v mode="$mode" '
      {
        if ($1 == "DIR") next
        date=$1; time=$2; sc=$3; etag=$4; size=$5
        gsub(/"/,"",etag)
        gsub(/\//,"-",date)
        # rebuild key from field 6 to the last relevant field
        last = NF
        if (mode == "allversions") last = NF - 1   # drop trailing versionID
        key=$6
        for (i=7; i<=last; i++) key = key " " $i
        mtime = date "T" time "Z"
        print pfx key, size, etag, mtime, sc
      }' ;;
  delimiter)
    # Text delimiter mode. DIR lines: "<blanks> DIR <relkey/>"  ($1=="DIR").
    # Object lines: same 6-column layout as recursive. CommonPrefixes carry no
    # size/etag/mtime/storage_class -> `-`.
    awk -v OFS='\t' -v pfx="$prefix" '
      $1 == "DIR" {
        key=$2
        for (i=3; i<=NF; i++) key = key " " $i
        print pfx key, "-", "-", "-", "-"
        next
      }
      {
        date=$1; time=$2; sc=$3; etag=$4; size=$5
        gsub(/"/,"",etag)
        gsub(/\//,"-",date)
        key=$6
        for (i=7; i<=NF; i++) key = key " " $i
        mtime = date "T" time "Z"
        print pfx key, size, etag, mtime, sc
      }' ;;
  fullpath)
    # --show-fullpath output: one absolute s3://bucket/key URL per line, nothing
    # else. Strip the s3://bucket/ scheme+bucket to the full key; the mode
    # exposes no size/etag/mtime/storage-class, so those are `-`.
    awk -v OFS='\t' '{ k=$0; sub(/^s3:\/\/[^/]*\//,"",k); print k, "-", "-", "-", "-" }' ;;
  rootkeys)
    # Remainder adapter for the fan-out --scope union. Input is a root delimiter
    # listing (same raw output as `delimiter`); we keep ONLY the unprefixed
    # OBJECT rows and DROP the DIR common-prefix rows, because the union's
    # remainder is verified against the manifest's unprefixed keys only — a DIR
    # pseudo-key would read as an out-of-scope extra. prefix is empty (remainder).
    awk -v OFS='\t' -v pfx="$prefix" '
      $1 == "DIR" { next }
      {
        date=$1; time=$2; sc=$3; etag=$4; size=$5
        gsub(/"/,"",etag)
        gsub(/\//,"-",date)
        key=$6
        for (i=7; i<=NF; i++) key = key " " $i
        mtime = date "T" time "Z"
        print pfx key, size, etag, mtime, sc
      }' ;;
  json)
    # JSON lines. `.key` is the absolute s3://bucket/key URL; strip scheme+bucket
    # to get the full key. Skip dir-type records (none in a recursive listing).
    # `.last_modified` is already RFC3339 `...Z`.
    jq -rj '
      select(.type != "dir")
      | (.key | sub("^s3://[^/]*/"; "")) as $k
      | [$k, (.size|tostring), (.etag // "-"),
         (.last_modified // "-"), (.storage_class // "-")]
      | @tsv, "\n"' ;;
  *)
    printf 'normalize.sh: unknown mode: %s\n' "$mode" >&2; exit 2 ;;
esac
