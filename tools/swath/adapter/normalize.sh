#!/usr/bin/env bash
# tools/swath/adapter/normalize.sh — swath raw output -> contract-v2 TSV.
#
# CONTRACT (brief Stage C):
#   reads a mode's raw tool output on STDIN, emits one line per OBJECT:
#     key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class
#   - `-` for any field the mode does not expose.
#   - KEY FIDELITY LIMITATION (codex F2): swath's text sinks escape control bytes
#     as \xHH by default (--raw-output off), and JSONL's `jq @tsv` escapes embedded
#     tabs/newlines; these adapters do NOT de-escape. So keys are byte-exact only
#     for control-char-free keys (true of noaa-normals-pds, ASCII). A weird-key
#     corpus needs `--raw-output` in run.sh + de-escaping here before this is
#     byte-exact. Not exercised (EDGE_BUCKET=none).
#   - mtime is YYYY-MM-DDTHH:MM:SSZ UTC. swath renders last_modified with
#     DateTimeFormatter.ISO_INSTANT (Fields.isoMicros); S3 LastModified is
#     second-precision, so the fractional part is absent and the value is already
#     the canonical `...Z` form (Containers run TZ=UTC). The ALIGNED parser assumes
#     this 20-char time in fixed columns; a sub-second ISO_INSTANT would shift
#     columns (codex F8) — safe for S3 today, a hardening item for other stores.
#   - swath emits FULL keys (it lists s3://bucket/prefix and returns whole keys),
#     so the [prefix] arg is not needed to reconstruct keys and is ignored.
#   - tsv/jsonl filter to row_type==OBJECT (dropping COMMON_PREFIX/DELETE_MARKER;
#     recursive listing produces none anyway). ALIGNED carries no row_type column,
#     so it cannot filter by type — it relies on recursive listing emitting only
#     objects (codex F8); no CommonPrefix/DeleteMarker appears in these runs.
#
# Field exposure by mode:
#   recursive-tsv / seed-none : key,size,etag,mtime,storage_class  (all)
#   recursive-jsonl           : key,size,etag,mtime,storage_class  (all)
#   recursive-aligned         : key,size,mtime                     (no etag/sc -> `-`)
#
# Usage: normalize.sh <mode> [prefix]
set -euo pipefail
export LC_ALL=C

MODE="${1:?mode required}"

case "$MODE" in
  recursive-tsv|seed-none)
    # swath TSV columns: key\tsize\tlast_modified\tetag\tstorage_class\trow_type
    # Reorder to contract: key\tsize\tetag\tmtime\tstorage_class. Skip the header.
    awk -F'\t' -v OFS='\t' '
      NR==1 && $1=="key" && $2=="size" { next }          # header line
      { rt = $6 }
      rt != "" && rt != "OBJECT" { next }                # keep OBJECT rows only
      { print $1, $2, ($4==""?"-":$4), $3, ($5==""?"-":$5) }
    '
    ;;
  recursive-jsonl)
    # One JSON object per line. row_type filters non-objects.
    jq -r 'select((.row_type // "OBJECT") == "OBJECT")
           | [ .key,
               (.size // "-" | tostring),
               (.etag // "-"),
               (.last_modified // "-"),
               (.storage_class // "-") ]
           | @tsv'
    ;;
  recursive-aligned)
    # Fixed-width layout (AlignedFormatter): size right-justified in cols [0,14),
    # two spaces, time in cols [16,40), two spaces, key from col 42 onward.
    # etag / storage_class are NOT emitted by the aligned formatter -> `-`.
    awk '
      {
        size = substr($0, 1, 14);  gsub(/^ +| +$/, "", size)
        time = substr($0, 17, 24); gsub(/^ +| +$/, "", time)
        key  = substr($0, 43)
        if (key == "") next
        print key "\t" size "\t" "-" "\t" time "\t" "-"
      }
    '
    ;;
  *)
    printf 'normalize.sh: unknown mode: %s\n' "$MODE" >&2
    exit 2 ;;
esac
