# s7cmd v1.8.5 source update — 2026-10-15

This is a source and build-preparation update for study issue
[#29](https://github.com/varveio/s3-listing-study/issues/29), not a run record.
The upstream CLI and engine were read before consulting the existing capsule.
No subject command, including help/version, container build, package install,
benchmark, or cloud task was run. Existing research and receipts are unchanged.

## Selected releases and request mapping

Issue #29 was opened 2026-09-23 by the author, requesting then-latest s7cmd
v1.8.4 and linking the s3ls-rs 1.1.0 listing fix. The official GitHub latest
release API checked 2026-10-15 selects **v1.8.5**, published 2026-09-26.
Its immutable tag resolves to `1e8c7d0644f30cb5da927b87277376620fcd0554`.
Its Cargo.toml pins **s3ls-rs =1.3.4**, whose tag resolves to
`2993e4f8fd23316c63f63d04a6b6f2102cfdd41b`.
[SRC Cargo.toml:1-25 @ 1e8c7d0644f30cb5da927b87277376620fcd0554]

Version 1.8.4 was a documentation/support-policy release. Version 1.8.5 fixes
stdout flushing for `get-object-annotation`, not listing; both pin listing
engine 1.3.4. Thus latest 1.8.5 addresses the request's listing concern while
including the subsequent unrelated fix.
[SRC CHANGELOG.md:8-71 @ 1e8c7d0644f30cb5da927b87277376620fcd0554]

The official Linux x86-64 glibc2.28 archive's GitHub asset digest and separately
downloaded bytes agree on SHA-256
`a8ebdaf9922b52659311cbf66264291b0a8cac7072b77323899e3cb41f8ed27c`.
Archive member inspection found `s7cmd`. This verifies selected artifact bytes
and layout only, not that the executable loads or behaves as source suggests.
[DOC official release](https://github.com/nidor1998/s7cmd/releases/tag/v1.8.5).

## Exact listing bug and fix

The referenced s3ls-rs **1.1.0** change is a concurrency-limit correction.
Parallel listing discovers `/` prefixes until `max_parallel_listing_max_depth`,
then handles a deeper prefix with delimiter-free sequential pagination.
Previously it released the task's semaphore permit before awaiting that leaf
scan. Many deep prefixes could therefore drain simultaneously without consuming
permits, exceeding `--max-parallel-listings`. Current source awaits the entire
leaf scan while holding the permit, then explicitly drops it. Sequential leaf
scans never acquire another permit; parents release theirs before awaiting
children, avoiding a recursive permit deadlock.
[SRC CHANGELOG.md:75-85 @ 2993e4f8fd23316c63f63d04a6b6f2102cfdd41b]
[SRC src/storage/s3/mod.rs:582-595 @ 2993e4f8fd23316c63f63d04a6b6f2102cfdd41b]
[SRC src/storage/s3/mod.rs:685-738 @ 2993e4f8fd23316c63f63d04a6b6f2102cfdd41b]

This changes the meaning of the configured cap for sufficiently deep
hierarchies. It could alter resource pressure, retries, throughput and elapsed
time in either direction; none is established without a run. The fix is not
itself evidence of skipped or duplicated objects in the previous release.
The historical diagnostic timings and memory remain observations of that old
executable and setup. They cannot transfer as observations of the new engine.

The same 1.1.0 changelog also fixes rate limiting: earlier refill arithmetic
rounded effective rates down to multiples of ten; the selected engine computes
an exact refill schedule. No rate-limited run is claimed here.
[SRC src/storage/s3/mod.rs:863-895 @ 2993e4f8fd23316c63f63d04a6b6f2102cfdd41b]

## CLI and output source derivation

The umbrella CLI uses `s3ls_rs::CLIArgs`, converts it to the engine Config, and
runs `ListingPipeline`; this is not an independent listing reimplementation.
[SRC src/cli.rs:270-273 @ 1e8c7d0644f30cb5da927b87277376620fcd0554]
[SRC src/dispatch.rs:58-70 @ 1e8c7d0644f30cb5da927b87277376620fcd0554]
[SRC src/ls_bin/mod.rs:64-81 @ 1e8c7d0644f30cb5da927b87277376620fcd0554]

Recursive parallel dispatch still requires concurrency greater than one and no
delimiter, with special opt-in for Express One Zone. Discovery is prefix based;
a flat namespace provides no child-prefix fan-out. Defaults are 64 concurrent
listings, depth 2, queue size 200000, sorting threshold 1000000. These are source
constants, not a captured help receipt from the new binary.
[SRC src/storage/s3/mod.rs:402-432 @ 2993e4f8fd23316c63f63d04a6b6f2102cfdd41b]
[SRC src/config/args/mod.rs:23-26 @ 2993e4f8fd23316c63f63d04a6b6f2102cfdd41b]

TSV builds columns then appends the key. JSON writes Key, LastModified, ETag,
Size and optional StorageClass, consistent with the existing normalizer's
metadata projection. Real output compatibility still requires executing the
new binary. The wrapper now returns 130 after a caught Ctrl-C, so the historical
exit-zero source account must remain scoped to the old subject.
[SRC src/display/tsv.rs:15-21 @ 2993e4f8fd23316c63f63d04a6b6f2102cfdd41b]
[SRC src/display/json.rs:27-67 @ 2993e4f8fd23316c63f63d04a6b6f2102cfdd41b]
[SRC src/ls_bin/mod.rs:72-81 @ 1e8c7d0644f30cb5da927b87277376620fcd0554]

## Readiness and evidence boundary

Canonical identity, executable adapters, build pins and the historical claims
ledger remain unchanged at v1.5.0. Candidate source claims are kept in the
sibling `update-1.8.5-2026-10-15.claims.json`, explicitly outside the canonical
ledger; they are not a new runtime qualification. Published release files and
all old research/receipts remain untouched.

The remaining retarget checklist is concrete:

1. Preserve historical source provenance while selecting a new tested revision.
   The current validator binds tested-variant evidence to its one current
   revision; upstream evidence is bound to the separate s3ls-rs repository.
   Historical s7cmd source anchors cannot simply be labelled as current.
2. Decide how genuinely new claims coexist with the frozen migration stratum.
   The existing schema requires a nonempty legacy origin on every claim in a
   migrated ledger. New findings have no genuine old origin; inventing one or
   rewriting the frozen migration map would misstate their derivation.
3. In a later reviewed retarget, update canonical subject to v1.8.5 and source
   revision, both capsule/shared recipe URLs and checksum to the proposed asset,
   and both build/adapter digests. Source-review the carried-forward modes and
   metadata fields; use source provenance for default concurrency 64, rather
   than the v1.5.0 help receipt. Preserve old evidence under its original subject.
4. After execution is authorized, capture the actual binary version/help and
   exercise intended mode/output pairs, then run matched fixture qualification.
   Deep-leaf, flat and skewed layouts answer different questions. Matching
   counts alone will not establish completeness. Disclose inherited timeout
   setup and explore comparable knobs for the other tools.

No schema or gate was loosened to make this research look runnable. No new
receipt, performance result, correctness verdict, or claim of completed runtime
onboarding exists here. Independent review and owner review still gate the
research update.

## Static verification of this update

Capsule structure and repository link checks pass. The source-anchor checker
verified all 14 labels in this new report and all 11 source references in its
separate research ledger against the exact tagged checkouts, with none skipped
or failed. These checks read Git objects; they never execute a subject.

The combined canonical-ledger anchor gate also exposed ten pre-existing
historical paths beginning `config/` or `display/` that do not exist at the old
engine revision without their `src/` prefix. Those entries were not rewritten
in this narrowly scoped research update. This inherited anchor debt remains a
separate correction before a future capsule retarget; it does not invalidate
the separately checked candidate anchors or provide new runtime evidence.
