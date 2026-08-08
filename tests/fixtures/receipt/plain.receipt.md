# Smoke receipt — `fixture-tool` / mode `recursive`

Produced by `harness/run-attempt.sh`. Not a benchmark: this run makes no
comparative claim and its duration is a fact about this run only.

## Result

| | |
| --- | --- |
| Date (UTC) | 2026-01-02T03:04:05Z |
| Exit code | `0` |
| Wall-clock | 12.345s (container lifetime, StartedAt→FinishedAt) |
| Auth mode | `anonymous` — AWS_EC2_METADATA_DISABLED=true; credential values emptied and credential file sources pointed at a nonexistent path in-container, overriding any baked into the image; no mounted profile or config |
| Observability env (--env) | none |
| Functional env (--env) | none |
| Verifier verdict | _(filled in by `harness/verify-listing.sh`)_ |
| Tool version | `v1.2.3` — caller-supplied |

## Invocation

<pre><code>docker create --name s3study-smoke-x example/fixture ls
docker start s3study-smoke-x</code></pre>

Serialized from the same argv array that was executed — not reconstructed.
Container is created under a stable wrapper-owned name, then started detached, so the wrapper can sample memory and read the
cgroup while the process lives; it is removed by the wrapper afterwards.

## Subject

| | |
| --- | --- |
| Image | `example/fixture@sha256:0000000000000000000000000000000000000000000000000000000000000001` |
| Image arch | `arm64` |
| Entrypoint override | none |
| Emulated | no — image arm64 on host arm64 |
| Measured process | `fixturetool` (container main process) |

## Security boundary

| | |
| --- | --- |
| Profile | `fixture-profile-1` |
| Provider adapter | `fixture-provider` |
| Docker network | `s3study-net` (user-defined bridge, MTU 1460) |
| Firewall policy | sha256 `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa` |
| Container hardening | `--pull=never`; `--cap-drop ALL`; `--security-opt no-new-privileges:true` |
| Docker control bounds | 30s ordinary calls; 60s cleanup calls |
| Docker logging | driver `json-file`; canonical config sha256 `bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`; option keys (base64) `bWF4LXNpemU=` |

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
| Bucket | `fixture-bucket` |
| Region | `us-east-1` |
| Prefix scope | full bucket |
| Registry | `data/registry.toml` (sha256 `cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc`) |
| Manifest | `manifests/fixture-bucket.2026-01-02.tsv.gz` |
| Manifest sha256 | `dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd` — verified against the file before this run |
| Snapshot date | 2026-01-02 |
| Manifest keys | 1234 |

### Measured shape (from the registry)

<pre>- **Top level**: 2 prefixes — &#96;alpha/&#96; 700 · &#96;beta/&#96; 534.
- Shallow and broad.</pre>

## Memory

| | | |
| --- | --- | --- |
| `peak_rss` | 38.5 MB | `VmHWM` of the container's main process, 1302 successful samples. **Main process only** — a multi-process fan-out mode's children are not included. |
| `cgroup_peak_mem` | 29.1 MB | cgroup v2 `memory.peak`, whole container tree, 1302 successful samples. **Page cache and kernel/socket memory included. Never present this as RSS.** |

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

_(TODO: agent fills in where the tool exposes a counter; otherwise
"not exposed" — request-shape capture defers to the replay-server phase.)_

## Raw output

- stdout: inline — &#96;stdout.txt&#96; (321 bytes, sha256 &#96;eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee&#96;)
- stderr: inline — &#96;stderr.txt&#96; (0 bytes, sha256 &#96;ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff&#96;)
- Redaction altered bytes: **no**

Redacted and secret-scanned **before** hashing: the hash freezes the bytes,
so redaction after it would redact nothing. Machine-readable binding for the
verifier is in `run.meta`.
