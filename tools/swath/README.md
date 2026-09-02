# Swath

[Swath](https://github.com/varveio/swath) lists an S3 bucket and emits the
listing as JSON Lines, TSV, fixed-width text, or a Parquet dataset, driven by a
work-stealing parallel scan in which idle workers steal and split key ranges so
many `ListObjectsV2` paginations run at once instead of one serial walk.
Swath is built by Varve, which also maintains this study — a conflict we disclose
and control for rather than treat as licence to relax the run-record rules; see
[Varve and Swath](#varve-and-swath).

> **Study status (2026-09-scale-diagnostics).** This tool's standing in the current release:
> Completed the 143M-object fixture with a count that matched it, in 173.7 s at c256 (`swath.be4140354dd1.s1`); every replay row on the small-directory fixtures failed the timing gate because of the instrument's structure-probe defect.
> The release is diagnostic: no attempt in it carries `purpose = measurement`, so
> nothing here is a calibrated benchmark or a ranking. Report and data:
> [`results/2026-09-scale-diagnostics/REPORT.md`](../../results/2026-09-scale-diagnostics/REPORT.md).

## In the current release

The release `2026-09-scale-diagnostics` is diagnostic: it settles what ran,
what each run returned, and how much memory it used; no row in it is a
calibrated measurement, and nothing here is a ranking. Swath ran as versions
`0.2.5-SNAPSHOT` (a pre-release build, the report's documented exception to
the released-version rule), `0.3.0` and `0.3.1`, in adapter modes
`recursive-tsv` (`list --format tsv` to stdout), `recursive-tsv-dataset`
(`list --format tsv -o <dir>`), `recursive-parquet` (`list --format
parquet`), and, on live S3 only, `recursive-parquet-sorted` (`list --format
parquet --sort`) and `recursive-tsv-zstd` (the TSV dataset with
`--compression zstd`); the `--concurrency` of each arm is in its row. The
groundwork subject further down this page is v0.3.1, the same build as the
release's `0.3.1` rows; the earlier release rows are builds the groundwork
does not describe.

| fixture | attempts | outcomes | timing grades of completed rows | row cited in the report |
| --- | ---: | --- | --- | --- |
| FourCast 4.08M | 39 | SUCCEEDED 39 | NOT_APPLICABLE 10, INSUFFICIENT_EVIDENCE 20, CAPACITY_FAILED 8, PRESSURE_DEGRADED 1 | `swath.88891b1437de.s1` (the roster figure's `0.2.5-SNAPSHOT` row) |
| NARA 13.5M | 17 | SUCCEEDED 17 | CAPACITY_FAILED 17 | none cited |
| real-changesets 13.9M (flat) | 1 | SUCCEEDED 1 | INSUFFICIENT_EVIDENCE 1 | `swath.666e471aac76.s1` |
| idc-open-data 56.3M (no latency treatment) | 13 | SUCCEEDED 12, FAILED 1 | NOT_APPLICABLE 12 | none cited |
| NBM 66.4M | 3 | SUCCEEDED 3 | CAPACITY_FAILED 3 | `swath.1a8598593dbb.s1` (the NBM figure's `0.2.5-SNAPSHOT` row) |
| blockchain 143M | 11 | SUCCEEDED 11 | CAPACITY_FAILED 11 | `swath.be4140354dd1.s1` |
| live S3 `real-changesets` | 1 | SUCCEEDED 1 | not graded: live S3, no replay instrument | `swath.85ccd37c1b88.s1` |
| live S3 `fah-public-data-covid19-absolute-free-energy` | 4 | SUCCEEDED 3, FAILED 1 | not graded: live S3, no replay instrument | `swath.d7668a13dc4c.s1`, `swath.4b2db6e9f6a9.s1`, `swath.a0f1b2c053d8.s1` |
| live S3 `janelia-cosem-datasets` | 3 | SUCCEEDED 2, FAILED 1 | not graded: live S3, no replay instrument | `swath.0b5db9b35947.s1`, `swath.0d01af45ef74.s1` |
| live S3 `sentinel-cogs` | 3 | SUCCEEDED 2, FAILED 1 | not graded: live S3, no replay instrument | `swath.6b1ffae260c0.s1`, `swath.7b028bd8c692.s1` |

The live-S3 rows carry the caveats the report gives them: each is a single,
uncontrolled run of one tool against a public bucket not under our control,
with machine type, output mode and in three cases Swath version differing
between rows, and nothing repeated. No reference manifest exists for a live
bucket, so each count is the integer the run returned, not a verified count;
the two `sentinel-cogs` counts differ because the bucket changed between
runs. The wall in those rows is the whole process from start to exit,
including writing the output to disk, not the listing phase alone; the
report's 5 m 12 s listing milestone for the billion-object run comes from the
private run summary, which is not in this release, and is not a release
field. The three live rows that failed settled with `MISSING_EVIDENCE` and no
outcome fields.

Largest fixture attempted: 143M, which is the largest replay fixture in the
release, so no larger one was available to schedule. The report's "largest
fixture attempted" table records that the c256 arm completed with a count
that matched the staged fixture, and that every Swath row on that fixture
failed the timing gate. The live-S3 rows go beyond that object count but are
not fixtures and are not comparable to any replay row.

Two rows of the report's "Where the setup was not equal" table name Swath, and
its instrument section adds two more conditions; subject allocations were
equal across the blockchain rows.

| asymmetry | detail | attempts |
| --- | --- | --- |
| replay server size on blockchain | Swath's blockchain rows ran against replay servers of 20, 32 and 64 vCPU on hosts of 32, 48 and 80 vCPU; every other tool's blockchain row had a 20-vCPU server on a 32-vCPU host. Two `0.2.5-SNAPSHOT` pairs differ only in server size (c256: 209.7 s on 32 vCPU against 238.4 s on 20; c128: 206.2 s against 217.4 s). The `0.3.1` row on the 64-vCPU server is a different build, so nothing separates build from server size for it. All are `CAPACITY_FAILED`. | `swath.be4140354dd1.s1` (64, `0.3.1`); `swath.9a948772a8d5.s1` (32) and `swath.bfbcc490799b.s1` (20); `swath.67ef93751978.s1` (32) and `swath.c59f4b2e8eed.s1` (20) |
| pre-release build in two figures | the Swath row in the FourCast roster figure and in the NBM same-allocation figure is `0.2.5-SNAPSHOT`; the dispositions row is `0.3.1` | `swath.88891b1437de.s1`, `swath.1a8598593dbb.s1` |
| the instrument defect (from the report's instrument section) | the replay server missed its `structure_probe` deadline under page load on the fixtures with many small directories (NARA, NBM, blockchain); Swath issues that request shape at volume, so every Swath row on those fixtures is `CAPACITY_FAILED`. A warmed-server control changed nothing, and is exported as its own case. | `swath.be4140354dd1.s1` (cold), `swath.6d4bdaf4f615.s1` (warmed) |
| shared host (from the report's instrument section) | the replay server and the subject share one VM and one boot disk, and no disk metric is exported; the report notes that subjects writing Parquet or compressed TSV, which the Swath dataset modes do, compete more with the server than subjects writing text | every replay row |

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
original result files and logs are private. The round-trip observation under
`receipts/` in this directory is groundwork evidence and does not cover the
release rows.

## At a glance

Groundwork subject: the pinned v0.3.1 image, the eight-mode adapter
round-trip and the source study. The current release's rows are in the
section above.

The tested-subject facts are stated here; the canonical record is
[`data/tool.json`](data/tool.json).

| Question | Current answer |
| --- | --- |
| Tested subject | Upstream's own published image for `v0.3.1` — no fork, no patch, nothing built locally — pulled anonymously by digest, its `org.opencontainers.image.revision` label equal to the tested commit `7b9a5e2` on both architectures, self-reporting `swath 0.3.1` / `Commit: 7b9a5e2fba04`. The registry tag is `0.3.1`; `v0.3.1` is a 404. Canonical identity: [`data/tool.json`](data/tool.json). The release's `0.3.1` rows are this build; its `0.2.5-SNAPSHOT` and `0.3.0` rows are earlier builds (section above). |
| Exercised coverage | During groundwork: all eight adapter modes — TSV, JSONL and table streams, `seed.mode=none`, plain and zstd TSV directory datasets, direct and sorted Parquet datasets — round-tripped anonymously over the 2,549-key `normals-hourly/` prefix on amd64, each exiting 0 and normalizing to the same key set — claim `round-trip-count-and-cross-mode-agreement`. No full-bucket, credentialed, edge-key, crash, resume, discard-sink, arm64 or high-rate run during groundwork. The release ran whole fixtures to 143M objects and live buckets to 1.07B rows at `--concurrency` up to 2048 (section above); the credentialed, edge-key, crash, resume, discard-sink and arm64 gaps stand in both layers. |
| Correctness and verifier state | Groundwork: **no verifier verdict exists for any run**, and **no completeness check was performed**: count against the registry's recorded figure plus cross-mode agreement is the only cross-check, and it cannot detect a substituted key or compensating errors — reasons in [`docs/running.md`](docs/running.md#what-the-verifier-could-not-check). Release: the rows check the row count against a staged fixture count where one exists, and against the capture report's count in working notes where none was staged, never key by key; the live-S3 counts have no reference at all (section above). |
| Receipts | Groundwork: none. The round-trip is a direct `docker run` observation on the maintainer's workstation, not a harness run record; nothing confirms a claim. Evidence boundary: [`docs/running.md`](docs/running.md#no-receipts-and-no-verifier-verdict). The release rows are a separate layer and are not receipts in this directory (section above). |
| Results | No calibrated benchmark or comparative result exists in this study. The current release's rows for this tool (section above) are diagnostic; the round-trip figures in this table describe single groundwork runs. |

## How it works

`swath list` drives one work-stealing engine: the keyspace is divided into
half-open byte ranges, a worker with nothing to do steals and splits a busy
peer's range at a synthesized pivot key, and each worker paginates its range with
`start_after` rather than a continuation token. A serial `delimiter=/` seed
descent creates the first ranges; `--concurrency` is an AIMD ceiling, not a
setpoint, and the store sets the steady-state level. Output streams straight
through the formatter with no per-run accumulation, to stdout text or a Parquet
dataset; since 0.3.0 the text formats can also be gzip- or zstd-compressed or
written as partitioned directory datasets, and a discard sink runs the engine
with no output at all. Full account: [`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

Upstream's mode surface and what this study exercised are separate.

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| `list --format jsonl \| tsv \| table` | Fully enumerate a bucket to a text stream. | Groundwork: all three round-tripped over the prefix, exit 0. No verifier verdict on any. **Release:** ran in `2026-09-scale-diagnostics` as adapter mode `recursive-tsv` (`--format tsv` to stdout) on the 4.08M fixture (section above); `jsonl` and `table` have no release rows. |
| `list --tune seed.mode=shallow \| none` | Change whether an up-front `delimiter=/` descent runs at all — a request-pattern change, not an output change. | Groundwork: both round-tripped; the run counters were not compared, so the cost arms remain uncompared. **Release:** no row passes a `seed.mode` tune (each row's `invocation.argv` is in the release rows), so the release did not vary the seed mode and the arms remain uncompared. |
| `list --tune seed.mode=hints` | Declared hinted seeding. | Not run in either layer; it still throws at seed time, so there is no hinted mode. |
| `list --format tsv \| jsonl -o <dir>`, `--compression` | Partitioned, optionally compressed text directory datasets with a manifest and `_SUCCESS` (0.3.0). | Groundwork: plain and zstd TSV datasets round-tripped; JSONL datasets and gzip not run. Non-resumable by construction. **Release:** ran as adapter mode `recursive-tsv-dataset` on fixtures to 143M objects and as `recursive-tsv-zstd` on live S3 (section above). |
| `list --format parquet` / `--sort` | Write a multi-part, optionally globally key-sorted Parquet dataset to a directory. | Groundwork: both round-tripped over the prefix through the adapter's native-sink route; no groundwork attempt through the worker. Parquet is Swath's byte-exact output path, now typed `STRING` and UTF-8-only. **Release:** ran through the worker as adapter mode `recursive-parquet` on fixtures to 56.3M objects, and as `recursive-parquet-sorted` on live S3 only (section above). The release checks row counts, not Parquet byte fidelity. |
| `list --format discard` | Run the listing engine with no output, to separate listing cost from output cost (0.3.0). | Not run in either layer; not a declared adapter mode. |
| `swath resume <dir>` | Resume a crashed managed-Parquet listing from a SQLite checkpoint. | Not run in either layer; it needs a durable checkpoint, which needs a Parquet directory dataset. |

Swath has no shallow `ls`-style output mode and no `inspect` or `diff`
subcommand. Mechanism detail is in [`docs/mechanism.md`](docs/mechanism.md);
mode-by-mode coverage and its blockers are in
[`docs/running.md`](docs/running.md).

## What we learned

Each finding links its owning explanation and its canonical claim IDs; claim IDs
resolve in [`data/claims.json`](data/claims.json).

- **`--concurrency N` is a ceiling, not a setpoint.** A run starts at
  `min(4, N)` permits and the store, not the flag, sets the steady-state level,
  and the controller never searches the level back down for efficiency, so a
  benchmark that reads the flag as "N concurrent requests" will be wrong and
  must instrument effective concurrency separately. The v0.2.0 engine-default
  flips and their one supported rollback are explained on the mechanism page.
  [`Concurrency, AIMD, and flow control`](docs/mechanism.md#concurrency-aimd-and-flow-control)
  · `concurrency-flag-is-aimd-ceiling`, `aimd-does-not-search-down`,
  `v020-engine-default-flips`, `engine-toggles-are-diagnostic`

- **No claim is receipt-backed and no verifier ran.** This is the groundwork
  layer. The only runtime evidence there is the eight-mode round-trip, an
  observation that supports only the facts its committed commands and payload
  hashes expose: every mode parses, completes, and agrees with the others on
  one prefix. The absent completeness check and the bucket's drift are stated
  once, in the linked section. **Release update:** release rows exist for
  `0.2.5-SNAPSHOT`, `0.3.0` and `0.3.1` (section above); they are a separate
  evidence layer whose check is a row count against a staged or capture
  count, not a verifier verdict, and they confirm no claim.
  [`What the verifier could not check`](docs/running.md#what-the-verifier-could-not-check)
  · `round-trip-count-and-cross-mode-agreement`, `aimd-necessity`

- **Since 0.3.0 Swath parses S3's response and decodes keys itself.** A
  Swath-owned interceptor streams the `ListObjectsV2` XML and percent-decodes
  keys with the same `URLDecoder` call the SDK used, so the earlier finding
  that it performed no decoding of its own is reversed and recorded as
  contradicted under its ID; the two encoding hazards below moved with it into
  Swath's own tree. Text sinks also now print timestamps as S3 spells them, with a
  millisecond fraction, and the Parquet key column is annotated `STRING` and
  refuses non-UTF-8 keys.
  [`Key fidelity and the encoding contract`](docs/mechanism.md#key-fidelity-and-the-encoding-contract)
  · `encoding-type-url-no-local-decode`, `listobjects-response-streamed-by-swath-interceptor`,
  `last-modified-text-is-endpoint-spelling`, `parquet-key-is-string-annotated-utf8-only`

- **Several knobs a cross-tool comparison would reach for do not exist.** Page
  size is a hard-coded 1000 with no `--max-keys`, so it is not sweepable without
  patching source; there is no `--delimiter` or `--recursive`; the owner-split
  kill switch has no flag spelling and sits behind an `--engine-toggle` option
  that 0.3.0 hid from the help; and versioned listing is dead code.
  [`Absences, dead code, and documentation drift`](docs/mechanism.md#absences-dead-code-and-documentation-drift)
  · `page-size-fixed-no-max-keys`, `no-shallow-listing-mode`,
  `no-owner-split-flag-absent`, `versions-listing-is-dead-code`

- **The tool's javadoc and older prose are not a reliable statement of what
  ships.** Fifteen drift items were consolidated at 0.2.0, three of which state
  engine defaults backwards, one of which has a correctness consequence in the
  encoding-decode contract, and two of which are live error messages telling a
  user to pass flags that do not exist; the re-checked items, both error
  messages included, persist at v0.3.1. The reference tables, golden help
  captures, the code, and 0.3.0's test-enforced supported-surface page are
  reliable.
  [`Absences, dead code, and documentation drift`](docs/mechanism.md#absences-dead-code-and-documentation-drift)
  · `docs-and-javadoc-drift`, `live-error-messages-name-absent-flags`

## Limitations and open questions

### Coverage gaps

- The groundwork runtime evidence is one prefix in single runs per mode; no
  full-bucket, credentialed, edge-key, crash, resume, or high-rate run during
  groundwork — claims `control-char-key-fidelity-untested`,
  `crash-resume-works`, `exactly-once-under-crash`. **Release update:** the
  release ran whole fixtures to 143M objects and live buckets to 1.07B rows
  at `--concurrency` up to 2048 (section above); the credentialed, edge-key,
  crash and resume gaps stand in both layers.
- The round-trip ran amd64. amd64 is supported across every publishing
  channel, and upstream's workflows still do not runtime-smoke arm64 because
  their runners are amd64; this study holds no arm64 observation of v0.3.1 —
  claims `amd64-built-and-smoked-upstream`, `arm64-not-runtime-smoked-at-v020`.
  **Release update:** the release rows ran on `n4-highcpu` machines (the
  `machine.type` field in the rows), so they add no arm64 observation.

### Harness and verifier blockers

- No run has a verifier verdict and no claim is `confirmed`; see
  [`docs/running.md`](docs/running.md#no-receipts-and-no-verifier-verdict),
  which owns that caveat. **Release update:** release rows exist on three
  versions (section above); none carries a verifier verdict, so no claim moves
  to `confirmed`.
- The adapter directs Parquet and text datasets into the benchmark worker's
  native sink, and that shape was exercised outside the worker in the
  round-trip. **Release update:** `recursive-parquet` rows through the worker
  exist on fixtures to 56.3M objects and `recursive-tsv-dataset` rows to 143M
  (section above); those rows check row counts, so the byte-fidelity claim is
  still unexercised — claims `file-sinks-not-harness-capturable`,
  `parquet-key-column-is-byte-exact`.
- The committed adapter was validated by execution, which also caught and
  fixed a normalizer defect in the aligned-table mode. A claim-confirming run
  still needs the required execution profile and the reference/verifier path —
  see [`docs/running.md`](docs/running.md#adapter-and-harness-contract).

### Tool findings and risks

- Swath never validates that an endpoint honoured `encoding-type=url`, and its
  own decode is gated on a case-sensitive echo. A nonconforming endpoint would
  produce a wrong answer with a clean exit and no warning. Conditional, not
  observed — claims `encoding-contract-not-validated`,
  `plus-to-space-conditional-hazard`.
- Pages are assumed to arrive in ascending byte order and never checked — claim
  `no-intra-page-ordering-check`. Since 0.3.0 a terminal page that repeats the
  cursor is fatal, which a replay server must honour — claim
  `forward-progress-guard-covers-terminal-page`.
- The project is young and solo: 39 days old at the 2026-09-02 update, created
  2026-07-25, with one human contributor — claim
  `upstream-is-young-and-solo-maintained` — and eight releases in five weeks,
  claim `upstream-publishes-tagged-releases`. Upstream's nightly
  deep-verification workflow was intermittent at the 2026-09-02 reading, five
  of the last eight runs failing — claim `nightly-deep-verification-failing`. All three are read from
  the GitHub APIs on one day and move with time. The code is careful and the
  javadoc has not caught up, which is claim `docs-and-javadoc-drift`.

### Benchmark questions

- The `--concurrency` sweep above 8 with effective concurrency instrumented, the
  `seed.mode=none` versus `shallow` arm (the cleanest experiment Swath offers),
  the documented pre-0.2.0 rollback A/B, probe overhead versus scale, AIMD
  necessity under real throttling, memory at scale, Parquet fidelity and cost,
  and crash-resume under SIGKILL. All currently `unverified`; the full list with
  reasons is in [`docs/running.md`](docs/running.md#deferred-coverage).
  **Release update:** no comparative numbers exist; the scale rows are in the
  release section above and are diagnostic. Concurrency arms from c16 to c256
  exist on replay fixtures, but the report states that single-run arms under a
  pressured instrument are not evidence of a knee, and effective concurrency was
  not instrumented. On memory at scale, `swath.be4140354dd1.s1` peaked at
  2,232,392 KiB on the 143M fixture. On live S3, `swath.0d01af45ef74.s1`
  (sorted Parquet) peaked at 13,703,164 KiB. Parquet fidelity, the seed-mode
  arm, the rollback A/B, AIMD under real throttling and crash-resume remain
  `unverified`.
  Comparisons against other listers are not Swath's to hold: they live in
  [`docs/open-questions.md`](../../docs/open-questions.md).

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand the engine, ranges, seeding, output, resume, and failure surface | [`docs/mechanism.md`](docs/mechanism.md) |
| See what image was selected, what ran, what was blocked, and how to reproduce it | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity, study states, and the full claim ledger | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness | [`adapter/`](adapter/) |
| See which subject image the derived attempt image is built from | [`build/`](build/) |
| See how the ledger was derived, the installed-help diff against 0.2.0, and the cross-model review | [`research/`](research/) |
| Audit an individual claim's evidence in depth | [`data/claims.json`](data/claims.json), then the owning `research/reader-*.md` |
| Inspect the committed round-trip observation | [`receipts/`](receipts/) |

## Provenance

**Firsthand.** Every claim resolves to source lines at the pinned commit
`7b9a5e2`, to upstream's own documentation, or to the study's one committed
observation. The ledger descends from a blind, source-first derivation of the
previous release, v0.2.0 — five readers over disjoint file sets with no access
to any existing capsule prose, an integrator, and an independent cross-model
review — which was then re-tested claim by claim against the frozen v0.3.1
tree by four readers over the same areas, every anchor re-checked
mechanically, one claim reversed, twenty-nine revised, seventeen added, and
the result reviewed again by a different model family. That update was
diff-driven rather than blind, a stated deviation from the study's
re-derivation rule argued in [`research/report.md`](research/report.md). The
v0.2.0 layer itself — its reports, run observations and diagnostic receipts —
was retired when the capsule became 0.3.1-only, before publication;
[`research/README.md`](research/README.md) records what was retired, why, and
where it remains reachable.

This page has **a single layer.** The capsule carries no migration stratum — no
frozen pre-restructure page, no conservation map, and no claim in
[`data/claims.json`](data/claims.json) carries a legacy origin. Every claim
states the v0.3.1 subject on its own evidence.

The adapter round-trip is the study's own and is not **a run record** in the
harness sense; source reading is not a run record either — see
[`docs/running.md`](docs/running.md#no-receipts-and-no-verifier-verdict).
The release rows in `results/`, on `0.2.5-SNAPSHOT`, `0.3.0` and `0.3.1`, are
a separate evidence layer, described in
[In the current release](#in-the-current-release).

## Evidence boundary

Source and documentation can establish a mechanism or a risk; they cannot
establish that a run behaved as designed. No receipt here carries the verifier
verdict needed to support `confirmed`. The runtime facts here are direct
container observations — Swath's own self-reported counters and the rows it
emitted — from an eight-mode round-trip over one prefix, one run per mode.
They are groundwork, not benchmark results, and no number here is comparative.
Rows in `results/` are the public projection of the campaign ledger, separate
from the receipts here; neither is a benchmark result.

## Varve and Swath

Varve builds Swath and maintains this study. Before building Swath, Varve studied
how existing listing tools approached the problem, and that work informed Swath's
design; we also know Swath's tuning envelope more deeply than we know the other
tools, which makes us participants in the space we are studying. We apply the
same harness, buckets, and run-record requirements to Swath as to every other
tool, and publish the results on the same terms whether or not they favour it —
which is why this page states plainly that no run of Swath here has produced a
receipt or a verifier verdict, and why the release's instrument defect, which
slows Swath, is published with its rows. Swath's earlier internal benchmark
history is **not** used here; any number must be produced again on this
harness. The structural control on the first-party source basis is that the
original derivation was source-first and deliberately blind, every claim is
anchored, independent cross-model reviews re-verified the anchors at both
versions, and the v0.3.1 update re-tested every claim rather than carrying it
forward — claim `first-party-source-basis`. We welcome help from people who
know the other tools better; the run records are published so readers can
inspect and improve the setup.
