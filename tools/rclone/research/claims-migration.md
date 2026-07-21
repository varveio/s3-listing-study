# rclone claim-ledger migration

This is the human-auditable conservation map from the pre-restructure landing
page ([`tool-page.md`](tool-page.md)) to the atomic records in
[`../data/claims.json`](../data/claims.json). Every legacy origin appears
exactly once below. A row may map to several atomic claims, and one atomic claim
may conserve propositions from more than one legacy origin. The validator checks
this map against every `legacy_origins` value in both directions.

## Labeling scheme

- The single "Notes, questions, and observations" table keeps its existing
  typed labels: mechanism rows `M1`–`M7`, modes/tunables rows `T1`–`T7`, and
  numbers/weaknesses rows `W1`–`W8`.
- The four trailing "Strength" rows of that table carry no inherited IDs, so
  they take deterministic top-to-bottom labels `STR-1`–`STR-4`.
- The six rows of the separate "Additive rows (entered via groundwork/review)"
  table take deterministic top-to-bottom labels `ADD-1`–`ADD-6`.
- The one status-bearing identity-table row takes a `META-n` label: `META-1`
  is the Testability row's "Trivial to install" judgment.
- Status-bearing propositions outside any table take `PROSE-n` labels
  top-to-bottom: `PROSE-1` is the firsthand smoke-correctness statement opening
  "What we saw" (every verifier-checked mode PASSed and the full bucket re-listed
  byte-exact); `PROSE-2` is the intro's "the `ListObjectVersions` API was not
  smoked (the bucket is unversioned)"; `PROSE-3` is the intro's "`ls`/`lsl`/`lsd`
  are output-format variants over the same requests and were not separately
  timed."

## What is conserved elsewhere, and by what judgment

- The top identity/"At a glance" table (Repo, Language, License, Version tested,
  Tier, Testability) is mostly tool identity and study state, not the claim
  ledger: Repo, Language, License, and **Version tested** are conserved by
  [`../data/tool.json`](../data/tool.json), and **Tier** (Tier 1 — included in
  the planned comparative runs) is conserved by the repository-level
  [`../../README.md`](../../README.md). The Testability row splits: its
  forward-looking "the memory and exit-code questions need a constrained-memory
  harness" note is conserved by `T7`, `W2`, and `W3`; its "Trivial to install"
  judgment is the status-bearing `META-1` claim; and "Groundwork ran the pinned
  container image" is tested-subject identity in `tool.json`.
- The prose bullets under "What we saw" restate table rows (`M2`/`M3`/`M5`, the
  additive rows, and the `W3` exit-0 block) and take no separate label, except
  the aggregate smoke-correctness proposition captured as `PROSE-1`.
- The numbered "Open hypotheses for the benchmark" list restates `W2`, `W3`,
  `W4`, `W5`, `W6`, and `T7`; its checker-sweep, list-chunk-sweep, and v1-vs-v2
  items assert no tool proposition beyond `T3`, `T4`, and `ADD-4` and are
  conserved there. Its CPU-vs-network item is a benchmark methodology note, not
  a tool claim.

## Status, disposition, and forced demotions

`status` records evidence strength: `confirmed` requires a receipt-backed run,
`supported` requires source, documentation, run, or observation evidence, and
`unverified` retains the legacy `VERIFIED: no` / "Unaddressed" state (testable
but unsettled). `disposition` separately records whether inherited wording was
retained, corrected, or contradicted.

Demotions recorded here, never promotions:

- The `ADD-5` compound splits into a confirmed leg
  (`forced-walk-under-disable-listr-traced`, receipt-backed), a source
  correction (`mode-selector-is-not-fast-list`), and an observation-only leg
  (`plain-recursive-r-is-flat-obs`, `supported`, since the "plain -R is already
  flat" trace is an ad-hoc capture in the recursive-hierarchical correction
  block, not a committed `--dump headers` receipt).
- `W3` (exit-0-after-OOM) stays `unverified`: its provenance citation was
  corrected to the two-issue record, but the behavioral claim is not settled and
  is conserved with its sync-path and version-delta caveats intact.
- `W6` splits the source observation (`no-list-checkpoint-state-in-source`,
  `supported`) from the unrun runtime proof (`list-crash-resume-run`,
  `unverified`).

## Conservation map

