# s7cmd

[s7cmd](https://github.com/nidor1998/s7cmd) is an umbrella Rust CLI whose `ls` subcommand lists an S3 bucket and prints the result as aligned text, TSV, one-line, or JSON; its listing engine is the separate [s3ls-rs](https://github.com/nidor1998/s3ls-rs) crate pinned at `=1.0.3`, which discovers common-prefixes in parallel to a fixed depth and then drains each leaf with sequential pagination.
It is not a fork: the study built and ran s7cmd unmodified from its own repository, and every listing-engine source anchor resolves in the s3ls-rs crate it depends on rather than in a reimplementation.

> **Study status (2026-09-scale-diagnostics).** This tool's standing in the current release:
> Matched the 13.5M-object fixture's count in 601.0 s (`s7cmd.bdba69aad415.s1`); returned no count at 66.4M and reached the 7,200 s cap at 143M.
> The release is diagnostic: no attempt in it carries `purpose = measurement`, so
> nothing here is a calibrated benchmark or a ranking. Report and data:
> [`results/2026-09-scale-diagnostics/REPORT.md`](../../results/2026-09-scale-diagnostics/REPORT.md).

## In the current release

The release `2026-09-scale-diagnostics` is diagnostic: it settles what ran,
what each run returned, and how much memory it used; no row in it is a
calibrated measurement, and nothing here is a ranking. s7cmd ran as version
1.5.0 in three adapter modes, `recursive-one-nosort`, `recursive-tsv` and
`recursive-tsv-nosort`, all of them upstream `ls -r`; the
`--max-parallel-listings` of each arm is in its row.

| fixture | attempts | outcomes | timing grades of completed rows | row cited in the report |
| --- | ---: | --- | --- | --- |
| FourCast 4.08M | 10 | SUCCEEDED 10 | CAPACITY_FAILED 5, PRESSURE_DEGRADED 3, NOT_APPLICABLE 2 | none cited |
| NARA 13.5M | 8 | SUCCEEDED 8 | INSUFFICIENT_EVIDENCE 3, CAPACITY_FAILED 5 | `s7cmd.bdba69aad415.s1` |
| real-changesets 13.9M (flat) | 1 | SUCCEEDED 1 | TIMING_VALID 1 | `s7cmd.97b265107a89.s1` |
| NBM 66.4M | 4 | SUCCEEDED 4 | INSUFFICIENT_EVIDENCE 4 | none cited |
| blockchain 143M | 1 | SUCCEEDED 1 | CAPACITY_FAILED 1 | `s7cmd.a9b999169187.s1` |

"Outcomes" is the settled state of the attempt, and a settled row can carry
no count: the four NBM rows have no `row_count` and a subject exit code of 1,
and the 143M row has no `row_count` and a subject exit code of 124, the
7,200 s cap. On the flat 13.9M fixture the cited row's count matched the
staged fixture count. On the 13.5M fixture the cited row's count matched the
capture report's count, which is in the study's working notes because no
fixture count was staged there (the report's "Per-tool dispositions" labels it
"= capture").

Largest fixture attempted: 143M, with no count. The report's "largest fixture
attempted" table gives the reason no larger one was scheduled: parallel prefix
discovery, then serial pagination per leaf (from its source); on the skewed
143M fixture it reached the 7,200 s cap with exit 124, and that one dominant
leaf was still draining is a diagnosis from the runner log, not a release
field.

No row of the report's "Where the setup was not equal" table names s7cmd.

What `TIMING_VALID`, `PRESSURE_DEGRADED`, `CAPACITY_FAILED`,
`INSUFFICIENT_EVIDENCE` and `NOT_APPLICABLE` mean is on
[`docs/instrument.md`](../../docs/instrument.md#how-a-release-grades-the-delivered-treatment);
a grade describes how the replay instrument delivered its latency treatment
during the run, not whether the tool succeeded.

Report:
[`results/2026-09-scale-diagnostics/REPORT.md`](../../results/2026-09-scale-diagnostics/REPORT.md).
Findings: [`RESULTS.md`](../../RESULTS.md). Rows:
[`results/2026-09-scale-diagnostics/attempts.jsonl`](../../results/2026-09-scale-diagnostics/attempts.jsonl).

The rows are an allowlisted public projection of the campaign ledger; the
original result files and logs are private. The receipts under `receipts/` in
this directory are groundwork evidence and do not cover the release rows.

## At a glance

Groundwork subject: the pinned build, smoke runs and source study from
August 2026. The current release's rows are in the section above.

| Question | Current answer |
| --- | --- |
| Tested subject | s7cmd v1.5.0 (commit `d589df7`), built from its own Dockerfile at the pinned SHA and run anonymously (arm64, native). The `ls` engine is the s3ls-rs crate v1.0.3 (commit `bf42067`). Full canonical identity is in [`data/tool.json`](data/tool.json). |
| Exercised coverage | During groundwork: twelve `ls` mode/scope runs (recursive TSV/aligned/JSON/one-line, `--no-sort`, `--all-versions`, `--max-depth`, and shallow), plus a `_build` capture and a bucket-list capability probe. Scale behaviour was not exercised during groundwork; the release ran `recursive-one-nosort`, `recursive-tsv` and `recursive-tsv-nosort` on fixtures to 143M objects (section above). |
| Correctness | During groundwork, the verifier returned PASS on all twelve exercised runs against `noaa-normals-pds`; canonical claim `smoke-modes-all-pass`. The release rows check the row count against a staged fixture count where one exists, and against the capture report's count in the study's working notes where none was staged; never key by key (section above). Anonymous `ListBuckets` is blocked (307, exit 1), so that path is untested-for-that-reason, not skipped. See [`Running details`](docs/running.md#smoked-modes). |
| Smoke observation | A single recursive full-bucket run recorded 204 counted page fetches and a 120.8 MB peak RSS at 148,917 keys. These are facts of one groundwork run each, not benchmark results; the page-fetch figure is a page-fetch count, not a wire-level request count. |
| Results | No calibrated benchmark or comparative result exists in this study. The current release's rows for this tool (section above) are diagnostic; smoke timing and memory values in this table describe single groundwork runs. |

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
| `ls` object listing | List a bucket or prefix, recursively or at one delimiter level, in aligned / TSV / one-line / JSON form. | Groundwork: twelve anonymous runs against one public bucket across a full scope and several prefixes; all PASSED the verifier. **Release:** ran in `2026-09-scale-diagnostics` as adapter modes `recursive-one-nosort`, `recursive-tsv` and `recursive-tsv-nosort`, all `ls -r` (fixtures to 143M objects; section above). |
| `ls --all-versions` | Switch the API to `ListObjectVersions`, adding `VersionId` and delete-marker rows. | Groundwork: run once; passed only because the smoke bucket has single null-version objects, so genuine multi-version collapse was never exercised. Not run in the release: the adapter refuses this mode against the replay endpoint because it does not issue `ListObjectsV2` (from the adapter source). |
| `ls` with no target | Call `ListBuckets` to enumerate buckets. | Groundwork: probed as a capability only; anonymous `ListBuckets` is blocked (307, exit 1). Not run in the release, for the same adapter reason as `--all-versions`. |
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
  **Release update:** on the flat 13.9M fixture, `s7cmd.97b265107a89.s1`
  returned the fixture's object count. Its wall was 1,289.5 s. That the drain
  was one serial pass there is read from the tool's source, not a release
  field. On the skewed 143M fixture, `s7cmd.a9b999169187.s1` reached the
  7,200 s cap with no count; that one dominant leaf was still draining is a
  diagnosis from the runner log, not a release field.
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
  **Release update:** the flat keyspace was run once, `s7cmd.97b265107a89.s1`
  (section above); the Express One Zone, rate-limit and sort-threshold controls
  were not exercised during groundwork or in the release.
- Run `ls` with credentials so the `ListBuckets` path can be exercised rather
  than blocked.

### Harness and verifier notes

- Anonymous `ListBuckets` is blocked (307, exit 1); canonical claim
  `bucket-listing-blocked-anonymously`.
- The Batch renderer gives every s7cmd subject a fixed
  `nofile=1048576:1048576` container limit. This is harness headroom for the
  prefix-discovery socket set, not a plan tuning axis or an upstream default.
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
  **Release update:** no comparative numbers exist; the scale rows are in the
  release section above and are diagnostic. The 66.4M rows returned no count
  (subject exit 1, all four arms), and the 143M row reached the 7,200 s cap
  (`s7cmd.a9b999169187.s1`).
- Where does the buffer-all sorted mode's peak memory grow relative to `--no-sort`,
  and what is the latency step at the 1,000,000-key sort threshold?
  **Release update:** on the 4.08M fixture the sorted `recursive-tsv` row
  `s7cmd.5941a76a7bbc.s1` peaked at 3,067,664 KiB. The `--no-sort` rows on the
  same fixture peaked near 50,000 KiB; each row's `max_rss_kb` is in the release
  rows (section above). Single diagnostic rows, not a measurement of the
  sort threshold.
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
| Inspect registered inputs for the shared derived-image build | [`build/image.json`](build/image.json) |
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
Rows in `results/` are the public projection of the campaign ledger, separate
from the receipts here; neither is a benchmark result.
