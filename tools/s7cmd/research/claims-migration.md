# s7cmd claim-ledger migration

This is the human-auditable conservation map from the pre-restructure landing
page ([`tool-page.md`](tool-page.md)) to the atomic records in
[`../data/claims.json`](../data/claims.json). Each legacy origin appears exactly
once below; a row may map to several atomic claims, and one atomic claim may
conserve the same proposition from more than one origin.

## Labeling scheme

The legacy page carried two labeled inherited-claim tables and one status-bearing
identity table, plus status-bearing prose:

- **`A1`-`A14`** — the page's own inherited-claim walk ("Notes, questions, and
  observations", table A), kept at their existing labels.
- **`B1`-`B27`** — the inherited `s3ls-rs` engine-claim walk (table B), kept at
  their existing labels. `s7cmd`'s `ls` **is** the `s3ls-rs` crate at `=1.0.3`,
  so these are inherited claims about this tool.
- **`META-1`-`META-7`** — the status-bearing identity/metadata table (Repo,
  Language, License, Version reviewed, Listing engine, Tier, Testability). These
  rows restate findings already carried by the A-table and, for the engine
  version, `B25`; the corresponding atomic claim therefore conserves both the
  `META` origin and its A/B counterpart.
- **`PROSE-1`-`PROSE-6`** — genuinely table-external status-bearing propositions:
  the `api_calls` page-fetch-versus-request scoping and its run observations
  (`PROSE-1`), the "Fully AI-generated" provenance flag (`PROSE-2`), the
  incomplete container-image channel check (`PROSE-3`), the `all-versions`
  `IsLatest` output-contract limitation (`PROSE-4`), the deferred versioned-bucket
  fidelity gap (`PROSE-5`), and the upstream-health/maintenance figures
  (`PROSE-6`).

**Prose conserved without its own label.** Several page sections purely restate
labeled table rows and take no separate label: "What this tool is" restates `A9`
plus `A6`/`A7`; the "Identity finding" section restates `A13` (engine identical
by construction, CLI surface not identical, runtime equivalence inferred, process
wrapper divergence); the "Scope decision" blockquote restates `A14`; and the
"What we tried and saw" 12/12-PASS and blocked-`ListBuckets` bullets restate `A5`
and `B13`. The "Review history" section is derivation history, conserved in
[`codex-review.md`](codex-review.md), not a tool claim. The `s3ls-rs` inherited
dossier is inherited background held in internal notes (not included in this
public repository) and is referenced, not re-conserved here.

## Status and disposition semantics

The migration does not promote evidence. `status` records evidence strength:
`confirmed` requires a receipt-backed run or build fact, `supported` requires
source, documentation, run, or observation evidence, and `unverified` retains a
former unaddressed or unrun state. `disposition` separately records whether
inherited wording was retained, corrected, or contradicted.

**Status demotions forced by the schema (down, never up).** Two legacy rows were
walked as "Partly settled" or as `[INFERRED]` and are split so the settled facet
stays evidence-appropriate while the unsettled facet becomes `unverified`:
`A13`'s "runtime equivalence" facet becomes `runtime-equivalence-is-inferred`
(`unverified`); `B13`'s bucket-listing internals become
`bucket-listing-internals-unread` (`unverified`) alongside the receipt-confirmed
`bucket-listing-blocked-anonymously` and the documentation-supported
`bucket-listing-mode-parameters`, which conserves the inherited mode description
at base strength; and `B21`'s incompleteness risk becomes
`stuck-token-incompleteness-risk-untested` (`unverified`) alongside the
source-supported `anti-stuck-token-bails`. One post-migration review demotion:
`smoke-never-crossed-sort-threshold` moved `confirmed` to `supported` because
which sort implementation ran rests on the source branch logic, not on a
receipt-recorded fact.

