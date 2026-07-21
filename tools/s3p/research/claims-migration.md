# s3p claim-ledger migration

This is the human-auditable conservation map from the frozen pre-restructure
landing page ([`tool-page.md`](tool-page.md)) to the atomic records in
[`../data/claims.json`](../data/claims.json). Each legacy origin appears exactly
once below. A row may map to several atomic claims, and one atomic claim may
conserve more than one legacy origin.

## Labeling scheme

The tool page carried typed reconciliation sub-tables, so those labels are kept
verbatim: metadata/testability `M1`–`M5`, mechanism `X1`–`X8`, published numbers
`N1`, and weaknesses `W1`–`W8`. The single code-anchor table (seven anchors
re-verified against the pinned checkout) is a status-bearing identity table and
takes the label `ANCHORS`; its seven anchors are conserved by the mechanism
claims that carry those source locations. One anchor — the re-pinned
`eachRecursive` range `S3Comprehensions.caf:361-503` — carries two distinct
propositions that split across two claims: the engine re-pin is conserved by
`bisects-unknown-keyspace`, while the two-`s3.list`-calls-per-node and full-page
recursion-stop propositions are conserved by `two-lists-per-node`, so `ANCHORS`
maps to eight claims. The identity table's `Tier` row ("1 — included in the
planned comparative runs") is a study-scope bookkeeping fact, not a tool claim;
it carries no `M` label and is conserved in the Tier column of
[`../../README.md`](../../README.md) (Tier 1) rather than as a ledger row. The
additive new-findings
rows keep their labels, except that the schema requires all-uppercase origin
tokens, so the auth finding written `H-Auth` on the tool page is labeled
`HAUTH` here; the other additive rows stay `V1`, `V2`, `V3`. Three status-bearing
propositions that live only in the tool page's prose — the soft-cap semantics of
`--max-list-requests`, the lossy nature of `ls --long`, and the synthetic-fixture
validation of the adapter — take deterministic `PROSE-1`, `PROSE-2`, `PROSE-3`
labels. Prose bullets that merely restate a table row (the "What we tried and
saw" bullets and the reconciled "Claimed strengths") are conserved by that row's
origin and get no separate label. The tool page's four cross-cutting mentions of
s3p are explicitly routed to `docs/open-questions.md` on that page and are
out of scope for this ledger.

## Status and disposition semantics

The migration does not promote evidence. `status` records evidence strength:
`confirmed` requires a receipt-backed run fact, `supported` requires source,
documentation, observation, or run evidence, and `unverified` retains the former
`VERIFIED: no` state. The legacy `CONFIRMED` on the auth finding is preserved as
`confirmed` only for the receipted run fact (the probes' credential-resolution
failure); its source-level "no anonymous path anywhere" facet is split into a
separate `supported` claim. `disposition` separately records whether inherited
wording was retained, corrected, or contradicted. No claim in this ledger is
`unverifiable`.

| Legacy origin | Conserved subject | Atomic claim IDs |
| --- | --- | --- |
| M1 | Canonical repository | `repo-is-generalui-s3p` |
| M2 | Language and runtime | `language-is-caffeinescript-on-node` |
| M3 | License correction | `license-is-isc` |
| M4 | Pinned source vs smoked version | `tested-version-is-3-7-2` |
| M5 | Testability reframed to listing isolability | `listing-is-isolable` |
| X1 | Keyspace bisection and its comparative uniqueness | `bisects-unknown-keyspace`, `bisection-unique-among-survey` |
| X2 | Arithmetic midpoint without key sampling | `bisect-key-is-arithmetic-no-sampling` |
| X3 | Two LISTs per node and the scheduled probe | `two-lists-per-node`, `probe-scheduled-two-lists` |
| X4 | Disjoint kept-sets and wasted-work fraction | `per-node-kept-sets-disjoint`, `list-waste-fraction` |
| X5 | LIFO pool and concurrency default | `lifo-pool-default-100` |
| X6 | Prefix bisection is internal and its no-faster claim | `prefix-bisect-is-library-internal`, `prefix-bisect-no-faster` |
| X7 | Listing is a standalone subcommand | `listing-is-isolable` |
| X8 | Alphabet size correction | `alphabet-is-95-chars` |
| N1 | Published throughput numbers and their reproduction | `throughput-numbers-are-author-self-reported`, `throughput-numbers-reproduced` |
| W1 | Wasted-work fraction and the logical request counter | `list-waste-fraction`, `request-counter-is-logical-not-http` |
| W2 | Skewed-keyspace extra rounds | `skewed-keyspace-extra-rounds` |
| W3 | Single-core architecture and throughput plateau | `single-core-node-process`, `single-core-throughput-plateau` |
| W4 | CLI streaming, library accumulation, and the OOM report | `cli-ls-streams`, `library-api-accumulates`, `oom-at-100m-objects` |
| W5 | SDK default retries and the transient-503 crash hypothesis | `no-s3p-level-retry-sdk-defaults-remain`, `transient-503-crashes-run` |
| W6 | Non-ASCII throw and its runtime behavior | `non-ascii-key-throws`, `non-ascii-runtime-behavior` |
| W7 | UTF-16 ordering divergence and its runtime effect | `utf16-ordering-runs-before-throw`, `utf16-ordering-runtime-behavior` |
| W8 | Author-flagged bug and its guarded assertion | `bisect-key-postcondition-assertion`, `bisect-key-postcondition-runtime` |
| ANCHORS | Seven re-verified code anchors | `bisect-key-is-arithmetic-no-sampling`, `alphabet-is-95-chars`, `non-ascii-key-throws`, `utf16-ordering-runs-before-throw`, `bisect-key-postcondition-assertion`, `bisects-unknown-keyspace`, `two-lists-per-node`, `lifo-pool-default-100` |
| HAUTH | No anonymous access and the blocked probes | `no-anonymous-access-path`, `anonymous-listing-blocked-at-auth` |
| V1 | Git tags lag npm | `git-tags-lag-npm` |
| V2 | Published v3.6.0 cannot start | `v3-6-0-cannot-start` |
| V3 | 3.7.x adds flags | `v3-7-x-adds-flags` |
| PROSE-1 | Soft-cap request budget | `max-list-requests-is-soft-cap` |
| PROSE-2 | Lossy ls --long output | `ls-long-is-lossy` |
| PROSE-3 | Adapter fixture validation | `normalize-validated-against-synthetic-fixtures` |

The validator compares this map against every `legacy_origins` value in
[`../data/claims.json`](../data/claims.json) in both directions. Reviewers should
also compare it against the preserved tables in [`tool-page.md`](tool-page.md),
because deciding where a compound sentence splits remains a human judgment.
