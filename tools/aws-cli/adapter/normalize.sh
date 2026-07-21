#!/usr/bin/env bash
# tools/aws-cli/adapter/normalize.sh <mode> [prefix]
#
# Reads one aws-cli listing mode's RAW output on stdin and emits the contract-v2
# 5-field record per line on stdout:
#
#   key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class
#
#   * raw key bytes, no re-encoding
#   * `-` for any field the mode does not expose (verifier asserts by policy)
#   * mtime canonicalised to YYYY-MM-DDTHH:MM:SSZ (UTC). Containers run TZ=UTC,
#     so any mode that prints a timezone-less local time is printing UTC by
#     construction; we stamp the explicit `Z`.
#   * etag emitted UNQUOTED (S3 returns it wrapped in literal double quotes).
#
# `prefix` ($2) is the scope the run used (the verifier passes it from run.meta).
# aws-cli's s3api commands and `s3 ls --recursive` print FULL keys, so they
# ignore it; only the non-recursive `s3 ls` (delimiter) path prints
# path-relative names and needs it to reconstruct full keys.
#
# Adapter runs on the HOST, AFTER the wrapper's clock stops (measurement
# boundary): jq / python are fair game here, never inside a timed window.
set -euo pipefail
export LC_ALL=C

MODE="${1:?mode required}"
PREFIX="${2:-}"

# ---- shared post-processor: canonicalise mtime `+00:00`/`+0000` -> `Z` on the
#      4th TAB field, pass everything else through untouched.
mtime_z() {
  awk -F'\t' 'BEGIN{OFS="\t"} { sub(/\+00:00$/,"Z",$4); sub(/\+0000$/,"Z",$4); print }'
}

case "$MODE" in
  # ---- s3api text family: `--query Contents[]/Versions[].[Key,Size,ETag,
  #      LastModified,StorageClass] --output text`. Rows are already 5 TAB
  #      fields; strip the literal quotes botocore leaves around the ETag.
  s3api-v2-text|s3api-v1-text|s3api-versions-text|s3api-v2-remainder)
    awk -F'\t' 'BEGIN{OFS="\t"} NF==0 || $0=="" {next}
                { gsub(/"/,"",$3); print $1,$2,$3,$4,$5 }' | mtime_z
    ;;

  # ---- s3api JSON (recursive, buffered FullyBufferedFormatter). One merged
  #      Contents array across pages. StorageClass may be absent -> `-`.
  s3api-v2-json)
    jq -r '.Contents[]? | [ .Key, (.Size|tostring),
                            (.ETag|gsub("\"";"")), .LastModified,
                            (.StorageClass // "-") ] | @tsv' | mtime_z
    ;;

  # ---- s3api JSON with --delimiter /: emit CommonPrefixes as `-`-field rows
  #      plus the root-level Contents keys.
  s3api-v2-delimiter)
    jq -r '( .CommonPrefixes[]?.Prefix | [ ., "-","-","-","-" ] | @tsv ),
           ( .Contents[]? | [ .Key, (.Size|tostring),
                              (.ETag|gsub("\"";"")), .LastModified,
                              (.StorageClass // "-") ] | @tsv )' | mtime_z
    ;;

  # ---- s3api yaml-stream (StreamedYAMLFormatter): one YAML document per page,
  #      streamed. pyyaml parses the multi-doc stream; LastModified may parse as
  #      a datetime or a string depending on serialisation.
  s3api-v2-yamlstream)
    # NOTE: program via `-c` (NOT a `<<'PY'` heredoc) — the heredoc would BE
    # stdin, leaving no stdin for the listing data the adapter must read.
    python3 -c '
import sys, yaml, datetime
def z(v):
    if v is None: return "-"
    if isinstance(v, datetime.datetime):
        return v.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(v).replace("+00:00","Z").replace("+0000","Z")
def pages(doc):
    # yaml-stream serialises the paginated result as a LIST of per-page dicts
    # (tolerate a bare dict too).
    if isinstance(doc, list):
        for p in doc:
            if isinstance(p, dict): yield p
    elif isinstance(doc, dict):
        yield doc
for doc in yaml.safe_load_all(sys.stdin):
    if not doc: continue
    for page in pages(doc):
        for o in (page.get("Contents") or []):
            etag=(o.get("ETag") or "-").replace(chr(34),"")
            print("\t".join([o.get("Key",""), str(o.get("Size","-")),
                              etag, z(o.get("LastModified")),
                              o.get("StorageClass") or "-"]))
'
    ;;

  # ---- s3 ls --recursive: fixed text `YYYY-MM-DD HH:MM:SS <size> <key>`.
  #      Exposes key,size,mtime; no etag/storage_class. Full keys (basename off).
  s3-ls-recursive)
    awk 'BEGIN{OFS="\t"} NF==0 {next}
         { key=$4; for(i=5;i<=NF;i++) key=key" "$i;
           print key, $3, "-", $1"T"$2"Z", "-" }'
    ;;

  # ---- s3 ls (non-recursive, delimiter): `PRE <name>/` rollups + keys.
  #      upstream `_display_page` prints only the LAST path component
  #      (`Key.split('/')[-1]` for objects, `Prefix.split('/')[-2]` for common
  #      prefixes) [SRC subcommands.py:865-889]. The full key is therefore the
  #      DIRECTORY portion of the run prefix (up to and including its last '/',
  #      empty if the prefix has none) + the printed name — NOT the whole prefix:
  #      prepending a non-'/'-terminated prefix `foo` to key `foobar.txt` would
  #      wrongly yield `foofoobar.txt`. (Root run: prefix empty -> dir empty.)
  s3-ls-delimiter)
    case "$PREFIX" in */*) dir="${PREFIX%/*}/";; *) dir="";; esac
    awk -v dir="$dir" 'BEGIN{OFS="\t"} NF==0 {next}
         $1=="PRE" { name=$2; for(i=3;i<=NF;i++) name=name" "$i;
                     print dir name, "-","-","-","-"; next }
         { name=$4; for(i=5;i<=NF;i++) name=name" "$i;
           print dir name, $3, "-", $1"T"$2"Z", "-" }'
    ;;

  *)
    printf 'normalize.sh: unknown mode: %s\n' "$MODE" >&2
    exit 2
    ;;
esac
