# s5cmd

[s5cmd](https://github.com/peak/s5cmd) is a Go CLI that lists an S3 bucket by walking one ListObjectsV2 continuation chain, printing each object as a text or JSON row; its worker-pool speed reputation is about transfers, not listing.
The study runs the upstream project unmodified — anonymous access uses s5cmd's own `--no-sign-request` flag, not a fork or patch.
This study's groundwork is complete; no benchmark comparison has been run.

## At a glance

| Question | Current answer |
| --- | --- |
| Tested subject | Upstream s5cmd `v2.3.0` (commit `991c9fb`), run anonymously from upstream's own published image `peakcom/s5cmd:v2.3.0` pinned by digest. Full canonical identity is in [`data/tool.json`](data/tool.json). |
| Exercised coverage | Every listing mode smoked: recursive, delimiter, JSON, ListObjects v1, all-versions, full-path, and the hand-built per-prefix fan-out. Transfers (`cp`/`sync`) are out of scope. |
| Correctness | The verifier PASSed all ten smoke receipts with 0 duplicates/missing/extra in each; the full-bucket modes and the fan-out union each matched all 148,917 manifest keys, and the scoped runs matched their smaller scopes. See [`docs/running.md`](docs/running.md). |
| Smoke observation | A receipted recursive full-bucket run listed 148,917 keys and exited 0 in 16.96 s at a 40.3 MB peak RSS. This is a single groundwork run, not a benchmark result. |
| Results | No benchmark or comparative result exists. Smoke timing and memory values describe individual groundwork runs only. |

## How it works

A single `s5cmd ls` issues one ListObjectsV2 call per URL and consumes the
pages with a plain serial loop over an unbuffered channel — no worker pool and
no keyspace sharding. The `--numworkers` pool (default 256) parallelizes only
the downstream `cp`/`rm`/`mv` transfers, never the LIST. Listing is streaming
(each object printed as it arrives, no accumulation), a wildcard is one
recursive List plus a client-side filter, and page size is fixed at S3's own
1,000-key ceiling. The only way to list multiple prefixes concurrently is the
user-built fan-out: `s5cmd run <file>` dispatches per-prefix `ls` lines through
the same pool. Full detail: [`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

The [upstream](https://github.com/peak/s5cmd) surface and this study's actual
coverage are shown in separate columns.

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| `ls` (recursive / delimiter / full-path) | List a bucket or prefix through ListObjectsV2, recursively or one level deep, as text or absolute paths. | Smoked anonymously against one public bucket; every scope PASSed the verifier. |
| `ls --json` | Emit one JSON object per key. | Smoked; same key set as recursive, output-only difference. |
| `ls --use-list-objects-v1` / `ls --all-versions` | List through the legacy ListObjects v1 API or ListObjectVersions. | Both smoked and PASSed; all-versions validates the request/output contract only, on a non-versioned bucket. |
| `run <file>` + `--numworkers` | Execute a batch of commands in parallel through the worker pool. | Used to fan out per-prefix `ls` lines; completeness verified, speed not measured. |
| `cp` / `sync` (transfer) | Parallel object transfer — the tool's headline feature. | Not run: out of listing scope and mutating. |

Detailed mode and source coverage is in
[`docs/mechanism.md`](docs/mechanism.md), while build and smoke coverage is in
[`docs/running.md`](docs/running.md).

## What we learned

Each finding links its owning explanation and its canonical claim ID; claim IDs
resolve in [`data/claims.json`](data/claims.json).

- **Listing is one serial ListObjectsV2 chain, not a parallel lister.** Source
  shows one call per URL consumed serially, and a captured trace corroborates it
  as a single continuation-token chain for one `ls`; behavior at scale is not
  receipt-settled.
  [`Listing is one serial, paginated stream`](docs/mechanism.md#listing-is-one-serial-paginated-stream)
  · `ls-is-one-serial-list-chain`, `serial-listing-at-scale-unverified`

- **All parallelism is transfer-side — except the `run` fan-out.** `--numworkers`
  does nothing for a lone `ls`, but `s5cmd run` dispatches its `ls` lines through
  the same pool, so a per-prefix batch lists concurrently.
  [`The run/--numworkers fan-out dispatch`](docs/mechanism.md#the-run--numworkers-fan-out-dispatch)
  · `parallelism-is-transfer-side-for-lone-ls`, `numworkers-sizes-run-fanout-concurrency`

- **The hand-built fan-out covers the bucket completely.** Four prefix shards
  plus the unprefixed remainder, unioned, listed 148,917 keys with zero
  duplicates; its speed against a native parallel lister is unmeasured.
  [`The run/--numworkers fan-out dispatch`](docs/mechanism.md#the-run--numworkers-fan-out-dispatch)
  · `fanout-completeness-verified`, `fanout-speed-vs-native-unverified`

- **The 1,000-key page size is S3's ceiling, not an s5cmd deficit.** s5cmd never
  sets `MaxKeys`, so S3 returns its own 1,000-key maximum; no real-S3 client can
  exceed it, so this only matters versus tools that parallelize pages across
  sharded prefixes.
  [`Page size`](docs/mechanism.md#page-size-1000-is-s3s-ceiling-not-an-s5cmd-disadvantage)
  · `page-size-1000-is-s3-ceiling`

- **`--log trace` is the only request-level window.** `--stat` counts operations
  and `--log debug` shows nothing per-request, but `--log trace` writes full SDK
  request/response records to stdout, making the per-page count obtainable.
  [`Observability`](docs/mechanism.md#observability)
  · `log-trace-exposes-per-request-pages`

## Limitations and open questions

### Coverage gaps

- Edge-key fidelity (unicode, spaces, tabs, newlines, multipart ETags) is
  deferred with the edge-case fixture (`EDGE_BUCKET=none`); the study adapter
  does not reproduce whitespace-bearing keys byte-for-byte.
- Multi-version and delete-marker fidelity of `--all-versions` needs a versioned
  bucket.
- Transfer commands (`cp`/`sync`) and their memory reports are out of listing
  scope and were not run.

### Benchmark questions

- Serial listing at 10^6–10^8 keys, quantified against parallel-capable tools.
- v1 vs v2 vs ListObjectVersions cost — smoke saw roughly 17 s / 70 s / 87 s on
  the same bucket, single-run observations only.
- Fan-out throughput sweeping `--numworkers` and shard granularity.
- `--retry-count` behavior under throttling, and memory at scale.

### Inherited reports not settled here

- The survey figures (15M objects in 733 s; killed at 15M objects; ~11 GB RAM
  syncing 400k files; a 1.5× vs `aws s3api` figure) are scale/timing or transfer
  claims that smoke does not decide. The 2025 re-testing pointer remains unread.

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand the listing, concurrency, memory, output, and observability model | [`docs/mechanism.md`](docs/mechanism.md) |
| Reproduce any smoke receipt and see exactly what ran or was deferred | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity, tested-subject, eligibility, and claim status data | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness | [`adapter/`](adapter/) |
| Audit how every old ledger row and prose claim became atomic current claims | [`research/claims-migration.md`](research/claims-migration.md) and the preserved reconciliation in [`research/`](research/) |
| Read the historical pre-restructure landing page | [`research/tool-page.md`](research/tool-page.md) — frozen historical research, not the current entry point |
| Inspect the observations and immutable run records | [`receipts/`](receipts/) |

## Provenance

**Mixed provenance.** This page combines firsthand source review and anonymous
smoke runs with inherited secondhand notes compiled from public sources. The
seed was **not a run record**, so every inherited claim was treated as
unverified until source or a receipt settled it. See
[`research/tool-page.md`](research/tool-page.md) and
[`research/reconciliation.md`](research/reconciliation.md). Cross-cutting claims
that name s5cmd alongside other tools live in `docs/open-questions.md`.

## Evidence boundary

Source and documentation explain mechanisms and correct the inherited framing;
only a committed receipt confirms run-dependent study behavior, and an `[OBS]`
capability probe supports without confirming. Smoke observations are facts about
single groundwork runs, not benchmark or comparative results.
