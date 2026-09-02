# Swath v0.3.1 — delta re-derivation record

Subject: swath **v0.3.1**, git tag `v0.3.1`, commit
`7b9a5e2fba045c67165c76511f8c40c880406a8a` (short `7b9a5e2`), released
2026-09-01. Previous subject: v0.2.0, `cef8ec2`, whose blind five-reader
derivation is the rest of this directory.

## Method, and how it deviates from the written procedure

[`tool-onboarding.md`](../../../../docs/operating/tool-onboarding.md) § Re-deriving
says a subject change is re-derived blind, never patched, because editing pages
in place inherits what the old pages got wrong. This record deliberately
deviates: the owner asked for the minimal change that makes the capsule
describe 0.3.1, so the v0.2.0 ledger was taken as the hypothesis set and every
one of its claims was re-tested against the frozen v0.3.1 tree, claim by claim,
anchor by anchor. Nothing was carried forward on trust — a claim kept its
status only when a reader located the code at `7b9a5e2` and confirmed the
proposition there — but the *question set* is the v0.2.0 one, so behaviours
that are new at 0.3.1 and touch none of the old claims were caught only by the
readers' change-driven sweep of their areas (`new_facts` below), not by a blind
read. Where the two methods would have differed is in what a fresh reader might
have asked; where they agree is in what a stated claim must show.

The shape mirrored the original: four readers over disjoint file sets
(engine; S3 store; CLI, modes and resume; output) working the frozen worktrees
`swath-v0.2.0` and `swath-v0.3.1` side by side with the v0.2.0..v0.3.1 diff, a
fifth area (build, image, upstream health) read by the integrator, then
integration into the ledger. Each reader returned, per claim, one of
`holds`, `holds-reanchored`, `changed`, `contradicted`, `gone`, with the
evidence lines it actually read at `7b9a5e2`; the ledger gate
(`check-source-anchors --require-checked` against the `7b9a5e2` checkout) then
re-checked every anchor mechanically. The per-claim table is at the end of this
page.

## The tested subject

Upstream's published release image, an OCI index pulled anonymously by digest:

| Fact | Value |
| --- | --- |
| Index digest | `sha256:776e788200a1e70f30206897303a34e4faabd56c591e1c9562277677085c4f60` |
| Registry tag | `0.3.1` (no `v`); `v0.3.1` returns `manifest unknown`, as at 0.2.0 |
| Children | `linux/amd64` `sha256:2829c514…`, `linux/arm64` `sha256:31934653…`, plus two `unknown/unknown` attestation manifests |
| Labels, both arches | `org.opencontainers.image.revision=7b9a5e2fba045c67165c76511f8c40c880406a8a`, `org.opencontainers.image.version=0.3.1`, `org.opencontainers.image.source=https://github.com/varveio/swath` |
| Self-report | `swath 0.3.1` / `Commit: 7b9a5e2fba04` / `Runtime: 25.0.4+7-LTS` (the 0.2.1 multi-line `--version` format) |
| Entrypoint | `["java","-jar","/opt/swath/swath.jar"]`, user `10001:10001`, workdir `/opt/swath` |
| Signature | cosign signature tag `sha256-776e7882….sig` present in the registry |
| Git tag | `v0.3.1` → commit `7b9a5e2` "Release v0.3.1", 2026-09-01; `gradle.properties` `version=0.3.1` |

All of these were direct `docker` and registry observations on 2026-09-02 with
no receipt, exactly as the v0.2.0 identity facts were.

## What changed between the two images' installed help

Root help:

```diff
--- 0.2.0 swath --help
+++ 0.3.1 swath --help
@@ -1,5 +1,5 @@
 Usage: swath [-hVqv] [--color=MODE] [COMMAND]
-High-performance object-store lister.
+Parallel, resumable S3 object lister.
       --color=MODE   Color the progress line and end-of-run summary: auto,
                        always, or never (default: auto).
   -h, --help         Show this help message and exit.
@@ -9,5 +9,7 @@
   -V, --version      Print version information and exit.
 Commands:
   list    List a bucket or prefix.
-  resume  Resume a run by its output directory: swath resume <dir>.
+  resume  Resume a managed Parquet run by its output directory: swath resume
+            <dir>.
   help    Display help information about the specified command.
+Built by Varve: https://varve.io
```

`list --help`:

