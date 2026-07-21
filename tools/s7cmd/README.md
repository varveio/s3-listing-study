# s7cmd

[s7cmd](https://github.com/nidor1998/s7cmd) is an umbrella Rust CLI whose `ls` subcommand lists an S3 bucket and prints the result as aligned text, TSV, one-line, or JSON; its listing engine is the separate [s3ls-rs](https://github.com/nidor1998/s3ls-rs) crate pinned at `=1.0.3`, which discovers common-prefixes in parallel to a fixed depth and then drains each leaf with sequential pagination.
It is not a fork: the study built and ran s7cmd unmodified from its own repository, and every listing-engine source anchor resolves in the s3ls-rs crate it depends on rather than in a reimplementation.
This study's groundwork is complete; no benchmark comparison has been run.

## At a glance

| Question | Current answer |
| --- | --- |
| Tested subject | s7cmd v1.5.0 (commit `d589df7`), built from its own Dockerfile at the pinned SHA and run anonymously (arm64, native). The `ls` engine is the s3ls-rs crate v1.0.3 (commit `bf42067`). Full canonical identity is in [`data/tool.json`](data/tool.json). |
| Exercised coverage | Twelve `ls` mode/scope runs (recursive TSV/aligned/JSON/one-line, `--no-sort`, `--all-versions`, `--max-depth`, and shallow), plus a `_build` capture and a bucket-list capability probe. |
| Correctness | The verifier returned PASS on all twelve exercised runs against `noaa-normals-pds`; canonical claim `smoke-modes-all-pass`. Anonymous `ListBuckets` is blocked (307, exit 1), so that path is untested-for-that-reason, not skipped. See [`Running details`](docs/running.md#smoked-modes). |
| Smoke observation | A single recursive full-bucket run recorded 204 counted page fetches and a 120.8 MB peak RSS at 148,917 keys. These are facts of one groundwork run each, not benchmark results; the page-fetch figure is a page-fetch count, not a wire-level request count. |
| Results | No benchmark or comparative result exists. Smoke timing, memory, and page-fetch values describe individual groundwork runs only. |

## How it works

s7cmd's `ls` builds the s3ls-rs `ListingPipeline` directly from the pinned crate.
In recursive mode the engine discovers common-prefixes in parallel with a
delimiter to a fixed fan-out depth (default 2), then drains each leaf prefix with
a flat sequential `ListObjectsV2` pagination; a bucket with no `/` hierarchy
finds no sub-prefixes and collapses to a single sequential pass. Non-recursive
listings always set a `/` delimiter and run sequentially. Output is buffered and
sorted by default, or streamed under `--no-sort`. Full detail:
[`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

The [upstream](https://github.com/nidor1998/s7cmd) mode surface and this study's
actual coverage are shown in separate columns. Only `ls` is in study scope.

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| `ls` object listing | List a bucket or prefix, recursively or at one delimiter level, in aligned / TSV / one-line / JSON form. | Twelve anonymous runs against one public bucket across a full scope and several prefixes; all PASSED the verifier. |
| `ls --all-versions` | Switch the API to `ListObjectVersions`, adding `VersionId` and delete-marker rows. | Run once; passed only because the smoke bucket has single null-version objects, so genuine multi-version collapse was never exercised. |
| `ls` with no target | Call `ListBuckets` to enumerate buckets. | Probed as a capability only; anonymous `ListBuckets` is blocked (307, exit 1). |
| `cp` / `mv` / `rm` / `sync` / `clean` and bucket admin | The umbrella's other subcommands, composing three sibling crates. | Out of scope; not exercised. |

Upstream also exposes concurrency, rate-limit, Express One Zone, and sort-threshold
controls. Their presence does not mean the study exercised them. Mode-by-mode
build and smoke coverage is in
[`docs/running.md`](docs/running.md#smoked-modes).

## What we learned

Each finding links its owning explanation and its canonical claim ID; claim IDs
resolve in [`data/claims.json`](data/claims.json).

- **s7cmd's `ls` is the s3ls-rs crate, not a reimplementation.** The `ls`
  subcommand is a thin wrapper that builds `s3ls_rs::ListingPipeline` from the
  crate pinned at exactly `=1.0.3`, so the listing engine, defaults, and output
  formatters are identical to standalone s3ls by construction; the CLI surface
  differs only in the hidden `--auto-complete-shell` flag and a modified process
  wrapper. Full runtime equivalence beyond the shared crate and these two known
  divergences is inferred from the dependency pin, not measured side-by-side.
  [`The pipeline`](docs/mechanism.md#the-pipeline)
  · `ls-is-s3ls-rs-crate`, `engine-identical-by-construction`,
  `cli-surface-omits-auto-complete-shell`, `process-wrapper-drops-exit-helper`,
  `runtime-equivalence-is-inferred`

- **Parallelism is delimiter-based common-prefix discovery to a fixed depth,
  then a flat drain.** The engine fans out on `/`-delimited prefixes to the
  fan-out depth and then paginates each leaf sequentially; a flat keyspace
  discovers no sub-prefixes and collapses to one sequential pass — a source-
  established shape not measured at smoke.
  [`Parallel discovery and flat drain`](docs/mechanism.md#parallel-discovery-and-flat-drain)
  · `parallel-path-algorithm`, `flat-bucket-collapses-to-sequential`

- **The tool's `api_calls` counter is a page-fetch count, not a wire request
  count.** It is bumped once before each page fetch in both paths, so under the
  SDK's default ten-attempt retry a single counted fetch can cost more than one
  chargeable request; the full-bucket run recorded 204 page fetches against a
  ~149-page floor.
  [`The api_calls counter`](docs/mechanism.md#the-api_calls-counter)
  · `api-calls-is-page-fetch-count`

- **`all-versions` omits `IsLatest` by default and versioned fidelity is
  deferred.** `-r --all-versions` adds `VersionId` but not `IsLatest` (which
  needs `--show-is-latest`), and the smoke bucket's single null-version objects
  mean multi-version collapse and delete markers were never exercised.
  [`all-versions output contract`](docs/mechanism.md#all-versions-output-contract)
  · `all-versions-omits-is-latest-by-default`, `versioned-bucket-fidelity-deferred`

- **All twelve smoke modes passed, but bucket listing is blocked anonymously.**
  Every exercised mode/scope run exited 0 and the verifier returned PASS;
  `ls` with no target calls `ListBuckets`, which anonymously returns a 307 and
  exits 1, so that path is blocked rather than skipped.
  [`Smoked modes`](docs/running.md#smoked-modes)
  · `smoke-modes-all-pass`, `bucket-listing-blocked-anonymously`

## Limitations and open questions

### Coverage gaps

- Exercise `all-versions` against a genuinely versioned/edge bucket with a
  version-aware manifest; the current pass had `EDGE_BUCKET=none`.
- Exercise a flat (non-hierarchical) keyspace, an Express One Zone bucket, and
  the rate-limit and sort-threshold controls.
- Run `ls` with credentials so the `ListBuckets` path can be exercised rather
  than blocked.

### Harness and verifier notes

- Anonymous `ListBuckets` is blocked (307, exit 1); canonical claim
  `bucket-listing-blocked-anonymously`.
- The adapter keys the `all-versions` normalized form on object key alone and
  discards `VersionId`/`IsLatest`, so it cannot validate genuine version collapse
  until a version-aware manifest exists; canonical claim
  `versioned-bucket-fidelity-deferred`.
- No upstream container image was found (Docker Hub 404), but the check is
  incomplete because GHCR could not be enumerated; canonical claim
  `no-container-image-found-check-incomplete`.

### Benchmark questions

- How does listing throughput change with fan-out depth, concurrency, key
  distribution, and prefix shape, including the flat-keyspace collapse?
- Where does the buffer-all sorted mode's peak memory grow relative to `--no-sort`,
  and what is the latency step at the 1,000,000-key sort threshold?
- What is the true wire-level request count behind `api_calls` under retries?

### Tool risks to test

- Reproduce or falsify the stuck-continuation-token bail yielding an incomplete
  listing against a misbehaving endpoint.
- Confirm `all-versions` fidelity (collapse, delete markers, `IsLatest`) on a
  versioned bucket.
- Determine whether `zeroize` clears access keys on drop.

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand the pipeline, parallel discovery, pagination, memory, and retry model | [`docs/mechanism.md`](docs/mechanism.md) |
| See how the image was built and exactly which modes ran or were blocked | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity, tested-subject, eligibility, and claim status data | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness | [`adapter/`](adapter/) |
| Audit how every old ledger row and status-bearing prose claim became atomic current claims | [`research/claims-migration.md`](research/claims-migration.md) and the preserved reconciliation in [`research/`](research/) |
| Read the historical pre-restructure landing page | [`research/tool-page.md`](research/tool-page.md) — frozen historical research, not the current entry point |
| Inspect the observations and immutable run records | [`receipts/`](receipts/) |

## Provenance

**Mixed provenance.** This page combines firsthand source, build, and smoke work
— a pinned source read of s7cmd v1.5.0 (`d589df7`) and its s3ls-rs v1.0.3 engine
(`bf42067`), twelve committed anonymous smoke receipts, and two rounds of
critical cross-check — with inherited secondhand notes compiled from public
sources. The seed was not a run record. The inherited s3ls-rs dossier is
inherited background held in internal notes and is not included in this
public repository. See [`research/tool-page.md`](research/tool-page.md) and
[`research/reconciliation.md`](research/reconciliation.md).

## Evidence boundary

Source and documentation explain mechanisms and risks; only a committed receipt
confirms run-dependent study behavior. The twelve verifier PASSes and the
single-run page-fetch, timing, and memory figures are smoke observations, not
benchmark results, and are not bound to one another across execution paths.
