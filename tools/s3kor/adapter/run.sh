#!/usr/bin/env bash
# tools/s3kor/adapter/run.sh <mode> <bucket> <region> [prefix]
#
# Prints the argv to run INSIDE the s3kor container, NUL-delimited, and nothing
# else. The wrapper (harness/smoke-run.sh) owns docker run, mounts, auth, and
# the timeout, and APPENDS this argv to the image ENTRYPOINT.
#
# Image ENTRYPOINT is ["/usr/local/bin/s3kor"] (verified with
#   docker inspect -f '{{json .Config.Entrypoint}}' <image>)
# so the argv here starts at the SUBCOMMAND, not the binary name.
#
# Bucket, region, and prefix are ALWAYS parameters — a hardcoded bucket name
# anywhere here is a defect the wrapper's scan gate rejects.
#
# s3kor listing surface (v0.0.37): a single `ls` subcommand with one listing
# flag, --all-versions. There is no delimiter/shallow flag and no output-format
# flag, so s3kor exposes exactly two listing MODES:
#   list           ListObjectsV2, recursive, key-only output
#   list-versions  ListObjectVersions (--all-versions), "<versionId> <key>" output
#
# s3kor has NO anonymous/unsigned request mechanism (no --no-sign-request
# equivalent; see ../research/report.md). Every mode issues SIGNED requests and
# fails under credential starvation. run.sh still emits correct argv so the
# capability failure is demonstrated by a real run, not asserted.
set -euo pipefail

mode="${1:?usage: run.sh <mode> <bucket> <region> [prefix]}"
bucket="${2:?bucket required}"
region="${3:?region required}"
prefix="${4:-}"

# Build the S3 URI. Empty prefix → full-bucket root (no Prefix sent).
if [ -n "$prefix" ]; then
  uri="s3://${bucket}/${prefix}"
else
  uri="s3://${bucket}"
fi

emit() { printf '%s\0' "$@"; }

case "$mode" in
  list)
    emit ls --region "$region" "$uri"
    ;;
  list-versions)
    emit ls --all-versions --region "$region" "$uri"
    ;;
  *)
    printf 'run.sh: unknown mode: %s\n' "$mode" >&2
    exit 2
    ;;
esac