```diff
--- 0.2.0 swath list --help
+++ 0.3.1 swath list --help
@@ -4,8 +4,9 @@
                   [--bearer-token-command=CMD]
                   [--bearer-token-refresh-interval=DURATION]
                   [--checkpoint=PATH|none|auto] [--color=MODE]
-                  [--concurrency=N] [--endpoint-url=URL] [--exclude=REGEX]
-                  [--format=FORMAT] [--idle-timeout=DURATION] [--include=REGEX]
+                  [--compression=none|gzip|zstd] [--concurrency=N]
+                  [--endpoint-url=URL] [--exclude=REGEX] [--format=FORMAT]
+                  [--idle-timeout=DURATION] [--include=REGEX]
                   [--max-duration=DURATION] [--max-size=SIZE]
                   [--metrics-endpoint=URL] [--min-size=SIZE]
                   [--modified-since=DATE] [--modified-until=DATE]
@@ -16,28 +17,33 @@
                   [--part-rotation-max-rows=N] [--profile=NAME]
                   [--progress-interval=DURATION] [--region=REGION]
                   [--report=PATH] [--request-rate=N]
-                  [--requester-pays=requester] [--trace=PATH]
-                  [--engine-toggle=NAME=VALUE]... [--storage-class=CLASS[,CLASS]
-                  [,CLASS[,CLASS]...]]... [--tune=KEY=VALUE]... [<s3-uri>]
+                  [--requester-pays=requester] [--text-part-size=SIZE]
+                  [--text-writers=N] [--trace=PATH] [--writeback-size=SIZE]
+                  [--storage-class=CLASS[,CLASS][,CLASS[,CLASS]...]]...
+                  [--tune=KEY=VALUE]... [<s3-uri>]
 List a bucket or prefix.
       [<s3-uri>]             Source URI, e.g. s3://bucket/prefix (required
-                               unless requesting tune help)
+                               unless using --tune help or --tune KEY=?)
   -h, --help                 Show this help message and exit.
   -V, --version              Print version information and exit.
 
 Output:
-      --format=FORMAT        Output encoding: auto, table, tsv, jsonl, or
-                               parquet (default: auto).
+      --compression=none|gzip|zstd
+                             Compress table/TSV/JSONL streams, files, or
+                               dataset parts; inferred from .gz/.zst file names
+                               when omitted (default: none).
+      --format=FORMAT        Output encoding/sink: auto, table, tsv, jsonl,
+                               parquet, or discard (default: auto).
   -o, --output=PATH          Write to PATH; known extension selects a file,
                                otherwise a dataset; - is stdout.
       --output-type=file|dir Override -o file/directory inference.
       --parquet-part-size=SIZE
                              Target Parquet part size (default: 256mb).
       --part-rotation-interval=DURATION
-                             Rotate Parquet parts by age (default: 30s; 0/none
+                             Rotate dataset parts by age (default: 30s; 0/none
                                disables).
       --part-rotation-max-rows=N
-                             Rotate Parquet parts by row count (default:
+                             Rotate dataset parts by row count (default:
                                2000000; 0 disables).
       --[no-]progress        Print live progress records to stderr (default: on
                                when stderr is a terminal and neither -q nor -v
@@ -47,6 +53,14 @@
                                on for runs over 1.5s, runs that produce output,
                                and runs that stop short of finishing; a closed
                                downstream pipe stays silent).
+      --text-part-size=SIZE  Target uncompressed text part size (default:
+                               256mb).
+      --text-writers=N       Parallel writers for a TSV/JSONL directory dataset
+                               (default: 3; range: 2-64).
+      --writeback-size=SIZE  Shape writeback for open TSV/JSONL/Parquet dataset
+                               parts and sorted Parquet finals without rotating
+                               (default: off; minimum: 4mb; does not change
+                               crash recovery).
 
 Connection:
       --bearer-token-command=CMD
@@ -60,7 +74,8 @@
                              How often to re-run --bearer-token-command for a
                                fresh token (default: 45m). Size it comfortably
                                under the token source's real expiry.
-      --concurrency=N        Maximum concurrent listing requests (default: 64).
+      --concurrency=N        AIMD ceiling for concurrent listing requests
+                               (default: 64).
       --endpoint-url=URL     Custom S3 endpoint (LocalStack/MinIO).
       --fetch-owner          Request object owner fields from S3.
       --[no-]force-path-style
@@ -109,9 +124,10 @@
 Resume:
       --checkpoint=PATH|none|auto
                              auto (default) co-locates the checkpoint in a
-                               directory output (<dir>/.swath/checkpoint.
-                               sqlite) and keeps nothing for stdout; a path
-                               pins the SQLite file; none runs ephemeral.
+                               managed Parquet directory (<dir>/.
+                               swath/checkpoint.sqlite) and keeps nothing for
+                               streams/files; a path pins the SQLite file; none
+                               runs ephemeral (required for TSV/JSONL datasets).
       --force, --overwrite   Discard a COMPLETED run for this destination and
                                re-list it.
       --restart              Discard any prior checkpoint and start fresh.
@@ -119,9 +135,6 @@
 Diagnostics:
       --color=MODE           Color the progress line and end-of-run summary:
                                auto, always, or never (default: auto).
-      --engine-toggle=NAME=VALUE
-                             Set a diagnostic engine ablation (repeatable; see
-                               docs/usage.md).
       --metrics-endpoint=URL Export OTLP metrics to URL (overrides
                                SWATH_OTLP_ENDPOINT).
       --no-metrics           Disable metrics export, including environment
@@ -130,6 +143,6 @@
                                suppresses the startup destination echo.
       --trace=PATH           Write a diagnostic JSONL run trace to PATH.
       --tune=KEY=VALUE       Set a typed expert option (repeatable; use --tune
-                               help).
+                               help; see Tuning in docs/configuration.md).
   -v                         Increase verbosity (-v INFO, -vv DEBUG, -vvv
                                TRACE).
```

