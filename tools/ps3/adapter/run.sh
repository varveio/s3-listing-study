#!/usr/bin/env bash
# tools/ps3/adapter/run.sh <mode> <bucket> <region> [prefix]
#
# Prints the tool argv to run INSIDE the container, NUL-delimited, and nothing
# else. A derived image's shared Python attempt runner owns subject execution,
# capture, auth starvation, and timeout. The fixed command prefix is the pS3 binary, so argv starts at
# the SUBCOMMAND, not the binary name.
#
# Bucket, region, and prefix are always parameters — never hardcoded.
#
# Capability note (see research/report.md): besides the persistent root flags
# (--region, --output, --endpoint-url, --profile, --no-verify-ssl, --verbose),
# pS3's listing subcommands add only --bucket and --prefix-count. There is NO
# --prefix flag and no delimiter flag, so the tool cannot scope a listing to a
# key prefix at all. A prefix
# argument therefore cannot be honoured; rather than silently list the whole
# bucket under a "scoped" label (which would verify against the wrong expected
# set), we refuse it.
set -euo pipefail

mode="${1:?mode required}"
bucket="${2:?bucket required}"
region="${3:?region required}"
prefix="${4:-}"

if [ -n "$prefix" ]; then
  echo "run.sh: pS3 has no --prefix flag; mode '$mode' cannot address prefix '$prefix'" >&2
  exit 3
fi

case "$mode" in
  list)          set -- list-objects-v2      --bucket "$bucket" --region "$region" ;;
  list-versions) set -- list-object-versions --bucket "$bucket" --region "$region" ;;
  head)          set -- head-objects         --bucket "$bucket" --region "$region" ;;
  *) echo "run.sh: unknown mode '$mode'" >&2; exit 3 ;;
esac

for a in "$@"; do printf '%s\0' "$a"; done
