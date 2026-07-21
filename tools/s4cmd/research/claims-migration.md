# s4cmd claim-ledger migration

This is the human-auditable conservation map from the pre-restructure landing
page ([`tool-page.md`](tool-page.md)) to the atomic records in
[`../data/claims.json`](../data/claims.json). Each conserved legacy origin
appears exactly once below. A row may map to several atomic claims, and one
atomic claim may conserve overlapping propositions from more than one legacy
origin.

## Legacy source and label scheme

The legacy ledger is the "Notes, questions, and observations" table on the
frozen tool page, whose rows already carry stable reconciliation labels: `M1`-`M6`
(metadata/identity), `C1`-`C4` (capability/positioning), `T1`-`T3` (test-mode
premises), `S1`-`S2` (strengths), `W1`-`W3` (weaknesses), `WV` (what-to-verify),
and `N1` (a firsthand additive finding). Those existing labels are reused
verbatim; no relabeling scheme was invented. Status-bearing propositions in the
tool-page body that lie outside that table take deterministic `PROSE-n` labels,
numbered top-to-bottom through the page.

## Judgments recorded for audit

- **Identity and bookkeeping rows are conserved canonically, not as behavioral
  claims.** Per the driver ruling, status-bearing origins are declared in
  `legacy_origins`; pure identity and bookkeeping rows are conserved canonically
  and named here. `M1` (repository is the canonical bloomreach project, not a
  fork) is conserved in [`../data/tool.json`](../data/tool.json) `tested.variant`
  (`upstream`) and the README; `M2` (language: single-file Python) in `tool.json`
  `language`; `M3` (license: Apache-2.0) in `tool.json` `license`; and `M5`
  (study-design Tier 2) in the Tier-1/Tier-2 catalog column of
  [`../../README.md`](../../README.md), which is study bookkeeping rather than a
  tool fact. These four are preserved verbatim in the frozen tool page, carry no
  separate atomic claim, and are excluded from `expected_origins`. `M4` (reviewed
  version corrected from unknown to 2.1.0) is a correction and therefore
  status-bearing: it is conserved as claim `tested-version-corrected-to-2-1-0`.
  `M6` (testability) is behavioral and is conserved as a claim.
- **Prose bullets that restate a table row carry no separate label.** The four
  "What we tried and saw" bullets restate ledger rows (`N1`; the `C`/`T`/`S`/`W`
  parallelism cluster; `M6`; `W3`) and are conserved through those origins.
- **Only genuinely table-external propositions take `PROSE` labels.** These are
  the "Open hypotheses" and "Known caveats" propositions with no ledger row:
  memory model and OOM ceiling (`PROSE-1`), request amplification and true LIST-
  page counting (`PROSE-2`), 503/throttling handling (`PROSE-3`), client-CPU cost
  (`PROSE-4`), pure-Python architecture neutrality (`PROSE-5`), retry-induced key
  duplication (`PROSE-6`), the `S3APICALL` observability limit (`PROSE-7`), and
  key-byte fidelity (`PROSE-8`). The open-hypothesis "flat prefix collapses to
  serial" restates the `C3` residual truth and is conserved through `C3`; the
  open-hypothesis "current-S3 v1 compatibility" is explicitly carried from `W3`
  and is conserved through `W3`. The tool page's "no claimed numbers inherited"
  note asserts the absence of any numeric claim, so it has no atomic proposition
  to conserve and survives only in the frozen page.

## Status and disposition semantics

The migration does not promote evidence. `status` records evidence strength:
`confirmed` requires a receipt-backed run or build fact, `supported` requires
source, documentation, run, or observation evidence, and `unverified` retains
the legacy `VERIFIED: no` state (a testable but unsettled proposition).
`disposition` separately records whether inherited wording was `retained`,
`corrected`, or `contradicted`. No claim is classified `unverifiable`.

Two demotions were forced downward, never upward. The nine legacy
`CONTRADICTED` rows (`C2`, `C3`, `C4`, `T1`, `T2`, `S1`, `W1`, `W2`, `WV`)
falsify the inherited wording, but the corrected delimiter-recursion mechanism
they point to is itself `VERIFIED: no` at runtime because no listing mode could
execute; each corrected mechanism claim therefore stays `supported`, with the
unrun runtime facet split into its own `unverified` claim
(`flat-prefix-serial-collapse-unverified`, `delimiter-recursion-scaling-benchmark`).
The legacy `N1` row is `CONFIRMED` only for the one recursive capability probe;
the other-mode block is demoted to a `supported` source inference.

