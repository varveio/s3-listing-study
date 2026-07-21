#!/usr/bin/env bash
# tools/s7cmd/adapter/normalize.sh — adapter from s7cmd `ls` raw output to contract v2.
#
#   normalize.sh <mode> [prefix]
#
# Reads one mode's raw stdout on stdin, emits one line per entry:
#   key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class
# with `-` for any field the mode does not expose. Raw key bytes are passed
# through unchanged (no re-encoding); s7cmd escapes control chars to `\xNN` by
# default, but every key in the smoke bucket is plain ASCII, so escaping is an
# identity here (see report § Output). mtime is s7cmd's native
# YYYY-MM-DDTHH:MM:SSZ (chrono SecondsFormat::Secs, Z); containers run TZ=UTC.
#
# `prefix` (the run's scope, passed by the verifier from run.meta) is accepted
# per the adapter contract but unused: none of these modes use
# --show-relative-path, so keys are already absolute.
#
# Runs AFTER the wrapper's clock stops (measurement boundary) — adapter cost is
# never on the tool's clock.
set -euo pipefail
export LC_ALL=C

MODE="${1:?usage: normalize.sh <mode> [prefix]}"
PREFIX="${2:-}"
: "$PREFIX"

case "$MODE" in
  # --------------------------------------------------------------- TSV family
  # `--tsv --show-storage-class --show-etag`. Column order (columns.rs):
  #   DATE  SIZE  STORAGE_CLASS  ETAG  [VERSION_ID]  KEY
  # ETag is already quote-trimmed by the TSV formatter. KEY is always the last
  # field ($NF) — robust to the extra VERSION_ID column that --all-versions
  # inserts before KEY. A CommonPrefix (PRE) row is: (empty) PRE (empty) (empty) KEY,
  # detected by $2=="PRE"; it carries no size/etag/mtime/storage_class.
  #
  # LIMITATION (all-versions): this keys on the object KEY only and DISCARDS
  # VersionId/IsLatest. On a *versioned* bucket that would collapse multiple
  # versions (and delete markers) of one key onto a single normalized row — but
  # the contract-v2 manifest and the verifier are keyed on `key` alone, with no
  # version axis. So all-versions is validated ONLY against the non-versioned
  # smoke bucket (each key has a single "null" version, no collapse — which is
  # why it PASSED). A versioned/edge bucket is deferred (EDGE_BUCKET=none) and
  # would need a version-aware manifest first. See report §8.
  recursive-tsv|recursive-tsv-nosort|all-versions|max-depth|shallow-tsv)
    awk -F'\t' -v OFS='\t' '
      $2=="PRE"   { print $NF, "-", "-", "-", "-"; next }
      $2=="DELETE"{ print $NF, "-", "-", $1, "-"; next }   # delete marker (versions)
                  { print $NF, $2, $4, $1, $3 }
    ' ;;

  # ---------------------------------------------------------- aligned (default)
  # Default whitespace-aligned text, no --show-* flags: DATE SIZE KEY (3 cols).
  # Keys in this bucket contain no spaces, so a whitespace field split is exact.
  # Object row -> $1=date $2=size $3=key; PRE row -> empty date collapses, so
  # $1="PRE" $2=key. (This mode is only smoked recursively, where no PRE rows
  # occur; the PRE branch is kept for completeness.)
  recursive-aligned)
    awk '
      $1=="PRE" { print $2 "\t-\t-\t-\t-"; next }
      NF>=3     { print $3 "\t" $2 "\t-\t" $1 "\t-" }
    ' ;;

  # -------------------------------------------------------------------- NDJSON
  # One JSON object per line. Object: {"Key","LastModified","ETag","Size","StorageClass",...}
  # CommonPrefix: {"Prefix":...}. ETag JSON value retains its literal quotes -> strip.
  recursive-json)
    python3 -c '
import sys, json
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line:
        continue
    o = json.loads(line)
    if "Prefix" in o:
        print("\t".join([o["Prefix"], "-", "-", "-", "-"])); continue
    key = o["Key"]
    size = str(o["Size"]) if "Size" in o and o["Size"] is not None else "-"
    etag = o.get("ETag", "-")
    if etag != "-":
        etag = etag.strip("\"")
    mtime = o.get("LastModified", "-")
    sc = o.get("StorageClass", "-")
    print("\t".join([key, size, etag, mtime, sc]))
' ;;

  # ----------------------------------------------------------------- one-line
  # `-1`: just the key (or prefix) per line, no other columns. Keys only.
  recursive-one)
    awk 'NF>0 || $0!="" { print $0 "\t-\t-\t-\t-" }' ;;

  *)
    printf 'normalize.sh: unknown mode %s\n' "$MODE" >&2; exit 3 ;;
esac
