#!/usr/bin/env bash
# tools/s4cmd/adapter/normalize.sh <mode> [prefix]
#
# Reads s4cmd's raw `ls` output on stdin and emits, per line:
#   key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class
# on stdout. The adapter does not re-encode keys, but s4cmd itself is lossy for
# some keys (see the rstrip/newline caveat below). `-` for any field this mode
# does not expose.
#
# s4cmd `ls` output shape (s4cmd.py pretty_print, ~line 1592):
#   "<mtime> <size> <name>"   with space-padded, aligned columns.
#   - <name> is the LAST column, left-justified, and is always a full
#     "s3://<bucket>/<key>" URL. The key is therefore absolute; `prefix` ($2) is
#     not needed to reconstruct it (accepted for interface uniformity).
#   - <size> is the object size in bytes, or the literal "DIR" for a directory
#     (CommonPrefix) entry.
#   - <mtime> is TIMESTAMP_FORMAT = "%04d-%02d-%02d %02d:%02d" (s4cmd.py:55) —
#     MINUTE precision, no seconds, no zone marker. It is UTC only because
#     botocore hands `pretty_print` a tz-aware UTC datetime whose fields are
#     formatted as-is (s4cmd.py:1602 — NO timezone conversion; TZ=UTC does not
#     affect this field). The SECOND is not exposed, so the contract-v2 canonical
#     (…:SSZ) value is not derivable: mtime is emitted as `-`.
#   - etag and storage_class are never printed by `ls`: emitted as `-`.
#
# LOSSY-KEY CAVEAT (tool-side, not adapter): s4cmd `rstrip()`s each output line
# (s4cmd.py:1622), so a key with TRAILING whitespace loses it before this adapter
# ever sees it; a key containing a NEWLINE is split across lines by the
# line-oriented formatter. Such keys cannot be faithfully normalized — a limit of
# the tool's output, recorded here so it is not mistaken for adapter fidelity.
#
# Fields exposed by mode: key (all), size (files only). etag/mtime/storage_class
# are `-` for every mode.
set -euo pipefail
mode="${1:?mode required}"
: "${2:-}"   # prefix — accepted, unused (keys are absolute)

case "$mode" in
  recursive|shallow|show-directory) ;;
  du) echo "normalize.sh: mode 'du' emits an aggregate size, not per-key listing; nothing to normalize" >&2; exit 0 ;;
  *) echo "normalize.sh: unknown mode: $mode" >&2; exit 2 ;;
esac

# Extract full URL as the substring beginning at the first "s3://", then strip
# "s3://<bucket>/" to yield the absolute key. Whatever precedes the URL holds the
# aligned <mtime> <size> columns; <size> is the last whitespace token there.
awk '
{
  i = index($0, "s3://")
  if (i == 0) next
  name = substr($0, i)                 # s3://bucket/key...
  head = substr($0, 1, i - 1)          # "<mtime> <size> " (aligned)

  # size = last whitespace-delimited token of head (trim padding first so the
  # trailing gutter space before the URL does not read as an empty token)
  gsub(/^[ \t]+|[ \t]+$/, "", head)
  n = split(head, a, /[ \t]+/)
  size = (n > 0 ? a[n] : "-")

  # key = name with leading "s3://" and bucket segment removed
  rest = substr(name, 6)               # drop "s3://"
  slash = index(rest, "/")
  if (slash == 0) next                 # bucket root with no key — skip
  key = substr(rest, slash + 1)
  if (key == "") next

  if (size == "DIR") {                 # directory / CommonPrefix entry
    printf "%s\t-\t-\t-\t-\n", key
  } else {
    printf "%s\t%s\t-\t-\t-\n", key, size
  }
}
'
