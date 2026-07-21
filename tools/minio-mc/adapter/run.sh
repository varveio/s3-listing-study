#!/usr/bin/env bash
# run.sh <mode> <bucket> <region> [prefix]
#
# Prints the argv to execute inside the minio/mc container, NUL-delimited.
# It prints ONLY argv; the harness wrapper (smoke-run.sh) owns `docker run`,
# the anonymous MC_HOST alias injection (via --env), timeout, and measurement.
#
# The image ENTRYPOINT is ["mc"] (verified: docker inspect), so argv starts at
# the global flags / subcommand, NOT at "mc".
#
# Anonymous access: mc has no --no-sign-request flag. It resolves credentials
# from an alias. The wrapper is invoked with
#   --env MC_HOST_s3=https://s3.amazonaws.com
# which defines an ad-hoc alias "s3" carrying NO embedded credentials; minio-go
# then resolves SignatureAnonymous and issues unsigned requests. The alias NAME
# ("s3") is not a bucket name; the bucket is always the $2 parameter.
#
# Region ($3) is accepted for interface parity but mc/minio-go auto-resolve the
# region for s3.amazonaws.com; it is not placed on the mc argv (mc ls has no
# region flag). Left unused deliberately.
set -euo pipefail

mode="${1:?mode required}"
bucket="${2:?bucket required}"
# shellcheck disable=SC2034  # accepted per the runner contract, intentionally unused on the mc argv
region="${3:-}"
prefix="${4:-}"      # optional scope; if set, expected to end with '/'

# Build the mc target: alias/bucket[/prefix], always with a trailing slash so mc
# lists container contents rather than stat-ing a single object.
target="s3/${bucket}/"
if [ -n "$prefix" ]; then
  # strip any accidental leading slash on the prefix
  prefix="${prefix#/}"
  target="s3/${bucket}/${prefix}"
fi

emit() { printf '%s\0' "$@"; }

case "$mode" in
  recursive)       emit ls --recursive "$target" ;;
  recursive-json)  emit --json ls --recursive "$target" ;;
  shallow)         emit ls "$target" ;;
  shallow-json)    emit --json ls "$target" ;;
  versions-json)   emit --json ls --versions --recursive "$target" ;;
  find)            emit find "$target" ;;
  find-json)       emit --json find "$target" ;;
  *) printf 'run.sh: unknown mode: %s\n' "$mode" >&2; exit 3 ;;
esac
