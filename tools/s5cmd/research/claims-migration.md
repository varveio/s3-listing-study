# s5cmd claim-ledger migration

This is the human-auditable conservation map from the pre-restructure landing
page to the atomic records in [`../data/claims.json`](../data/claims.json). Each
legacy origin appears exactly once below. A row may map to several atomic
claims, and one atomic claim may conserve duplicated propositions from more than
one origin.

The legacy source is the frozen [`tool-page.md`](tool-page.md). Its
status-bearing propositions come from three places, labelled deterministically:

- `META-1` — the **Testability** row of the identity table at the top of the
  page. The other identity rows (repo, language, license, version tested, tier)
  are canonical identity and live in [`../data/tool.json`](../data/tool.json),
  not as claims.
- `NOTE-1` .. `NOTE-19` — the nineteen rows of the **Notes, questions, and
  observations** table, top to bottom.
- `PROSE-1` .. `PROSE-6` — status-bearing propositions outside that table: the
  observability finding and the every-mode-passed statement in **What we tried
  and saw**, and the four bullets in **Known caveats carried forward**. The two
  remaining **What we tried and saw** bullets restate `NOTE-1` (serial chain)
  and `NOTE-7`/`NOTE-18` (the fan-out) and carry no origin of their own.

The migration does not promote evidence. `status` records evidence strength:
`confirmed` requires a receipt-backed run, `supported` requires source,
documentation, run, or observation evidence, and `unverified` retains the former
`VERIFIED: no` or `Unaddressed` state. `disposition` separately records whether
inherited wording was retained, corrected, or contradicted. No claim is
classified `unverifiable`: the legacy legend defined that term but no legacy row
used it, and the open owner ruling on the 2025 re-testing pointer (`NOTE-14`) is
not settled here, so that row keeps the conservative `unverified` reading.

One schema-forced demotion, never a promotion: `NOTE-1`'s legacy CONFIRMED
runtime facet rests on an `[OBS]` capability probe, not a smoke-run receipt,
and `confirmed` requires run evidence — so `ls-is-one-serial-list-chain` is
`supported`, with the unrun scale facet split into the `unverified`
`serial-listing-at-scale-unverified`.

`NOTE-19` (the 1,000-key page-size correction) is additive to the inherited
dossier: it entered via the codex review, not the seed page, as the tool page's
own footer records.

| Legacy origin | Conserved subject | Atomic claim IDs |
| --- | --- | --- |
| META-1 | Testability: prebuilt binaries and the study image | `prebuilt-binaries-published-per-release` |
| NOTE-1 | Serial ls chain, no keyspace division, and the scale scope | `ls-is-one-serial-list-chain`, `no-native-keyspace-division`, `serial-listing-at-scale-unverified` |
| NOTE-2 | Responsible-file anchor correction | `list-issued-in-storage-not-ls-consumer` |
| NOTE-3 | All parallelism transfer-side, scoped to a lone ls | `parallelism-is-transfer-side-for-lone-ls` |
| NOTE-4 | The 5-10x reputation is transfers, and the ls magnitude | `speed-reputation-is-about-transfers`, `ls-vs-aws-s3api-magnitude-unverified` |
| NOTE-5 | Native ls baseline runs | `recursive-ls-runs-as-baseline` |
| NOTE-6 | Output flags are formatting-only | `output-flags-are-formatting-only` |
| NOTE-7 | Mandatory per-prefix fan-out and the run flag correction | `fanout-completeness-verified`, `run-takes-file-positionally-no-f-flag` |
| NOTE-8 | numworkers on run and the sweep | `numworkers-sizes-run-fanout-concurrency`, `numworkers-sweep-unverified` |
| NOTE-9 | cp/sync transfer separation | `cp-sync-transfer-separation-unverified` |
| NOTE-10 | The 15M-object, 733s survey figure | `swath-survey-15m-733s-unverified` |
| NOTE-11 | ls versus aws s3api magnitude | `ls-vs-aws-s3api-magnitude-unverified` |
| NOTE-12 | The 400k-file sync RAM report and the ls smoke memory | `sync-400k-ram-report-unverified`, `ls-streaming-memory-at-smoke` |
| NOTE-13 | Killed at 15M objects | `killed-at-15m-objects-unverified` |
| NOTE-14 | The 2025 re-testing pointer | `retest-2025-pointer-unverified` |
| NOTE-15 | Docs describe transfer worker-pool parallelism | `speed-reputation-is-about-transfers` |
| NOTE-16 | Glob and wildcard support | `glob-wildcard-support` |
| NOTE-17 | Low resource use, transfer and sync side | `sync-400k-ram-report-unverified` |
| NOTE-18 | Fan-out behavior question: coverage and speed | `fanout-completeness-verified`, `fanout-speed-vs-native-unverified` |
| NOTE-19 | The 1,000-key page-size correction | `page-size-1000-is-s3-ceiling` |
| PROSE-1 | --log trace exposes per-request pages | `log-trace-exposes-per-request-pages` |
| PROSE-2 | Every smoked mode passed unsigned | `all-smoked-modes-passed-anonymous` |
| PROSE-3 | Adapter whitespace key-byte fidelity | `adapter-whitespace-key-fidelity-loss` |
| PROSE-4 | allversions request-contract scope and version fidelity | `allversions-validates-request-contract-only`, `allversions-multiversion-fidelity-unverified` |
| PROSE-5 | Cosmetic double-prefix receipt cell | `receipt-double-prefix-cosmetic-defect` |
| PROSE-6 | Codex correction of the observability/serial-listing record | `log-trace-exposes-per-request-pages` |

The validator compares this map against every `legacy_origins` value in the
canonical ledger in both directions. Reviewers should additionally compare this
table with the preserved [`tool-page.md`](tool-page.md), because deciding where a
compound row splits into atomic claims remains a human judgment.
