#!/usr/bin/env bash
# tools/s3-fast-list/adapter/normalize.sh — raw tool output -> contract-v2 TSV.
#
# CONTRACT (harness/README.md, brief § Stage C):
#   reads the tool's raw output for a mode on STDIN and emits, per line:
#       key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class
#   raw key bytes, no re-encoding; `-` for any field the mode does not expose.
#   mtime is YYYY-MM-DDTHH:MM:SSZ (UTC). Runs AFTER the wrapper's clock stops
#   (measurement boundary) — adapter cost is the study's, never the tool's.
#
# What s3-fast-list actually emits: a PARQUET file. run.sh routes it to
# /dev/stdout, so STDIN here is the raw parquet byte stream. Its Arrow schema
# (s3-fast-list/src/utils.rs @ 6c72f59) is:
#     Key(Utf8)  Size(UInt64)  LastModified(UInt64)  ETag(Utf8)  DiffFlag(UInt8)
#   - Key         : the FULL object key (encode/decode round-trips it; core.rs).
#   - Size        : bytes.
#   - LastModified: Unix epoch SECONDS (core.rs: last_modified().secs()).
#   - ETag        : lowercase hex MD5, UNQUOTED; multipart -> "<hex>-<parts>"
#                   (core.rs etag_string()). Matches the manifest's unquoted ETag.
#   - StorageClass: NOT captured by the tool -> emitted as "-" (unexposed).
#
# Parquet needs random access (footer + seeks), so a pipe is not directly
# readable; spool stdin to a temp file, then read_parquet() it with duckdb.
#
# $2 (prefix scope) is accepted for contract compatibility but unused: this
# tool's Key column is already absolute, so no prefix reconstruction is needed.
set -euo pipefail

MODE="${1:?mode required}"
# PREFIX="${2:-}"   # unused: keys are absolute

case "$MODE" in
  list) ;;
  *) printf 'normalize.sh: unknown mode: %s\n' "$MODE" >&2; exit 3 ;;
esac

command -v duckdb >/dev/null 2>&1 || { printf 'normalize.sh: duckdb not found on host\n' >&2; exit 4; }

tmp="$(mktemp /tmp/s3fl-normalize.XXXXXX.parquet)"
trap 'rm -f "$tmp"' EXIT
cat >"$tmp"

# An empty stream is not a valid parquet; a run that listed zero objects still
# writes a valid 0-row parquet. Treat a truly empty (0-byte) stdin as 0 keys.
if [ ! -s "$tmp" ]; then
  exit 0
fi

# -list mode + tab separator emits raw values with NO CSV quoting, one row per
# line. make_timestamp() takes microseconds; epoch is UTC and the timestamp is
# tz-naive, so the formatted components are UTC by construction (append Z).
#
# CAVEAT (adapter limitation, not a tool defect): because the output is unquoted
# and uses TAB as the field separator and NEWLINE as the record separator, a
# valid S3 Key that itself contains a literal TAB or newline byte would break the
# five-column / one-record-per-line contract (an extra field, or an extra
# record). The smoke bucket's keys are plain ASCII with neither byte, so no run
# was affected; but the deferred edge-key bucket (unicode/control chars) cannot
# be verified through this adapter until it gains binary-safe framing. See
# ../research/report.md §5 and the Open questions.
duckdb -list -noheader -separator $'\t' -c "
  SELECT
    Key,
    CAST(Size AS BIGINT),
    ETag,
    strftime(make_timestamp(CAST(LastModified AS BIGINT) * 1000000), '%Y-%m-%dT%H:%M:%SZ'),
    '-'
  FROM read_parquet('$tmp');
"
