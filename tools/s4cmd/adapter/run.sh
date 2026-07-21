#!/usr/bin/env bash
# tools/s4cmd/adapter/run.sh <mode> <bucket> <region> [prefix]
#
# Prints the s4cmd argv for a listing mode, NUL-delimited, and nothing else.
# The harness wrapper (smoke-run.sh) owns `docker run`, mounts, auth, timeout,
# and appends this argv to the image ENTRYPOINT, which is ["s4cmd"] — so the
# argv here starts at the SUBCOMMAND, not the binary name.
#
# Bucket / region / prefix are always parameters (owner's rule: no executable
# artifact embeds a bucket name).
#
# Region note: s4cmd 2.1.0 exposes no region flag — the boto3 client resolves
# region itself (S3 default us-east-1, plus bucket-region redirect). The region
# arg is accepted for interface uniformity and intentionally unused; recorded in
# the report so it cannot be mistaken for a dropped knob.
#
# Concurrency note: s4cmd's default thread count is cpu_count*4 (get_default_
# thread_count, s4cmd.py:120-121), which blows this campaign's CONCURRENCY_CAP.
# Every mode pins -c to a value within the cap. Override with S4CMD_SMOKE_THREADS
# (kept <= 8).
set -euo pipefail

mode="${1:?mode required}"
bucket="${2:?bucket required}"
region="${3:?region required}"   # accepted, unused (see header)
prefix="${4:-}"
: "${region}"

threads="${S4CMD_SMOKE_THREADS:-4}"
# Enforce the campaign concurrency cap here, not just in prose: a stray override
# must not silently emit -c 99. 8 is this subject card's CONCURRENCY_CAP.
if ! printf '%s' "$threads" | grep -Eq '^[0-9]+$' || [ "$threads" -lt 1 ] || [ "$threads" -gt 8 ]; then
  echo "S4CMD_SMOKE_THREADS must be an integer in 1..8 (CONCURRENCY_CAP); got: $threads" >&2
  exit 2
fi

url="s3://${bucket}/${prefix}"

emit() { printf '%s\0' "$@"; }

case "$mode" in
  recursive)
    # Full recursive listing: client-side fan-out, one delimited list_objects
    # paginator per discovered pseudo-directory (s4cmd always sends
    # Delimiter='/'); many can run at the same depth.
    emit ls -r -c "$threads" "$url" ;;
  shallow)
    # Single level (delimiter mode): current level's keys + subdirs as DIR.
    emit ls -c "$threads" "$url" ;;
  show-directory)
    # -d: show the directory entry itself instead of its contents.
    emit ls -d -c "$threads" "$url" ;;
  du)
    # du: same recursive s3walk request pattern, different output contract
    # (aggregate size). Included as a listing-request mode.
    emit du -r -c "$threads" "$url" ;;
  *)
    echo "unknown mode: $mode" >&2; exit 2 ;;
esac
