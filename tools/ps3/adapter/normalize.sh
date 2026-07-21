#!/usr/bin/env bash
# tools/ps3/adapter/normalize.sh <mode> [prefix]
#
# Reads pS3's raw stdout on stdin, emits one contract-v2 record per object:
#   key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class
# raw key bytes, no re-encoding; "-" for any field this mode does not expose.
#
# pS3's only object-emitting code path (cmd/listObjectsV2.go readObjectsV2,
# [SRC readObjectsV2 @ 9428492]) prints, per object:
#     Object: <LastModified> \t <size> \t <key>\n
# where <LastModified> is a Go time.Time rendered with %v, i.e.
#     2006-01-02 15:04:05.999999999 -0700 MST   ->  e.g. "2020-05-01 12:00:00 +0000 UTC"
# Fields exposed: key, size, mtime. NOT exposed: etag, storage_class -> "-".
# The container runs TZ=UTC and S3 LastModified is UTC, so the printed zone is
# UTC by construction; we canonicalize to YYYY-MM-DDTHH:MM:SSZ.
#
# NOTE (see research/report.md § Smoke results): pS3 cannot make unsigned
# requests and the campaign is CREDS=none, so no live pS3 listing could be
# produced to exercise this adapter end-to-end. The parser below is written
# from [SRC] and validated only against a synthetic fixture
# (receipts/smoke/_adapter/). list-object-versions / head-objects share the
# same readObjectsV2 printer in the shipped binary's --help surface but their
# source is absent from the pinned checkout, so their exact line format is
# unverified; this adapter assumes the list-objects-v2 format for all.
set -euo pipefail
export LC_ALL=C

# shellcheck disable=SC2034  # accepted per the verifier contract; every mode shares one format below
mode="${1:-list}"
# prefix ("$2") is accepted per the verifier contract but unused: pS3 emits full
# keys and cannot scope by prefix, so nothing needs reconstructing.

awk -F'\t' '
  # Only object lines start with the literal "Object: " sentinel.
  /^Object: / {
    # Field 1 is "Object: <datetime>", field 2 is size, field 3.. is key.
    dt = $1
    sub(/^Object: /, "", dt)          # "2020-05-01 12:00:00 +0000 UTC " (may have frac secs)
    size = $2
    gsub(/ /, "", size)               # printf pads " %d " with spaces; size is digits
    # Key may itself contain tabs (S3 keys can). Reassemble fields 3..NF.
    key = $3
    for (i = 4; i <= NF; i++) key = key "\t" $i
    # The printf format is "... \t %s", inserting exactly ONE space before the
    # key. Strip that single delimiter space; a genuine leading space in the key
    # survives (there are always two spaces then, one stripped). NOT lossy for
    # leading spaces. The real gap is embedded NEWLINES in keys (legal in S3):
    # pS3 prints the raw key into a \n-terminated line, so this line-oriented
    # parser would split/drop such a key — untested; see report § 5.
    sub(/^ /, "", key)

    # Canonicalize datetime -> YYYY-MM-DDTHH:MM:SSZ.
    # dt = "<date> <time>[.frac] <zone> <MST>"; take date and time head.
    n = split(dt, p, " ")
    date = p[1]
    tm = p[2]
    sub(/\..*$/, "", tm)              # drop fractional seconds if present
    mtime = date "T" tm "Z"

    printf "%s\t%s\t%s\t%s\t%s\n", key, size, "-", mtime, "-"
  }
'
