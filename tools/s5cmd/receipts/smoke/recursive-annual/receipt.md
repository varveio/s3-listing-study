# Smoke receipt — `s5cmd` / mode `recursive`

Produced by `harness/smoke-run.sh`. Not a benchmark: this run makes no
comparative claim and its duration is a fact about this run only.

## Result

| | |
| --- | --- |
| Date (UTC) | 2026-07-17T08:12:01Z |
| Exit code | `0` |
| Wall-clock | 1.510s (container lifetime, StartedAt→FinishedAt) |
| Auth mode | `anonymous` — AWS_EC2_METADATA_DISABLED=true; credential values emptied and credential file sources pointed at a nonexistent path in-container, overriding any baked into the image; no mounted profile or config |
| Passed env (--env) | none |
| Verifier verdict | **PASS** — see `verify.md` |
| Tool version | `v2.3.0-991c9fb` — caller-supplied |

## Invocation

```sh
docker run -d --network host -e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC -e AWS_ACCESS_KEY_ID= -e AWS_SECRET_ACCESS_KEY= -e AWS_SESSION_TOKEN= -e AWS_SECURITY_TOKEN= -e AWS_CONTAINER_CREDENTIALS_RELATIVE_URI= -e AWS_CONTAINER_CREDENTIALS_FULL_URI= -e AWS_CONTAINER_AUTHORIZATION_TOKEN= -e AWS_ROLE_ARN= -e AWS_SHARED_CREDENTIALS_FILE=/nonexistent-by-harness -e AWS_CONFIG_FILE=/nonexistent-by-harness -e AWS_WEB_IDENTITY_TOKEN_FILE=/nonexistent-by-harness peakcom/s5cmd@sha256:2ff939e2ee3c76adcadd78dbfc3e2569b18a3743ed9dcfccb1ec589af7fb9903 --no-sign-request ls -e -s s3://noaa-normals-pds/normals-annualseasonal/1981-2010/access/\*
```

Serialized from the same argv array that was executed — not reconstructed.
Container is started detached so the wrapper can sample memory and read the
cgroup while the process lives; it is removed by the wrapper afterwards.

## Subject

| | |
| --- | --- |
| Image | `peakcom/s5cmd@sha256:2ff939e2ee3c76adcadd78dbfc3e2569b18a3743ed9dcfccb1ec589af7fb9903` |
| Image arch | `arm64` |
| Entrypoint override | none |
| Emulated | no — image arm64 on host arm64 |
| Measured process | `s5cmd` (container main process) |

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
> can mask per-page CPU cost that would be significant in-region. It does
> **not** bias serial-vs-parallel comparison — that ratio is the
> concurrency factor, independent of RTT. Recorded so a reader can judge;
> irrelevant at smoke scale, which produces no comparative numbers.

## Bucket

| | |
| --- | --- |
| Bucket | `noaa-normals-pds` |
| Region | `us-east-1` |
| Prefix scope | `normals-annualseasonal/1981-2010/access/`normals-annualseasonal/1981-2010/access/ |
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
| `peak_rss` | 37.5 MB | `VmHWM` of the container's main process, 27 successful samples. **Main process only** — a multi-process fan-out mode's children are not included. |
| `cgroup_peak_mem` | 27.7 MB | cgroup v2 `memory.peak`, whole container tree, 27 successful samples. **Page cache and kernel/socket memory included. Never present this as RSS.** |

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

**Obtainable via `--log trace`** (s5cmd surfaces no built-in numeric counter). `--stat` reports operation counts only (`ls 1 0 1`); `--log debug` emits nothing per-request; `--log trace` writes full AWS SDK request/response records to **stdout** (one HeadBucket region-probe + one per ListObjectsV2 page, walked sequentially). This wrapper run did not pass `--log trace` (it would bloat the payload with the request dump); the page-count evidence is in `receipts/smoke/_capability/observability/` [OBS]. Scale request-shape capture defers to the replay-server phase.

## Raw output

- stdout: external — `receipts/s5cmd/recursive.noaa-normals-pds.normals-annualseasonal_1981-2010_access_.anonymous.stdout.txt` (964222 bytes, sha256 `f8f416bc87fe878ae720bbf533cd716f5c508b283ab5f2cbb23c10bb9d027eb5`) — redacted and scanned before hashing; published as a release asset at publication
- stderr: inline — `stderr.txt` (0 bytes, sha256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`)
- Redaction altered bytes: **no**

Redacted and secret-scanned **before** hashing: the hash freezes the bytes,
so redaction after it would redact nothing. Machine-readable binding for the
verifier is in `run.meta`.
