# s3-fast-list

[s3-fast-list](https://github.com/aws-samples/s3-fast-list) is an AWS Samples Rust tool that lists an S3 bucket through ListObjectsV2 and exports the object metadata to a Parquet file.
Its distinctive trick is to split the keyspace into byte-range slices — supplied as a hints file — and list those slices concurrently instead of walking one serial pagination chain.
This study's groundwork is complete; no benchmark comparison has been run.

## At a glance

| Question | Current answer |
| --- | --- |
| Tested subject | An anonymous-access fork: upstream `b11e385` + the 51-line `--no-sign-request` patch, built at checkout `6c72f59` (version `1.1.0`) and run anonymously. Full canonical identity is in [`data/tool.json`](data/tool.json). |
| Exercised coverage | Plain serial `list` only. The hinted path, `diff`, and both `ks-tool` modes were not exercised. |
| Correctness | The standard verifier was blocked because the harness capture was not binary-safe. A limited direct-capture diff matched the manifest on all four smoke scopes (148,917 keys for the full bucket); canonical claim `limited-direct-capture-manifest-match` records this as an observation, not a certified verdict. See [`Running details`](docs/running.md#the-harness-capture-incompatibility-and-the-direct-capture-procedure). |
| Smoke observation | A receipted harness run of the full bucket exited 0 in 20.06 s with a 65.1 MB main-process peak RSS. These are facts of single groundwork runs, not benchmark results, and are not execution-bound to the direct-capture comparison above. |
| Results | No benchmark or comparative result exists. Smoke timing and memory values describe individual groundwork runs only. |

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
| Plain `list` | Recursively list one bucket through ListObjectsV2 and write object metadata to Parquet plus a key-distribution CSV. | Built and smoked anonymously against one public bucket in a full scope and three prefixes. Source and run facts are recorded, but the standard correctness verifier was blocked. |
| Hinted `list -k` | Convert supplied keyspace cut points into multiple concurrently listed ranges. Hints may be hand-written or prepared from earlier key-distribution data. | Not run through the study harness because the frozen wrapper cannot mount the required hints file. Its mechanism was read from source. |
| `diff` | List two buckets and emit differing object records. | Not run; groundwork had no second bucket configured for this mode. |
| `ks-tool split` | Turn a key-distribution file into hints for a chosen segment count. | The subcommand's existence is source-supported, but its internals were not independently audited and it was not run. |
| `ks-tool inventory` | Prepare keyspace information from S3 Inventory input. | The subcommand's existence is source-supported, but its internals were not independently audited and it was not run. |

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
  source-derived risk, not run-confirmed behavior; the hinted path has not been
  exercised.
  [`Boundary semantics`](docs/mechanism.md#boundary-semantics)
  · `hint-boundary-key-can-be-omitted`

- **A fatal range error can still exit zero with partial output.** The reviewed
  fatal-error path completes the range normally enough for accumulated data to be
  dumped and the process to return without a failing exit status. This
  source-supported correction is a silent-incompleteness risk awaiting fault
  injection, not something observed during smoke.
  [`Error handling`](docs/mechanism.md#error-handling)
  · `fatal-slice-error-can-exit-zero`

- **Every object is held in memory until one Parquet dump at the end.** The
  implementation holds object records in a two-level map before writing Parquet,
  which makes peak-memory growth an important benchmark question; the small
  groundwork runs cannot establish a scaling curve or an out-of-memory threshold,
  so that scaling stays unverified.
  [`Memory model`](docs/mechanism.md#memory-model--accumulate-then-dump)
  · `listing-accumulates-before-dump`, `memory-grows-with-bucket-size`

## Limitations and open questions

### Coverage gaps

- Exercise hinted `list -k` with a mounted hints file, including a real object
  whose key exactly equals a cut point.
- Exercise `diff`, `ks-tool split`, `ks-tool inventory`, filters, and custom
  endpoint behavior with inputs appropriate to each mode.
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
- How do throttling, retries, cancellation, and output finalization behave under
  controlled interruption and fault injection?

### Tool risks to test

- Reproduce or falsify key omission at an exact hint boundary.
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
| Integrate the subject with the shared harness | [`adapter/`](adapter/) |
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
results.
