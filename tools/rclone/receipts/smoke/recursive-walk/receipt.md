# Smoke receipt — `rclone` / mode `recursive-walk`

Produced by `harness/smoke-run.sh`. Not a benchmark: this run makes no
comparative claim and its duration is a fact about this run only.

## Result

| | |
| --- | --- |
| Date (UTC) | 2026-07-17T14:38:17Z |
| Exit code | `0` |
| Wall-clock | 1.388s (container lifetime, StartedAt→FinishedAt) |
| Auth mode | `anonymous` — AWS_EC2_METADATA_DISABLED=true; credential values emptied and credential file sources pointed at a nonexistent path in-container, overriding any baked into the image; no mounted profile or config |
| Passed env (--env) | none |
| Verifier verdict | **PASS** — see `verify.md` |
| Tool version | `rclone v1.74.4` — caller-supplied |

## Invocation

```sh
docker run -d --network host -e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC -e AWS_ACCESS_KEY_ID= -e AWS_SECRET_ACCESS_KEY= -e AWS_SESSION_TOKEN= -e AWS_SECURITY_TOKEN= -e AWS_CONTAINER_CREDENTIALS_RELATIVE_URI= -e AWS_CONTAINER_CREDENTIALS_FULL_URI= -e AWS_CONTAINER_AUTHORIZATION_TOKEN= -e AWS_ROLE_ARN= -e AWS_SHARED_CREDENTIALS_FILE=/nonexistent-by-harness -e AWS_CONFIG_FILE=/nonexistent-by-harness -e AWS_WEB_IDENTITY_TOKEN_FILE=/nonexistent-by-harness rclone/rclone@sha256:c61954aaa32328a5486715dd063a81c7879f5195ad3505cd362deddd509dc4a1 lsjson --files-only --use-server-modtime --no-mimetype --disable ListR --checkers 4 -R :s3\,provider=AWS\,region=us-east-1:noaa-normals-pds/normals-annualseasonal/1981-2010/
```

Serialized from the same argv array that was executed — not reconstructed.
Container is started detached so the wrapper can sample memory and read the
cgroup while the process lives; it is removed by the wrapper afterwards.

## Subject

| | |
| --- | --- |
| Image | `rclone/rclone@sha256:c61954aaa32328a5486715dd063a81c7879f5195ad3505cd362deddd509dc4a1` |
| Image arch | `arm64` |
| Entrypoint override | none |
| Emulated | no — image arm64 on host arm64 |
| Measured process | `rclone` (container main process) |

## Box

| | |
| --- | --- |
| Arch | `aarch64` |
| Cores | 8 |
| RAM | 31 GB |
| Kernel | `6.17.0-1020-gcp` |
| Runner location | `gcp:us-east1-b` |

> Runner location is recorded because RTT sets the ratio of network
> time to CPU time in a listing run: a runner outside the bucket region
> can mask per-page CPU cost that would be significant in-region. For an
> RTT-bound tool it does **not** bias serial-vs-parallel comparison — to
> first order that ratio is the concurrency factor — but client CPU,
> output back-pressure, and throttling can pull real ratios below it.
> Recorded so a reader can judge; irrelevant at smoke scale, which
> produces no comparative numbers.

## Bucket

| | |
| --- | --- |
| Bucket | `noaa-normals-pds` |
| Region | `us-east-1` |
| Prefix scope | `normals-annualseasonal/1981-2010/` |
| Registry | `docs/smoke-bucket.md` (sha256 `254c8cfedd06b1b8671c5bbabc753bfe45462124821eacf44bd27b43c67bbced`) |
| Manifest | `manifests/noaa-normals-pds.2026-07-17.tsv.gz` |
| Manifest sha256 | `c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb` — verified against the file before this run |
| Snapshot date | 2026-07-17 |
| Manifest keys | 148917 |

### Measured shape (from the registry)

