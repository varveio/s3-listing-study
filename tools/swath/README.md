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
| Tested subject | Upstream's own published image for `v0.2.0` — no fork, no patch, nothing built locally — pulled anonymously by digest, its `org.opencontainers.image.revision` label equal to the tested commit `cef8ec2`, self-reporting `swath 0.2.0 (cef8ec24a74f)`. The registry tag is `0.2.0`; `v0.2.0` is a 404. Run anonymously (`--no-sign-request`) at `--concurrency 8`, native arm64. Canonical identity: [`data/tool.json`](data/tool.json). |
| Exercised coverage | Two direct v0.2.0 `jsonl` observations, one on the full bucket and one prefix. A later diagnostic attempt ran v0.2.4 `recursive-tsv` over `normals-hourly/` on amd64. A separate four-mode adapter summary lacks the exact commands and raw normalized outputs needed to support a canonical runtime claim. No Parquet, sorted-Parquet or resume run; no credentialed, edge-key, crash, or high-concurrency run. |
| Correctness and verifier state | **No verifier verdict exists for any run**, and **no completeness check was performed**: count-and-uniqueness against the registry's recorded figures is the only cross-check, and it cannot detect a substituted key or compensating errors — claim `smoke-output-count-and-uniqueness`, with the reasons in [`docs/running.md`](docs/running.md#what-the-verifier-could-not-check). |
| Receipts | Diagnostic attempt receipts exist, but none has a verifier verdict or confirms a claim. Latest: Swath 0.2.4, anonymous amd64 `recursive-tsv` over `normals-hourly/`, exit 0, clean secret scan, 2,549 rows counted. Evidence boundary: [`docs/running.md`](docs/running.md#diagnostic-attempt-receipts-but-no-verifier-verdict). |
| v0.2.0 smoke observation | The full-bucket observation exited 0 having emitted 148,917 JSON Lines rows with zero duplicate keys, and reported eight concurrent listings in flight — claims `smoke-output-count-and-uniqueness`, `full-run-reported-parallel-listings`. A single unreplicated groundwork run, counted by the tool itself: not a benchmark result and not comparable to anything. |
| Results | No benchmark or comparative result exists. |

## How it works

`swath list` drives one work-stealing engine: the keyspace is divided into
half-open byte ranges, a worker with nothing to do steals and splits a busy
peer's range at a synthesized pivot key, and each worker paginates its range with
`start_after` rather than a continuation token. A serial `delimiter=/` seed
descent creates the first ranges; `--concurrency` is an AIMD ceiling, not a
setpoint, and the store sets the steady-state level. Output streams straight
through the formatter with no per-run accumulation, to stdout text or a Parquet
dataset. Full account: [`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

Upstream's mode surface and what this study exercised are separate.

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| `list --format jsonl \| tsv \| table` | Fully enumerate a bucket to a text stream. | `jsonl` in two direct v0.2.0 observations; `tsv` in a later v0.2.4 diagnostic attempt with no verifier verdict. A four-mode adapter summary is preserved but is not independently auditable and supports no canonical runtime claim. |
| `list --tune seed.mode=shallow \| none` | Change whether an up-front `delimiter=/` descent runs at all — a request-pattern change, not an output change. | `shallow` in the two direct observations. `none` appears only in the unauditable adapter summary, so the cost arms remain uncompared. |
| `list --tune seed.mode=hints` | Declared hinted seeding. | Not run; it throws at seed time, so there is no hinted mode. |
| `list --format parquet` / `--sort` | Write a multi-part, optionally globally key-sorted Parquet dataset to a directory. | Not run; the current driver writes `/tmp/swout`, which the minimal attempt contract does not publish. Parquet is also Swath's only byte-exact output path. |
| `swath resume <dir>` | Resume a crashed listing from a SQLite checkpoint. | Not run; it needs a durable checkpoint, which needs a directory dataset, which needs a mount. |

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

- **No claim is receipt-backed and no verifier ran.** Later diagnostic attempt
  receipts exist, but carry no verifier verdict and cannot confirm the canonical
  v0.2.0 claims. The retained observations support only the facts their
  committed commands and payload samples expose. The absent completeness check
  and the bucket's drift are stated once, in the linked section.
  [`What the verifier could not check`](docs/running.md#what-the-verifier-could-not-check)
  · `smoke-output-count-and-uniqueness`, `aimd-idle-at-smoke`

- **Several knobs a cross-tool comparison would reach for do not exist.** Page
  size is a hard-coded 1000 with no `--max-keys`, so it is not sweepable without
  patching source; there is no `--delimiter` or `--recursive`; the owner-split
  kill switch has no flag spelling; and versioned listing is dead code.
  [`Absences, dead code, and documentation drift`](docs/mechanism.md#absences-dead-code-and-documentation-drift)
  · `page-size-fixed-no-max-keys`, `no-shallow-listing-mode`,
  `no-owner-split-flag-absent`, `versions-listing-is-dead-code`

- **The tool's prose docs and javadoc are not a reliable statement of what
  ships.** Fifteen drift items were consolidated, three of which state engine
  defaults backwards, one of which has a correctness consequence in the
  encoding-decode contract, and two of which are live error messages telling a
  user to pass flags that do not exist. The reference tables, golden help
  captures and code are reliable.
  [`Absences, dead code, and documentation drift`](docs/mechanism.md#absences-dead-code-and-documentation-drift)
  · `docs-and-javadoc-drift`, `live-error-messages-name-absent-flags`

## Limitations and open questions

### Coverage gaps

- Only `jsonl` with the default shallow seed is supported by auditable direct
  observations, on one bucket in two unreplicated runs.
  No credentialed, edge-key, crash, resume, or high-concurrency run — claims
  `control-char-key-fidelity-untested`, `crash-resume-works`,
  `exactly-once-under-crash`.
- Both canonical v0.2.0 observations were native arm64. A later v0.2.4
  diagnostic attempt exercised amd64, but does not settle the v0.2.0 runtime
  claim. amd64 is supported across every publishing channel, including a real
  child manifest in the published index, and the v0.2.0 workflows do not
  runtime-smoke arm64 because their runners are amd64 —
  claims `amd64-built-and-smoked-upstream`, `arm64-not-runtime-smoked-at-v020`.

### Harness and verifier blockers

- Diagnostic attempt receipts exist, but no run has a verifier verdict and no
  claim is `confirmed`; see
  [`docs/running.md`](docs/running.md#diagnostic-attempt-receipts-but-no-verifier-verdict),
  which owns that caveat.
- The current driver sends Parquet probes to `/tmp/swout`, while the minimal
  attempt contract publishes only the two raw streams and `result.json`; those
  native outputs are therefore not currently preserved. This is a driver and
  publication limitation, not a tool one. Because Parquet is Swath's only byte-exact
  output path, leaving the gap open means never exercising it — claims
  `file-sinks-not-harness-capturable`, `parquet-key-column-is-byte-exact`.
- The committed adapter has been rewritten for v0.2.0, and `recursive-tsv` has
  executed through the current derived-image attempt path on v0.2.4. A
  claim-confirming run still needs the required execution profile and the
  reference/verifier path — see
  [`docs/running.md`](docs/running.md#adapter-and-harness-contract).

### Tool findings and risks

- Swath never validates that an endpoint honoured `encoding-type=url`, and the
  SDK's decode is gated on a case-sensitive echo. A nonconforming endpoint would
  produce a wrong answer with a clean exit and no warning. Conditional, not
  observed — claims `encoding-contract-not-validated`,
  `plus-to-space-conditional-hazard`.
- Pages are assumed to arrive in ascending byte order and never checked — claim
  `no-intra-page-ordering-check`.
- The project is new: eight days old at the 2026-08-02 research date, created
  2026-07-25, with a single contributor — claim
  `upstream-is-young-and-solo-maintained` — and two releases in six days, claim
  `upstream-publishes-tagged-releases`. Upstream's nightly deep-verification
  workflow had failed on every visible run at that date while pull-request and
  `main` CI were green — claim `nightly-deep-verification-failing`. All three are
  read from the GitHub APIs on one day and move with time. The code is careful
  and the prose has not caught up, which is claim `docs-and-javadoc-drift`.

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
| Read the independent blind re-derivation at v0.2.0, its errata, and its cross-model review | [`research/`](research/) |
| Audit an individual claim's evidence in depth | [`data/claims.json`](data/claims.json), then the owning `research/reader-*.md` |
| Inspect the committed observations | [`receipts/`](receipts/) |

## Provenance

**Firsthand, single-subject.** Everything current about v0.2.0 comes from a
source-first derivation against the pinned commit `cef8ec2` — five readers over
a frozen worktree, deliberately blind to any existing capsule prose —
consolidated, then re-checked by an independent cross-model review whose
findings were all re-verified against source before being accepted. It is
recorded in
[`research/`](research/), with known defects in that record listed in
[`research/ERRATA.md`](research/ERRATA.md).

This page has **a single layer.** The capsule carries no migration stratum — no
frozen pre-restructure page, no conservation map, and no claim in
[`data/claims.json`](data/claims.json) carries a legacy origin. Every claim
states the v0.2.0 subject on its own evidence.

The two canonical v0.2.0 runtime observations are the study's own, and neither
is **a run record** in the harness sense; source reading is not a run record
either. Later diagnostic attempt receipts, most recently v0.2.4 attempt 3, are a
separate evidence layer and carry no verifier verdict — see
[`docs/running.md`](docs/running.md#diagnostic-attempt-receipts-but-no-verifier-verdict).

## Evidence boundary

Source and documentation can establish a mechanism or a risk; they cannot
establish that a run behaved as designed. No receipt here carries the verifier
verdict needed to support `confirmed`; the later diagnostic attempts do not
change that boundary. The canonical runtime facts here are direct v0.2.0
container observations — Swath's own self-reported counters and the rows it
emitted — from single unreplicated runs, two of them instrumented. They and the
later v0.2.4 diagnostic attempt are groundwork, not benchmark results, and no
number here is comparative.

## Varve and Swath

Varve builds Swath and maintains this study. Before building Swath, Varve studied
how existing listing tools approached the problem, and that work informed Swath's
design; we also know Swath's tuning envelope more deeply than we know the other
tools, which makes us participants in the space we are studying. We apply the
same harness, buckets, and run-record requirements to Swath as to every other
tool, and publish the results on the same terms whether or not they favour it —
which is why this page states plainly that Swath's v0.2.0 pass produced no
wrapper-era receipt and no verifier verdict, while later diagnostic receipts do
not confirm its claims. Swath's earlier internal benchmark history is
**not** used here; any number must be produced again on this harness. The
structural control on the first-party source basis is that the v0.2.0 derivation
was source-first and deliberately blind, every claim is anchored, and an
independent cross-model review re-verified the anchors — claim
`first-party-source-basis`. We welcome help from people who know the
other tools better; the run records are published so readers can inspect and
improve the setup.
