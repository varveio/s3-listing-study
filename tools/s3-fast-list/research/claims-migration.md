# s3-fast-list claim-ledger migration

This is the current human-auditable conservation map for the capsule pilot. It
maps all 43 table rows plus six status-bearing prose claims from the
pre-restructure landing page to the atomic records in
[`../data/claims.json`](../data/claims.json). Each legacy row appears exactly
once in this table. A row may map to several atomic claims, and the same atomic
claim may conserve duplicated propositions from more than one legacy row.

The migration does not promote evidence. `status` records evidence strength:
`confirmed` requires a receipt-backed run or build fact, `supported` requires
source, documentation, run, or observation evidence, and `unverified` retains
the former Unaddressed or VERIFIED: no state. `disposition` separately records
whether inherited wording was retained, corrected, or contradicted. No claim
in this ledger was classified `unverifiable`.

| Legacy origin | Conserved subject | Atomic claim IDs |
| --- | --- | --- |
| M1 | Range bounds and disjointness | `range-start-is-exclusive`, `range-end-is-exclusive`, `ranges-are-disjoint` |
| M2 | Cargo workspace and two crates | `workspace-has-two-crates` |
| M3 | Range count and correctness regardless of hint balance | `hint-count-creates-range-count`, `hint-boundary-key-can-be-omitted`, `hinted-correctness-run` |
| M4 | No-hints serial path, concurrency effect, and accumulation memory | `no-hints-creates-one-range`, `concurrency-needs-multiple-ranges`, `listing-accumulates-before-dump`, `memory-grows-with-bucket-size` |
| M5 | Three hint sources, ks-tool existence, and ks-tool internals | `hints-have-three-input-sources`, `ks-tool-input-generation-internals`, `ks-tool-subcommands-exist` |
| M6 | Runtime and top-level task counts | `tokio-runtime-is-multithreaded`, `mode-task-counts` |
| M7 | Start barrier | `barrier-synchronizes-task-start` |
| M8 | Hand-rolled reactor and concurrency default | `reactor-uses-task-vector`, `concurrency-default-is-100` |
| M9 | Page loop, timeout, resume, channel, error threshold, and fatal behavior | `page-timeout-is-five-seconds`, `page-loop-resumes-from-last-key`, `result-channel-is-unbounded`, `error-threshold-is-hex-10`, `fatal-slice-error-can-exit-zero` |
| M10 | SDK and application retry layers | `sdk-retry-policy`, `application-retry-layer` |
| M11 | Global atomics, cancellation, and error counters | `global-state-uses-atomics`, `no-structured-cancellation-token`, `three-error-counters` |
| M12 | Two-level accumulation map | `object-map-has-two-lock-levels` |
| M13 | Packed ObjectProps layout | `object-props-is-packed-and-aligned` |
| M14 | ETag representation and panic behavior | `etag-stored-as-md5-and-part-count`, `unsupported-etag-format-panics`, `unusual-etag-runtime-behavior` |
| M15 | Diff classification and Rhai filtering | `diff-classifies-object-matches`, `rhai-filter-application-points` |
| M16 | Parquet and keyspace CSV outputs, including encoding correction | `parquet-output-contract`, `parquet-uses-dictionary-encoding`, `keyspace-csv-output-contract` |
| M17 | Rhai allowlist and limits | `rhai-expression-allowlist`, `rhai-resource-limits` |
| MODE-1 | Optional hints, without-hints run, and blocked hinted path | `no-hints-creates-one-range`, `hint-file-is-optional`, `hinted-mode-run` |
| MODE-2 | Concurrency default and no-hints effect | `concurrency-needs-multiple-ranges`, `concurrency-default-is-100` |
| MODE-3 | Worker-thread default | `thread-default-is-10` |
| MODE-4 | Rhai filter surface | `filter-exposes-two-object-maps` |
| MODE-5 | Endpoint path style and absence of a virtual-hosted override | `custom-endpoint-forces-path-style`, `custom-endpoint-has-no-virtual-hosted-override` |
| MODE-6 | List and Diff task counts | `mode-task-counts` |
| MODE-7 | ks-tool subcommand existence and unreviewed internals | `ks-tool-input-generation-internals`, `ks-tool-subcommands-exist` |
| MODE-8 | Output formats, absence of an alternate listing format, and smoke routing | `parquet-output-contract`, `keyspace-csv-output-contract`, `no-alternate-listing-output`, `parquet-routed-to-stdout-at-smoke` |
| NUMBER-1 | Published concurrency ladder, reproduction state, and instance memory | `vendor-concurrency-ladder-is-published`, `vendor-concurrency-ladder-reproduced`, `m6i-8xlarge-has-128-gib` |
| NUMBER-2 | ObjectProps byte size | `object-props-size-is-40-bytes` |
| NUMBER-3 | 100-million-object memory and ETag saving estimates | `object-props-100m-memory-estimate`, `binary-etag-memory-saving` |
| NUMBER-4 | SDK retry and page timeout constants | `page-timeout-is-five-seconds`, `sdk-retry-policy` |
| NUMBER-5 | Parquet, keyspace CSV, and ks-tool reader buffers | `parquet-output-contract`, `keyspace-csv-output-contract`, `ks-tool-split-reader-buffer` |
| NUMBER-6 | Rhai limits | `rhai-resource-limits` |
| NUMBER-7 | Dependency versions and absent upper bounds | `dependency-versions-are-unbounded-above` |
| W1 | Unbounded accumulation and possible OOM | `listing-accumulates-before-dump`, `oom-at-smaller-memory-limit` |
| W2 | Unbounded channel and possible high-concurrency memory effect | `result-channel-is-unbounded`, `unbounded-channel-worsens-high-concurrency-memory` |
| W3 | Cancellation mechanism and possible shutdown latency | `no-structured-cancellation-token`, `ctrl-c-shutdown-is-sluggish` |
| W4 | Located and unlocated panic surfaces and real-object behavior | `unsupported-etag-format-panics`, `unusual-etag-runtime-behavior`, `none-code-and-key-count-can-panic`, `match-enum-panic-site`, `channel-send-panic-site` |
| W5 | Two retry layers and throttling behavior | `sdk-retry-policy`, `application-retry-layer`, `retry-behavior-under-throttling` |
| W6 | Error threshold and possible misclassification | `error-threshold-is-hex-10`, `error-threshold-misclassifies-errors` |
| W7 | Blocking runtime bridges | `blocking-runtime-bridges-exist` |
| W8 | Unit-test counts in the main crate and workspace | `main-crate-has-one-unit-test`, `workspace-has-more-than-one-unit-test` |
| W9 | Floating dependencies and partial build-failure evidence | `dependency-versions-are-unbounded-above`, `rust-1-86-build-failure-observed` |
| W10 | Endpoint path style and absence of an override | `custom-endpoint-forces-path-style`, `custom-endpoint-has-no-virtual-hosted-override` |
| W11 | Interruption warning and unrun interruption behavior | `ctrl-c-output-can-be-inconsistent`, `ctrl-c-output-behavior-run` |
| PROSE-1 | No client-side rate limiting | `no-client-side-rate-limiting` |
| PROSE-2 | Express One Zone remains roadmap-only | `express-one-zone-roadmap-only` |
| PROSE-3 | Small-prefix fixed-overhead risk | `small-prefix-high-fixed-overhead` |
| PROSE-4 | No crash-resume across runs | `page-loop-resumes-from-last-key` |
| PROSE-5 | Non-ASCII and control-character key fidelity, including the known adapter boundary | `tool-non-ascii-key-fidelity`, `tool-control-character-key-fidelity`, `adapter-tab-newline-key-loss` |
| PROSE-6 | Limited direct-capture match and separate harness smoke facts | `limited-direct-capture-manifest-match`, `full-bucket-smoke-exited-zero`, `full-bucket-smoke-wall-clock`, `full-bucket-smoke-peak-rss` |

The validator compares the declared 49-origin set with every
`legacy_origins` value in the canonical ledger. Reviewers should additionally
compare this table with the preserved tables in
[`tool-page.md`](tool-page.md), because deciding where a compound sentence
splits remains a human judgment rather than a safe generic inference.
