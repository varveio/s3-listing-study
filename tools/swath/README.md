# Swath

[Swath](https://github.com/varveio/swath) lists an S3 bucket and emits the
listing as JSON Lines, TSV, fixed-width text, or a Parquet dataset, driven by a
work-stealing parallel scan in which idle workers steal and split key ranges so
many `ListObjectsV2` paginations run at once instead of one serial walk.
Swath is built by Varve, which also maintains this study — a conflict we disclose
and control for rather than treat as licence to relax the run-record rules; see
[Varve and Swath](#varve-and-swath).
This study's groundwork is complete; no benchmark comparison has been run.

## At a glance

The tested-subject facts are stated here; the canonical record is
[`data/tool.json`](data/tool.json).

| Question | Current answer |
| --- | --- |
| Tested subject | Upstream's own published image for `v0.3.1` — no fork, no patch, nothing built locally — pulled anonymously by digest, its `org.opencontainers.image.revision` label equal to the tested commit `7b9a5e2` on both architectures, self-reporting `swath 0.3.1` / `Commit: 7b9a5e2fba04`. The registry tag is `0.3.1`; `v0.3.1` is a 404. Canonical identity: [`data/tool.json`](data/tool.json). The capsule previously described v0.2.0; every claim was re-tested at v0.3.1 — [`research/v0.3.1/report.md`](research/v0.3.1/report.md). |
| Exercised coverage | On v0.3.1: all eight adapter modes — TSV, JSONL and table streams, `seed.mode=none`, plain and zstd TSV directory datasets, direct and sorted Parquet datasets — round-tripped anonymously over the 2,549-key `normals-hourly/` prefix on amd64, each normalizing to the same key set. On v0.2.0: two instrumented `jsonl` observations, one full-bucket, both native arm64. No credentialed, edge-key, crash, resume, discard-sink or high-rate run. |
| Correctness and verifier state | **No verifier verdict exists for any run**, and **no completeness check was performed**: count-and-uniqueness against the registry's recorded figures, plus cross-mode agreement on v0.3.1, is the only cross-check, and it cannot detect a substituted key or compensating errors — claim `smoke-output-count-and-uniqueness`, with the reasons in [`docs/running.md`](docs/running.md#what-the-verifier-could-not-check). |
| Receipts | Diagnostic attempt receipts exist from 2026-08-10 (Swath 0.2.2 and 0.2.4, `recursive-tsv`, exit 0, clean secret scan, 2,549 rows), but none has a verifier verdict or confirms a claim; the v0.3.1 round-trip is an observation, not a receipt. Evidence boundary: [`docs/running.md`](docs/running.md#diagnostic-attempt-receipts-but-no-verifier-verdict). |
| v0.2.0 smoke observation | The full-bucket observation exited 0 having emitted 148,917 JSON Lines rows with zero duplicate keys, and reported eight concurrent listings in flight — claims `smoke-output-count-and-uniqueness`, `full-run-reported-parallel-listings`. A single unreplicated groundwork run on the previous subject, counted by the tool itself: not a benchmark result and not comparable to anything. |
| Results | No benchmark or comparative result exists. |

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
| `list --format jsonl \| tsv \| table` | Fully enumerate a bucket to a text stream. | All three round-tripped on v0.3.1 over the prefix; `jsonl` also in two direct v0.2.0 observations and `tsv` in a 0.2.4 diagnostic attempt. No verifier verdict on any. |
| `list --tune seed.mode=shallow \| none` | Change whether an up-front `delimiter=/` descent runs at all — a request-pattern change, not an output change. | Both round-tripped on v0.3.1; the run counters were not compared, so the cost arms remain uncompared. |
| `list --tune seed.mode=hints` | Declared hinted seeding. | Not run; it still throws at seed time, so there is no hinted mode. |
| `list --format tsv \| jsonl -o <dir>`, `--compression` | Partitioned, optionally compressed text directory datasets with a manifest and `_SUCCESS` (0.3.0). | Plain and zstd TSV datasets round-tripped on v0.3.1; JSONL datasets and gzip not run. Non-resumable by construction. |
| `list --format parquet` / `--sort` | Write a multi-part, optionally globally key-sorted Parquet dataset to a directory. | Both round-tripped on v0.3.1 over the prefix through the adapter's native-sink route; no benchmark attempt through the worker yet. Parquet is Swath's byte-exact output path, now typed `STRING` and UTF-8-only. |
| `list --format discard` | Run the listing engine with no output, to separate listing cost from output cost (0.3.0). | Not run; not a declared adapter mode. |
| `swath resume <dir>` | Resume a crashed managed-Parquet listing from a SQLite checkpoint. | Not run; it needs a durable checkpoint, which needs a Parquet directory dataset. |

Swath has no shallow `ls`-style output mode and no `inspect` or `diff`
subcommand. Mechanism detail is in [`docs/mechanism.md`](docs/mechanism.md);
mode-by-mode coverage and its blockers are in
[`docs/running.md`](docs/running.md).

## What we learned

Each finding links its owning explanation and its canonical claim IDs; claim IDs
resolve in [`data/claims.json`](data/claims.json).

- **`--concurrency N` is a ceiling, not a setpoint.** A run starts at
  `min(4, N)` permits and the store, not the flag, sets the steady-state level,
  so a benchmark that reads the flag as "N concurrent requests" will be wrong and
  must instrument effective concurrency separately.
  [`Concurrency, AIMD, and flow control`](docs/mechanism.md#concurrency-aimd-and-flow-control)
  · `concurrency-flag-is-aimd-ceiling`

- **v0.2.0's engine change is two default flips, and the old one was
  structurally blind.** Rate-anchored sensing and a floored tail reach are both
  on by default now; the previous tail-floor reading multiplied any estimate to
  exactly zero once its reach term went non-positive. The documented pre-0.2.0
  rollback pair is the one supported non-default engine configuration.
  [`Engine defaults and the one supported rollback`](docs/mechanism.md#engine-defaults-and-the-one-supported-rollback)
  · `v020-engine-default-flips`, `engine-toggles-are-diagnostic`

- **No claim is receipt-backed and no verifier ran.** Diagnostic attempt
  receipts exist, but carry no verifier verdict and cannot confirm a claim; the
  v0.3.1 round-trip and the v0.2.0 runs are observations. They support only the
  facts their committed commands and payload samples expose. The absent
  completeness check and the bucket's drift are stated once, in the linked
  section.
  [`What the verifier could not check`](docs/running.md#what-the-verifier-could-not-check)
  · `smoke-output-count-and-uniqueness`, `aimd-idle-at-smoke`

- **Since 0.3.0 Swath parses S3's response and decodes keys itself.** A
  Swath-owned interceptor streams the `ListObjectsV2` XML and percent-decodes
  keys with the same `URLDecoder` call the SDK used, so the v0.2.0 finding that
  it performed no decoding of its own is reversed and recorded as contradicted
  under its ID; the two encoding hazards below moved with it into Swath's own
  tree. Text sinks also now print timestamps as S3 spells them, with a
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
  ships.** Fifteen drift items were consolidated at v0.2.0, three of which state
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

- The v0.3.1 evidence is one prefix in single runs per mode; the only
  full-bucket observations are the two v0.2.0 `jsonl` runs. No credentialed,
  edge-key, crash, resume, or high-rate run on either subject — claims
  `control-char-key-fidelity-untested`, `crash-resume-works`,
  `exactly-once-under-crash`.
- The v0.3.1 round-trip and the diagnostic attempts ran amd64; both v0.2.0
  observations were native arm64. amd64 is supported across every publishing
  channel, and upstream's workflows still do not runtime-smoke arm64 because
  their runners are amd64 — claims `amd64-built-and-smoked-upstream`,
  `arm64-not-runtime-smoked-at-v020`.

### Harness and verifier blockers

- Diagnostic attempt receipts exist, but no run has a verifier verdict and no
  claim is `confirmed`; see
  [`docs/running.md`](docs/running.md#diagnostic-attempt-receipts-but-no-verifier-verdict),
  which owns that caveat.
- The retired wrapper could not capture file sinks; the current adapter
  directs Parquet and text datasets into the benchmark worker's native sink,
  and that shape was exercised outside the worker in the v0.3.1 round-trip.
  No benchmark attempt has yet run it through the worker — claims
  `file-sinks-not-harness-capturable`, `parquet-key-column-is-byte-exact`.
- The committed adapter was validated by execution against v0.3.1, which
  also caught and fixed a normalizer defect in the aligned-table mode. A
  claim-confirming run still needs the required execution profile and the
  reference/verifier path — see
  [`docs/running.md`](docs/running.md#adapter-and-harness-contract).

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
  `upstream-is-young-and-solo-maintained` — and nine releases in five weeks,
  claim `upstream-publishes-tagged-releases`. Upstream's nightly
  deep-verification workflow failed on every visible run at the 2026-08-02
  research date and was intermittent at the update, five of the last eight runs
  failing — claim `nightly-deep-verification-failing`. All three are read from
  the GitHub APIs on one day and move with time. The code is careful and the
  javadoc has not caught up, which is claim `docs-and-javadoc-drift`.

### Benchmark questions

- The `--concurrency` sweep above 8 with effective concurrency instrumented, the
  `seed.mode=none` versus `shallow` arm (the cleanest experiment Swath offers),
  the documented pre-0.2.0 rollback A/B, probe overhead versus scale, AIMD
  necessity under real throttling, memory at scale, Parquet fidelity and cost,
  and crash-resume under SIGKILL. All currently `unverified`; the full list with
  reasons is in [`docs/running.md`](docs/running.md#deferred-coverage).
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
| See how each claim fared when re-tested at v0.3.1, and the installed-help diff | [`research/v0.3.1/report.md`](research/v0.3.1/report.md) |
| Read the independent blind derivation at v0.2.0, its errata, and its cross-model review | [`research/`](research/) |
| Audit an individual claim's evidence in depth | [`data/claims.json`](data/claims.json), then the owning `research/v0.3.1/reader-*.md` or `research/reader-*.md` |
| Inspect the committed observations | [`receipts/`](receipts/) |

## Provenance

**Firsthand, two subjects in sequence.** The foundation is a source-first
derivation of v0.2.0 against the pinned commit `cef8ec2` — five readers over a
frozen worktree, deliberately blind to any existing capsule prose —
consolidated, then re-checked by an independent cross-model review whose
findings were all re-verified against source before being accepted. It is
recorded in [`research/`](research/), with known defects in that record listed
in [`research/ERRATA.md`](research/ERRATA.md). When upstream released 0.3.1 the
ledger was re-tested claim by claim against the frozen `7b9a5e2` tree by four
readers over the same disjoint areas, every anchor was re-checked mechanically,
and the changes were folded in under the existing claim IDs: one claim
reversed, twenty-nine were revised, seventeen were added. That update was
diff-driven rather than blind, a stated deviation from the study's
re-derivation rule, argued in
[`research/v0.3.1/report.md`](research/v0.3.1/report.md).

This page has **a single layer.** The capsule carries no migration stratum — no
frozen pre-restructure page, no conservation map, and no claim in
[`data/claims.json`](data/claims.json) carries a legacy origin. Every claim
states the v0.3.1 subject on its own evidence, and says so where its evidence
is a v0.2.0 observation.

The v0.2.0 runtime observations and the v0.3.1 adapter round-trip are the
study's own, and none is **a run record** in the harness sense; source reading
is not a run record either. The 2026-08-10 diagnostic attempt receipts are a
separate evidence layer and carry no verifier verdict — see
[`docs/running.md`](docs/running.md#diagnostic-attempt-receipts-but-no-verifier-verdict).

## Evidence boundary

Source and documentation can establish a mechanism or a risk; they cannot
establish that a run behaved as designed. No receipt here carries the verifier
verdict needed to support `confirmed`; the diagnostic attempts do not change
that boundary. The runtime facts here are direct container observations —
Swath's own self-reported counters and the rows it emitted — from single
unreplicated runs: two instrumented v0.2.0 runs and an eight-mode v0.3.1
round-trip over one prefix. They and the diagnostic attempts are groundwork,
not benchmark results, and no number here is comparative.

## Varve and Swath

Varve builds Swath and maintains this study. Before building Swath, Varve studied
how existing listing tools approached the problem, and that work informed Swath's
design; we also know Swath's tuning envelope more deeply than we know the other
tools, which makes us participants in the space we are studying. We apply the
same harness, buckets, and run-record requirements to Swath as to every other
tool, and publish the results on the same terms whether or not they favour it —
which is why this page states plainly that neither the v0.2.0 pass nor the
v0.3.1 update produced a receipt or a verifier verdict, and that the diagnostic
receipts do not confirm its claims. Swath's earlier internal benchmark history is
**not** used here; any number must be produced again on this harness. The
structural control on the first-party source basis is that the v0.2.0 derivation
was source-first and deliberately blind, every claim is anchored, an
independent cross-model review re-verified the anchors, and the v0.3.1 update
re-tested every claim rather than carrying it forward — claim
`first-party-source-basis`. We welcome help from people who know the
other tools better; the run records are published so readers can inspect and
improve the setup.
