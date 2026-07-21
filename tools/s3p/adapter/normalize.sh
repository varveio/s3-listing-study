#!/usr/bin/env bash
# tools/s3p/adapter/normalize.sh <mode> [prefix]
#
# Reads one mode's RAW s3p stdout on stdin, emits contract-v2 TSV on stdout:
#   key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class
# one line per object, raw key bytes (no re-encoding). `-` for any field the
# mode does not expose (the verifier asserts a field only where it is non-`-`).
# mtime is YYYY-MM-DDTHH:MM:SSZ UTC. Runs AFTER the measurement clock stops.
#
# s3p writes object lines to STDOUT; its progress/heartbeat and final-stats lines
# go through art-standard-lib `log` to STDERR, so stdout is clean object data.
# (`--quiet` also silences progress but is not needed for stdout cleanliness.)
#
# Output contracts, per mode ([SRC] source of each is s3p's ls command in
# S3PCliCommands.caf and the listObjectsV2 Contents shape):
#   ls        -> one KEY per line (default onItem prints `item.Key`). Key only;
#                size/etag/mtime/storage_class are not emitted -> `-`.
#   ls-raw    -> one JSON object per line = a listObjectsV2 Contents element:
#                {Key,LastModified,ETag,Size,StorageClass[,Owner]}. Full 5 fields.
#   ls-long   -> "<yyyy-mm-dd HH:MM:ss> <human-size> <Key>": human-rounded size
#                (lossy) and a space-joined line. Key recoverable only when keys
#                contain no spaces; size/etag not exact -> key only, rest `-`.
#   summarize -> aggregate report, NO per-object records; not normalizable to key
#                lines. Emits nothing (verification is N/A for this mode).
#
# NOTE (provenance): these contracts are [SRC]-derived. They could NOT be
# confirmed against a live listing in this phase because s3p cannot make
# anonymous requests and CREDS=none (all modes blocked at auth). The ls-raw JSON
# path is exercised by a synthetic fixture under adapter/fixtures/.
set -euo pipefail
export LC_ALL=C

mode="${1:?usage: normalize.sh <mode> [prefix]}"
# prefix ($2) is accepted for signature compatibility; s3p prints FULL keys in
# every mode (no path-relative output), so it is not needed to reconstruct keys.
: "${2:-}"

case "$mode" in
  ls)
    # Pure key per line -> key + 4 unexposed fields. `awk 'length'` prints only
    # non-empty lines AND exits 0 on empty input (an empty listing must normalize
    # to empty output, not fail under `set -o pipefail`).
    awk 'length { print $0 "\t-\t-\t-\t-" }'
    ;;
  ls-long)
    # "<date> <time> <human-size> <key>" -> key is field 4 onward (space-joined).
    # size is human-rounded (lossy) and etag/mtime absent -> emit key only.
    # LOSSY: a key containing runs of spaces cannot be reconstructed faithfully
    # from this human format — ls-long is not a verification mode; use ls-raw.
    awk 'NF >= 4 { key=$4; for (i=5;i<=NF;i++) key=key" "$i; print key "\t-\t-\t-\t-" }'
    ;;
  ls-raw)
    # JSON-per-line -> TSV. Use raw `-j` output with EXPLICIT tab/newline
    # separators, NOT `@tsv`: @tsv escapes backslash/tab/newline, and backslash
    # is inside s3p's 95-char supported alphabet, so @tsv would corrupt a legal
    # key. `-j` emits .Key byte-for-byte (raw key bytes, no re-encoding).
    # ETag arrives wrapped in literal quotes -> strip. LastModified is ISO8601
    # with millis (….SSSZ) -> canonicalize to …Z (whole second).
    jq -j '
      def z: sub("\\.[0-9]+Z$"; "Z");
      .Key, "\t",
      (.Size|tostring), "\t",
      (.ETag // "-" | gsub("\""; "")), "\t",
      (.LastModified // "-" | if . == "-" then . else z end), "\t",
      (.StorageClass // "-"), "\n"
    '
    ;;
  summarize)
    # No per-object output; nothing to verify against the manifest.
    cat >/dev/null || true
    ;;
  *)
    printf 'normalize.sh: unknown mode: %s\n' "$mode" >&2
    exit 64
    ;;
esac
