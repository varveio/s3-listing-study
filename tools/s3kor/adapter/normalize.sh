#!/usr/bin/env bash
# tools/s3kor/adapter/normalize.sh <mode> [prefix]
#
# Reads s3kor's raw stdout for <mode> on stdin, emits one canonical record per
# object on stdout:
#   key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class
# Raw key bytes, no re-encoding. "-" for any field the mode does not expose.
#
# The verifier runs this AFTER the measurement clock stops (adapter cost is the
# study's cost, never the tool's) and checks only the fields a mode exposes.
#
# s3kor's `ls` output contract (v0.0.37, source list.go printAllObjects):
#   list           one line per object: the FULL object key, nothing else.
#                  → key exposed; size/etag/mtime/storage_class NOT exposed.
#   list-versions  one line per version: "<versionId> <key>" (single space).
#                  Keys may contain spaces, so we strip only the first token.
#                  → key exposed; version id is dropped (not a contract field);
#                    size/etag/mtime/storage_class NOT exposed.
#                  CAVEAT: ListObjectVersions returns every version AND every
#                  delete marker, so dropping the version id makes this mode
#                  comparable to a current-object manifest ONLY on an
#                  UNVERSIONED bucket (one version/key, no markers). On a
#                  versioned bucket it legitimately emits duplicate/marker keys
#                  the current-object manifest lacks — a property of the mode,
#                  not a tool fault. See ../research/report.md § 5.
#
# s3kor prints full keys (not path-relative), so [prefix] is unused for key
# reconstruction; it is accepted for interface compatibility with the verifier.
set -euo pipefail
export LC_ALL=C

mode="${1:?usage: normalize.sh <mode> [prefix]}"
# prefix ("${2:-}") intentionally unused: s3kor emits absolute keys.

case "$mode" in
  list)
    # Each line is a whole key. Preserve it verbatim; skip blank lines.
    awk 'length($0) > 0 { printf "%s\t-\t-\t-\t-\n", $0 }'
    ;;
  list-versions)
    # "<versionId> <key>": drop the first space-delimited token (version id),
    # keep the remainder verbatim as the key (keys may contain spaces).
    awk '
      length($0) > 0 {
        sp = index($0, " ")
        if (sp == 0) next          # malformed line without a space: skip
        key = substr($0, sp + 1)
        printf "%s\t-\t-\t-\t-\n", key
      }'
    ;;
  *)
    printf 'normalize.sh: unknown mode: %s\n' "$mode" >&2
    exit 2
    ;;
esac