`resume --help`:

```diff
--- 0.2.0 swath resume --help
+++ 0.3.1 swath resume --help
@@ -2,9 +2,9 @@
                     [--bearer-token-command=CMD]
                     [--bearer-token-refresh-interval=DURATION] [--color=MODE]
                     [--tune=KEY=VALUE]... [<dir>]
-Resume a run by its output directory: swath resume <dir>.
-      [<dir>]            The directory-dataset run handle to resume (opens
-                           <dir>/.swath/checkpoint.sqlite).
+Resume a managed Parquet run by its output directory: swath resume <dir>.
+      [<dir>]            Managed Parquet output directory; its checkpoint is in
+                           .swath/.
       --bearer-token-command=CMD
                          Shell command whose stdout is a fresh OAuth bearer
                            token (e.g. 'gcloud auth print-access-token' for
@@ -28,6 +28,7 @@
                            for runs over 1.5s, runs that produce output, and
                            runs that stop short of finishing; a closed
                            downstream pipe stays silent).
-      --tune=KEY=VALUE   Set a resume-safe typed expert option (repeatable).
+      --tune=KEY=VALUE   Set a resume-safe typed expert option (repeatable; use
+                           --tune help; see Tuning in docs/configuration.md).
   -v                     Increase verbosity (-v INFO, -vv DEBUG, -vvv TRACE).
   -V, --version          Print version information and exit.
```

`list --tune help`:

```diff
--- 0.2.0 swath list --tune help
+++ 0.3.1 swath list --tune help
@@ -1,6 +1,8 @@
 Tune keys:
   engine.readahead: type=boolean; values=on|off; default=off; stability=experimental; resume=free; applies=fresh list
   seed.mode: type=enum; values=shallow|none|hints; default=shallow; stability=stable (hints reserved); resume=identity; applies=fresh list
-  parquet.writers: type=integer; values=2..4; default=3; stability=stable; resume=free; applies=fresh list
+  parquet.writers: type=integer; values=2..64 (heap-admitted above 4); default=3; stability=stable; resume=free; applies=fresh list
   summary.interval: type=duration; values=positive duration (for example 2s, 500ms, or PT2S); default=--progress-interval; stability=stable; resume=free; applies=fresh list
+  sort.merge-parallelism: type=integer; values=1..16; default=4; stability=stable; resume=free; applies=fresh list and resume
+  sort.keep-staging: type=boolean; values=on|off; default=off; stability=diagnostic; resume=free; applies=fresh list and resume
   sort.ignore-disk-check: type=boolean; values=on|off; default=off; stability=diagnostic; resume=free; applies=fresh list and resume
```

Read against the adapter: every option `adapter/command.py` emits exists on
the 0.3.1 surface (`--color`, `--checkpoint`, `--format`, `--output-type`, `-o`,
`--text-writers`, `--compression`, `--text-part-size`, `--writeback-size`,
`--parquet-part-size`, `--part-rotation-*`, `--sort`, `--tune parquet.writers`,
`--tune sort.merge-parallelism`, `--tune sort.ignore-disk-check`,
`--tune seed.mode`). The one option the v0.2.0 capsule named that is gone from
the help, `--engine-toggle`, is hidden rather than removed and the adapter never
emitted it.

## Adapter verification against the real binary

All eight declared modes were compiled by `adapter/command.py`, executed on the
pinned image, and read back through `adapter/normalize.py`; the record is
[`../receipts/observations-v0.3.1/adapter-modes/`](../../receipts/observations-v0.3.1/adapter-modes/).
Every mode exited 0 and normalized to the prefix's registered 2,549 keys with
one identical key-set digest across all eight. One defect was found and fixed in
the study's own code: the aligned `table` sink now prints a millisecond
fraction on every timestamp, which the normalizer's table query did not strip,
so that mode first normalized to zero rows. The fix strips fractions there as
the TSV and JSONL queries already did.

## Upstream between the two subjects

Eighty-four commits and six releases (0.2.1 through 0.3.1) separate the two
tags; the release notes are in `docs/ops/dev/RELEASE_NOTES.md` at each tag. The
changes that reach this capsule's claims are summarised per area in the
reader companions below and settled in the claim table.

## Per-claim verdicts

Verdict vocabulary: `holds` (file unchanged, anchors identical), `holds-reanchored`
(true, lines moved), `changed` (true in substance, wording or qualification
revised — recorded as disposition `corrected`), `contradicted` (reversed at
0.3.1 — recorded under the same ID as disposition `contradicted`, with the
successor statement). Unverified claims keep their `none` evidence and reason;
readers only confirmed the reason still applies.

