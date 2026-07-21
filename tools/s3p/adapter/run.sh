#!/usr/bin/env bash
# tools/s3p/adapter/run.sh <mode> <bucket> <region> [prefix]
#
# Prints the tool argv to execute inside the container, NUL-delimited
# (printf '%s\0' per argument). Nothing else. The harness wrapper
# (harness/smoke-run.sh) owns `docker run`, mounts, auth, and the timeout, and
# APPENDS this argv to the image ENTRYPOINT, which is ["s3p"] (see the image's
# Dockerfile). So argv here starts at the SUBCOMMAND, not the `s3p` binary.
#
# Bucket, region, and prefix are ALWAYS parameters — no bucket name is embedded
# (owner's rule; smoke-run.sh greps this file for the bucket name and refuses if
# it finds one).
#
# CONCURRENCY: every mode pins --list-concurrency 8 to honour this subject's
# CONCURRENCY_CAP=8. s3p's default list-concurrency is 100, which would blow the
# campaign-wide aggregate cap. --max-sockets defaults to match list-concurrency
# for list-only commands (s3p 3.7.x), so 8 also bounds the HTTP socket pool.
# The benchmark phase should SWEEP list-concurrency (see report Open questions);
# 8 is a smoke-scale ceiling, not a recommended production value.
#
# AUTH NOTE: s3p has no --no-sign-request / anonymous / unsigned option (neither
# in v3.6.0 source nor in the smoked 3.7.2 build). Under the wrapper's
# credential-starved anonymous mode every mode below fails at AWS SDK credential
# resolution before issuing a LIST. This is a recorded capability finding, not a
# run.sh defect. See research/report.md § Smoke results.
set -euo pipefail

mode="${1:?usage: run.sh <mode> <bucket> <region> [prefix]}"
bucket="${2:?bucket required}"
region="${3:?region required}"
prefix="${4:-}"

emit() { printf '%s\0' "$@"; }

# Common trailing options shared by every list-only mode.
common=( --bucket "$bucket" --region "$region" --list-concurrency 8 )
[ -n "$prefix" ] && common+=( --prefix "$prefix" )

case "$mode" in
  ls)         emit ls        "${common[@]}" ;;
  ls-long)    emit ls --long "${common[@]}" ;;
  ls-raw)     emit ls --raw  "${common[@]}" ;;
  summarize)  emit summarize "${common[@]}" ;;
  *) printf 'run.sh: unknown mode: %s\n' "$mode" >&2; exit 64 ;;
esac