| Legacy origin | Conserved subject | Atomic claim IDs |
| --- | --- | --- |
| M1 | Listing framing versus first-class ls* commands | `listing-is-first-class-subcommand` |
| M2 | Two distinct listing modes and the corrected selector | `two-distinct-request-patterns`, `mode-selector-is-not-fast-list`, `forced-walk-under-disable-listr-traced` |
| M3 | The flat, serial, undelimited chain | `flat-listr-is-serial-undelimited-chain` |
| M4 | Fewer-calls/more-RAM tradeoff, streaming, and scale memory | `fast-list-tradeoff-fewer-calls-more-memory`, `s3-listr-streams-entries`, `fast-list-memory-at-scale` |
| M5 | No keyspace sharding and serial pagination (v2 and v1) | `no-intra-prefix-keyspace-sharding`, `pagination-is-serial-within-prefix`, `pagination-v1-serial-marker` |
| M6 | Pacer control law corrected (error-driven, decays to zero) | `s3-pacer-is-error-driven` |
| M7 | Pacer adapts sleep, not concurrency | `pacer-adapts-sleep-not-concurrency` |
| T1 | Default is the flat ListR, not a walk; the walk is distinct but forced | `two-distinct-request-patterns`, `mode-selector-is-not-fast-list` |
| T2 | lsjson --fast-list is the flat ListR | `flat-listr-is-serial-undelimited-chain` |
| T3 | --checkers scope on the walk, its unsmoked sweep, and --transfers | `checkers-bounds-walk-fanout`, `checkers-nondefault-timing-unsmoked`, `transfers-irrelevant-to-listing` |
| T4 | Default list-chunk observed and its unsmoked effect | `list-chunk-default-1000-observed`, `list-chunk-effect-unsmoked` |
| T5 | tpslimit token bucket distinct from the pacer | `tpslimit-is-separate-token-bucket` |
| T6 | Output formats run and no Parquet | `output-formats-lsf-lsjson-run`, `no-parquet-output` |
| T7 | Constrained-memory run deferred | `constrained-memory-fastlist-run` |
| W1 | No intra-prefix sharding; walk parallelizes across directories | `no-intra-prefix-keyspace-sharding`, `checkers-bounds-walk-fanout` |
| W2 | Fast-list memory at scale, the streaming rebuttal, the smoke data point, and the list-cutoff fix | `fast-list-memory-at-scale`, `s3-listr-streams-entries`, `fastlist-smoke-peak-rss`, `list-cutoff-external-sort` |
| W3 | OOM-killed runs reportedly exit 0 (provenance corrected, behavior unsettled) | `oom-exit-zero-report` |
| W4 | Reported >3h stall is a sync-pipeline, not listing, concern | `three-hour-stall-before-transfer` |
| W5 | Reported ~7 GB RSS at scale | `seven-gb-rss-large-listings` |
| W6 | No LIST crash-resume: source observation and unrun proof | `no-list-checkpoint-state-in-source`, `list-crash-resume-run` |
| W7 | No Parquet output | `no-parquet-output` |
| W8 | Pacer "AIMD on delay" contradicted | `s3-pacer-is-error-driven` |
| STR-1 | Mature, high-star project | `upstream-star-count` |
| STR-2 | Broad backend support | `broad-backend-support` |
| STR-3 | Structured lsf/lsjson output | `output-formats-lsf-lsjson-run` |
| STR-4 | Genuine adaptive rate-control pacer exists | `pacer-exists-adaptive-backoff` |
| ADD-1 | HEAD-per-object footgun: suppressed path run, storm mechanism source-only | `head-per-object-suppressed-at-smoke`, `head-per-object-storm-mechanism` |
| ADD-2 | Anonymous equals absence of credentials: credential starvation and wire-level no-Authorization | `anonymous-is-absence-of-credentials`, `anonymous-no-authorization-on-wire` |
| ADD-3 | storage_class rides along free from the list response | `storage-class-from-list-response` |
| ADD-4 | Legacy v1 list API is distinct, and its v1-vs-v2 parity at scale is unsmoked | `legacy-v1-list-api-distinct`, `v1-vs-v2-parity-unsmoked` |
| ADD-5 | Plain -R ignores --fast-list: source, forced-walk receipt, and OBS trace | `mode-selector-is-not-fast-list`, `forced-walk-under-disable-listr-traced`, `plain-recursive-r-is-flat-obs` |
| ADD-6 | Live NOAA bucket drift event | `noaa-bucket-drift-event` |
| META-1 | Testability row's "Trivial to install" judgment | `install-is-trivial` |
| PROSE-1 | Smoke-scale listing correctness across all verified modes | `smoke-listing-correct-all-modes` |
| PROSE-2 | Intro: ListObjectVersions API not smoked (unversioned bucket) | `versions-api-unsmoked` |
| PROSE-3 | Intro: ls/lsl/lsd are output-format variants, not separately timed | `ls-lsl-lsd-not-separately-timed` |

Reviewers should compare this map against the preserved tables in
[`tool-page.md`](tool-page.md); deciding where a compound sentence splits remains
a human judgment rather than a safe generic inference.