| Claim | Area | Verdict | Reader rationale |
| --- | --- | --- | --- |
| `work-stealing-range-engine` | engine | holds-reanchored | WorkStealingScan gained two imports (OutputException, LastModifiedParseException) and a sort-only completion-marker helper; the steal loop, single-attempt slot and lock order are textually identical, shifted by +2 lines. Worklist and ThiefPolicy are unchanged. |
| `termination-cannot-see-false-quiescence` | engine | holds-reanchored | Worklist is byte-identical; the WorkStealingScan quiescence block is identical text at 928-944. |
| `listing-is-adaptive-density-aware` | engine | holds | All three cited files are byte-identical between v0.2.0 and v0.3.1. |
| `pivot-placement-is-multi-phase` | engine | holds | ThiefPolicy and WorkerState unchanged; ByteMidpoint lost only the isValidUtf8 helper after line 219 (StealMath 322 now calls KeyBytes.isValidUtf8), so every cited line is identical at the same number. |
| `internal-tiling-is-disjoint` | engine | holds-reanchored | All five mechanisms are intact. The only substantive engine change near the emission seam is sort-mode-only: when the completing page retains no rows (empty after trim or after filters) the engine now sends a PageBatch.completion marker to the sort tripwire (WorkStealingScan 738-750, 835-846); it emits no keys, so disjointness is unaffected. |
| `split-commit-is-atomic-cas` | engine | holds-reanchored | SqliteCheckpointStore changes are confined to part_file metadata columns (format_version, extension_type) and a schema-version accessor; the split transaction is identical text. |
| `no-dedup-pass-by-construction` | engine | holds | SeedStep byte-identical; no engine change introduces a dedup pass. The crash-path caveat still routes to exactly-once-under-crash. |
| `seed-descent-is-serial` | engine | holds | HybridSeedPlanner is byte-identical; the upstream statement about --concurrency lengthening the seed survives at algorithms.md 1421-1423. The seed's fetcher is now wrapped by TransientRetryFetcher.forSeed (ListCommand 1350-1351), which adds a fail-fast path but does not change the probe budget or serial structure. |
| `concurrency-flag-is-aimd-ceiling` | engine | changed | Mechanism unchanged (all gauge anchors identical text). What changed is upstream disclosure: the help string, docs/cli.md 53, a new 'Choosing --concurrency' section and a 'Capacity boundary' paragraph. Any study prose that contrasts the claim with upstream's 'Maximum concurrent' wording must be updated. |
| `aimd-adapts-to-503` | engine | holds-reanchored | Factor 0.7, the 0.5 shed and both growth freezes are unchanged. The only mechanism edit is event ordering on the success path (latency before status), which affects when the latency-inflation freeze can veto a growth step but not the 503 decrease/increase rule. algorithms.md also dropped its stale 'optional latency-EWMA breach decrease trigger' sentence; the source never had one, so the claim's decrease-on-503-only reading is now also the documented one. |
| `v020-engine-default-flips` | engine | holds | EngineToggles and TailFloorMode changed only in javadoc doc-path references (docs/usage.md -> docs/configuration.md); the defaults and fallbacks are identical. |
| `engine-toggles-are-diagnostic` | engine | changed | Substance holds; the operability surface changed (hidden flag, relocated documentation). Old anchor 22-27 no longer matches textually because of the doc-path edit; re-anchored to 22-30. |
| `seed-hints-unimplemented` | engine | holds | Both cited files are byte-identical; the tune registry and docs still describe hints as reserved. The failure still occurs after the checkpoint is opened and the S3 client built (seedFreshRun at ListCommand 970-982 runs inside the S3Client try block at 942). |
| `queue-size-is-entry-budget` | engine | holds-reanchored | Channel is byte-identical; only the CLI anchor shifted by one line. |
| `request-rate-limiter-inside-retry-loops` | engine | holds-reanchored | Limiter classes unchanged; wrapping order unchanged on both the engine (GaugedFetcher) and seed (forSeed) retry loops. |
| `retry-default-is-ride-out` | engine | changed | Policy selection and both loops are otherwise identical. The new fail-fast is a carve-out from 'over-cap transient faults retry indefinitely' on the seed path and belongs in the qualification. |
| `watchdog-two-tripwires` | engine | holds | Windows, feeds and escalation ladder unchanged; the only edits are javadoc phrasing about finalize-tail progress ticks and a renamed sort lane class. |
| `aimd-necessity` | engine | holds | Still unverified for the same reason: no throttling or high-concurrency scenario has exercised the controller at either tag. The new upstream 'Capacity boundary' text is consistent with the hypothesis but is design prose, not a measurement. |
| `seed-cost-direction-at-smoke` | engine | holds | Still unverified: no instrumented seed.mode=none versus shallow comparison exists. The seed planner and SeedStep are byte-identical at 0.3.1, so a comparison run at 0.3.1 would measure the same mechanism as at 0.2.0. |
| `exactly-once-under-crash` | engine | holds | Still unverified: no crash, SIGKILL or resume run has been performed at either tag, and the reason (stdout runs resolve --checkpoint auto to a memory store) still applies. Note for a future test: 0.3.0's page-run staging is format v4 with no legacy read path, so a sorted run interrupted under 0.2.x cannot be resumed by 0.3.x (CHANGELOG 0.3.0). |
| `pagination-uses-start-after` | store | holds-reanchored | Statement and qualification unchanged in substance. The S3PageFetcher and compat-doc anchors moved (fetcher +12/+13 lines from the direct-page carrier code; doc +7 lines). The streaming interceptor parses ContinuationToken/NextContinuationToken elements only to carry them; the request builder still sets bucket/maxKeys/encodingType/prefix/startAfter/delimiter only. |
| `sdk-internal-retry-disabled` | store | holds-reanchored | Value still hard-wired at 1 and not CLI-tunable. The new StreamingListObjectsV2Interceptor runs inside the same SDK attempt (modifyHttpResponseContent), so the one-increment-per-attempt relation is unaffected. The v0.3.0 seed fail-fast (see new facts) is a swath-side retry-loop change, not an SDK retry change. |
| `encoding-type-url-no-local-decode` | store | contradicted | The statement's core proposition ('performs no percent-decoding of its own, relying entirely on the AWS SDK's response interceptor') is reversed at 0.3.1: StreamingListObjectsV2Interceptor (new in 0.3.0, commit 5772d1e 'Accelerate S3 listing and TSV throughput') decodes in swath's own source tree. The observable round-trip property is unchanged, and the request side (encoding-type=url) still holds, so a corrected successor claim is proposed above. |
| `plus-to-space-conditional-hazard` | store | changed | Hazard unchanged in substance, but the decoding locus moved from the SDK's DecodeUrlEncodedResponseInterceptor into swath's StreamingListObjectsV2Interceptor, which calls the same URLDecoder-backed utility (SDK pin 2.31.78 unchanged). Wording updated; the IT anchor still applies and now covers the new path. |
| `encoding-contract-not-validated` | store | changed | Proposition (no validation, exact-match gate, silent wrong output) still true; the qualification's provenance ('established by disassembling the pinned SDK jar', 'only EncodingType reference in main source is request-side') is now stale because swath re-implemented the gate at interceptor line 533. |
| `protocol-violation-defences` | store | changed | Two material changes: the engine forward-progress guard was widened to terminal pages (RangeScanner 274-281, commit range 0.2.x-0.3.0), and the new streaming interceptor added store-side parse/root/error-in-200 guards. Count and qualification need updating; the exit-1 fatal disposition of the original three holds. |
| `no-intra-page-ordering-check` | store | holds-reanchored | Still true. A repository search of swath-s3 main and the scan loop at 0.3.1 finds no ordering check; the new interceptor copies keys in wire order. The widened forward-progress guard still only compares the last key with the previous cursor. KeyBytes anchor narrowed to 45-47 because isValidUtf8 was inserted after compareUnsigned. |
| `error-classification-is-specific` | store | holds | S3FaultClassifier and SwathException are byte-identical between the tags. New error subtypes (InvalidKeyEncodingException, MergePendingException, PublicationPendingException) are output-side under OutputException and do not touch listing classification. The interceptor's SdkClientException falls under the existing non-service-client-exception arm. |
| `wrong-region-is-fatal` | store | holds | Neither cited file changed between v0.2.0 and v0.3.1; a 301 response is not a 2xx so the streaming interceptor passes it through untouched (interceptor 91-94). |
| `region-required-even-anonymously` | store | changed | Code proposition unchanged (independent region and credential resolution, exit 2 before any request). The qualification's claim that upstream quickstarts omit --region is no longer true at 0.3.1 (README 42, 54; operating.md 27), so the qualification is revised; FAQ anchor moved. |
| `anonymous-listing-supported` | store | holds-reanchored | Three-branch precedence and the orthogonal bearer path are unchanged; anchors shift by one line. The bearer-token supplier gained output/termination bounds (see new facts) without changing when it is selected. |
| `api-calls-counter-is-trustworthy` | store | holds-reanchored | Still exactly one increment site firing before the SDK call; the streaming interceptor issues no requests of its own and runs inside the same attempt. A repository search of swath-s3 main at 0.3.1 still finds listObjectsV2 as the only request kind. The v0.2.0 note that the private apiCalls() accessor is not the reported number remains true (it is now read by a unit test only). |
| `page-size-fixed-no-max-keys` | store | holds-reanchored | Still a hard-coded local variable with no option feeding it; the only --max-keys in the tree remains the replay-server bench command (the module was renamed swath-replay-server -> swath-replay in 0.3.0). ListCommand grew by ~600 lines, so the anchor moved. |
| `non-snapshot-pagination-misses-late-inserts` | store | holds | The reason it is unverified still applies: no concurrent-mutation or replay experiment exists in the study and nothing between the tags adds one; source at 0.3.1 can no more settle mutation-time behaviour than it could at v0.2.0. Status stays unverified. |
| `mode-inventory-v020` | CLI/resume | changed | The v0.2.0 count of ten is stale: OutputFormat gained DISCARD, OutputOptions.resolveOutput now admits TSV/JSONL directory datasets (only table is refused for a directory), and --compression/--text-writers/--text-part-size/--writeback-size are visible flags in the golden help. Seed modes and the resume subcommand are unchanged. |
| `no-inspect-or-diff-subcommand` | CLI/resume | holds-reanchored | No inspect or diff class exists under swath-cli at 7b9a5e2 (grep for such command names is empty); the command surface is still list, resume, help plus hidden dump-run and completion. Only line numbers moved (App.java grew a --version block and a messageChain helper). The root help now carries a 'Built by Varve' footer and the description changed to 'Parallel, resumable S3 object lister.' |
| `no-shallow-listing-mode` | CLI/resume | holds-reanchored | Every engine dispatch (now five, including the new discard and text-dataset runners) still passes the OBJECTS literal; no --delimiter/--recursive option exists on swath list at 0.3.1, confirmed by the golden help capture and a repo-wide grep. |
| `no-owner-split-flag-absent` | CLI/resume | changed | The proposition holds but the spelling of the kill switch now points at a hidden option, which a benchmark author must know because the flag is absent from --help. EngineToggles line numbers are unchanged (its diff only re-pointed doc references). |
| `versions-listing-is-dead-code` | CLI/resume | holds-reanchored | Nothing changed in substance: no --all-versions option, the fetcher throws, the checkpoint schema admits the value, and the flag survives only in ROADMAP/javadoc/contracts prose. The upstream documentation anchor moved from docs/operating.md to docs/usage.md:30. |
| `filters-are-post-listing` | CLI/resume | holds-reanchored | The filter call site is the same code with a small line shift, the regex filter is byte-identical, and the upstream prose was rewritten but says the same thing in two documents. The only filter change is that MtimeFilter wraps a LastModified parse failure into ListingException. |
| `exit-code-map` | CLI/resume | changed | Seven became eight with DISK_FULL 74 (release 0.2.2, PR #98). The mapping logic and the sealed hierarchy are otherwise unchanged; line numbers shifted by six. |
| `checkpoint-resume-design-exists` | CLI/resume | changed | The design (SQLite, listing_node worklist, single checkpoint-writer thread, version pin) is intact. Two qualification details changed: part_file gained two migrated columns used by the sorted-staging compatibility refusal, and the text-cursor half of the at-most-once/exactly-once sentence is moot because text output is now explicitly non-resumable. |
| `only-parquet-directory-is-resumable` | CLI/resume | changed | The v0.2.0 statement was true because text directories did not exist. At 0.3.1 they exist and are deliberately non-resumable, enforced at three points in ListCommand; the ephemeral store and the directory-as-run-handle design are unchanged. |
| `resume-identity-classification` | CLI/resume | holds | ResumeRegistry.java has no diff between the tags and BearerTokenOptions' cited lines are unchanged (its only diff is a comment at line 43). Every flag added since v0.2.0 is classified FREE, so the identity and sticky sets described in the qualification are exactly those at 0.3.1. |
| `resume-cli-surface-is-restricted` | CLI/resume | changed | The resume option set is unchanged, but the set of tune keys it will accept grew from one to three, which matters for a benchmark that resumes a sorted run on a different machine. |
| `live-error-messages-name-absent-flags` | CLI/resume | holds-reanchored | Both messages survive verbatim at 0.3.1. SortDiskGuard was renamed and moved to output/sorted/StagingDiskGuard in the sort-package split (#178-#180) without editing the string. The one improvement is that the separate startup pre-check message in ListCommand names the correct tune key. |
| `docs-and-javadoc-drift` | CLI/resume | changed | The pattern is unchanged for every item this reader owns; the documentation rewrite improved the reference layer (cli.md, configuration.md tables) but did not touch the javadoc or the two runtime strings. |
| `file-sinks-not-harness-capturable` | CLI/resume | holds-reanchored | Swath still refuses Parquet on stdout; the driver/harness side of the claim is study-owned and unaffected by the upstream bump. Two adjacent facts are new: text directory datasets are equally path-based, and the discard sink produces no output file at all, so its only artifact is --report/--trace. |
| `crash-resume-works` | CLI/resume | holds | The reason the claim is unverified still applies: exercising crash-resume requires a managed Parquet directory dataset, which the study's stdout-capturing harness cannot mount, and the new text datasets and discard sink are explicitly non-resumable so they do not open a cheaper path. Status should remain unverified. |
| `text-sink-key-encoding-is-lossy` | output | changed | Escaper and asString are unchanged (anchor lines identical for ControlCharEscaper; KeyBytes.asString moved 65-72 -> 113-120 because isValidUtf8 was inserted). The statement needs the new dataset-part formatter (Utf8TsvFormatter) folded in, and the qualification should say where non-UTF-8 keys can actually come from at 0.3.1. |
| `jsonl-escaping-is-invertible` | output | holds-reanchored | Json.java is byte-identical between tags. JsonlFormatter's only change is that time() takes the entry's last_modified text instead of epoch micros; the range 84-95 became 84-94. Invertibility of the escaping and the omit-when-null behaviour are unchanged, and partitioned JSONL parts reuse the same formatter. |
| `tsv-header-and-field-order` | output | changed | Header and order are unchanged on both the streaming writer and the new byte-oriented dataset writer, and the anchor line numbers are the same, but line 45/49 content changed (lastModifiedText) and a per-part header now exists, so the qualification needs the dataset-part sentence. The study normalizer's SWATH_TSV_COLUMNS order and per-part header skip already match. |
| `parquet-key-column-is-byte-exact` | output | changed | Storage and write path are the same bytes (binary("key", 0, key)), so byte-exactness holds; the 'only byte-exact path' and 'raw BINARY rather than a string decode' framing no longer describe 0.3.1, where the column is STRING-annotated and a UTF-8 guard precedes the write. The release notes (v0.3.0) confirm physical bytes, statistics and sort order are unchanged. |
| `aligned-fixed-column-timestamp-assumption` | output | changed | Widths and no-header are unchanged; the write() block gained an escaped() helper so the cited 58-78 range is now 58-82. The substantive change is that the time text is no longer swath's ISO_INSTANT rendering of a second-precision value but the endpoint's text (24 chars for S3), so the 'second-precision ISO_INSTANT' framing of the statement is out of date even though the column arithmetic survives. |
| `timestamp-precision-is-variable` | output | changed | The mechanism named in the statement (ISO_INSTANT with 0/3/6/9 digits) was removed between the tags: text formatters now call lastModifiedText(), which on the production path is S3's own text and otherwise a hand-rolled renderer with at most six digits. The observable consequence, variable fraction digits that must be stripped, still holds and has in fact become the common case, so the claim is retained with a rewritten statement rather than contradicted. |
| `stdout-is-clean` | output | changed | The sink anchor moved (488 -> 670) and gained a compression wrapper; ListCommand's stderr anchors moved (280 -> 316, 387 -> 429). A new stdout writer exists for --tune help, so the literal 'only writer' wording needs the listing-run scoping; for a listing run the property holds. |
| `output-is-streaming` | output | holds | The cited OutputStage lines and RowTally are unchanged (OutputStage's only diff is a javadoc line about exit 74). The streaming invariant for the text sinks holds. The qualification's carve-outs still apply; note for the record that the direct-Parquet and text-dataset pools now hold bounded batch queues (a configuration-sized buffer, not a per-run collection) and that dataset manifests are no longer re-serialized during the run. |
| `bounded-memory-at-scale` | output | holds | Still unverified and the reason still applies: no scale campaign has run, and memory cliffs are scale-dependent. What a future run must check changed in three ways: (1) the 'Parquet part-metadata re-serialization' growth path named in the qualification no longer exists, manifests are published once at completion; (2) direct-Parquet and text-dataset output now go through a bounded writer pool whose queue budget and heap admission are configuration, which is the thing to vary; (3) sorted finalization is a new pipeline that prices heap and file-descriptor budgets before opening readers (release notes v0.3.0), so sort staging peak (sort.staging_bytes_peak) and finalize parallelism are the report fields to record. Replace the qualification's two named growth paths with the writer-pool queues and the sort pipeline's decoded-page budget. |
| `control-char-key-fidelity-untested` | output | holds | Still unverified for the same reason: no edge-case bucket is configured and the corpus has no such keys. 0.3.1 narrows what a future run must check: the faithful capture paths are Parquet (control bytes are well-formed UTF-8, so the new guard does not reject them) and JSONL; TSV and table remain lossy by construction. A Parquet read-back now returns VARCHAR, so fidelity must be compared as UTF-8 bytes of the returned string, not as BLOB bytes. |
| `parquet-modes-execute` | output | holds | The source-level reason for being unverified (no Parquet run in the study's ledger at v0.2.0) is unchanged by the source; note, however, that the study capsule now holds receipts/observations-v0.3.1/adapter-modes with a sorted-Parquet stderr and an observation saying the sorted run published data/part-00000.parquet, so the parent may already have an execution observation to promote. What a future check must expect at 0.3.1: zero-based sorted part names with one-based file_index, part-w<lane>-<seq> names for direct parts, the sort staging container at version 4 with ZSTD1 default (release notes v0.3.0), --tune sort.merge-parallelism replacing the JVM property, and manifests appearing only at completion. |
| `parquet-output-byte-exact` | output | holds | Still unverified: no read-back step is recorded in the claim ledger. 0.3.1 changes the read-back contract: DuckDB yields VARCHAR keys (compare as UTF-8 bytes), every key in a 0.3.1 dataset is well-formed UTF-8 by construction, sorted parts are numbered from zero, and a dataset without _SUCCESS has no manifest at all rather than a partial one. The qualification's arm64 Zstd/Parquet native-code caveat still applies. |
| `repo-is-public-at-v020` | build/upstream | changed | Re-read from the repository API on 2026-09-02. |
| `language-is-java` | build/upstream | holds-reanchored | Conventions file grew (dependency exclusions) and the toolchain block moved from 22-26 to 32-37; no resolver plugin appears in settings.gradle.kts or build-logic/settings.gradle.kts. |
| `license-is-apache-2-0-with-notice` | build/upstream | changed | NOTICE byte-identical; the gradle task moved and now names the CLI runtime graph explicitly because a replay distribution gained its own notices. |
| `image-self-reports-v020` | build/upstream | changed | Direct --version probe on 2026-09-02. |
| `upstream-publishes-tagged-releases` | build/upstream | changed | Releases API read 2026-09-02; task block moved and gained the rc grammar. |
| `image-label-binds-to-source-commit` | build/upstream | changed | Same mechanism; the promotion payload now also carries the swath-replay distribution. |
| `published-image-is-anonymously-pullable` | build/upstream | changed | Direct registry observations. |
| `first-party-source-basis` | build/upstream | changed | Second subject added. |
| `amd64-built-and-smoked-upstream` | build/upstream | holds-reanchored | Workflow restructured; same facts at new lines; amd64 now also exercised by the study. |
| `arm64-not-runtime-smoked-at-v020` | build/upstream | holds-reanchored | Comment moved from 382-384 to 492-493. |
| `upstream-is-young-and-solo-maintained` | build/upstream | changed | Re-read 2026-09-02. |
| `nightly-deep-verification-failing` | build/upstream | changed | Actions runs API read 2026-09-02. |
| `runs-executed-natively-on-arm64` | build/upstream | holds | A v0.2.0 observation claim; unchanged. The v0.3.1 round-trip was amd64 and does not touch it. |
| `aimd-idle-at-smoke` | observation | holds | A v0.2.0 run observation; the qualification now says so and that the 2026-09-02 v0.3.1 round-trip is a separate run. |
| `full-run-reported-parallel-listings` | observation | holds | A v0.2.0 run observation; the qualification now says so and that the 2026-09-02 v0.3.1 round-trip is a separate run. |
| `peak-in-flight-reached-ceiling-at-both-scopes` | observation | holds | A v0.2.0 run observation; the qualification now says so and that the 2026-09-02 v0.3.1 round-trip is a separate run. |
| `smoke-output-count-and-uniqueness` | observation | holds | A v0.2.0 run observation; the qualification now says so and that the 2026-09-02 v0.3.1 round-trip is a separate run. |
| `probe-overhead-higher-on-small-prefix` | observation | holds | A v0.2.0 run observation; the qualification now says so and that the 2026-09-02 v0.3.1 round-trip is a separate run. |
| `non-worker-page-call-share` | observation | holds | A v0.2.0 run observation; the qualification now says so and that the 2026-09-02 v0.3.1 round-trip is a separate run. |
| `parallelism-ratio-at-higher-concurrency` | observation | holds | Still unverified; the reason now records the single v0.3.1 run at ceiling 64 (peak 18) as not a ratio measurement. |
| `java-handicap-at-high-rates` | observation | holds | Still unverified; nothing between the tags bears on it. |

## Claims added at v0.3.1

Seventeen claims were added from the readers' change sweeps, each anchored at
`7b9a5e2` and pointing at the reader companion that found it:
`seed-endpoint-unreachable-fails-fast`, `aimd-does-not-search-down`, `forward-progress-guard-covers-terminal-page`, `user-agent-identifies-swath`, `listobjects-response-streamed-by-swath-interceptor`, `parquet-key-is-string-annotated-utf8-only`, `last-modified-text-is-endpoint-spelling`, `discard-sink-measures-listing-engine`, `partitioned-text-datasets`, `text-datasets-require-checkpoint-none`, `text-compression-flag`, `sorted-staging-version-refusal`, `sort-merge-parallelism-tune`, `sorted-parts-zero-based-file-index-one-based`, `manifests-published-only-at-completion`, `parquet-writers-range-widened`, `supported-cli-surface-page-is-tested`.

Facts the readers reported but that were not promoted to claims, because they
change nothing a benchmark author here would act on: the AIMD success-path
ordering fix, the `--bearer-token-command` process bounds, managed-path symlink
refusal, the run-report field changes (`listing_duration_ms`,
`recovered_objects`, the rewritten `sort` block), and the `--tune help` stdout
writer (folded into `stdout-is-clean`). They remain in the reader companions.

## Reader companions

- [`reader-A.md`](reader-A.md) — engine: work stealing, seeding, AIMD, retries, watchdog, engine toggles.
- [`reader-B.md`](reader-B.md) — S3 store: request shape, streamed response parsing and key decoding, retries, region, User-Agent.
- [`reader-C.md`](reader-C.md) — CLI, modes, exit codes, checkpoints and resume, documentation drift.
- [`reader-D.md`](reader-D.md) — output formats, timestamps, Parquet key typing, datasets and publication.

The build, image and upstream-health area was read by the integrator and is
recorded only in the ledger and in this page's identity table.
