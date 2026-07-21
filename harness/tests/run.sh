#!/usr/bin/env bash
# harness/tests/run.sh — a test-fixture run.sh for the harness regression suite.
#
# NOT a subject tool. It drives the pinned aws-cli image as a stand-in "tool" so
# the smoke-run.sh -> verify-listing.sh path can be exercised end to end against
# the real manifest. Prints NUL-delimited argv only; the wrapper owns docker run.
#
#   run.sh <mode> <bucket> <region> [prefix]
#
# The image entrypoint is `aws`, so argv starts at the subcommand.
# Bucket/region/prefix are ALWAYS parameters (owner's rule — no bucket name here).
set -euo pipefail
MODE="${1:?mode}"; BUCKET="${2:?bucket}"; REGION="${3:?region}"; PREFIX="${4:-}"

emit() { printf '%s\0' "$@"; }

case "$MODE" in
  listobjects|badadapter|keysonly)
    set -- s3api list-objects-v2 --bucket "$BUCKET" --region "$REGION" --no-sign-request
    [ -n "$PREFIX" ] && set -- "$@" --prefix "$PREFIX"
    set -- "$@" --query 'Contents[].[Key,Size,ETag,LastModified,StorageClass]' --output text
    emit "$@" ;;
  *) printf 'run.sh: unknown mode %s\n' "$MODE" >&2; exit 3 ;;
esac
