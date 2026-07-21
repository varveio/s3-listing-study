# Build / first-execution evidence (non-mode, verifier-exempt)

Image: `rclone/rclone@sha256:c61954aaa32328a5486715dd063a81c7879f5195ad3505cd362deddd509dc4a1` (tag 1.74.4, manifest-list digest; resolves linux/arm64 here)
Entrypoint: `["rclone"]`  Architecture: arm64 (native on aarch64 host, no emulation)
Captured (UTC): 2026-07-17T12:10:24Z

## rclone version (in container, --network none)
```
rclone v1.74.4
- os/version: alpine 3.24.1 (64 bit)
- os/kernel: 6.17.0-1020-gcp (aarch64)
- os/type: linux
- os/arch: arm64 (ARMv8 compatible)
- go/version: go1.26.5
- go/linking: static
- go/tags: none
```

## Listing-relevant flags (rclone help flags, in container)
```
 --checkers int Number of checkers to run in parallel (default 8)
 --use-server-modtime Use server modified time instead of object metadata
 --fast-list Use recursive list if available; uses more memory but fewer transactions
 --rc-serve-no-modtime Don't read the modification time (can speed things up)
 --drive-fast-list-bug-fix Work around a bug in Google Drive listing (default true)
 --s3-list-chunk int Size of listing chunk (response list for each ListObject S3 request) (default 1000)
 --s3-list-version int Version of ListObjects to use: 1,2 or 0 for auto
 --s3-list-versions-oldest-first Tristate Set if the backend returns object versions oldest first (default unset)
```

Diff vs Stage-A doc reading: none — live help matched docs; --use-server-modtime, --no-mimetype, --fast-list, --checkers (default 8), --s3-list-chunk (default 1000), --s3-list-version all present as documented.
