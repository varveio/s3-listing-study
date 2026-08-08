#!/usr/bin/env bash
# tools/swath/adapter/run.sh — prints the swath argv (NUL-delimited) for one mode.
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
# SUBJECT: swath v0.2.0 (commit cef8ec2). The spellings emitted below are the
# v0.2.0 ones — --concurrency N, --tune seed.mode=none,
# --tune sort.ignore-disk-check=on, --format table — verified against
# `list --help` on the pinned image; see claim `mode-inventory-v020`.
#
# Every adapter mode passes the study setting `--concurrency 8`.
#
# Region is mandatory even anonymously: --no-sign-request does not affect region
# resolution, and a credential-starved container with no AWS_REGION exits 2
# before issuing a request (claim `region-required-even-anonymously`).
set -euo pipefail

MODE="${1:?mode required}"
BUCKET="${2:?bucket required}"
REGION="${3:?region required}"
PREFIX="${4:-}"

# swath takes the prefix scope in the URI path, not a flag. There is no
# --prefix, and no --delimiter/--recursive: `list` is unconditionally recursive
# (claim `no-shallow-listing-mode`).
if [ -n "$PREFIX" ]; then
  URI="s3://${BUCKET}/${PREFIX}"
else
  URI="s3://${BUCKET}"
fi

# Shared by every mode:
#   -v                  INFO logs, so the list_run_summary line (api_calls,
#                       strategy) reaches stderr. -v is a TOP-LEVEL option and
#                       must precede `list`.
#   --checkpoint none   ephemeral in-memory SQLite: same work-stealing engine,
#                       nothing durable on disk, not resumable — the clean
#                       choice for a one-shot run. Note this still loads
#                       sqlite-jdbc (claim `only-parquet-directory-is-resumable`).
common_tail=( list "$URI"
  --region "$REGION"
  --no-sign-request
  --checkpoint none
  --concurrency 8 )

emit() { for a in "$@"; do printf '%s\0' "$a"; done; }

case "$MODE" in
  recursive-tsv)
    emit -v "${common_tail[@]}" --format tsv ;;
  recursive-jsonl)
    emit -v "${common_tail[@]}" --format jsonl ;;
  recursive-table)
    # v0.2.0 name for what v0.1.0 called `aligned`. Exposes no etag and no
    # storage_class, so the normalizer emits `-` for both.
    emit -v "${common_tail[@]}" --format table ;;
  seed-none)
    # Request-pattern variant: no up-front delimiter=/ seed probe, a single root
    # range subdivided purely by demand-driven stealing. The only *supported*
    # control over whether swath issues delimiter=/ requests at all.
    emit -v "${common_tail[@]}" --format tsv --tune seed.mode=none ;;
  parquet-probe)
    # CAPABILITY PROBE ONLY. The parquet sink writes a dataset directory (-o) and
    # refuses stdout; the wrapper mounts no volume, so the output is NOT
    # capturable or verifiable here (claim `file-sinks-not-harness-capturable`).
    # The probe only shows the file-sink path executes; the dataset dies with the
    # container.
    emit -v list "$URI" --region "$REGION" \
      --no-sign-request --checkpoint none --concurrency 8 \
      --format parquet -o /tmp/swout ;;
  sort-probe)
    # CAPABILITY PROBE ONLY. Globally-sorted parquet is a distinct output
    # contract. --sort requires a checkpoint (refused under --checkpoint none)
    # and a directory dataset, so this uses --checkpoint auto, container-internal.
    # Output uncapturable as above.
    emit -v list "$URI" --region "$REGION" \
      --no-sign-request --checkpoint auto --concurrency 8 \
      --format parquet --sort --tune sort.ignore-disk-check=on -o /tmp/swout ;;
  *)
    printf 'run.sh: unknown mode: %s\n' "$MODE" >&2
    exit 2 ;;
esac
