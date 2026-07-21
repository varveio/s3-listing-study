#!/usr/bin/env bash
# tools/s7cmd/adapter/run.sh — emit the s7cmd argv for a smoke mode, NUL-delimited.
#
#   run.sh <mode> <bucket> <region> [prefix]
#
# Prints ONLY the argv to execute inside the container, one element per
# `printf '%s\0'` record. It never runs anything — harness/smoke-run.sh owns
# `docker run`, mounts, auth injection, and the timeout.
#
# IMPORTANT: the argv is APPENDED to the image ENTRYPOINT, which for the
# s7cmd image is `/usr/local/bin/s7cmd` (verified with
# `docker inspect -f '{{json .Config.Entrypoint}}'`). So argv starts at the
# `ls` SUBCOMMAND, not the binary name.
#
# Bucket, region, and prefix are ALWAYS parameters (owner's rule): a hardcoded
# bucket name anywhere here is a defect the wrapper greps for and refuses.
#
# Anonymous access uses s7cmd's own `--target-no-sign-request` (s3ls-rs
# `S3Credentials::NoSign` -> `config_loader.no_credentials()`); `--target-region`
# is always passed because the no-sign path consults no profile for a default.
#
# Concurrency: `--max-parallel-listings 16` on every recursive mode — the
# tool default is 64, which exceeds this subject's CONCURRENCY_CAP=16. Non-
# recursive modes list sequentially regardless (the flag is inert there).
#
# Observability: `-vv --disable-color-tracing` so the tool's own
# `Listing pipeline completed api_calls=N` debug line (its API-call counter)
# lands on stderr, uncolored, without the per-request TRACE spam of `-vvv`.
# Listing output goes to stdout; tracing goes to stderr — the two never mix.
set -euo pipefail
export LC_ALL=C

die() { printf 'run.sh: %s\n' "$*" >&2; exit 1; }

MODE="${1:?usage: run.sh <mode> <bucket> <region> [prefix]}"
BUCKET="${2:?bucket required}"
REGION="${3:?region required}"
PREFIX="${4:-}"

TARGET="s3://${BUCKET}/${PREFIX}"

# Common flags shared by every listing invocation.
COMMON_ANON=( --target-no-sign-request --target-region "$REGION" )
OBS=( -vv --disable-color-tracing )
PAR=( --max-parallel-listings 16 )

argv=()
case "$MODE" in
  recursive-tsv)
    argv=( ls -r "${OBS[@]}" --tsv --show-storage-class --show-etag "${PAR[@]}" "${COMMON_ANON[@]}" "$TARGET" ) ;;
  recursive-tsv-nosort)
    argv=( ls -r "${OBS[@]}" --no-sort --tsv --show-storage-class --show-etag "${PAR[@]}" "${COMMON_ANON[@]}" "$TARGET" ) ;;
  recursive-aligned)
    argv=( ls -r "${OBS[@]}" "${PAR[@]}" "${COMMON_ANON[@]}" "$TARGET" ) ;;
  recursive-json)
    argv=( ls -r "${OBS[@]}" --json "${PAR[@]}" "${COMMON_ANON[@]}" "$TARGET" ) ;;
  recursive-one)
    argv=( ls -r "${OBS[@]}" -1 "${PAR[@]}" "${COMMON_ANON[@]}" "$TARGET" ) ;;
  all-versions)
    argv=( ls -r "${OBS[@]}" --all-versions --tsv --show-storage-class --show-etag "${PAR[@]}" "${COMMON_ANON[@]}" "$TARGET" ) ;;
  max-depth)
    # Recursive but bounded to depth 1: exercises the PARALLEL engine's
    # depth-limit + boundary CommonPrefix synthesis (mod.rs:682-699) that the
    # sequential shallow path never runs. At the bucket root, --max-depth 1
    # emits depth-1 objects + the top-level prefixes as PRE — the same output
    # contract as a delimiter-at-root listing, so it is cleanly verifiable
    # against --scope delimiter --scope-delimiter /.
    argv=( ls -r "${OBS[@]}" --max-depth 1 --tsv --show-storage-class --show-etag "${PAR[@]}" "${COMMON_ANON[@]}" "$TARGET" ) ;;
  shallow-tsv)
    # Non-recursive: s3ls uses delimiter="/" automatically, returning objects
    # at this level plus CommonPrefixes (PRE). Sequential by construction.
    argv=( ls "${OBS[@]}" --tsv --show-storage-class --show-etag "${COMMON_ANON[@]}" "$TARGET" ) ;;
  bucket-list)
    # No target -> ListBuckets (ListAllMyBuckets). Capability probe only:
    # anonymous cannot enumerate buckets, so this is expected to fail.
    argv=( ls "${OBS[@]}" "${COMMON_ANON[@]}" ) ;;
  *)
    die "unknown mode: $MODE" ;;
esac

for a in "${argv[@]}"; do
  printf '%s\0' "$a"
done
