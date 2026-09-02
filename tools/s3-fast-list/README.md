# s3-fast-list

[s3-fast-list](https://github.com/aws-samples/s3-fast-list) is an AWS Samples Rust tool that lists an S3 bucket through ListObjectsV2 and exports the object metadata to a Parquet file.
Its distinctive trick is to split the keyspace into byte-range slices — supplied as a hints file — and list those slices concurrently instead of walking one serial pagination chain.

> **Study status (2026-09-scale-diagnostics).** This tool's standing in the current release:
> Completed the 66.4M-object fixture with a count that matched it, in 333.8 s, on harness-supplied cut-points (`s3-fast-list.246cf7252988.s1`); one other attempt was killed at 8 GiB and one failed at 16 GiB with exit 0.
> The release is diagnostic: no attempt in it carries `purpose = measurement`, so
> nothing here is a calibrated benchmark or a ranking. Report and data:
> [`results/2026-09-scale-diagnostics/REPORT.md`](../../results/2026-09-scale-diagnostics/REPORT.md).

## In the current release

The release `2026-09-scale-diagnostics` is diagnostic: it settles what ran, what
each run returned, and how much memory it used; no row in it is a calibrated
measurement, and nothing here is a ranking. s3-fast-list `1.1.0` (the
anonymous-access fork build) ran in the release in adapter modes `list`,
`list-hinted` and `list-hinted-fixture`.

| fixture | attempts | outcomes | timing grades of completed rows | row cited in the report |
| --- | ---: | --- | --- | --- |
| FourCast 4.08M | 32 | SUCCEEDED 30, FAILED 2 | INSUFFICIENT_EVIDENCE 19, NOT_APPLICABLE 7, TIMING_VALID 4 | none cited |
| NARA 13.5M | 6 | SUCCEEDED 6 | TIMING_VALID 3, PRESSURE_DEGRADED 3 | none cited |
| NBM 66.4M | 3 | SUCCEEDED 1, FAILED 2 | PRESSURE_DEGRADED 1 | `s3-fast-list.246cf7252988.s1` |

`list` and `list-hinted` ran on the 4.08M and 13.5M fixtures; only
`list-hinted-fixture` ran on the 66.4M fixture.

Largest fixture attempted: 66.4M. Memory is the reason no larger one was
scheduled. One of the three attempts on that fixture failed with subject
exit -9 under an 8 GiB limit; that the memory limit killed it is a diagnosis
from the runner log, not a release field. A second failed at 16 GiB with exit
0 and no recorded reason. The one that completed, at 16 GiB, peaked at
11,347,320 KiB (`s3-fast-list.246cf7252988.s1`). Not carried further (study
decision).

Setup asymmetry named in the report: the 66.4M arm was fed keyspace
cut-points generated from the fixture (`list-hinted-fixture`). The
`list-hinted` arms on the smaller fixtures took their cut-points from a
chained prior `list` attempt through the adapter's inline split step; that the
inline step runs the upstream `ks-tool split` is an adapter declaration, not a
release field.

On timing grades: what `TIMING_VALID` / `PRESSURE_DEGRADED` /
`CAPACITY_FAILED` / `INSUFFICIENT_EVIDENCE` / `NOT_APPLICABLE` mean is on
[`docs/instrument.md`](../../docs/instrument.md); a grade describes the replay
instrument, not whether the tool succeeded.

Report: [`results/2026-09-scale-diagnostics/REPORT.md`](../../results/2026-09-scale-diagnostics/REPORT.md).
Findings page: [`RESULTS.md`](../../RESULTS.md). Rows:
[`results/2026-09-scale-diagnostics/attempts.jsonl`](../../results/2026-09-scale-diagnostics/attempts.jsonl).

The rows are an allowlisted public projection of the campaign ledger; the
original result files and logs are private. The receipts under `receipts/` in
this directory are groundwork evidence and do not cover the release rows.

## At a glance

Groundwork subject: the pinned build, smoke runs and source study from August
2026. The current release's rows are in the section above.

| Question | Current answer |
| --- | --- |
| Tested subject | An anonymous-access fork: upstream `b11e385` + the 51-line `--no-sign-request` patch, built at checkout `6c72f59` (version `1.1.0`) and run anonymously. Full canonical identity is in [`data/tool.json`](data/tool.json). |
| Exercised coverage | During groundwork: plain serial `list` only. The hinted path was not exercised during groundwork; the release ran `list`, `list-hinted` and `list-hinted-fixture` on fixtures to 66.4M objects (section above). `diff` and both `ks-tool` modes as standalone attempts were not exercised during groundwork or in the release. |
| Correctness | The standard verifier was blocked because the harness capture was not binary-safe. A limited direct-capture diff matched the manifest on all four smoke scopes (148,917 keys for the full bucket); canonical claim `limited-direct-capture-manifest-match` records this as an observation, not a certified verdict. See [`Running details`](docs/running.md#the-harness-capture-incompatibility-and-the-direct-capture-procedure). |
| Smoke observation | A receipted harness run of the full bucket exited 0 in 20.06 s with a 65.1 MB main-process peak RSS. These are facts of single groundwork runs, not benchmark results, and are not execution-bound to the direct-capture comparison above. |
| Results | No calibrated benchmark or comparative result exists in this study. The current release's rows for this tool (section above) are diagnostic; smoke timing and memory values in this table describe single groundwork runs. |

## How it works

Without a hints file, `s3-fast-list` runs one serial ListObjectsV2 pagination
over the whole bucket. Given a hints file of N keyspace cut points, it lists
the resulting N+1 ranges concurrently — parallelism is bought with a hints
file, not discovered automatically. Hints come from a prior run's
key-distribution file, an S3 Inventory report, or hand-written lines, so the
fast path is typically two-pass. Every listed object accumulates in memory
until listing finishes, then one Parquet dump is written at the end. Full
detail: [`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

The [Upstream](https://github.com/aws-samples/s3-fast-list) mode surface and
this study's actual coverage are shown in separate columns.

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| Plain `list` | Recursively list one bucket through ListObjectsV2 and write object metadata to Parquet plus a key-distribution CSV. | Built and smoked anonymously against one public bucket in a full scope and three prefixes. Source and run facts are recorded, but the standard correctness verifier was blocked. **Release:** ran in `2026-09-scale-diagnostics` as adapter mode `list` (fixtures to 13.5M objects; section above). |
| Hinted `list -k` | Convert supplied keyspace cut points into multiple concurrently listed ranges. Hints may be hand-written or prepared from earlier key-distribution data. | The current capsule has a preparation-backed real-S3 mode and a replay-only fixture-companion mode. The latter removes the serial bootstrap from repeated replay cells; it has no groundwork receipt; release rows exist (section above). **Release:** ran in `2026-09-scale-diagnostics` as adapter mode `list-hinted` (fixtures to 13.5M objects) and as adapter mode `list-hinted-fixture` (the 66.4M fixture; section above). |
| `diff` | List two buckets and emit differing object records. | Not run during groundwork or in the release; groundwork had no second bucket configured for this mode. |
| `ks-tool split` | Turn a key-distribution file into hints for a chosen segment count. | Its split loop is now source-audited for the standalone fixture utility, but the upstream binary has no committed groundwork receipt and no standalone release row; the release's `list-hinted` attempts consumed cut-points from the adapter's inline split step (section above). |
| `ks-tool inventory` | Prepare keyspace information from S3 Inventory input. | The subcommand's existence is source-supported, but its internals were not independently audited and it was not run during groundwork or in the release. |

The upstream project also exposes concurrency, Tokio worker-count, endpoint,
and constrained Rhai-filter controls. Their presence does not mean the study
exercised them. Detailed mode and source coverage is in
[`docs/mechanism.md`](docs/mechanism.md#modes), while build and smoke coverage
is in [`docs/running.md`](docs/running.md#smoked-and-blocked-modes).

## What we learned

Each finding links its owning explanation and its canonical claim ID; claim IDs
resolve in [`data/claims.json`](data/claims.json).

- **Without a hints file, listing is a single serial pass.** Source establishes
  the one-range path; a separate debug capture is consistent with it but lacks
  the independent run binding to promote the runtime claim to confirmed, so it
  stays source-supported.
  [`The listing algorithm`](docs/mechanism.md#the-listing-algorithm--concurrency-comes-only-from-hints)
  · `no-hints-creates-one-range`

- **Concurrency only helps when multiple ranges are supplied.** The reactor
  cannot create more active listing tasks than the hints-derived range list
  contains, so raising concurrency alone cannot parallelize the no-hints path —
  the source-supported mechanism behind the observed one-range run.
  [`The listing algorithm`](docs/mechanism.md#the-listing-algorithm--concurrency-comes-only-from-hints)
  · `concurrency-needs-multiple-ranges`

- **A key sitting exactly on a hint boundary can be dropped.** `StartAfter` is
  exclusive and the upper-bound check runs before insertion, so source review
  indicates a key equal to a cut point can fall between adjacent open ranges.
  This contradicts the inherited correctness-regardless-of-balance claim and is a
  source-derived risk, not run-confirmed behavior; the hinted path was not
  exercised during groundwork.
  [`Boundary semantics`](docs/mechanism.md#boundary-semantics)
  · `hint-boundary-key-can-be-omitted`
  **Release update:** the hinted 66.4M row returned a count that matched the
  fixture (`s3-fast-list.246cf7252988.s1`); the fixture generator refuses a
  cut-point equal to an object key (adapter source, not a release field), so
  that row does not test the boundary case.

- **A fatal range error can still exit zero with partial output.** The reviewed
  fatal-error path completes the range normally enough for accumulated data to be
  dumped and the process to return without a failing exit status. This
  source-supported correction is a silent-incompleteness risk awaiting fault
  injection, not something observed during smoke.
  [`Error handling`](docs/mechanism.md#error-handling)
  · `fatal-slice-error-can-exit-zero`
  **Release update:** one 66.4M attempt at 16 GiB failed with subject exit 0
  and no recorded reason (`s3-fast-list.540930a67436.s1`); the release rows
  do not say whether this path was the cause.

- **Every object is held in memory until one Parquet dump at the end.** The
  implementation holds object records in a two-level map before writing Parquet,
  which makes peak-memory growth an important benchmark question; the small
  groundwork runs cannot establish a scaling curve or an out-of-memory threshold,
  so that scaling stays unverified.
  [`Memory model`](docs/mechanism.md#memory-model--accumulate-then-dump)
  · `listing-accumulates-before-dump`, `memory-grows-with-bucket-size`
  **Release update:** the completed 66.4M row peaked at 11,347,320 KiB
  (`s3-fast-list.246cf7252988.s1`). One attempt on the same fixture ended with
  subject exit -9 under an 8 GiB limit (`s3-fast-list.500509011e5c.s1`); that
  the limit killed it is a diagnosis from the runner log, not a release field.

## Limitations and open questions

### Coverage gaps

- Exercise hinted `list -k` with a mounted hints file, including a real object
  whose key equals a cut point. The release ran hinted arms (section above)
  but none with a key on a cut point.
- Exercise `diff`, `ks-tool split`, `ks-tool inventory`, filters, and custom
  endpoint behavior with inputs appropriate to each mode; none ran as a
  standalone attempt during groundwork or in the release.
- Confirm the eventual upstream benchmark subject and common architecture; only
  an arm64 fork image was built and run during groundwork.

### Verifier and harness blocker

- The tool writes binary Parquet to a file. Groundwork routed that file to
  standard output, but the harness collected container logs through a
  non-binary-safe path, so the shared verifier could not certify completeness.
- A separate direct-capture manifest comparison matched all four smoke scopes,
  but its provenance is intentionally limited and it does not replace a
  verifier verdict. See
  [`The harness capture incompatibility`](docs/running.md#the-harness-capture-incompatibility-and-the-direct-capture-procedure).
- The adapter's tab-delimited normalized form cannot faithfully represent a key
  containing a literal tab or newline. Binary-safe framing is required before
  using an edge-key corpus; canonical claim `adapter-tab-newline-key-loss` in
  [`data/claims.json`](data/claims.json).

### Benchmark questions

- How does hinted throughput change with segment count, concurrency, key
  distribution, and Tokio worker count?
- What is the end-to-end cost of preparing hints before a first parallel list?
- How does peak memory grow with object count and metadata shape, and where does
  it fail under a fixed memory limit?
  **Release update:** the release rows give peak RSS per fixture up to 66.4M
  (section above and the report's RSS chart); one attempt at 8 GiB ended with
  exit -9 (`s3-fast-list.500509011e5c.s1`). The rows are single runs and do
  not establish a curve.
- How do throttling, retries, cancellation, and output finalization behave under
  controlled interruption and fault injection?

### Tool risks to test

- Reproduce or falsify key omission at a hint boundary.
- Reproduce or falsify partial output with exit zero after a fatal range error.
- Test unusual ETags and service errors against the source-located panic and
  error-classification paths.
- Determine whether interruption can leave apparently usable but incomplete or
  inconsistent Parquet without an external completeness signal.

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand the listing, concurrency, memory, output, and error model | [`docs/mechanism.md`](docs/mechanism.md) |
| Reproduce the image and understand exactly what smoke did or could not do | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity, tested-subject, eligibility, and claim status data | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness or inspect fixture-derived hints | [`adapter/`](adapter/) and [its operating notes](docs/running.md#fixture-derived-hints-utility-and-replay-bundle) |
| Build the local subject image | [`build/Dockerfile`](build/Dockerfile) |
| Audit how every old ledger row and status-bearing prose claim became atomic current claims | [`research/claims-migration.md`](research/claims-migration.md) and the preserved reconciliation in [`research/`](research/) |
| Read the historical pre-restructure landing page | [`research/tool-page.md`](research/tool-page.md) — frozen historical research, not the current entry point |
| Inspect the observations and immutable run records | [`receipts/`](receipts/) |

## Provenance

**Mixed provenance.** This page combines firsthand source, build, and smoke
work with inherited secondhand notes compiled from public sources. The seed was
not a run record. See [`research/tool-page.md`](research/tool-page.md) and
[`research/reconciliation.md`](research/reconciliation.md).

## Evidence boundary

Source and documentation explain mechanisms and risks; only a committed receipt
confirms run-dependent study behavior. Smoke observations are not benchmark
results. Rows in `results/` are the public projection of the campaign ledger,
separate from the receipts here; neither is a benchmark result.
