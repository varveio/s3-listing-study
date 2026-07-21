# s7cmd

> **Historical landing page (2026-07-20, capsule migration).** This is the full
> pre-restructure landing page. Any `current-state` wording below is historical
> as of the date it records and is superseded by the root README and `data/`.
> Only this banner and link targets changed; body prose and evidence
> qualifications are preserved.

**Status: current-state page, consolidated 2026-07-17.** This page was
rewritten from the 2026-07-17 groundwork pass's mixed-provenance tool page into
a clean current-state summary, per owner-approved restructuring (not new
research — every claim below was already receipt- or source-backed before
this rewrite). Full detail lives in three companion documents:

- [`mechanism.md`](../docs/mechanism.md) — the source-anchored architecture (pipeline,
  parallel discovery, pagination, retries, memory model, the `api_calls`
  counter).
- [`running.md`](../docs/running.md) — build, every smoked mode with its invocation,
  reproduction via the shared harness, and the architecture matrix.
- [`research/report.md`](report.md) — the full source-and-run
  report this page and `mechanism.md`/`running.md` were distilled from, plus
  [`research/reconciliation.md`](reconciliation.md) (row-by-row
  reconciliation against every inherited source) and
  [`research/codex-review.md`](codex-review.md) (the two-round
  critical cross-check, including the source-anchored **Round 2** pass whose
  corrections this page reflects). These preserved research files may use the
  project's older terminology; this page is the current summary.

