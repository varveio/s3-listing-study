# AWS CLI

[aws-cli](https://github.com/aws/aws-cli) is AWS's official command-line client; it lists an S3 bucket through the `ListObjectsV2` continuation-token paginator and prints the keys as text, JSON, or one of several other output formats.
It exposes two independent listing surfaces — the high-level `aws s3 ls` and the low-level `aws s3api list-objects-v2` (plus the legacy V1 `list-objects` and `list-object-versions`) — both serial and single-threaded, with no listing-concurrency knob anywhere; aws-cli is also the study's pinned harness client, at version 2.36.0 one patch behind the 2.36.1 tested here, so a sibling build produces the listings every other tool is checked against.
This study's groundwork is complete; no benchmark comparison has been run.

## At a glance

| Question | Current answer |
| --- | --- |
| Tested subject | The official upstream image `amazon/aws-cli:2.36.1`, source pinned at commit `12d962d2`, run anonymously with `--no-sign-request` and no self-authored Dockerfile. Full canonical identity is in [`data/tool.json`](data/tool.json). |
| Exercised coverage | Both surfaces (`s3 ls` and `s3api` V2, legacy V1, and versions), the `text`, `json`, and `yaml-stream` output formats, delimiter-root listing, a resume round-trip probe, and a manual prefix fan-out. Every smoke mode ran anonymously and passed the verifier. |
| Correctness | Every mode matched its own registered scope in the manifest, and the two full-bucket executions (`s3 ls --recursive` and `s3api-v2-text`) matched all 148,917 keys; a four-shard-plus-remainder fan-out reconstructed the whole bucket with zero duplicates and zero missing or extra keys. |
| Smoke observation | The full-bucket `s3api-v2-text` run listed 148,917 keys and exited 0 in about 26 s. This is a single groundwork fact, not a benchmark result. |
| Results | No benchmark or comparative result exists. Smoke timing values describe individual groundwork runs only. |

## How it works

aws-cli lists through a single serial `ListObjectsV2` continuation-token
paginator in both the `s3 ls` and `s3api` surfaces; there is no listing
concurrency anywhere, and the only way to parallelise is to fan out disjoint
`--prefix` invocations by hand. Memory use is decided by the output *format*,
not the command: `s3 ls`, `s3api --output text`, and `s3api --output
yaml-stream` stream page-by-page, while `--output json` (the default), `yaml`,
and `table` buffer the whole result set before printing. Resume is a manual
`--starting-token` chunking primitive that persists nothing. Full account:
[`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

The [upstream](https://github.com/aws/aws-cli) surface and this study's actual
coverage are shown in separate columns.

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| `s3 ls` | High-level recursive or delimiter listing with fixed text output. | Ran recursively over the full bucket and non-recursively at the delimiter root, both verifier PASS. |
| `s3api list-objects-v2` | Low-level ListObjectsV2 pagination with a projectable output format. | Ran full-bucket and three scoped prefixes in `text`, plus `json` and `yaml-stream` on one prefix, all PASS. |
| `s3api list-objects` / `list-object-versions` | Legacy V1 Marker pagination and version listing. | Ran one prefix each, verifier PASS. |
| Manual prefix fan-out | Not a tool feature: N caller-run disjoint-prefix invocations, unioned. | Four prefix shards plus a delimiter-root remainder reconstructed the full bucket clean. |
| `--page-size`, timeouts, retries | Pagination and resilience tunables. | Defaults read from source; no page-size sweep or fault injection was run. |

## What we learned

Each finding links its owning explanation and its canonical claim ID; claim IDs
resolve in [`data/claims.json`](data/claims.json).

- **Memory behavior splits by output format, not by command surface.** `s3 ls`,
  `s3api --output text`, and `s3api --output yaml-stream` stream and hold no key
  set, while `--output json`/`yaml`/`table` buffer the entire result via
  `build_full_result()` — so any memory claim must name the surface *and* the
  format.
  [`Memory behavior by output format`](docs/mechanism.md#memory-behavior-by-output-format)
  · `memory-format-split`

- **There is no listing parallelism anywhere; the only parallel path is a manual
  prefix fan-out.** Both surfaces run one serial paginator; concurrency exists
  only in transfers, and a caller-run four-shard fan-out reconstructed the full
  bucket clean.
  [`Concurrency`](docs/mechanism.md#concurrency--serial-listing-supported-by-source-and-one-probe)
  · `no-listing-parallelism`, `fanout-union-reconstructs-bucket`

- **Both surfaces are one serial ListObjectsV2 request stream.** Source shows a
  synchronous paginator loop in each; a single `--debug` probe of one s3api
  invocation observed the single-thread continuation chain, but that is one
  invocation, not an exhaustive per-flag sweep.
  [`Request patterns`](docs/mechanism.md#request-patterns)
  · `both-surfaces-paginate-listobjectsv2`, `serial-single-thread-one-probe`

- **Resume is a manual chunking primitive, not crash-safe.** `--max-items` →
  `NextToken` → `--starting-token` round-trips cleanly, but nothing persists the
  token, so a killed unbounded run has nothing to resume from. No process was
  killed in the probe.
  [`Pagination and continuation`](docs/mechanism.md#pagination-and-continuation-resume)
  · `resume-primitive-round-trips`, `resume-token-not-persisted`

- **`s3 ls` without `--recursive` lists only the first delimiter level.** It
  shows folders as `PRE` rollups rather than the whole bucket — a common footgun
  that the delimiter-root smoke run confirmed.
  [`Request patterns`](docs/mechanism.md#request-patterns)
  · `s3-ls-nonrecursive-lists-first-level`

## Limitations and open questions

### Coverage gaps

- No page-size sweep (`page-size-ceiling-sweep`) and no fault-injection of the
  timeout/retry behavior (`timeout-retry-behavior-under-fault`) were run.
- The buffered `--output json`/`yaml`/`table` path was not driven to a
  many-million-key OOM; whether it OOMs at scale is unsettled by smoke.

### Scale-dependent hypotheses

- The secondhand 15M-objects/1110s throughput figure
  (`throughput-15m-1110s-secondhand`), the ~8h billion-object extrapolation
  (`billion-objects-eight-hour-estimate`, `billion-objects-serial-roundtrips`),
  and the third-party ~12M-object mid-run failure
  (`twelve-million-midrun-failure-thirdparty`) are inherited and unexecuted;
  smoke scale cannot speak to them.

### Benchmark questions

- Does buffered `--output json` OOM at scale versus streaming `text`/`yaml-stream`?
- How does single-invocation serial pagination compare to a k-way prefix
  fan-out, and where do diminishing returns or throttling begin?
- What is the confirmed common architecture and page-size optimum for the
  campaign?

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand the two surfaces, request stream, concurrency, and memory-by-format model | [`docs/mechanism.md`](docs/mechanism.md) |
| Reproduce the image and see every smoked mode, the fan-out union, and the capability probes | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity, tested subject, study states, and claim status data | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness | [`adapter/`](adapter/) |
| Audit how every old ledger row and prose finding became atomic current claims | [`research/claims-migration.md`](research/claims-migration.md) and the preserved review in [`research/`](research/) |
| Read the historical pre-restructure landing page | [`research/tool-page.md`](research/tool-page.md) — frozen historical research, not the current entry point |
| Inspect the observations and immutable run records | [`receipts/`](receipts/) |

## Provenance

**Mixed provenance.** This page combines firsthand source, image-selection, and
smoke work with inherited secondhand notes compiled from AWS documentation and
one GitHub issue. The seed was not a run record. The metadata cells and the
mechanism, output-format, anonymous-access, and resume claims were re-derived
from the pinned source and checked against committed receipts; the scale claims
remain inherited and unexecuted. See [`research/tool-page.md`](research/tool-page.md)
and [`research/reconciliation.md`](research/reconciliation.md).

## Evidence boundary

Source and documentation explain the mechanisms and risks; only a committed
receipt confirms run-dependent study behavior. A `--debug` probe is an
observation of one invocation, not a receipted run, and smoke observations are
not benchmark results.
