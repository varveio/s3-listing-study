#!/usr/bin/env bash
# tools/s5cmd/adapter/run.sh <mode> <bucket> <region> [prefix]
#
# Prints the s5cmd argv to run inside the container, NUL-delimited (one
# `printf '%s\0'` per argument), and nothing else. The wrapper (harness/
# smoke-run.sh) owns `docker run`, mounts, auth injection, and the timeout, and
# APPENDS this argv to the image ENTRYPOINT, which for peakcom/s5cmd is
# ["/s5cmd"] (verified: docker inspect -f '{{json .Config.Entrypoint}}'). So the
# argv here starts at the s5cmd GLOBAL FLAGS / SUBCOMMAND, never at the binary.
#
# Bucket, region and prefix are ALWAYS parameters (owner's rule: no executable
# artifact embeds a bucket name). NOTE: s5cmd `ls`/`du` have NO region flag —
# region is auto-detected server-side (default us-east-1). `region` is accepted
# for signature uniformity and is intentionally not emitted into argv.
#
# Anonymous access is the global `--no-sign-request` flag (Stage A: storage/
# s3.go:1242 wires it to credentials.AnonymousCredentials). Every mode here is
# unsigned.
set -euo pipefail
export LC_ALL=C

mode="${1:?mode required}"
bucket="${2:?bucket required}"
region="${3:?region required}"   # accepted, not emitted (ls has no region flag)
prefix="${4:-}"
: "$region"

emit() { local a; for a in "$@"; do printf '%s\0' "$a"; done; }

case "$mode" in
  recursive)
    # Full recursive listing (ListObjectsV2). Glob => Delimiter="" (recursive)
    # + client-side filter. `-e -s` expose ETag and storage class so the
    # verifier can assert all five contract fields.
    emit --no-sign-request ls -e -s "s3://${bucket}/${prefix}*" ;;
  delimiter)
    # Shallow/delimiter listing (Delimiter="/"): CommonPrefixes as DIR entries
    # + direct keys. No glob in the URL. prefix="" => bucket root.
    emit --no-sign-request ls -e -s "s3://${bucket}/${prefix}" ;;
  rootkeys)
    # Remainder shard for the fan-out --scope union: a root delimiter listing
    # (identical request to `delimiter`) whose adapter (normalize.sh rootkeys)
    # keeps only the unprefixed OBJECT keys and drops the DIR common-prefixes,
    # so the union's unprefixed-remainder contract is satisfied exactly. run.meta
    # prefix MUST be empty (the remainder is the unprefixed complement).
    emit --no-sign-request ls -e -s "s3://${bucket}/${prefix}" ;;
  json)
    # Same request as `recursive` (ListObjectsV2, recursive) but JSON output
    # contract via the global --json flag.
    emit --json --no-sign-request ls "s3://${bucket}/${prefix}*" ;;
  listv1)
    # Legacy ListObjects (v1) API via --use-list-objects-v1. Recursive.
    emit --no-sign-request --use-list-objects-v1 ls -e -s "s3://${bucket}/${prefix}*" ;;
  allversions)
    # ListObjectVersions API via --all-versions. Recursive.
    emit --no-sign-request ls --all-versions -e -s "s3://${bucket}/${prefix}*" ;;
  fullpath)
    # Same ListObjectsV2 request as `recursive`, but --show-fullpath changes the
    # output contract to absolute-path-only (no size/etag/mtime/storage-class).
    emit --no-sign-request ls --show-fullpath "s3://${bucket}/${prefix}*" ;;
  *)
    printf 'run.sh: unknown mode: %s\n' "$mode" >&2; exit 2 ;;
esac
