#!/usr/bin/env bash
# tools/rclone/adapter/run.sh — prints the rclone argv for one listing mode, NUL-delimited.
#
#   run.sh <mode> <bucket> <region> [prefix]
#
# Prints ONLY the argv (each argument followed by a NUL). It never executes
# anything: harness/smoke-run.sh owns `docker run`, mounts, auth starvation, and
# the timeout, and APPENDS this argv to the image ENTRYPOINT — which for
# rclone/rclone is `["rclone"]`, so the argv starts at the SUBCOMMAND, not `rclone`.
#
# Bucket, region and prefix are ALWAYS parameters (owner's rule): nothing here
# hardcodes a bucket name. The smoke wrapper greps this file for every registered
# bucket name and refuses to run if it finds one.
#
# Anonymous access: rclone's S3 backend goes unsigned when no access_key_id /
# secret_access_key are set and env_auth is false (its default) — it installs
# aws.AnonymousCredentials [SRC backend/s3/s3.go:1508-1511 @ 5bc93a2a7]. We use the
# on-the-fly connection-string remote `:s3,provider=AWS,region=R:BUCKET[/PREFIX]`
# so no config file is needed; the wrapper additionally starves every AWS_* env
# credential, so auth=anonymous is enforced, not merely configured.
#
# The "run it properly for listing" flags, on every lsjson mode:
#   --use-server-modtime  — return LastModified from the listing. WITHOUT it,
#                           rclone calls readMetaData -> a HEAD PER OBJECT to read
#                           x-amz-meta-mtime [SRC backend/s3/s3.go ModTime @ 5bc93a2a7].
#   --no-mimetype         — MimeType likewise HEADs every object
#                           [SRC backend/s3/s3.go MimeType @ 5bc93a2a7].
# Omitting either turns a ~149-page listing into 148,917 HEADs.
set -euo pipefail
export LC_ALL=C

MODE="${1:?mode}"; BUCKET="${2:?bucket}"; REGION="${3:?region}"; PREFIX="${4:-}"

emit() { local a; for a in "$@"; do printf '%s\0' "$a"; done; }

# Base backend spec. list_version is folded into the connection string for the
# legacy-API mode so it is unambiguous with the on-the-fly remote.
backend="s3,provider=AWS,region=${REGION}"
[ "$MODE" = listv1 ] && backend="${backend},list_version=1"

# Remote path: append the prefix (which carries its own trailing slash) when scoped.
remote=":${backend}:${BUCKET}"
[ -n "$PREFIX" ] && remote="${remote}/${PREFIX}"

case "$MODE" in
  recursive-fastlist)
    # ListR: single flat recursive listing, no delimiter, serial pagination.
    emit lsjson --fast-list --files-only --use-server-modtime --no-mimetype -R "$remote" ;;
  recursive-hierarchical)
    # HISTORICAL / MISLABELED — DO NOT treat this as a hierarchical walk.
    # `lsjson -R` calls walk.ListR directly [SRC fs/operations/lsjson.go:248 @ 5bc93a2a7],
    # which selects the S3 backend ListR whenever maxLevel<0 (unbounded -R),
    # IGNORING --fast-list entirely [SRC fs/walk/walk.go:149-163 @ 5bc93a2a7]. So this
    # emits a SINGLE flat undelimited ListObjectsV2 chain — identical shape to
    # recursive-fastlist — and --checkers is INERT (traced: 0 delimiter= requests).
    # The genuine per-directory walk is the recursive-walk mode below. This case is
    # kept only to reproduce the mislabeled 2026-07-17 receipt; see that receipt's
    # correction block.
    emit lsjson --files-only --use-server-modtime --no-mimetype --checkers 4 -R "$remote" ;;
  recursive-walk)
    # GENUINE hierarchical walk: --disable ListR nils the S3 ListR feature, so
    # walk.ListR falls back to the per-directory Walk [SRC fs/walk/walk.go:152-160,
    # 65-77 @ 5bc93a2a7]. Each directory is a delimiter=/ ListObjectsV2; children
    # discovered via CommonPrefixes are fanned across --checkers workers
    # [SRC fs/walk/walk.go:380,393 @ 5bc93a2a7]. Capped at 4 for the concurrency share.
    # Traced: every request carries delimiter=%2F, one per directory.
    emit lsjson --files-only --use-server-modtime --no-mimetype --disable ListR --checkers 4 -R "$remote" ;;
  delimiter-shallow)
    # Single delimiter level (no -R): files + CommonPrefixes (directories).
    emit lsjson --use-server-modtime --no-mimetype "$remote" ;;
  listv1)
    # Legacy ListObjects (v1) API instead of ListObjectsV2. Flat recursive.
    emit lsjson --fast-list --files-only --use-server-modtime --no-mimetype -R "$remote" ;;
  lsf)
    # Distinct output contract: lsf "path;size" lines. No modtime code -> no HEAD.
    emit lsf --fast-list --files-only --format ps --separator ";" -R "$remote" ;;
  debug)
    # NOT a listing mode — a request-shape probe. Same fast-list listing as
    # recursive-fastlist, plus -vv --dump headers so the HTTP request line of every
    # ListObjectsV2 page is emitted on stderr (lets us confirm serial pagination and
    # the API/version actually used). Anonymous requests carry no Authorization
    # header; the wrapper redacts + secret-scans the dump regardless. Staged under
    # receipts/smoke/_capability/ (verifier-exempt).
    emit lsjson --fast-list --files-only --use-server-modtime --no-mimetype -R -vv --dump headers "$remote" ;;
  walk-debug)
    # NOT a listing mode — the request-shape probe for the GENUINE hierarchical
    # walk (recursive-walk). --disable ListR forces the per-directory Walk; the trace
    # proves each request carries delimiter=%2F and there is one request per
    # directory node (vs the single undelimited chain of the fast-list debug probe).
    # Staged under receipts/smoke/_capability/ (verifier-exempt).
    emit lsjson --files-only --use-server-modtime --no-mimetype --disable ListR --checkers 4 -R -vv --dump headers "$remote" ;;
  *)
    printf 'run.sh: unknown mode %s\n' "$MODE" >&2; exit 3 ;;
esac
