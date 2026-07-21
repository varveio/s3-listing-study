#!/usr/bin/env bash
# normalize.sh <mode> [prefix]
#
# Reads a minio/mc `ls` mode's raw stdout on stdin, emits contract-v2 rows:
#   key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class
# one per line, raw key bytes, '-' for any field a mode does not expose.
#
# mtime is YYYY-MM-DDTHH:MM:SSZ UTC. Containers run TZ=UTC, so mc's text output
# stamps "... UTC" and its JSON stamps RFC3339 Z: both are genuinely UTC by
# construction.
#
# prefix ($2) is the scope the run used (the verifier passes it from run.meta).
# mc prints keys RELATIVE to the listed target, so for a scoped run we prepend
# the prefix to reconstruct full keys. At the bucket root ($2 empty) keys are
# already full.
#
# Field exposure by mode:
#   *-json      : key,size,etag,mtime,storage_class  (exact; the fidelity path)
#   recursive   : key,mtime  (size is humanised -> lossy '-'; no etag in text)
#   shallow     : key        (delimiter listing: common-prefix rows only; the
#                 one root object also carries mtime/sc, folders do not)
# Folders (common prefixes) carry a synthetic mtime (mc sets time.Now()) and no
# etag/size -> emitted as '-'.
set -euo pipefail
export LC_ALL=C

mode="${1:?mode required}"
prefix="${2:-}"
prefix="${prefix#/}"

case "$mode" in
  recursive-json|shallow-json|versions-json)
    # Single-line JSONL (observed): one JSON object per line. mc ls prints keys
    # RELATIVE to the target, so prepend the scope prefix to rebuild full keys.
    jq -rj --arg pfx "$prefix" '
      ( $pfx + .key ) as $k
      | ( if .type == "folder" then "-" else (.size | tostring) end ) as $size
      | ( if (.etag // "") == "" then "-" else .etag end ) as $etag
      | ( if .type == "folder" then "-" else .lastModified end ) as $mt
      | ( if (.storageClass // "") == "" then "-" else .storageClass end ) as $sc
      | $k + "\t" + $size + "\t" + $etag + "\t" + $mt + "\t" + $sc + "\n"
    '
    ;;
  find-json)
    # `mc find --json` prints .key as the ALIAS-prefixed absolute path
    # "<alias>/<bucket>/<fullkey>" (NOT scope-relative), and emits NO etag and
    # no storageClass (find does not fetch them). Recover the bucket-relative
    # full key by stripping the first two path segments (<alias>/<bucket>/); do
    # NOT prepend the scope prefix (the printed path is already full).
    jq -rj '
      ( .key | sub("^[^/]*/[^/]*/"; "") ) as $k
      | ( if (.size|type) == "number" then (.size|tostring) else "-" end ) as $size
      | ( if (.etag // "") == "" then "-" else .etag end ) as $etag
      | ( if (.lastModified // "") == "" then "-" else .lastModified end ) as $mt
      | ( if (.storageClass // "") == "" then "-" else .storageClass end ) as $sc
      | $k + "\t" + $size + "\t" + $etag + "\t" + $mt + "\t" + $sc + "\n"
    '
    ;;
  find)
    # `mc find` default output: one line per object, the whole line being the
    # ALIAS-prefixed absolute path. Strip <alias>/<bucket>/ to the full key; no
    # size/etag/mtime/sc exposed in this format.
    sed -E '/^[[:space:]]*$/d; s#^[^/]*/[^/]*/##; s/$/\t-\t-\t-\t-/'
    ;;
  recursive|shallow)
    PFX="$prefix" python3 -c '
import sys, os, re
pfx = os.environ.get("PFX", "")
SC = {"STANDARD","STANDARD_IA","ONEZONE_IA","INTELLIGENT_TIERING",
      "GLACIER","DEEP_ARCHIVE","REDUCED_REDUNDANCY","GLACIER_IR","OUTPOSTS",
      "SNOW","EXPRESS_ONEZONE","FSX_OPENZFS","FSX_ONTAP"}
# NOTE: text mode is inherently LOSSY and its key parse is BEST-EFFORT. The mc
# text columns are space-separated with no quoting, so a key that itself begins
# with a known-storage-class token followed by a space is indistinguishable from
# the SC column, and a size string is humanised (never exact). Use a *-json mode
# for authoritative keys/sizes/etags; this path asserts only key + mtime (+ SC
# when unambiguous). The SC set is kept current with the AWS enum to avoid
# mistaking a real SC column for part of the key.
# mc formats size with %7s and prints it with NO separator after "]" (cmd/ls.go
# String()); a size exactly 7 chars wide (e.g. "1006KiB") gets no leading pad, so
# the gap after "]" can be zero -> \s* not \s+ (else such rows are silently dropped).
pat = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\]\s*(\S+)(.*)$")
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line.strip():
        continue
    m = pat.match(line)
    if not m:
        continue
    mt_raw, _size_human, rest = m.group(1), m.group(2), m.group(3)
    # `rest` is exactly ONE separator space, then optionally "<SC> ", then the
    # key. Consume that single separator space only (NOT lstrip) so a genuine
    # leading space in the key survives (raw-key contract).
    if rest.startswith(" "):
        rest = rest[1:]
    sc = "-"
    sp = rest.find(" ")
    if sp > 0 and rest[:sp] in SC:
        # SC column present: consume "<SC>" + exactly one separator space; any
        # further leading spaces belong to the key and are preserved.
        sc = rest[:sp]
        key = rest[sp + 1:]
    else:
        key = rest
    # mc folder rows (synthetic time.Now) print seconds too; we cannot tell them
    # from real objects in text mode, so text mtime is emitted as-is. Folders in
    # delimiter mode have size 0B and no SC; their key ends with "/".
    key = pfx + key
    if key.endswith("/"):
        # common-prefix (folder) row: mtime is mc-synthetic time.Now(), no SC.
        mtime = "-"
        sc = "-"
    else:
        mtime = mt_raw.replace(" ", "T") + "Z"
    # size humanised -> lossy; etag absent in text -> both "-".
    sys.stdout.write(key + "\t-\t-\t" + mtime + "\t" + sc + "\n")
'
    ;;
  *)
    printf 'normalize.sh: unknown mode: %s\n' "$mode" >&2
    exit 3
    ;;
esac
