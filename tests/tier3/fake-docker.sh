#!/usr/bin/env bash
# Tier-3 fake `docker`, installed as `docker` on PATH in front of the staged
# verifier. It replays a fixture-recorded reference listing instead of listing a
# real bucket, which is the only reason the FAIL / DRIFT / ERROR branches are
# reachable without network, credentials, or a provisioned runner.
#
# The contract is environment variables and argv only — nothing about it is
# specific to the bash verifier, so the same stub judges a ported one:
#
#   S3STUDY_FAKE_DOCKER_REFERENCE  file of raw `s3api ... --output text` rows
#                                  (quoted ETag, `+00:00` mtime) to replay
#   S3STUDY_FAKE_DOCKER_RC         exit status for `docker run` (default 0)
#   S3STUDY_FAKE_DOCKER_STDERR     what a failing `docker run` writes to stderr
#
# `--prefix` is honoured, so the replayed reference is scoped exactly the way the
# real re-list would be, and a verifier that forgot to pass the run's prefix gets
# a reference for the whole bucket — as it would in production.
#
# Only `run` honours the failure status: the ERROR path immediately calls
# `security_reconcile_container_absent`, and a cleanup that also failed would
# turn the ERROR verdict into a different death ("discard this runner").
set -euo pipefail

fail() { printf 'fake-docker: %s\n' "$*" >&2; exit 125; }

case "${1:-}" in
  run) ;;
  # `image inspect` asks whether the digest-pinned harness image is present
  # locally. It is, by construction: this stub *is* the image.
  image|container|rm|network|logs) exit 0 ;;
  *) fail "unsupported docker subcommand: ${1:-<none>}" ;;
esac

rc="${S3STUDY_FAKE_DOCKER_RC:-0}"
if [ "$rc" != 0 ]; then
  printf '%s\n' "${S3STUDY_FAKE_DOCKER_STDERR:-fake docker: reference re-list refused}" >&2
  exit "$rc"
fi

reference="${S3STUDY_FAKE_DOCKER_REFERENCE:-}"
[ -n "$reference" ] || fail "S3STUDY_FAKE_DOCKER_REFERENCE is unset"
[ -r "$reference" ] || fail "reference listing not readable: $reference"

prefix=""
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) prefix="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done

LC_ALL=C awk -v p="$prefix" 'p == "" || index($0, p) == 1' "$reference"
