# Smoke receipt — `s3-fast-list` / mode `list`

Produced by `harness/smoke-run.sh`. Not a benchmark: this run makes no
comparative claim and its duration is a fact about this run only.

## Result

| | |
| --- | --- |
| Date (UTC) | 2026-07-17T12:05:52Z |
| Exit code | `0` |
| Wall-clock | 5.059s (container lifetime, StartedAt→FinishedAt) |
| Auth mode | `anonymous` — AWS_EC2_METADATA_DISABLED=true; credential values emptied and credential file sources pointed at a nonexistent path in-container, overriding any baked into the image; no mounted profile or config |
| Passed env (--env) | none |
| Verifier verdict | **BLOCKED via standard path — no verifier PASS was issued.** The wrapper captures stdout with `docker logs` (json-file driver), which is not binary-safe; this tool emits binary Parquet on stdout, so the stored `stdout` payload is corrupted and `verify-listing.sh`/duckdb cannot parse it. Correctness rests on an **[OBS] manifest-diff** against a faithful direct capture (`docker run > file`, intended to replicate this argv but not independently logged — provenance in `_capability/direct-capture.provenance.md`): prefix normals-hourly/: tool 2,549 = manifest 2,549; 0 missing/extra/mismatch/dup. Direct-capture parquet + sha256 under `receipts/smoke/_capability/`. See report §7/§8 and `_capability/HARNESS-INCOMPATIBILITY.txt`. |
| Tool version | `1.1.0 (checkout 6c72f59; upstream base b11e385 + no-sign-request patch)` — caller-supplied |

## Invocation

```sh
docker run -d --network host -e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC -e AWS_ACCESS_KEY_ID= -e AWS_SECRET_ACCESS_KEY= -e AWS_SESSION_TOKEN= -e AWS_SECURITY_TOKEN= -e AWS_CONTAINER_CREDENTIALS_RELATIVE_URI= -e AWS_CONTAINER_CREDENTIALS_FULL_URI= -e AWS_CONTAINER_AUTHORIZATION_TOKEN= -e AWS_ROLE_ARN= -e AWS_SHARED_CREDENTIALS_FILE=/nonexistent-by-harness -e AWS_CONFIG_FILE=/nonexistent-by-harness -e AWS_WEB_IDENTITY_TOKEN_FILE=/nonexistent-by-harness s3-fast-list@sha256:6246ee511116608864fab260aec1198c2761e42203316178a89ac1031664f2cc /usr/bin/s3-fast-list --no-sign-request --output-parquet-file /dev/stdout --output-ks-file /dev/null --prefix normals-hourly/ list --region us-east-1 --bucket noaa-normals-pds
```

Serialized from the same argv array that was executed — not reconstructed.
Container is started detached so the wrapper can sample memory and read the
cgroup while the process lives; it is removed by the wrapper afterwards.

## Subject

| | |
| --- | --- |
| Image | `s3-fast-list@sha256:6246ee511116608864fab260aec1198c2761e42203316178a89ac1031664f2cc` |
| Image arch | `arm64` |
| Entrypoint override | none |
| Emulated | no — image arm64 on host arm64 |
| Measured process | `s3-fast-list` (container main process) |

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
| Prefix scope | `normals-hourly/` |
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
| `peak_rss` | 19.1 MB | `VmHWM` of the container's main process, 92 successful samples. **Main process only** — a multi-process fan-out mode's children are not included. |
| `cgroup_peak_mem` | 7.9 MB | cgroup v2 `memory.peak`, whole container tree, 92 successful samples. **Page cache and kernel/socket memory included. Never present this as RSS.** |

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

not exposed — s3-fast-list has no total-request counter. Per-slice request
lines are visible only via `RUST_LOG=s3_fast_list=debug` (one `Sending S3
request` per keyspace pair; per-page `Waiting for S3 response` lines inside the
SDK paginator). See `_capability/debug-requestshape.stderr.txt`. Full
request-shape capture defers to the replay-server phase.

## Raw output

- stdout: external — `receipts/s3-fast-list/list.noaa-normals-pds.normals-hourly_.anonymous.stdout.txt` (127252 bytes, sha256 `71d854f9ea36eb286b5aeb6bddc6f6aeebc066efb9abc20a5dcd6a88b8beb2ed`) — redacted and scanned before hashing; published as a release asset at publication
- stderr: inline — `stderr.txt` (1335 bytes, sha256 `eadfa0259c5738eeced0658471f197666c0342807eceaf7341422ec599b79433`)
- Redaction altered bytes: **no**

Redacted and secret-scanned **before** hashing: the hash freezes the bytes,
so redaction after it would redact nothing. Machine-readable binding for the
verifier is in `run.meta`.
