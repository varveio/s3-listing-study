# Smoke receipt — `fix&amp;tool` / mode `mode&lt;v2&gt;`

Produced by `harness/smoke-run.sh`. Not a benchmark: this run makes no
comparative claim and its duration is a fact about this run only.

## Result

| | |
| --- | --- |
| Date (UTC) | 2026-01-02T03:04:05Z |
| Exit code | `137` — **killed at the 300s timeout** |
| Wall-clock | 300.000s (container lifetime, StartedAt→FinishedAt) |
| Auth mode | `anonymous` — AWS_EC2_METADATA_DISABLED=true; credential values emptied and credential file sources pointed at a nonexistent path in-container, overriding any baked into the image; no mounted profile or config |
| Observability env (--env) | `RUST_LOG=debug` — recorded verbatim |
| Functional env (--env) | `MC_HOST_s3=https://s3.amazonaws.com` — validated tool configuration, recorded verbatim |
| Verifier verdict | _(filled in by `harness/verify-listing.sh`)_ |
| Tool version | _(TODO: unavailable — --version exited 1 — agent records from the tool manually)_ |

## Invocation

<pre><code>docker create --name s3study-smoke-y -e A=1 example/fixture ls "a&#124;b"
docker start s3study-smoke-y</code></pre>

Serialized from the same argv array that was executed — not reconstructed.
Container is created under a stable wrapper-owned name, then started detached, so the wrapper can sample memory and read the
cgroup while the process lives; it is removed by the wrapper afterwards.

## Subject

| | |
| --- | --- |
| Image | `example/fixture@sha256:0000000000000000000000000000000000000000000000000000000000000002` |
| Image arch | `amd64` |
| Entrypoint override | /bin/sh -c &#96;x&#96; |
| Emulated | **yes** — image amd64 on host arm64 (qemu). Smoke only; must not carry into the benchmark. |
| Measured process | `sh` (container main process) |

## Security boundary

| | |
| --- | --- |
| Profile | `profile&lt;1&gt;` |
| Provider adapter | `prov&amp;ider` |
| Docker network | `s3study-net` (user-defined bridge, MTU 1460) |
| Firewall policy | sha256 `1111111111111111111111111111111111111111111111111111111111111111` |
| Container hardening | `--pull=never`; `--cap-drop ALL`; `--security-opt no-new-privileges:true` |
| Docker control bounds | 30s ordinary calls; 60s cleanup calls |
| Docker logging | driver `json-file`; canonical config sha256 `2222222222222222222222222222222222222222222222222222222222222222`; option keys (base64) `none` |

## Box

| | |
| --- | --- |
| Arch | `aarch64` |
| Cores | 8 |
| RAM | 31 GB |
| Kernel | `6.17.0-1020-gcp` |
| Runner location | `unknown` |

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
| Prefix scope | `alpha&#124;beta/` |
| Registry | `data/registry.toml` (sha256 `3333333333333333333333333333333333333333333333333333333333333333`) |
| Manifest | `manifests/fixture&amp;bucket.tsv.gz` |
| Manifest sha256 | `4444444444444444444444444444444444444444444444444444444444444444` — verified against the file before this run |
| Snapshot date | 2026-01-02 |
| Manifest keys | 0 |

### Measured shape (from the registry)

<pre>- **Top level**: &#96;a&lt;b&gt;/&#96; 1 · &#96;c&amp;d/&#96; 2 · pipe &#124; here.
- second line</pre>

## Memory

| | | |
| --- | --- | --- |
| `peak_rss` | unavailable MB | `VmHWM` of the container's main process, 0 successful samples. **Main process only** — a multi-process fan-out mode's children are not included. |
| `cgroup_peak_mem` | 0.0 MB | cgroup v2 `memory.peak`, whole container tree, 1 successful samples. **Page cache and kernel/socket memory included. Never present this as RSS.** |

**Both numbers are sampled**, polled every 1000 ms. Each is a
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

- stdout: external — &#96;/data/receipts/t/x.stdout.txt&#96; (67108864 bytes, sha256 &#96;5555555555555555555555555555555555555555555555555555555555555555&#96;) — redacted and scanned before hashing; published as a release asset at publication
- stderr: inline — &#96;stderr.txt&#96; (67108864 bytes, sha256 &#96;6666666666666666666666666666666666666666666666666666666666666666&#96;)
- Redaction altered bytes: **yes**
- **stdout TRUNCATED at the 67108864-byte (64 MiB) cap — 17 bytes dropped (head kept).**
- **stderr TRUNCATED at the 67108864-byte (64 MiB) cap — 3 bytes dropped (head kept).**

> **Truncation warning.** A capped stream is incomplete by construction. The
> verifier refuses a completeness verdict on any mode whose *verified* payload was
> truncated (a cut-off listing cannot prove it listed everything); truncation of
> stderr alone does not block verifying a complete stdout listing.

Redacted and secret-scanned **before** hashing: the hash freezes the bytes,
so redaction after it would redact nothing. Machine-readable binding for the
verifier is in `run.meta`.