| Legacy origin | Conserved subject | Atomic claim IDs |
| --- | --- | --- |
| M4 | Reviewed version corrected from unknown to 2.1.0 | `tested-version-corrected-to-2-1-0` |
| M6 | Testability: installs, imports, and runs under current boto3 | `installs-imports-runs-under-current-boto3` |
| C1 | Positioning as a Super S3 CLI for large-file workflows | `positioned-for-large-file-workflows` |
| C2 | Parallelism unit is delimiter recursion, not CLI-prefix sharding | `parallelism-unit-is-delimiter-recursion`, `ls-always-sends-delimiter`, `one-list-objects-v1-per-pseudo-directory` |
| C3 | In-prefix substructure parallelizes; flat prefix collapses to serial | `ls-always-sends-delimiter`, `single-prefix-substructure-parallelizes`, `flat-prefix-collapses-to-serial`, `flat-prefix-serial-collapse-unverified` |
| C4 | No caller sharding; ls takes exactly one path | `parallelism-unit-is-delimiter-recursion`, `ls-accepts-exactly-one-path` |
| T1 | Single-prefix "serial baseline" premise is false | `single-prefix-substructure-parallelizes` |
| T2 | Multi-prefix "best mode" does not exist | `ls-accepts-exactly-one-path` |
| T3 | Thread-count flag present and its default | `num-threads-flag-present`, `num-threads-default-is-cpu-count-times-four` |
| S1 | Multi-prefix parallelism misattributed to prefix arguments | `parallelism-unit-is-delimiter-recursion`, `ls-accepts-exactly-one-path` |
| S2 | Positioning for large-file, data-intensive use | `positioned-for-large-file-workflows` |
| W1 | No in-prefix keyspace discovery is false | `single-prefix-substructure-parallelizes` |
| W2 | Manual-sharding-only weakness is false | `parallelism-unit-is-delimiter-recursion`, `ls-accepts-exactly-one-path` |
| W3 | Upstream dormancy and current-S3 v1 compatibility | `upstream-is-dormant`, `v1-current-s3-compatibility-unverified` |
| WV | Real benchmark question is delimiter-recursion scaling | `delimiter-recursion-scaling-benchmark` |
| N1 | No unsigned access; every listing mode blocked | `no-unsigned-request-support`, `recursive-blocked-without-credentials`, `other-modes-share-blocked-constructor-path`, `bare-env-fails-at-first-list-call` |
| PROSE-1 | Accumulate-then-sort-then-dump memory model and OOM ceiling | `accumulate-then-sort-then-dump`, `memory-ceiling-oom-unverified` |
| PROSE-2 | Request amplification and true LIST-page counts | `request-amplification-on-deep-trees`, `delimiter-recursion-scaling-benchmark` |
| PROSE-3 | 503 SlowDown not retried; throttling behavior | `http-503-not-in-retryable-set`, `throttling-behavior-unverified` |
| PROSE-4 | Client-CPU cost of formatting plus full sort | `client-cpu-cost-unverified` |
| PROSE-5 | Pure-Python architecture neutrality | `pure-python-architecture-neutral` |
| PROSE-6 | Retry-induced key duplication | `retry-can-duplicate-keys`, `retry-duplication-run-unverified` |
| PROSE-7 | S3APICALL cannot count LIST pages | `s3apicall-cannot-count-list-pages` |
| PROSE-8 | Key-byte fidelity, synthetic fixtures, deferred edge keys | `key-byte-fidelity-tool-side-loss`, `adapter-fixtures-are-synthetic`, `edge-key-fidelity-deferred` |

The validator compares the declared 24-origin set with every `legacy_origins`
value in the canonical ledger and with this table in both directions. Reviewers
should additionally compare this map against the preserved tables in
[`tool-page.md`](tool-page.md), because deciding where a compound sentence splits
remains a human judgment rather than a safe generic inference.
