#!/usr/bin/env bash
# tools/swath/adapter/run.sh — prints the swath argv (NUL-delimited) for one smoke mode.
#
# CONTRACT (harness/README.md):
#   - Prints ONLY the argv, NUL-delimited (printf '%s\0' per arg). Never executes.
#   - The wrapper APPENDS this argv to the image ENTRYPOINT, which for the swath
#     image is ["java","-jar","/opt/swath/swath.jar"] — so argv starts at the
#     TOP-LEVEL options / subcommand, NOT the binary name.
#   - Bucket, region, prefix are ALWAYS parameters. A hardcoded bucket name here
#     is a defect (owner's rule; the wrapper greps for it and refuses to run).
#
# Usage: run.sh <mode> <bucket> <region> [prefix]
#
# Concurrency: every mode caps --max-parallel-listings at 8 (subject card
# CONCURRENCY_CAP=8). swath's default is 64; leaving it default would blow the
# campaign concurrency budget.
set -euo pipefail

MODE="${1:?mode required}"
BUCKET="${2:?bucket required}"
REGION="${3:?region required}"
PREFIX="${4:-}"

# Build the s3:// URI. swath takes the prefix scope in the URI path, not a flag.
if [ -n "$PREFIX" ]; then
  URI="s3://${BUCKET}/${PREFIX}"
else
  URI="s3://${BUCKET}"
fi

# Common args shared by every listing mode:
#   -v                          INFO logs → the list_run_summary line (api calls,
#                               strategy, peak_rss) lands on stderr for [RUN] evidence.
#                               (-v is a TOP-LEVEL option and must precede `list`.)
#   list <uri>                  the only listing subcommand.
#   --region                    explicit region (parameter; overrides SDK chain).
#   --no-sign-request           anonymous / unsigned (public bucket).
#   --checkpoint none           ephemeral in-memory store: same WorkStealingScan
#                               engine, no on-disk .swath-checkpoint/ file, not
#                               resumable — the clean choice for a one-shot smoke.
#   --max-parallel-listings 8   cap concurrency at the subject-card CONCURRENCY_CAP.
common_tail=( list "$URI"
  --region "$REGION"
  --no-sign-request
  --checkpoint none
  --max-parallel-listings 8 )

emit() { for a in "$@"; do printf '%s\0' "$a"; done; }

case "$MODE" in
  recursive-tsv)
    emit -v "${common_tail[@]}" --format tsv ;;
  recursive-jsonl)
    emit -v "${common_tail[@]}" --format jsonl ;;
  recursive-aligned)
    emit -v "${common_tail[@]}" --format aligned ;;
  seed-none)
    # Request-pattern variant: no up-front delimiter=/ seed probe; a single root
    # range that the work-stealing engine subdivides by demand-driven stealing.
    emit -v "${common_tail[@]}" --format tsv --seed none ;;
  parquet-probe)
    # CAPABILITY PROBE ONLY (receipts/smoke/_capability). The parquet sink writes
    # a dataset directory (-o), never stdout; the stdout-only smoke wrapper mounts
    # no volume, so this output is NOT capturable/verifiable here — the probe only
    # proves the file-sink path executes. Output goes to a container-internal dir
    # discarded with the container. NOTE: cannot reuse `--checkpoint none` common
    # tail (parquet still fine with none, but keep the probe self-describing).
    emit -v list "s3://${BUCKET}${PREFIX:+/$PREFIX}" --region "$REGION" \
      --no-sign-request --checkpoint none --max-parallel-listings 8 \
      --format parquet -o /tmp/swout ;;
  sort-probe)
    # CAPABILITY PROBE ONLY. Globally-sorted parquet (a distinct output contract).
    # --sort requires a checkpoint (refused with --checkpoint none), so this uses
    # --checkpoint auto (container-internal). Output uncapturable as above.
    emit -v list "s3://${BUCKET}${PREFIX:+/$PREFIX}" --region "$REGION" \
      --no-sign-request --checkpoint auto --max-parallel-listings 8 \
      --format parquet --sort --force-sort -o /tmp/swout ;;
  *)
    printf 'run.sh: unknown mode: %s\n' "$MODE" >&2
    exit 2 ;;
esac