- **Top level**: 4 prefixes + 1 root-level key —
  `normals-monthly/` 48,796 · `normals-daily/` 48,787 ·
  `normals-annualseasonal/` 48,784 · `normals-hourly/` 2,549 · `index.html` 1.
- **Depth histogram** (`/`-count): depth 0 → 1 key, depth 2 → 29,986,
  depth 3 → 118,930. No deep nesting; the tree is broad and shallow.
- **Largest second-level prefixes**: `normals-monthly/1991-2020/` 15,625 ·
  `normals-daily/1991-2020/` 15,624 · `normals-annualseasonal/1991-2020/`
  15,623 · the `2006-2020/` trio ≈ 13,480 each · the `1981-2010/` trio ≈
  9,840 each.
- ≥149 LIST pages un-delimited at the 1,000-key page cap.

## Memory

| | | |
| --- | --- | --- |
| `peak_rss` | 71.2 MB | `VmHWM` of the container's main process, 24 successful samples. **Main process only** — a multi-process fan-out mode's children are not included. |
| `cgroup_peak_mem` | 26.7 MB | cgroup v2 `memory.peak`, whole container tree, 25 successful samples. **Page cache and kernel/socket memory included. Never present this as RSS.** |

**Both numbers are sampled**, polled every 50 ms. Each is a
kernel-maintained high-water mark, so a poll returns the true peak as of
that read; the unmeasured window is between the last poll and process
exit. The container cgroup is destroyed at exit, so neither can be read
post-mortem. `unavailable` means the value was never successfully read —
it is not zero, and it is not a finding about the tool.

**Neither number bounds the other, and neither is a sanity check on the
other.** `VmHWM` counts pages resident in the main process, including
shared/file-backed pages that may be charged to a **different** cgroup;
`memory.peak` counts memory charged to **this** cgroup and excludes pages
charged elsewhere. `peak_rss` > `cgroup_peak_mem` is normal where the
image is already hot in page cache.

## API call count

**Not exposed as a counter** in `lsjson` output (as for every rclone list mode).
This is the **genuine per-directory hierarchical walk**: `--disable ListR` nils the
S3 ListR feature, so `walk.ListR` falls back to the per-directory `Walk`
[SRC fs/walk/walk.go:152-160, 65-77 @ 5bc93a2a7]; each directory is a
`Delimiter=/` `ListObjectsV2` and children discovered via `CommonPrefixes` are
fanned across `--checkers` workers [SRC fs/walk/walk.go:380,393 @ 5bc93a2a7]. The
request shape is traced in the sibling capability probe
`receipts/smoke/_capability/walk-debug` (same scope, `-vv --dump headers`): **13
`ListObjectsV2` requests, every one carrying `delimiter=%2F`** — one per directory
(`.../1981-2010/` discovering `access/`+`archive/`+`doc/`, then `access/` in 10
serial `continuation-token` pages for its 9,839 keys, `archive/` and `doc/` one
page each), no `Authorization` header. So request count here is set by the **tree
shape** (one LIST per directory node, each paged serially), NOT `ceil(keys /
list_chunk)` — that flat-chain formula is the `--fast-list`/full-recursive shape,
not this one. Contrast the flat probe `_capability/debug` (single undelimited
chain). [RUN receipts/smoke/_capability/walk-debug]

## Raw output

- stdout: external — `receipts/rclone/recursive-walk.noaa-normals-pds.normals-annualseasonal_1981-2010_.anonymous.stdout.txt` (1453736 bytes, sha256 `e95da70663fa7a86774e1375faea0575b5cd32a89c0e2f1263cb73147cfd9a2c`) — redacted and scanned before hashing; published as a release asset at publication
- stderr: inline — `stderr.txt` (96 bytes, sha256 `0d2698010a71ff88d8df6ed22031fb6b3e2bccaa4980329aa5b6d45072ab8e0b`)
- Redaction altered bytes: **no**

Redacted and secret-scanned **before** hashing: the hash freezes the bytes,
so redaction after it would redact nothing. Machine-readable binding for the
verifier is in `run.meta`.
