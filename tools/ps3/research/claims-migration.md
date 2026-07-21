# pS3 claim-ledger migration

This is the human-auditable conservation map for the pS3 capsule. It maps every
status-bearing origin on the pre-restructure landing page
([`tool-page.md`](tool-page.md)) to the atomic records in
[`../data/claims.json`](../data/claims.json). Each origin appears exactly once
below; an origin may map to several atomic claims.

## Labeling scheme

- The four typed reconciliation sub-tables keep their existing row labels:
  metadata `M1`-`M6`, mechanism `A1`-`A6`, tunables/modes `T1`-`T4`, published
  numbers `N1`-`N3`, and weakness hypotheses `W1`-`W4`. These 23 rows are the
  inherited ledger, matching [`reconciliation.md`](reconciliation.md).
- The identity table's status-bearing content that is table-external takes
  `META-n`. Its `Repo`/`Language`, `License`, and `Version reviewed` rows restate
  `M4`, `M1`, and `M2`/`M3`, and the `Tier 1 — primary subject` row is study
  bookkeeping, not a tool proposition, and gets no claim. Two identity rows carry
  genuinely additional facts beyond those restatements: the `Language` cell's
  build-metadata dependency versions become `META-1`, and the `Testability` row —
  besides restating `M5`'s does-not-build verdict — carries the amd64-only /
  qemu-emulation and only-working-artifact constraint, conserved as `META-2`.
- Genuinely table-external status-bearing prose takes `PROSE-n`. The four
  "Additive findings" bullets become `PROSE-1`-`PROSE-4`; the streaming
  memory-model hypothesis (Open hypotheses item 7) becomes `PROSE-5`; the
  `--prefix-count` primary-knob fact (Open hypotheses item 3 and the mechanism
  summary) becomes `PROSE-6`; and the repository-shape and staleness facts (the
  "What we tried and saw" no-README/docs/go.mod, 16-commits, single-author,
  last-2024-01-02 bullet) become `PROSE-7`.
- Prose that only restates a table row or an already-labeled origin is conserved
  by it and gets no separate label: the three "What limited the run" blockers
  restate `PROSE-1`/`T1`/`M5`, and blocker #3 also restates `META-2`; the "What
  we tried and saw" bullets restate `PROSE-2`/`M5`/`PROSE-3`/`N1`-`N3` (the
  repository-shape bullet is `PROSE-7`, above); the "Claimed strengths" mapping
  restates `A6` (zero-config) and `W2` (arbitrary keyspaces, including the
  positive flat-keyspace property); and the remaining Open-hypotheses items
  restate `N1`-`N3`, `T1`, `T2`, `W1`-`W4`, `M5`, and `META-2` (item 8,
  architecture).

## Status and disposition semantics

The migration does not promote evidence. `status` records evidence strength:
`confirmed` requires a receipt-backed run or build fact, `supported` requires
source, documentation, run, or observation evidence, and `unverified` retains the
former VERIFIED: no or Unaddressed state with a reason. Source reading alone never
promotes a proposition past `supported`. `disposition` separately records whether
inherited wording was `retained`, `corrected`, or `contradicted`.

Two forced status re-maps went down, never up: the inherited `M5` does-not-build
verdict is receipt-backed and stays `confirmed`, but the version self-report
(`M2`) is receipt-confirmed while the general no-unsigned-path finding stays
`supported` and its narrow one-invocation receipt fact is split into the separate
`confirmed` claim `list-anon-exit-1-narrow`. The unrun facets of every blocked
mode or benchmark question are split into their own `unverified` claims.

## Conservation map

| Legacy origin | Conserved subject | Atomic claim IDs |
| --- | --- | --- |
| M1 | License is GPL-3.0, not MIT | `license-is-gpl-3` |
| M2 | Version self-report 0.1.16 | `version-is-0-1-16` |
| M3 | No releases or tags; pinned HEAD | `pinned-to-head-no-releases` |
| M4 | Repository identity and language | `repo-is-jboothomas-ps3-go` |
| M5 | Source does not compile | `source-does-not-compile` |
| M6 | Source independently read | `source-independently-read` |
| A1 | Brute-force character walk, not bisection | `discovery-is-brute-force-char-walk` |
| A2 | Recursion on page overflow | `recursion-extends-prefix-on-overflow` |
| A3 | 999 branch never checks IsTruncated | `overflow-branch-tests-999-not-truncated` |
| A4 | Fixed 81-character alphabet var | `alphabet-is-fixed-81-char-var` |
| A5 | discoverPrefixes anchor accuracy | `discover-prefixes-anchor-accurate` |
| A6 | Discovery needs no bootstrap | `discovery-needs-no-bootstrap` |
| T1 | Concurrency: 256 pager var, unbounded discovery, uncappable | `pager-concurrency-is-256-var`, `discovery-goroutines-unbounded`, `no-concurrency-flag-uncappable` |
| T2 | Output flag inert in source; binary behavior unknown | `output-flag-inert-in-source`, `output-flag-binary-behavior` |
| T3 | Page size and threshold are 1000 | `page-size-is-1000-var` |
| T4 | Full flag surface captured | `full-flag-surface-captured` |
| N1 | Blog throughput 160 s | `blog-throughput-160s` |
| N2 | Blog ~7x versus aws s3api | `blog-aws-cli-7x` |
| N3 | Blog ~5x versus s5cmd | `blog-s5cmd-5x` |
| W1 | Discovery tax: speculative LISTs and cost at scale | `speculative-lists-exist`, `discovery-tax-cost-at-scale` |
| W2 | Out-of-alphabet keys dropped at source and runtime; positive flat-keyspace property | `out-of-alphabet-keys-dropped`, `out-of-alphabet-drop-runtime`, `flat-keyspace-no-degeneration` |
| W3 | Headline figures reproduce | `headline-figures-reproduce` |
| W4 | Multipliers internally falsifiable | `multipliers-internally-falsifiable` |
| META-1 | Binary build-metadata dependency versions | `binary-build-metadata-versions` |
| META-2 | Binary is amd64-only, ran under qemu emulation, only working artifact | `binary-amd64-only-ran-under-qemu` |
| PROSE-1 | No unsigned request path; narrow list-anon receipt; other modes blocked | `no-unsigned-request-path`, `list-anon-exit-1-narrow`, `other-modes-blocked-by-inference` |
| PROSE-2 | Silent exit-0 empty output on bare no-creds | `silent-exit-0-on-bare-no-creds` |
| PROSE-3 | Binary not reproducible from source | `binary-not-reproducible-from-source` |
| PROSE-4 | Region bug, error-swallowing nil-deref, debug/trace suppress output | `get-bucket-location-region-bug`, `error-swallowing-nil-deref`, `debug-trace-suppress-output` |
| PROSE-5 | Streaming memory model and scale behavior | `streaming-bounds-object-memory`, `memory-throughput-at-scale` |
| PROSE-6 | --prefix-count is the primary discovery knob | `prefix-count-is-primary-knob` |
| PROSE-7 | Repository is minimal and dormant (no README/docs/go.mod, 16 commits, single author, last 2024-01-02) | `repository-is-minimal-and-dormant` |

The validator compares this 32-origin set against every `legacy_origins` value in
[`../data/claims.json`](../data/claims.json) in both directions. Reviewers should
additionally compare this table with the preserved tables and prose in
[`tool-page.md`](tool-page.md), because deciding where a compound proposition
splits remains a human judgment.