The original tool page was seeded entirely from secondhand, unexecuted
research; a 2026-07-17 groundwork pass built, ran, and smoke-tested `s7cmd`
(v1.5.0, `d589df7`) and its `ls` engine, `s3ls-rs` (v1.0.3, `bf42067`)
directly. Everything below is that current state — see the
[Notes, questions, and observations](#notes-questions-and-observations) for what changed and why, and
[Provenance](#provenance) for what remains unexecuted secondhand material.

|  |  |
|---|---|
| **Repo** | https://github.com/nidor1998/s7cmd — canonical (crates.io `s7cmd`, author `nidor1998`) [DOC; RUN `receipts/smoke/_build`] |
| **Language** | Rust (edition 2024, `rust-version 1.91.1`) [SRC `Cargo.toml` @ d589df7] |
| **License** | Apache-2.0 [DOC `LICENSE`] |
| **Version reviewed** | v1.5.0 (`d589df7ce691edbede05fc9a691ab1787cdb6b9e`), latest release [SRC `Cargo.toml` `version = "1.5.0"` @ d589df7; RUN `receipts/smoke/_build` `s7cmd 1.5.0`] |
| **Listing engine** | `s3ls-rs` v1.0.3 (`bf42067537da476b157b5d289a3e72d049b60db2`) — a pinned **crate dependency**, not a reimplementation [SRC `Cargo.toml`, `src/ls_bin/mod.rs:15-88` @ d589df7] |
| **Tier** | Benchmark representative of the `s3ls-rs` family. **Owner decision (2026-07-17):** `s7cmd` is the family's **sole** benchmark subject; standalone `s3ls-rs` is not separately benchmarked, and results are noted as generalizing to the crate (see [Scope decision](#scope-decision)). |
| **Testability** | Builds from upstream's own `Dockerfile` at the pinned SHA (arm64, native); runs; `ls` CLI surface matches the `s3ls` flag set except `--auto-complete-shell` (hidden per subcommand [SRC `src/cli.rs:400-449` @ d589df7]). **12/12 smoke modes PASS** — see [`running.md`](../docs/running.md). |

## What this tool is

`s7cmd` is an umbrella CLI composing four of its author's Rust crates:
`s3sync` (sync — the project's one human-written reference implementation),
`s3util-rs` (bucket admin + `cp`/`mv`/`rm`), `s3rm-rs` (`clean`), and
**`s3ls-rs`** (`ls`) [DOC s7cmd README][SRC s7cmd Cargo.toml]. Only `ls` is in
scope for this study. Its `ls` subcommand is a ~90-line wrapper that builds
`s3ls_rs::ListingPipeline` directly from the `s3ls-rs` crate pinned at exactly
`=1.0.3` [SRC `Cargo.toml`, `src/ls_bin/mod.rs:15-88` @ d589df7] — a
**dependency, not a separate reimplementation**.

Upstream health: created 2026-03-27, last push 2026-07-11; v1.5.0 tagged
2026-07-11; 221 commits; 0 open issues/PRs, 8 stars, 0 forks; not archived.
The author states the project is "functionally complete," maintained
minimally with monthly dependency bumps [DOC README §Contributing]
[3P github.com/nidor1998/s7cmd API 2026-07-17].

### Identity finding (Stage E round 2 precision)

Because `s7cmd`'s `ls` compiles the **same crate version** as standalone
`s3ls`, the listing engine, its defaults, and its output formatters are
identical **by construction** [SRC]. The **CLI surface is not
byte-identical**, though, and full **runtime** equivalence is not a measured
result:

- `s7cmd` deliberately hides/strips each subcommand's inherited
  `--auto-complete-shell` from its help and completion output in favor of a
  single top-level flag [SRC `src/cli.rs:400-449` @ d589df7] — the committed
  `s7cmd ls --help` receipt correspondingly lacks it
  [RUN `receipts/smoke/_build/help-and-version.txt`], while standalone `s3ls`
  still exposes its own `--auto-complete-shell` [DOC s3ls-rs README].
- `s7cmd` runs a modified process-level wrapper: `src/ls_bin/mod.rs`
  documents dropping upstream's `load_config_exit_if_err` helper (which
  called `std::process::exit`), so a bad `Ls` config inside a `batch-run`
  script doesn't kill the whole batch [SRC `src/ls_bin/mod.rs:1-12` @
  d589df7].
- **Full runtime equivalence beyond the shared crate and these two known
  divergences is `[INFERRED]` from the dependency pin, not a separate
  comparative run** — standalone `s3ls` was not run side-by-side to confirm
  behavioral identity. Anyone benchmarking "`s7cmd` listing" is benchmarking
  `s3ls-rs 1.0.3`'s listing engine, wrapped.

This precision (same engine/defaults/formatters **by construction**; CLI
surface **not** identical; runtime equivalence beyond that **[INFERRED]**,
not measured) is the corrected form of the claim after the 2026-07-17 Stage E
round 2 review found the original wording overstated it as "no divergence" /
"behaviorally identical" — see [Review history](#review-history).

### Scope decision

> **Owner decision, 2026-07-17.** `s7cmd` is the `s3ls-rs` family's **sole**
> benchmark representative. `s7cmd`'s `ls` compiles the `s3ls-rs` crate
> pinned `=1.0.3`, so a standalone `s3ls-rs` run would re-measure the same
> engine; `s7cmd` represents the family, and its results generalize to the
> crate (subject to the CLI-surface divergences noted above, which do not
> touch the listing engine itself). Standalone `s3ls-rs` is not benchmarked.
> Its inherited hypothesis sheet — the only surviving copy of the
> Swath-seeded material on this engine, and the source that names `s3ls-rs`
> as Swath's own architectural ancestor — is retained as background in
> internal notes, not included in this public repository, rather than
> deleted, per an open, separate `tools/` fold (`consolidate/s7cmd` does not
> itself perform that fold; every reference below to the `s3ls-rs` tool page
> refers to that inherited material).

## What we tried and saw

- **12/12 smoke modes PASS** against the registered smoke bucket
  (`noaa-normals-pds`, 148,917 keys, anonymous) — every request-pattern/
  output-contract mode was exercised at least once; see
  [`running.md`](../docs/running.md) for the full table and every receipt link.
- **Parallel delimiter discovery is real, and measurable via the tool's own
  counter, but that counter is a page-fetch count, not a wire-level request
  count.** The full-bucket recursive run took **204** counted page fetches
  against an **~149-page sequential floor** (148,917 keys / 1000 max_keys) —
  the documented effect of delimiter-discovery pages mixing objects and
  prefixes on top of the leaf drain [RUN
  `receipts/smoke/recursive-tsv/full`]. This is `api_calls`, an `AtomicU64`
  incremented once **before** each `fetch_page` call in both the sequential
  and parallel paths [SRC `s3ls-rs src/storage/s3/mod.rs:466,604` @
  bf42067]; the client allows up to 10 SDK retry attempts behind that one
  increment [SRC `s3ls-rs src/storage/s3/client_builder.rs:153-160` @
  bf42067], so **one counted fetch can cost more than one chargeable HTTP
  request** — a Stage E round 2 correction, see
  [Review history](#review-history) and [`mechanism.md`](../docs/mechanism.md).
- **Anonymous `ListBuckets` is blocked upstream.** `s7cmd ls` with no target
  calls `ListBuckets`; anonymously S3 returns a 307 redirect and the tool
  exits 1 [RUN `receipts/smoke/_capability/bucket-list`] — untested-for-this-
  reason under `CREDS=none`, not silently skipped.
- **"Fully AI-generated (human-verified)."** The project prominently states
  that all `s7cmd`/`s3ls-rs`/`s3util-rs`/`s3rm-rs` source, tests, and docs
  are Claude-Code-generated under human review, with a stated 96%+
  test-coverage policy; `s3sync` alone is the human-written reference
  [DOC README §"Fully AI-generated"]. Flagged as unusual provenance worth the
  study owner's attention given this study's own "don't trust unverified
  prose" premise — though the source read during groundwork was coherent and
  well-tested.
- **No upstream container-image channel was found**, and the check is
  incomplete, not a confirmed universal negative: Docker Hub returned 404,
  but GitHub Packages/GHCR could not be enumerated (403 for missing
  `read:packages`, anonymous GHCR token denied). The smoke image was built
  from upstream's own `Dockerfile` at the pinned SHA instead — see
  [`running.md`](../docs/running.md) §Build.

## Known limitations

- **`all-versions` output contract is narrower than the mode name suggests.**
  `IsLatest` requires the separate `--show-is-latest` flag
  [SRC `s3ls-rs config/args/mod.rs:303-312` @ bf42067;
  `display/columns.rs:163-172` @ bf42067] — the smoke receipt's TSV payload
  has `VersionId` but no `IsLatest` column, consistent with not passing that
  flag.
- **Versioned-bucket fidelity is deferred, not settled.** The registered
  smoke bucket has only single, `null`-version objects, so `all-versions`
  mode never exercised genuine multi-version collapse or delete-marker rows;
  it PASSED because there was nothing to collapse. `EDGE_BUCKET=none` for
  this pass. See [`mechanism.md`](../docs/mechanism.md) for the full explanation and
  what a real test would need.

## Review history

**Stage E, round 1** (separate cross-model review, artifact-scoped).
Three successive attempts at full effort/timeout budgets to read both pinned
Rust source trees **timed out** inside the repo-phase budget; the round-1
review that completed was scoped to repo artifacts only (report,
reconciliation, `run.sh`, `normalize.sh`, tool page diff) at low effort.
`[SRC]` anchor re-verification against the pinned checkouts was deferred to
a spot-check (38/54 anchors sampled, all supported). All round-1 major
findings were fixed or reasoned-disagreement-resolved; see
[`research/codex-review.md`](codex-review.md) §1-§4 for the
complete findings and resolutions.

**Stage E, round 2** (full source-anchored review, owner-run, 2026-07-17).
The owner ran the review round 1 could not complete: full source access to
both pinned checkouts, no time-box. It checked **all 89** claim-bearing
`[SRC]` anchor groups (not a sample) — 71 supported, 13 mislocated, 5
unsupported — and raised two **major** and five **minor** findings. All
seven were independently re-verified against the pinned source and **fixed**:
the CLI-flag-surface/runtime-equivalence overclaim (now stated with the
precision in [Identity finding](#identity-finding-stage-e-round-2-precision)
above); the `api_calls`-as-request-count overclaim (now scoped as in
[What we tried and saw](#what-we-tried-and-saw) above); a tool page self-contradiction
(the old page asserted both "never run" and "groundwork ran it" — resolved
by this rewrite, which asserts only the current, true state); the
anonymous-region-provider explanation; the `all-versions`/`IsLatest`
output-contract overclaim; an unlabeled negative claim about hinted/two-pass
workflows (now labeled `[SRC sweep of s3ls-rs src/, README.md @ bf42067]`);
and the "no container image published anywhere" overclaim (now scoped to
what was actually checked). All 13 mislocated and 5 unsupported anchors were
corrected with independently re-verified `file:line @ sha` citations —
carried into [`mechanism.md`](../docs/mechanism.md) and
[`research/report.md`](report.md)/[`research/reconciliation.md`](reconciliation.md)
exactly as they now stand. Complete findings and resolutions:
[`research/codex-review.md`](codex-review.md) "Round 2."

## Notes, questions, and observations

Every claim retained from the inherited tool pages, walked individually against
the source-and-run groundwork. **Nothing here was
re-derived** — statuses and supporting records are carried from
[`research/reconciliation.md`](reconciliation.md), corrected to
Stage E round 2 wording where round 2 changed a claim's scope. `docs/cross-
cutting-claims.md` also names this subject (4 rows, §C in the
reconciliation); those edits are routed to the orchestrator as another
document's scope and are not reproduced here — see
[`research/reconciliation.md`](reconciliation.md) §C.

### A — this tool page's own inherited claims (originally thin/unresolved)

| # | Inherited observation | Status | Supporting record |
| --- | --- | --- | --- |
| A1 | Repo `github.com/nidor1998/s7cmd` (not independently confirmed) | Corroborated | Cloned, canonical (crates.io `s7cmd`, author nidor1998) [DOC][RUN `receipts/smoke/_build`] |
| A2 | Language unconfirmed (presumed Rust) | Corroborated | Rust, edition 2024, `rust-version 1.91.1` [SRC `Cargo.toml` @ d589df7] |
| A3 | License unconfirmed | Corroborated | Apache-2.0 [DOC `LICENSE`] |
| A4 | Version reviewed unknown | Corrected | Pinned v1.5.0 (`d589df7`), latest release |
| A5 | Testability unknown; no build/invocation known | Settled by smoke run | Builds from upstream `Dockerfile` at pinned SHA; runs; `ls` surface = `s3ls` flags; 12/12 smoke PASS [RUN `receipts/smoke/*`] |
| A6 | "possibly redundant with `s3ls-rs` rather than a distinct target" | Settled by smoke run + owner decision | `s7cmd`'s `ls` **depends on** `s3ls-rs =1.0.3` (not a reimplementation); same listing engine by construction [SRC], full runtime equivalence [INFERRED] — see [Identity finding](#identity-finding-stage-e-round-2-precision). Owner decision 2026-07-17: `s7cmd` is the family's sole benchmark subject [SRC `Cargo.toml`; `src/ls_bin/mod.rs` @ d589df7] |
| A7 | Mechanism "almost nothing known"; "bundles s3ls-rs"; "could be separate reimplementation" | Settled by smoke run + source | Cargo dep `s3ls-rs =1.0.3`; `ls_bin::run` builds `s3ls_rs::ListingPipeline` — a crate dependency, not a reimplementation [SRC `src/ls_bin/mod.rs:15-88`, `dispatch.rs:61-72` @ d589df7]. Full mechanism in [`mechanism.md`](../docs/mechanism.md) |
| A8 | Author now points users to `s7cmd` over `s3ls-rs` | Corroborated | `s3ls-rs` README: "please file new issues in the s7cmd repository" [DOC s3ls-rs README] |
| A9 | Umbrella/multi-tool CLI (subcommands), listing one capability among several | Corroborated | `ls`/`cp`/`mv`/`rm`/`sync`/`clean` + bucket admin, composing 4 sibling crates [DOC s7cmd README][SRC `Cargo.toml`] |
| A10 | Modes/tunables unknown (empty table) | Settled by smoke run | Full modes+tunables tables in `research/report.md` §3; every mode smoked [RUN] |
| A11 | Open-Q1: successor vs separate project | Corroborated | Umbrella successor; `s3ls-rs` still released standalone (v1.0.3 tag) [DOC] |
| A12 | Open-Q2: is standalone `s3ls-rs` still viable/maintained | Corroborated | v1.0.3 tagged, actively released; not frozen [3P git] — belongs to the archived `s3ls-rs` tool page |
| A13 | Open-Q3: does bundled listing differ from standalone `s3ls-rs` | Contradicted (partly) | `s7cmd` depends on the crate directly (`=1.0.3`), so the listing engine, defaults, and output formats are identical — the archived tool page's engine claims **do** transfer. The CLI surface is **not** fully identical: `s7cmd` hides `--auto-complete-shell` per subcommand and wraps the process entry differently [SRC `src/cli.rs:400-449` @ d589df7]; runtime equivalence beyond that is [INFERRED] — see [Identity finding](#identity-finding-stage-e-round-2-precision) |
| A14 | Open-Q4: benchmark s3ls-rs standalone, s7cmd, or both | Settled by owner decision (2026-07-17) | `s7cmd` only — one engine, one entry point benchmarked; results generalize to the crate (see [Scope decision](#scope-decision)) |

### B — `s3ls-rs` engine claims (inherited via the crate dependency)

`s7cmd`'s `ls` **is** the `s3ls-rs` crate at `=1.0.3`, so the archived
`s3ls-rs` tool page's engine claims are inherited claims about this tool. The
tool page was reviewed at v1.0.1; groundwork read v1.0.3 — anchors below are
re-checked at `@ bf42067` (v1.0.3).

| # | Inherited observation (`s3ls-rs` tool page) | Status | Supporting record (@ bf42067 = v1.0.3) |
| --- | --- | --- | --- |
| B1 | Mode split on target-bucket presence (list-buckets vs list-objects) | Corroborated | [SRC `ls_bin/mod.rs:34-49` (s7cmd wrapper); bucket_lister vs ListingPipeline] |
| B2 | Async producer/consumer pipeline, bounded mpsc, queue default 200000 | Corroborated | Bounded channels: [SRC `pipeline.rs:57-59`]; default 200000: [SRC `config/args/mod.rs:25,511`] |
| B3 | Await terminal-stage-first (writer→aggregator→lister); writer error flips cancel token | Corroborated | [SRC `pipeline.rs:68-107`] |
| B4 | Parallel iff (max_parallel>1) & (delimiter None/recursive) & (not-express or opt-in) | Corroborated | [SRC `mod.rs:409-413`] |
| B5 | Sequential path: cancel→rate-limit→bump counter→fetch→send→loop on truncated | Corroborated | [SRC `mod.rs:443-555`] |
| B6 | Parallel path: JoinSet, delimiter `/`, drop permit before spawning children, depth>max→sequential | Corroborated | [SRC `mod.rs:558-746`, drop at `:677`, fallback at `:581`] |
| B7 | Two depth concepts: fan-out `max_parallel_listing_max_depth` vs content `--max-depth` (synthesizes CommonPrefix at boundary) | Corroborated + Settled by smoke run | [SRC `mod.rs:333-350,682-699`][RUN `max-depth/root`: 1 API call, PRE at boundary] |
| B8 | Express One Zone via `--x-s3` suffix; parallel gated behind opt-in flag | Corroborated (source) | [SRC `mod.rs:26,302-303,409-413`]; not runtime-tested (no Express bucket) |
| B9 | ObjectLister drops `list_rx` before joining storage task (deadlock avoidance); named regression test | Corroborated | [SRC `lister.rs:70-74`; test `lister_does_not_deadlock_when_cancelled_with_full_queue` `:161`] |
| B10 | Aggregator `run_streaming` (`--no-sort`) vs `run_aggregate` (buffer-all-then-sort); rayon `par_sort_by` past threshold 1,000,000 | Corroborated + Settled by smoke run | [SRC `aggregate.rs:33-83` (streaming/buffering), `:144-168` (sort/rayon threshold)][RUN `recursive-tsv/normals-hourly` stderr: `sort_entries started entry_count=2549 parallel_sort_threshold=1000000`] |
| B11 | DisplayWriter: formatter selection once; `BufWriter<Stdout>` sink | Corroborated | [SRC `pipeline.rs:170-186`] |
| B12 | Control-char escaping `\x00-\x1f`,`\x7f`→hex; `Cow::Borrowed` fast path; `--raw-output` opt-out; JSON exempt | Corroborated | [SRC `display/mod.rs:78-108`; `json.rs` uses serde] |
| B13 | Bucket-listing mode: single async, `list_buckets`, `max_buckets(1000)`, `--bucket-name-prefix`, `use_max_buckets` for custom endpoints | Partly: Settled by smoke run (capability) + Unaddressed (internals) | Anonymous `ListBuckets` **blocked** (307, exit 1) [RUN `_capability/bucket-list`]; the `max_buckets`/`use_max_buckets` internals not source-read |
| B14 | Stuck-continuation-token bail (twice-in-a-row) + no-token-on-truncated bail | Corroborated (source) | [SRC `mod.rs:506-522,637-666`]; not runtime-triggered (real S3 well-behaved) |
| B15 | Retries: no self-retry; SDK `RetryConfig::standard()` (exp backoff + jitter); defaults 10 attempts / 100 ms | Corroborated | [SRC `client_builder.rs:154-160`][DOC help] |
| B16 | Tunable defaults (64, 2, 200000, 1M, 1000, 10, 100ms, rate-limit floor/refill N/10) | Corroborated | Declared: [SRC `config/args/mod.rs:23-29` (consts), `:501-560` (fields)]; consumed (semaphore sizing / limiter refill construction): [SRC `mod.rs:789,791-806`]; [DOC help] — all still current at v1.0.3 |
| B17 | Weakness H1: default mode buffers all (RSS ~linear); `--no-sort` flat | Corroborated (mechanism); Unaddressed (scale) | [SRC `aggregate.rs:33-83`]; smoke 120.8 MB @ 149k consistent; 9x gap needs ≥1M keys [RUN] |
| B18 | H2: billion-object whole-bucket is worst regime | Unaddressed | Scale-only; not settleable at smoke |
| B19 | H3: no crash-resume | Corroborated | One-shot CLI, no persisted state [SRC `ls_bin/mod.rs:52-88`][DOC Non-Goals] |
| B20 | H4: fan-out depth 2 → flat/shallow layout gets little speedup | Corroborated (mechanism); Unaddressed (runtime) | Collapse-to-sequential path: [SRC `mod.rs:590-674`]; default depth 2: [SRC `config/args/mod.rs:24,506-507`][DOC]; smoke bucket is hierarchical — flat-bucket test deferred to benchmark |
| B21 | H5: stuck-token bail yields incomplete listing on bad endpoints | Corroborated (mechanism); Unaddressed (runtime) | [SRC `mod.rs:515,637`]; real S3 only |
| B22 | H6: `--rate-limit-api` floor 10 req/s, refill N/10 | Corroborated | Floor of 10 (CLI validator): [SRC `config/args/mod.rs:517-519`]; refill N/10 construction: [SRC `mod.rs:791-806`] |
| B23 | H7: Express parallelism requires an opt-in flag | Corroborated (mechanism) | [SRC `mod.rs:409-413`] |
| B24 | H8: sort-threshold step at 1M (stdlib vs rayon) | Corroborated (mechanism); Unaddressed (latency curve) | Threshold default 1,000,000: [SRC `config/args/mod.rs:26,523`]; stdlib/rayon switch: [SRC `aggregate.rs:163-168`][DOC]; runtime step not measured |
| B25 | Version reviewed v1.0.1 | Corrected | Current v1.0.3; `s7cmd` depends on `=1.0.3` — belongs to the archived `s3ls-rs` tool page |
| B26 | Code anchors (v1.0.1 line numbers) | Mostly Corroborated; some drifted | Re-checked @ v1.0.3: most land within a few lines; e.g. semaphore-size at `:789` (tool page said `:825`), max-depth boundary at `:682` (tool page said `:679`) — corrected in [`mechanism.md`](../docs/mechanism.md) and `research/report.md`/`research/reconciliation.md` |
| B27 | Library inventory (tokio, aws-sdk-s3, rayon, leaky-bucket, fancy-regex, zeroize, …) | Corroborated (deps); zeroize Unaddressed | [SRC `s3ls-rs Cargo.toml`]; `zeroize`-on-drop of access keys not source-verified |

## Provenance

**Mixed provenance.** This page's Metadata table, Identity finding, Scope
decision, What we tried and saw, Review history, and Notes, questions, and observations are firsthand:
a pinned source read of `s7cmd` v1.5.0 (`d589df7`) and its `s3ls-rs` v1.0.3
engine (`bf42067`), 12 committed anonymous smoke receipts against
`noaa-normals-pds`, and two rounds of critical cross-check, one of them a full
source-anchored pass. This page began from inherited private notes that are now
unavailable; under the repository's provenance policy, that material is only a
secondhand seed and is not evidence. The current identity and mechanism
conclusions rest on the pinned public source and committed receipts described
above. The inherited `s3ls-rs` dossier is retained only as background in
internal notes (the archived `s3ls-rs` tool page) and is not included in this
public repository.

## Receipts

Committed under [`receipts/smoke/`](../receipts/smoke/) (12 mode/scope
receipts, all **PASS**, + `_build/` and `_capability/`). Anonymous,
`noaa-normals-pds`, manifest snapshot 2026-07-17 (sha256 `c78a…2adb`), image
`s7cmd@sha256:0709…da` (arm64, built from upstream `Dockerfile` @ d589df7).
Full invocation-by-invocation table, reproduction command, and architecture
matrix: [`running.md`](../docs/running.md). Large payloads (>100 KB) are stored
outside the repo at `<data>/receipts/s7cmd/` with sha256
recorded in each `run.meta` (no-data-in-repo rule).
