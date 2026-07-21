# Swath claim-ledger migration

This is the human-auditable conservation map from the pre-restructure landing
page ([`tool-page.md`](tool-page.md)) to the atomic records in
[`../data/claims.json`](../data/claims.json). Each legacy origin appears exactly
once below. A row may map to several atomic claims, and one atomic claim may
conserve duplicated propositions from more than one origin.

## Legacy source and labeling scheme

The legacy ledger is the "Notes, questions, and observations" table on the old
page, whose rows already carried the labels `M1`, `M2`, `P1`-`P6`, `S1`, `N1`,
`N2`, and `W1`-`W6`; those labels are kept verbatim. The table's four unlabeled
status-bearing identity rows (repository, language, license, version) take
`META-1`-`META-4`; the `|  |  |` identity table at the top of the page purely
restates those same four facts (its Tier and Testability rows are study-scope
framing, conserved by `tool.json` study states and the maintainer disclosure,
not as atomic tool claims). Genuinely table-external status-bearing propositions
take deterministic `PROSE-n` labels top-to-bottom: the unimplemented `--seed
hints` (`PROSE-1`), the inspect/diff stubs (`PROSE-2`), the absent shallow
output mode (`PROSE-3`), the small-prefix probe overhead (`PROSE-4`), the
key-byte fidelity and aligned-column caveats (`PROSE-5`), the agent-asserted
image binding (`PROSE-6`), the first-party private-source basis (`PROSE-7`), the
error-classification specificity (`PROSE-8`), and the inferred amd64 support
(`PROSE-9`).

Two whole page sections restate rows already labeled and get no separate labels:
the "Open hypotheses for the benchmark" list restates the Open ledger rows it
routes (item 1 -> `M1`, 2 -> `PROSE-4`, 3 -> `P3`/`W4`, 4 -> `P4`, 5 -> `P5`,
6 -> `W3`, 7 -> `S1`/`N1`/`N2`/`W1`/`W2`, 8 -> `P6`/`W6`, 9 -> `PROSE-9`; item 0
is a methodology check), and the codex additive footer `F1`-`F12` is woven into
existing origins (F1 -> `PROSE-6`, F2 -> `PROSE-5`, F3 -> `M2`, F4 -> `PROSE-7`,
F5 -> `W3`, F6 -> `M1`, F7 -> `P4`, F8 -> `PROSE-5`, F9 -> `META-3`,
F10 -> `PROSE-8`, F11 -> `PROSE-9`; F12, firsthand modes carry source anchors, is
provenance framing conserved by the README Provenance section, not an atomic
claim).

## Evidence and disposition semantics

The migration does not promote evidence. `status` records evidence strength:
`confirmed` requires a receipt-backed run or build fact from the exact receipted
run, `supported` requires source, documentation, run, or observation evidence,
and `unverified` retains the former `VERIFIED: no` / Open state. A legacy
`CONFIRMED` whose settled facet is a self-reported counter or an exit-0 probe is
kept `confirmed` only for the exact receipted fact and split so any unrun facet
becomes its own `unverified` claim. `disposition` separately records whether
inherited wording was `retained`, `corrected`, or `contradicted`. No claim here
is `unverifiable`.

| Legacy origin | Conserved subject | Atomic claim IDs |
| --- | --- | --- |
| META-1 | Repository identity and pre-release visibility | `repo-is-private-prerelease` |
| META-2 | Implementation language and toolchain | `language-is-java` |
| META-3 | License absence and dangling notice reference | `no-license-dangling-reference` |
| META-4 | Reported version and release/tag posture | `reported-version-is-snapshot`, `no-releases-or-tags` |
| M1 | Parallel LIST behavior and its adaptive characterization | `full-run-reported-parallel-listings`, `peak-concurrency-is-scope-dependent`, `listing-is-adaptive-density-aware`, `parallelism-ratio-at-higher-concurrency` |
| M2 | Keyspace sampling, disjoint internal tiling, and avoidance of up-front hints | `internal-tiling-is-disjoint`, `sampling-replaces-blind-midpoints`, `seed-hints-unimplemented` |
| P1 | Dense-prefix splitting by sampling versus blind midpoints | `sampling-replaces-blind-midpoints` |
| P2 | Exactly-once by construction with no dedup pass, clean and under crash | `smoke-output-complete-no-duplicates`, `exactly-once-under-crash`, `no-dedup-pass-by-construction` |
| P3 | Checkpointed crash-resume design and behavior | `checkpoint-resume-design-exists`, `crash-resume-works` |
| P4 | Parquet execution and byte-exact fidelity | `parquet-modes-execute`, `parquet-output-byte-exact` |
| P5 | Bounded memory at scale | `bounded-memory-at-scale` |
| P6 | AIMD 503 adaptation, engagement, and necessity | `aimd-adapts-to-503`, `aimd-idle-at-smoke`, `aimd-necessity` |
| S1 | Cross-tool feature-combination claim | `no-tool-combines-all-features` |
| N1 | Throughput-within-ten-percent design target | `throughput-within-10pct-of-s3-fast-list` |
| N2 | s3-fast-list published throughput figure | `s3-fast-list-published-throughput` |
| W1 | Possible loss to s3-fast-list on hinted throughput | `may-lose-to-s3-fast-list-hinted` |
| W2 | Java handicap at high list rates | `java-handicap-at-high-rates` |
| W3 | Seed-cost hypothesis, absent hinted mode, and observed direction | `seed-cost-comparison`, `seed-hints-unimplemented`, `seed-cost-direction-at-smoke` |
| W4 | Resume under SIGKILL including mid-checkpoint | `crash-resume-works` |
| W5 | Exactly-once as a duplicate-checked correctness claim | `smoke-output-complete-no-duplicates`, `exactly-once-under-crash` |
| W6 | 503/AIMD possibly dead weight if latency-bound | `aimd-idle-at-smoke`, `aimd-necessity` |
| PROSE-1 | Unimplemented --seed hints | `seed-hints-unimplemented` |
| PROSE-2 | inspect and diff stubs | `inspect-diff-are-stubs` |
| PROSE-3 | No shallow ls-style output mode | `no-shallow-listing-mode` |
| PROSE-4 | Probe overhead on the small prefix | `probe-overhead-higher-on-small-prefix` |
| PROSE-5 | Key-byte fidelity, aligned column assumption, and control-char scope | `text-sink-key-fidelity-ascii-only`, `aligned-fixed-column-timestamp-assumption`, `control-char-key-fidelity-untested` |
| PROSE-6 | Agent-asserted image-to-source binding | `image-source-binding-agent-asserted` |
| PROSE-7 | First-party private-source basis | `first-party-private-source-basis` |
| PROSE-8 | Error-classification specificity | `error-classification-is-specific` |
| PROSE-9 | Inferred amd64 support | `amd64-support-inferred` |

The validator compares this declared 30-origin set against every
`legacy_origins` value in the canonical ledger in both directions. Reviewers
should additionally compare this table against the preserved tables in
[`tool-page.md`](tool-page.md), because where a compound sentence splits remains
a human judgment.
