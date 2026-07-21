#!/usr/bin/env bash
# tools/aws-cli/adapter/run.sh <mode> <bucket> <region> [prefix]
#
# Prints the aws-cli argv to run INSIDE the container, NUL-delimited (one
# `printf '%s\0'` per argument) — nothing else. The shared wrapper
# (harness/smoke-run.sh) owns `docker run`, mounts, auth injection, network,
# and the timeout; this script never executes anything.
#
# The image ENTRYPOINT is `/usr/local/bin/aws` (verified via
# `docker inspect -f '{{json .Config.Entrypoint}}'`), so the argv starts at the
# SUBCOMMAND (`s3api` / `s3`), NOT the `aws` binary name.
#
# bucket / region / prefix are ALWAYS parameters — no bucket name is ever
# embedded here (owner's rule; the wrapper greps this file for the bucket name
# and refuses to run if it finds it).
#
# Anonymous access is `--no-sign-request` (global arg; sets botocore
# signature_version=UNSIGNED, applies to both `s3` and `s3api`).
set -euo pipefail
export LC_ALL=C

MODE="${1:?mode required}"
BUCKET="${2:?bucket required}"
REGION="${3:?region required}"
PREFIX="${4:-}"

emit() { for a in "$@"; do printf '%s\0' "$a"; done; }

# 5-field projection shared by every s3api text mode.
Q_CONTENTS='Contents[].[Key,Size,ETag,LastModified,StorageClass]'
Q_VERSIONS='Versions[].[Key,Size,ETag,LastModified,StorageClass]'

case "$MODE" in
  s3api-v2-text)
    set -- s3api list-objects-v2 --bucket "$BUCKET" --region "$REGION" --no-sign-request
    [ -n "$PREFIX" ] && set -- "$@" --prefix "$PREFIX"
    emit "$@" --query "$Q_CONTENTS" --output text
    ;;

  s3api-v2-json)
    set -- s3api list-objects-v2 --bucket "$BUCKET" --region "$REGION" --no-sign-request
    [ -n "$PREFIX" ] && set -- "$@" --prefix "$PREFIX"
    emit "$@" --output json
    ;;

  s3api-v2-yamlstream)
    set -- s3api list-objects-v2 --bucket "$BUCKET" --region "$REGION" --no-sign-request
    [ -n "$PREFIX" ] && set -- "$@" --prefix "$PREFIX"
    emit "$@" --output yaml-stream
    ;;

  s3api-v1-text)
    set -- s3api list-objects --bucket "$BUCKET" --region "$REGION" --no-sign-request
    [ -n "$PREFIX" ] && set -- "$@" --prefix "$PREFIX"
    emit "$@" --query "$Q_CONTENTS" --output text
    ;;

  s3api-versions-text)
    set -- s3api list-object-versions --bucket "$BUCKET" --region "$REGION" --no-sign-request
    [ -n "$PREFIX" ] && set -- "$@" --prefix "$PREFIX"
    emit "$@" --query "$Q_VERSIONS" --output text
    ;;

  # s3api list-objects-v2 --delimiter / (JSON: CommonPrefixes + root Contents)
  s3api-v2-delimiter)
    set -- s3api list-objects-v2 --bucket "$BUCKET" --region "$REGION" --no-sign-request --delimiter /
    [ -n "$PREFIX" ] && set -- "$@" --prefix "$PREFIX"
    emit "$@" --output json
    ;;

  # Fan-out remainder: delimiter-/ root run projecting ONLY Contents, so it
  # returns EXACTLY the unprefixed root keys (no CommonPrefixes) — the union
  # verifier's remainder must contain exactly the orphan keys.
  s3api-v2-remainder)
    emit s3api list-objects-v2 --bucket "$BUCKET" --region "$REGION" --no-sign-request \
         --delimiter / --query "$Q_CONTENTS" --output text
    ;;

  s3-ls-recursive)
    if [ -n "$PREFIX" ]; then TARGET="s3://$BUCKET/$PREFIX"; else TARGET="s3://$BUCKET/"; fi
    emit s3 ls "$TARGET" --recursive --region "$REGION" --no-sign-request
    ;;

  s3-ls-delimiter)
    if [ -n "$PREFIX" ]; then TARGET="s3://$BUCKET/$PREFIX"; else TARGET="s3://$BUCKET/"; fi
    emit s3 ls "$TARGET" --region "$REGION" --no-sign-request
    ;;

  *)
    printf 'run.sh: unknown mode: %s\n' "$MODE" >&2
    exit 2
    ;;
esac
