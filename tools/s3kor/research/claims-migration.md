# s3kor claim-ledger migration

This is the human-auditable conservation map from the pre-restructure landing
page ([`tool-page.md`](tool-page.md)) to the atomic records in
[`../data/claims.json`](../data/claims.json). Each legacy origin appears exactly
once in the table below. A row may map to several atomic claims, and one atomic
claim may conserve more than one legacy origin.

## Legacy source and semantics

The legacy source is the consolidated s3kor landing page, whose "Notes,
questions, and observations" section carries a single review ledger with stable
row labels already assigned in [`reconciliation.md`](reconciliation.md):
metadata (`M1`–`M5`), mechanism (`X1`–`X4`), modes/tunables (`D1`–`D3`),
claimed strengths (`S1`–`S3`), claimed weaknesses (`W1`–`W2`), what-to-verify
(`V1`), and net-new findings (`N1`–`N7`). The landing page states that every
ledger row traces to a reconciliation row and that no additive rows exist, so
these 25 labels are reused verbatim rather than reinvented.

**Labeling scheme (documented per the migration judgment rules).**

- The 25 typed ledger rows keep their existing `M`/`X`/`D`/`S`/`W`/`V`/`N`
  labels.
- The top identity table (Repo, Language, License, Version reviewed, Upstream
  health, Testability) restates ledger rows `M1`–`M5` and `N6`; its `Tier` row
  is study-process metadata, not a claim about the tool. These identity rows are
  therefore conserved by their `M`/`N` origins and take no separate `META`
  label. The "What we saw" and "What we tried and saw" summary bullets likewise
  purely restate ledger rows and take no separate label.
- Three genuinely table-external, status-bearing propositions carried in the
  "Open hypotheses" and "Known caveats" prose take deterministic `PROSE`
  labels: `PROSE-1` (memory is streaming but not back-pressured, and the
  scale-OOM question), `PROSE-2` (list-versions is manifest-comparable only on
  an unversioned bucket), and `PROSE-3` (architecture support: amd64/arm64
  native on every channel, and the arm64-native smoke run).

**Evidence-strength and disposition mapping.** `status` records current evidence
strength, `disposition` records the relationship to inherited wording, and the
two are independent. Legacy `CONFIRMED` rows keep a `confirmed` claim only for
the facet a committed receipt itself records; the receipt-backed capability
finding (`N1`) and the panic (`N2`) each keep a `confirmed` run claim, while the
source-only facet of `N1` (no unsigned listing path) is split into a `supported`
source claim — a down-split, never an upgrade. Legacy `VERIFIED: no` rows map to
`supported` where source, documentation, observation, or live-help evidence
corroborates the proposition, and to `unverified` where the proposition remains
an unrun hypothesis. Legacy `CORRECTED` rows (a factually wrong static value,
shown both sides) map to `disposition: corrected`. No claim is `unverifiable`.

Legacy `M4` (reviewed version corrected from unknown) is non-atomic, so it splits
into two `corrected` claims, each carrying evidence that records its own facet:
`reviewed-version-is-v0037` holds the module-version and self-reported-version
facet, evidenced by the panic-stack run receipt (which embeds the
`s3kor@v0.0.37` module path) and the first-execution observation (which records
`dev-local-version none unknown`); `reviewed-subject-pinned-commit` holds the
pinned-commit and release-tag facet, evidenced by the groundwork report.

## Conservation table

| Legacy origin | Conserved subject | Atomic claim IDs |
| --- | --- | --- |
| M1 | Repository identity | `repository-identity` |
| M2 | Language is Go | `written-in-go` |
| M3 | License is GPL-3.0 | `license-is-gpl3` |
| M4 | Reviewed version corrected from unknown | `reviewed-version-is-v0037`, `reviewed-subject-pinned-commit` |
| M5 | Trivial to install, untestable to list here | `install-is-trivial`, `credential-starved-listing-blocked` |
| X1 | aws s3 re-implementation with four subcommands | `is-aws-s3-reimplementation` |
| X2 | Transfer-side multipart/multithreaded shape | `transfer-concurrency-flags-not-listing` |
| X3 | Listing is serial; scale cost not settled | `listing-is-serial-paginator`, `serial-listing-scale-cost-unverified` |
| X4 | Does not collapse into the s5cmd finding | `not-a-s5cmd-corollary` |
| D1 | ls is the primary, serial listing surface | `listing-is-serial-paginator` |
| D2 | Flag is --all-versions; perf-vs-plain unrun | `list-versions-flag-is-all-versions`, `list-versions-perf-unverified` |
| D3 | cp/sync concurrency flags vs ls | `transfer-concurrency-flags-not-listing` |
| S1 | Multipart multithreaded transfer | `transfer-concurrency-flags-not-listing` |
| S2 | Cross-account bucket-to-bucket copy | `cross-account-copy-supported` |
| S3 | Go performance similar to s5cmd/rclone | `go-performance-comparable-unverified` |
| W1 | Serial listing confirmed, scale not settled | `listing-is-serial-paginator`, `serial-listing-scale-cost-unverified` |
| W2 | Distinct from s5cmd, not a corollary | `not-a-s5cmd-corollary` |
| V1 | Read listing source first; resolved serial | `listing-is-serial-paginator` |
| N1 | No unsigned listing path; blocked by smoke | `credential-starved-listing-blocked`, `no-unsigned-listing-path` |
| N2 | Session-build panic, scoped | `session-build-panic` |
| N3 | ls concurrency race, source-visible, unobserved | `ls-concurrency-race-source`, `ls-race-runtime-behavior-unverified` |
| N4 | README documents nonexistent --auto-region | `region-flag-doc-drift` |
| N5 | --verbose logs to a temp file | `verbose-logs-to-temp-file` |
| N6 | Upstream dormant since 2022 | `upstream-dormant` |
| N7 | No upstream image or Dockerfile | `no-upstream-image-or-dockerfile` |
| PROSE-1 | Memory streaming, not back-pressured; scale OOM | `memory-streaming-not-backpressured`, `memory-oom-at-scale-unverified` |
| PROSE-2 | list-versions comparable only on unversioned bucket | `list-versions-manifest-comparable-only-unversioned` |
| PROSE-3 | amd64/arm64 native support; arm64-native smoke | `amd64-native-support`, `smoke-ran-arm64-native` |

The validator compares this 28-origin set with every `legacy_origins` value in
[`../data/claims.json`](../data/claims.json) in both directions. Reviewers
should additionally compare this table against the preserved ledger in
[`tool-page.md`](tool-page.md), since deciding where a compound sentence splits
remains a human judgment.
