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
| Exercised coverage | All three stdout formats (`jsonl`, `tsv`, `table`) and both reachable seed modes (`shallow`, `seed.mode=none`): `jsonl` on the full bucket and one prefix, the other three modes once each on that prefix through the rewritten v0.2.0 adapter, all exit 0 and all normalizing to the same key set — claims `adapter-v020-modes-execute`, `cross-mode-key-set-agreement`. No Parquet, sorted-Parquet or resume run; no credentialed, edge-key, crash, or high-concurrency run. |
| Correctness and verifier state | **No verifier verdict exists for any run**: the reference manifest was absent, so the shared verifier could not run. Completeness rests only on count-and-uniqueness against the registry's recorded figures — weaker than a manifest diff. Claim `smoke-output-complete-no-duplicates`. |
| Receipts | **None.** The mandatory runner-security profile was not provisioned, so `harness/smoke-run.sh` was never used. Every runtime fact here rests on direct container observations — two instrumented runs plus four adapter-mode runs — and no claim on this subject is `confirmed`. |
| Smoke observation | The full-bucket run exited 0 having emitted 148,917 JSON Lines rows with zero duplicate keys, and reported eight concurrent listings in flight — claims `smoke-output-complete-no-duplicates`, `full-run-reported-parallel-listings`. A single unreplicated groundwork run, counted by the tool itself: not a benchmark result and not comparable to anything. |
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
| `list --format jsonl \| tsv \| table` | Fully enumerate a bucket to a text stream. | All three, once each on one prefix through the adapter, plus `jsonl` on the full bucket. Unreceipted observations. |
| `list --tune seed.mode=shallow \| none` | Change whether an up-front `delimiter=/` descent runs at all — a request-pattern change, not an output change. | Both, as unreceipted observations: `shallow` as the default, `none` once on one prefix. Their key sets matched; no counters were captured, so the cost arms remain uncompared. |
| `list --tune seed.mode=hints` | Declared hinted seeding. | Not run; it throws at seed time, so there is no hinted mode. |
| `list --format parquet` / `--sort` | Write a multi-part, optionally globally key-sorted Parquet dataset to a directory. | Not run and not capturable: the harness bind-mounts nothing. Parquet is also Swath's only byte-exact output path. |
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