| Legacy origin | Conserved subject | Atomic claim IDs |
| --- | --- | --- |
| META-1 | Repo identity | `repo-is-canonical` |
| META-2 | Language and edition | `language-is-rust` |
| META-3 | License | `license-is-apache-2` |
| META-4 | Version reviewed | `version-is-1-5-0` |
| META-5 | Listing engine crate and version | `ls-is-s3ls-rs-crate`, `engine-version-is-1-0-3` |
| META-6 | Tier and sole benchmark subject | `s7cmd-is-sole-benchmark-subject` |
| META-7 | Testability, build, and CLI surface | `cli-surface-omits-auto-complete-shell`, `builds-from-own-dockerfile`, `smoke-modes-all-pass` |
| A1 | Repo canonical | `repo-is-canonical` |
| A2 | Language presumed Rust | `language-is-rust` |
| A3 | License unconfirmed | `license-is-apache-2` |
| A4 | Version reviewed unknown | `version-is-1-5-0` |
| A5 | Testability and build unknown | `builds-from-own-dockerfile`, `smoke-modes-all-pass` |
| A6 | Possibly redundant with s3ls-rs | `ls-is-s3ls-rs-crate` |
| A7 | Bundles s3ls-rs or reimplements it | `ls-is-s3ls-rs-crate` |
| A8 | Author points users to s7cmd | `author-recommends-s7cmd-over-s3ls-rs` |
| A9 | Umbrella multi-tool CLI | `is-umbrella-cli` |
| A10 | Modes and tunables unknown | `smoke-modes-all-pass`, `modes-and-tunables-inventoried` |
| A11 | Successor versus separate project | `s7cmd-is-umbrella-successor` |
| A12 | Standalone s3ls-rs still maintained | `s3ls-rs-still-released-standalone` |
| A13 | Bundled listing versus standalone s3ls-rs | `engine-identical-by-construction`, `cli-surface-omits-auto-complete-shell`, `runtime-equivalence-is-inferred`, `process-wrapper-drops-exit-helper` |
| A14 | Benchmark subject decision | `s7cmd-is-sole-benchmark-subject` |
| B1 | Mode split on target-bucket presence | `mode-splits-on-bucket-presence` |
| B2 | Bounded mpsc pipeline and queue default | `pipeline-uses-bounded-mpsc` |
| B3 | Terminal-first await and error precedence | `pipeline-awaits-terminal-stage-first` |
| B4 | Parallel-versus-sequential decision | `parallel-decision-conditions` |
| B5 | Sequential path loop | `sequential-path-loop` |
| B6 | Parallel path algorithm | `parallel-path-algorithm` |
| B7 | Two depth concepts and boundary synthesis | `two-depth-concepts`, `max-depth-one-emits-pre-one-call` |
| B8 | Express One Zone opt-in gating | `express-one-zone-opt-in` |
| B9 | ObjectLister deadlock avoidance | `object-lister-deadlock-avoidance` |
| B10 | Aggregator streaming, buffering, and sort | `aggregator-streams-or-buffers`, `sort-uses-rayon-past-threshold`, `smoke-never-crossed-sort-threshold` |
| B11 | DisplayWriter formatter selection | `display-writer-picks-formatter-once` |
| B12 | Control-character escaping and JSON exemption | `control-char-escaping` |
| B13 | Bucket-listing mode capability and internals | `bucket-listing-blocked-anonymously`, `bucket-listing-internals-unread`, `bucket-listing-mode-parameters` |
| B14 | Anti-stuck-token pagination bails | `anti-stuck-token-bails` |
| B15 | SDK standard retry model | `retry-uses-sdk-standard` |
| B16 | Tunable defaults | `tunable-defaults` |
| B17 | Buffer-all memory model and scale | `default-mode-buffers-all`, `memory-scaling-unmeasured`, `full-bucket-smoke-peak-rss` |
| B18 | Billion-object worst regime | `billion-object-regime-unaddressed` |
| B19 | No crash-resume | `no-crash-resume` |
| B20 | Flat-layout fan-out collapse and speedup | `flat-bucket-collapses-to-sequential`, `flat-bucket-speedup-unmeasured` |
| B21 | Stuck-token incompleteness risk | `anti-stuck-token-bails`, `stuck-token-incompleteness-risk-untested` |
| B22 | Rate-limit floor and refill | `rate-limit-floor-and-refill` |
| B23 | Express parallelism opt-in flag | `express-one-zone-opt-in` |
| B24 | Sort threshold step at one million | `sort-uses-rayon-past-threshold`, `sort-threshold-latency-step-unmeasured` |
| B25 | s3ls-rs version reviewed | `engine-version-is-1-0-3` |
| B26 | Code anchors re-checked at v1.0.3 | `anchors-rechecked-at-v1-0-3` |
| B27 | Library inventory and zeroize | `dependency-inventory`, `zeroize-on-drop-unverified` |
| PROSE-1 | api_calls page-fetch scoping and counts | `api-calls-is-page-fetch-count`, `full-bucket-took-204-page-fetches`, `page-fetch-counts-track-prefix-shape` |
| PROSE-2 | Fully AI-generated provenance | `fully-ai-generated` |
| PROSE-3 | Container-image channel check incomplete | `no-container-image-found-check-incomplete` |
| PROSE-4 | all-versions IsLatest output contract | `all-versions-omits-is-latest-by-default` |
| PROSE-5 | Versioned-bucket fidelity deferred | `versioned-bucket-fidelity-deferred` |
| PROSE-6 | Upstream health and maintenance | `upstream-health` |

The validator compares this 54-origin set with every `legacy_origins` value in
[`../data/claims.json`](../data/claims.json) in both directions. Reviewers should
additionally compare this table against the preserved tables in
[`tool-page.md`](tool-page.md), because deciding where a compound row splits
remains a human judgment rather than a safe generic inference.
