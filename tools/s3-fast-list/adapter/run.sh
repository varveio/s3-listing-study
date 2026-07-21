#!/usr/bin/env bash
# tools/s3-fast-list/adapter/run.sh — prints the tool argv (NUL-delimited) for one mode.
#
# CONTRACT (harness/README.md § Contract):
#   - This script NEVER runs the tool. It prints argv only, NUL-delimited
#     (printf '%s\0' per argument), and the wrapper appends it to the image
#     ENTRYPOINT and owns `docker run`, mounts, auth injection, and timeout.
#   - Bucket, region, and prefix are ALWAYS parameters. A hardcoded bucket name
#     anywhere here is a defect the wrapper actively greps for and refuses.
#
# IMAGE ENTRYPOINT: the groundwork image (distroless/cc-debian12 runtime) sets
# no ENTRYPOINT, so argv must start with the binary path /usr/bin/s3-fast-list.
#
# OUTPUT ROUTING (the load-bearing trick): s3-fast-list writes its object
# listing to a PARQUET FILE, never to stdout. The harness captures only
# stdout/stderr (docker logs) and mounts nothing, so the only way to get the
# listing out is --output-parquet-file /dev/stdout, which streams the parquet
# bytes to stdout (env_logger writes its info/heartbeat lines to stderr, so
# stdout stays pure parquet). normalize.sh parses that parquet. The keyspace
# CSV (.ks) is redirected to /dev/null (we don't consume it; default would write
# it to the container CWD "/").
#
# Usage: run.sh <mode> <bucket> <region> [prefix]
set -euo pipefail

MODE="${1:?mode required}"
BUCKET="${2:?bucket required}"
REGION="${3:?region required}"
PREFIX="${4:-}"

BIN=/usr/bin/s3-fast-list

emit() { printf '%s\0' "$@"; }

case "$MODE" in
  list)
    # Plain recursive listing. Without a ks-hints file the tool builds exactly
    # ONE keyspace pair, so listing is a single serial ListObjectsV2 pagination
    # regardless of --concurrency (the flag only fans out when hints exist).
    # Global flags (--no-sign-request, --prefix, output paths) precede the
    # subcommand; --region/--bucket are subcommand args of `list`.
    set -- "$BIN" --no-sign-request \
      --output-parquet-file /dev/stdout \
      --output-ks-file /dev/null
    if [ -n "$PREFIX" ]; then
      set -- "$@" --prefix "$PREFIX"
    fi
    set -- "$@" list --region "$REGION" --bucket "$BUCKET"
    emit "$@"
    ;;
  *)
    printf 'run.sh: unknown mode: %s\n' "$MODE" >&2
    exit 3
    ;;
esac