- **Nothing is receipt-backed and no verifier ran.** The runner-security profile
  was not provisioned, so the harness wrapper was never used; the manifest was
  absent, so no run has a verdict; and the registered bucket has drifted since
  its snapshot. Completeness rests on count-and-uniqueness alone. The strongest
  cross-check available was four listing modes normalizing to a byte-identical
  key set on one prefix — engine-and-adapter consistency, not agreement with
  ground truth, and four arms can agree while being wrong the same way.
  [`What the verifier could not check`](docs/running.md#what-the-verifier-could-not-check)
  · `smoke-output-complete-no-duplicates`, `aimd-idle-at-smoke`,
  `cross-mode-key-set-agreement`

- **Several knobs a cross-tool comparison would reach for do not exist.** Page
  size is a hard-coded 1000 with no `--max-keys`, so it is not sweepable without
  patching source; there is no `--delimiter` or `--recursive`; the owner-split
  kill switch has no flag spelling; and versioned listing is dead code.
  [`Absences, dead code, and documentation drift`](docs/mechanism.md#absences-dead-code-and-documentation-drift)
  · `page-size-fixed-no-max-keys`, `no-shallow-listing-mode`, `versions-listing-is-dead-code`

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

- Three output formats and two seed modes were exercised, on one bucket, in
  single unreplicated runs — the three adapter-driven modes on one prefix only.
  No credentialed, edge-key, crash, resume, or high-concurrency run — claims
  `control-char-key-fidelity-untested`, `crash-resume-works`,
  `exactly-once-under-crash`.
- Every run was native arm64. amd64 is supported across every publishing channel,
  including a real child manifest in the published index, but was not exercised
  here, and upstream never runtime-smokes arm64 because its runners are amd64 —
  claims `amd64-support-inferred`, `arm64-never-runtime-smoked-upstream`.

### Harness and verifier blockers

- No receipts and no verifier verdict for any run; see
  [`docs/running.md`](docs/running.md#no-receipts-the-runner-security-blocker),
  which owns that caveat.
- Only the three stdout text formats are capturable. Parquet, sorted Parquet and
  resume are structurally uncapturable under a harness that bind-mounts nothing —
  a harness limitation, not a tool one. Because Parquet is Swath's only byte-exact
  output path, leaving the gap open means never exercising it — claims
  `file-sinks-not-harness-capturable`, `parquet-key-column-is-byte-exact`.
- The committed adapter has been rewritten for v0.2.0 and its four stdout modes
  execute, so it is no longer a blocker; a harness run still waits on the
  runner-security profile and the reference manifest — see
  [`docs/running.md`](docs/running.md#adapter-and-harness-contract).

### Tool findings and risks

- Swath never validates that an endpoint honoured `encoding-type=url`, and the
  SDK's decode is gated on a case-sensitive echo. A nonconforming endpoint would
  produce a wrong answer with a clean exit and no warning. Conditional, not
  observed — claims `encoding-contract-not-validated`,
  `plus-to-space-conditional-hazard`.
- Pages are assumed to arrive in ascending byte order and never checked — claim
  `no-intra-page-ordering-check`.
- The project is new — created 2026-07-25, one contributor, two releases in six
  days — and upstream's nightly deep-verification workflow had failed on every
  visible run at the research date, while pull-request and `main` CI were green
  ([`research/report.md`](research/report.md) § 9.4). The code
  is careful and the prose has not caught up, which is claim
  `docs-and-javadoc-drift`.

### Benchmark questions

- The `--concurrency` sweep above 8 with effective concurrency instrumented, the
  `seed.mode=none` versus `shallow` arm (the cleanest experiment Swath offers),
  the documented pre-0.2.0 rollback A/B, probe overhead versus scale, AIMD
  necessity under real throttling, memory at scale, Parquet fidelity and cost,
  crash-resume under SIGKILL, and every cross-tool comparison. All currently
  `unverified`; the full list with reasons is in
  [`docs/running.md`](docs/running.md#deferred-coverage).

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand the engine, ranges, seeding, output, resume, and failure surface | [`docs/mechanism.md`](docs/mechanism.md) |
| See what image was selected, what ran, what was blocked, and how to reproduce it | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity, study states, and the full claim ledger | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness | [`adapter/`](adapter/) |
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

The two runtime observations are the study's own. Neither is **a run record** in
the harness sense: source reading is not a run record either, and nothing here
was produced by the shared wrapper — see [`docs/running.md`](docs/running.md#no-receipts-the-runner-security-blocker).

## Evidence boundary

Source and documentation can establish a mechanism or a risk; they cannot
establish that a run behaved as designed. A committed receipt is what
`confirmed` requires, and this subject has none. The runtime facts here are
direct container observations — Swath's own self-reported counters and the rows
it emitted — from single unreplicated runs, two of them instrumented, with no
verifier verdict. They are
groundwork observations, not benchmark results, and no number here is
comparative.

## Varve and Swath

Varve builds Swath and maintains this study. Before building Swath, Varve studied
how existing listing tools approached the problem, and that work informed Swath's
design; we also know Swath's tuning envelope more deeply than we know the other
tools, which makes us participants in the space we are studying. We apply the
same harness, buckets, and run-record requirements to Swath as to every other
tool, and publish the results on the same terms whether or not they favour it —
which is why this page states plainly that Swath's v0.2.0 pass produced no
receipts and no verifier verdict. Swath's earlier internal benchmark history is
**not** used here; any number must be produced again on this harness. The
structural control on the first-party source basis is that the v0.2.0 derivation
was source-first and deliberately blind, every claim is anchored, and an
independent cross-model review re-verified the anchors — claim
`first-party-private-source-basis`. We welcome help from people who know the
other tools better; the run records are published so readers can inspect and
improve the setup.
