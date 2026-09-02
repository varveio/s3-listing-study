# s3p

[s3p](https://github.com/generalui/s3p) ("S3 Parallel") lists an S3 bucket and prints one object key per line, discovering the keyspace by recursive bisection — synthetic midpoint keys and two concurrent ListObjectsV2 calls per range node — instead of the serial continuation-token page loop used by the other tools in this survey. By this study's reading it is the only keyspace bisector among the tools surveyed here — a comparative claim this study has not audited.
It is an upstream tool published by GenUI; this study tested the npm distribution unmodified rather than a fork.

> **Study status (2026-09-scale-diagnostics).** This tool's standing in the current release:
> Completed the 143M-object fixture with a count that matched it, in 4,238.8 s at c16 (`s3p.1b77f20ed931.s1`), in its key-only mode.
> The release is diagnostic: no attempt in it carries `purpose = measurement`, so
> nothing here is a calibrated benchmark or a ranking. Report and data:
> [`results/2026-09-scale-diagnostics/REPORT.md`](../../results/2026-09-scale-diagnostics/REPORT.md).

## In the current release

The release `2026-09-scale-diagnostics` is diagnostic: it settles what ran,
what each run returned, and how much memory it used; no row in it is a
calibrated measurement, and nothing here is a ranking. s3p ran as version
3.7.2 in one adapter mode, `ls` (upstream `ls`, one key per line); the
`--list-concurrency` of each arm is in its row.

| fixture | attempts | outcomes | timing grades of completed rows | row cited in the report |
| --- | ---: | --- | --- | --- |
| FourCast 4.08M | 12 | SUCCEEDED 11, CANCELLED 1 | TIMING_VALID 3, PRESSURE_DEGRADED 6, CAPACITY_FAILED 1, NOT_APPLICABLE 1 | none cited |
| NARA 13.5M | 9 | SUCCEEDED 9 | TIMING_VALID 9 | none cited |
| NBM 66.4M | 1 | SUCCEEDED 1 | TIMING_VALID 1 | none cited |
| blockchain 143M | 1 | SUCCEEDED 1 | TIMING_VALID 1 | `s3p.1b77f20ed931.s1` |

Largest fixture attempted: 143M, which is the largest replay fixture in the
release, so no larger one was available to schedule. The report's "largest
fixture attempted" table records that the c16 key-only arm completed with a
count that matched the fixture, and that this release does not establish CPU
or width effects.

One row of the report's "Where the setup was not equal" table names s3p, the
replay server size on the 143M fixture; subject allocations were equal.

| asymmetry | s3p row | Swath's fastest 143M row |
| --- | --- | --- |
| replay server vCPU on blockchain | 20 (`s3p.1b77f20ed931.s1`) | 64 (`swath.be4140354dd1.s1`) |

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
| Tested subject | The upstream npm distribution `s3p@3.7.2` (the tool's own `version` self-report, captured in the build note), while `[SRC]` anchors use git tag v3.6.0 (`5a23b22e`). Full canonical identity is in [`data/tool.json`](data/tool.json). |
| Exercised coverage | During groundwork: `ls` has completed under a scoped list-only credential. Anonymously, `ls`, `ls --raw` and `summarize` were probed and all blocked at authentication; `ls --long` shares that code path and is blocked by inheritance. The release ran adapter mode `ls` on fixtures to 143M objects (section above). |
| Correctness | No verdict during groundwork. The credentialed `ls` attempt produced a listing, but auditing an attempt against a reference manifest is not implemented, so verification remains recorded as blocked. The release rows check the row count against a staged fixture count where one exists, and against the capture report's count in the study's working notes where none was staged; never key by key (section above). |
| Smoke observation | During groundwork: blocked, not skipped. The `ls`, `ls --raw`, and `summarize` probes each failed at AWS-SDK credential resolution with exit 1 before any LIST completed — s3p has no anonymous access path. `ls --long` and the other modes share that code path and are blocked by inheritance rather than re-run, so the block is command-independent across the modes probed. No listing was produced. See [`Running details`](docs/running.md#the-blocked-smoke-state--every-capability-receipt). |
| Results | No calibrated benchmark or comparative result exists in this study. The current release's rows for this tool (section above) are diagnostic; smoke timing and memory values in this table describe single groundwork runs. |

## How it works

Every read command funnels into one recursive engine that discovers an unknown
keyspace by bisection rather than continuation-token paging. For each range it
computes a synthetic midpoint key from a fixed 95-character alphabet — without
sampling any real keys — and issues two concurrent ListObjectsV2 calls, one from
the range start and one from the midpoint, recursing a half only when its page
came back full. A LIFO worker pool (default `--list-concurrency 100`) caps how
many ranges are in flight. The CLI `ls` path streams keys as they arrive, while
the library API accumulates them. Full detail:
[`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

The [upstream](https://github.com/generalui/s3p) listing surface and this study's
actual coverage are shown in separate columns.

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| `ls` | List a bucket and print one key per line via bisection. | Groundwork: attempted as a capability probe; blocked at AWS-SDK credential resolution before any LIST. **Release:** ran in `2026-09-scale-diagnostics` as adapter mode `ls` (fixtures to 143M objects; section above). |
| `ls --raw` | Print one JSON `listObjectsV2` Contents element per line. | Attempted as a capability probe; blocked at auth. Its normalizer is validated only against synthetic fixtures. |
| `ls --long` | Print a human date, human-rounded size, and key per line (lossy). | Not re-run; blocked by inheritance from the `ls` receipts. Lossy, so not a verification mode. |
| `summarize` | Emit an aggregate report with no per-object records. | Attempted as a capability probe on a genuinely different subcommand; blocked at auth. |
| `compare` / `each` / `map` | Two-bucket diff and the library primitives underlying `ls`. | Not run; out of scope for a single anonymous smoke bucket. |

Only adapter mode `ls` ran in the release; the other modes have no release
rows. `cp`, `sync`, and `delete` are mutating and excluded by the study guardrails.
Detailed mode and source coverage is in
[`docs/mechanism.md`](docs/mechanism.md#modes-and-output-contracts), while the
blocked smoke coverage is in
[`docs/running.md`](docs/running.md#the-blocked-smoke-state--every-capability-receipt).

## What we learned

Each finding links its owning explanation and its canonical claim ID; claim IDs
resolve in [`data/claims.json`](data/claims.json).

- **s3p cannot make anonymous requests, and that blocks every listing mode.** The
  `S3Client` is built without any credentials or unsigned hook, so under
  credential-starved smoke the `ls`, `ls --raw`, and `summarize` probes all failed
  at credential resolution with exit 1 before any LIST. Timing s3p against a public
  bucket therefore needs supplied credentials.
  **Release update:** the release rows ran against the replay instrument, not a
  public bucket; each row records its argv, and the credential environment is
  not a release field.
  [`The blocked smoke state`](docs/running.md#the-blocked-smoke-state--every-capability-receipt)
  · `no-anonymous-access-path`, `anonymous-listing-blocked-at-auth`

- **Listing is a first-class, isolable operation.** `ls` and `summarize` are
  standalone, non-mutating, list-only subcommands, and the `ls` probe issued real
  `listObjectsV2` calls before the auth failure. This corrects the inherited page's
  worry that listing might not be separately invokable.
  [`The core loop`](docs/mechanism.md#the-core-loop--s3comprehensionseach--eachrecursive)
  · `listing-is-isolable`

- **Parallelism is bought with a hard 95-character-set restriction.** Bisection
  needs a known alphabet, so a key with any character outside space `0x20` through
  `~` `0x7E` makes `getBisectKey` throw. This is a source-derived correctness
  boundary that contradicts an earlier secondhand "works on any keyspace" claim; it
  has not been exercised against a live edge-case bucket.
  [`Keyspace division`](docs/mechanism.md#keyspace-division--arithmetic-bisection-over-a-fixed-95-char-alphabet)
  · `non-ascii-key-throws`, `non-ascii-runtime-behavior`

- **The published throughput numbers are all author self-reports.** The ~20K and
  ~35K items/s figures and the conflicting 5-to-50-times and 15-to-100-times
  multipliers trace to the author with no third-party reproduction, and nothing has
  been benchmarked here.
  **Release update:** the release's rows for s3p (section above) are diagnostic
  and carry no throughput claim; no comparative numbers exist, and the scale
  rows are in the release section above.
  [`Concurrency`](docs/mechanism.md#concurrency--a-lifo-worker-pool-default-100)
  · `throughput-numbers-are-author-self-reported`, `throughput-numbers-reproduced`

- **The published v3.6.0 artifact cannot start, and git tags lag npm.** A clean
  install of the tagged v3.6.0 throws `Cannot find module 'colors'`; npm `latest`
  is 3.7.2, which has no corresponding git commit, so a reader trusting GitHub
  releases would pin a broken version.
  [`Version choice`](docs/running.md#image-study-authored)
  · `v3-6-0-cannot-start`, `git-tags-lag-npm`

## Limitations and open questions

### Coverage gaps

- Supply list-scoped credentials (or exclude s3p from anonymous-bucket runs and
  note why) so a real listing can be timed and verified.
  **Release update:** no live-S3 listing by s3p exists in the release; its rows
  are on replay fixtures (section above), and its 143M count matched the
  staged fixture count (`s3p.1b77f20ed931.s1`).
- Exercise the character-set boundary and UTF-16 ordering divergence with an
  edge-case bucket; `EDGE_BUCKET` was not configured.
- Exercise `compare`, `each`/`map`, and the library API memory path, which the CLI
  harness cannot reach.

### Harness and verifier blockers

- No mode produced a listing during groundwork, so the manifest verifier was
  never exercised; verification is blocked, not passed.
  **Release update:** the release rows report a row count checked against a
  staged fixture count, or the capture report's count where none was staged,
  not a key-by-key verification (section above).
- The `normalize.py` mode contracts are source-derived and validated only against
  synthetic adapter fixtures; canonical claim
  `normalize-validated-against-synthetic-fixtures` in [`data/claims.json`](data/claims.json).
- `ls --long` is lossy and cannot serve verification; use `ls --raw`.

### Benchmark questions

- Where does throughput plateau as `--list-concurrency` sweeps, given the single-core
  Node event loop?
  **Release update:** c16 and c100 arms exist on the FourCast and NARA fixtures
  (section above); the report states that this release does not establish CPU
  or width effects.
- How much LIST work is wasted per unique key, counted as HTTP requests rather than
  the logical `listRequests` counter?
- Does the reported ~100M-object OOM reproduce, and on which code path?
  **Release update:** the CLI `ls` path completed the 143M fixture
  (`s3p.1b77f20ed931.s1`). Its peak RSS was 335,352 KiB. The library API's
  accumulating path was not run in the release.
- Does s3p survive sustained throttling given it relies on the AWS SDK's default
  retries?

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand the bisection, concurrency, memory, output, and failure model | [`docs/mechanism.md`](docs/mechanism.md) |
| See how the image was built and exactly why every mode is blocked | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity, tested-subject, eligibility, and claim status data | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness, or read the synthetic QA fixtures | [`adapter/`](adapter/) and [`adapter/fixtures/`](adapter/fixtures/) |
| Build the local subject image | [`build/Dockerfile`](build/Dockerfile) |
| Audit how every old ledger row and status-bearing prose claim became atomic current claims | [`research/claims-migration.md`](research/claims-migration.md) and the preserved reconciliation in [`research/`](research/) |
| Read the historical pre-restructure landing page | [`research/tool-page.md`](research/tool-page.md) — frozen historical research, not the current entry point |
| Inspect the observations and immutable run records | [`receipts/`](receipts/) |

## Provenance

**Mixed provenance.** This page combines firsthand source-and-run groundwork —
the study-authored image, the three capability receipts, the corrected
license/alphabet/code-anchor facts, and the additive auth, version-channel, and
CLI-surface findings — with inherited secondhand notes compiled from blog posts,
GitHub issues, and source reading. The seed was **not a run record**. See the
frozen [`research/tool-page.md`](research/tool-page.md) and the row-by-row
[`research/reconciliation.md`](research/reconciliation.md).

## Evidence boundary

Source and documentation explain mechanisms and risks; only a committed receipt
confirms run-dependent study behavior. The one receipt-confirmed runtime fact
here is that the anonymous probes failed at credential resolution — not any
listing behavior. Smoke observations are not benchmark results, and no benchmark
has been run. Rows in `results/` are the public projection of the campaign
ledger, separate from the receipts here; neither is a benchmark result.
