# s7cmd v1.8.5 source update — 2026-10-15

This is a source and build-preparation update for study issue
[#29](https://github.com/varveio/s3-listing-study/issues/29), not a run record.
No subject command, including help/version, container build, package install,
benchmark, or cloud task was run. Existing research and receipts are unchanged.

## Selected releases and request mapping

Issue #29 was opened 2026-09-23 by the s7cmd author, requesting then-latest s7cmd
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
[Version 1.8.4 exact crate pin](https://github.com/nidor1998/s7cmd/blob/c443920f2596f2afa18a8f5c73f8d70fc46e90fe/Cargo.toml#L20-L25).

The official artifact is
[`s7cmd-1.8.5-linux-glibc2.28-x86_64.tar.gz`](https://github.com/nidor1998/s7cmd/releases/download/v1.8.5/s7cmd-1.8.5-linux-glibc2.28-x86_64.tar.gz).
Its GitHub asset digest and separately
downloaded bytes agree on SHA-256
`a8ebdaf9922b52659311cbf66264291b0a8cac7072b77323899e3cb41f8ed27c`.
Archive member inspection found `s7cmd`. This verifies selected artifact bytes
and layout only, not that the executable loads or behaves as source suggests.
[DOC official release](https://github.com/nidor1998/s7cmd/releases/tag/v1.8.5).

## Exact listing bug and fix

The referenced s3ls-rs **1.1.0** change is a concurrency-limit correction.
Parallel listing discovers `/` prefixes until `max_parallel_listing_max_depth`,
then handles a deeper prefix with delimiter-free sequential pagination.
At the registered engine revision 1.0.3, the branch explicitly calls
`drop(permit)` before awaiting that leaf scan.
[SRC src/storage/s3/mod.rs:580-587 @ bf42067537da476b157b5d289a3e72d049b60db2]
[Registered 1.0.3 leaf branch](https://github.com/nidor1998/s3ls-rs/blob/bf42067537da476b157b5d289a3e72d049b60db2/src/storage/s3/mod.rs#L580-L587).
Many deep prefixes could therefore drain simultaneously without consuming
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
rounded effective rates down to multiples of ten; the candidate engine computes
an exact refill schedule.
[SRC CHANGELOG.md:76-85 @ 2993e4f8fd23316c63f63d04a6b6f2102cfdd41b]
No rate-limited run is claimed here.
[SRC src/storage/s3/mod.rs:863-895 @ 2993e4f8fd23316c63f63d04a6b6f2102cfdd41b]

## Changelog coverage

The listing-relevant release notes between s3ls-rs 1.0.3 and 1.3.4, and
s7cmd 1.5.0 and 1.8.5, describe additional changes. In addition to permit accounting
and exact rate limiting, they document removal of the positional `TARGET`
environment fallback (1.2.0 engine / 1.7.0 umbrella), shell-completion broken-pipe
handling (1.2.2 / 1.7.2), Ctrl-C exit 130 (1.3.0 / 1.8.0), newline termination of
configuration errors (1.3.1 / 1.8.1), dependency/security refreshes, and the newer
S3-compatible-service support policy. This is changelog coverage, not runtime
verification of those changes. Existing explicitly passed bucket targets avoid
relying on the removed positional environment fallback.
[SRC CHANGELOG.md:8-95 @ 2993e4f8fd23316c63f63d04a6b6f2102cfdd41b]
[SRC CHANGELOG.md:8-590 @ 1e8c7d0644f30cb5da927b87277376620fcd0554]

## CLI and output source derivation

The carried-forward flags have source declarations: recursive listing,
`--no-sort`, `--show-etag`, `--show-storage-class`, `--json`, `--tsv`, `-1`,
`--disable-color-tracing`, region/endpoint/path-style/unsigned settings,
`--max-parallel-listings`, fan-out depth, retry attempts and connect timeout.
[Mode/output declarations](https://github.com/nidor1998/s3ls-rs/blob/2993e4f8fd23316c63f63d04a6b6f2102cfdd41b/src/config/args/mod.rs#L96-L428),
[AWS, concurrency, retry and timeout declarations](https://github.com/nidor1998/s3ls-rs/blob/2993e4f8fd23316c63f63d04a6b6f2102cfdd41b/src/config/args/mod.rs#L430-L550).
[SRC src/config/args/mod.rs:96-110,226-280,331-405,425-550 @ 2993e4f8fd23316c63f63d04a6b6f2102cfdd41b]


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

No v1.8.5 receipt, performance result, correctness verdict or completed
runtime qualification is recorded as of 2026-10-15.

### Future retarget file map and validation order

The following is a future implementation path, not a completed build. Follow
[tool onboarding](../../../docs/operating/tool-onboarding.md),
[capsule authoring](../../../docs/operating/capsule-authoring.md) and the
[build/identity contract](../../../benchmark/docs/identity.md) when reopening it.
The historical-source and migration-origin decisions above come first; this
map does not silently supply a schema solution.

| Files or helpers | Future change or check |
| --- | --- |
| `data/tool.json`, `data/claims.json` | Represent the new subject without rebinding historical claims or receipts; resolve the historical-source and new-origin constraints before changing canonical identity |
| `adapter/command.py`, `adapter/normalize.py` | Review every emitted mode/flag against pinned CLI source and every projected field against its formatter; preserve workload semantics, disclose the inherited timeout, and use candidate-source default provenance until real help exists |
| `build/Dockerfile`, `build/image.json` | Select the exact candidate asset URL/name/checksum above; keep executable and workdir declarations consistent with installation; derive both build-input and adapter-bundle digests |
| [shared Dockerfile](../../../benchmark/build/Dockerfile) | Update `s7cmd_install` to the identical artifact; preserve shared-base and runtime-closure contracts rather than introducing a standalone parent image |
| [build-selection helpers](../../../benchmark/src/benchmark/runtime/build_selection.py) | `adapter_bundle_sha256` and the existing build-input hashing implementation define the digests; `load_registered_selection` must accept the resulting metadata, with no hand-invented digest |
| [toolbox build helpers](../../../benchmark/src/benchmark/build_image.py), [context allowlist](../../../benchmark/build/Dockerfile.dockerignore) | `registered_selections`, `validate_executed_sources` and `toolbox_manifest` must bind the selected artifact and executed recipe; add `SUPPORT_INPUTS` and context entries only if the retarget introduces supporting build files |
| [command tests](../../../benchmark/tests/test_command_adapters.py), [normalizer tests](../../../benchmark/tests/test_adapters.py), [build tests](../../../benchmark/tests/test_build_image.py) | Update meaningful source-derived expectations when semantics change; old fixture tests alone cannot validate the new binary |
| `docs/`, `README.md`, dated research | State separately what is source-supported, built, actually executed and verified; retain historical evidence under its own version |

Validation proceeds in distinct stages:

1. **Static preparation:** capsule/schema and links, every old/new source anchor
   against its exact checkout, build/adapter digest checks, command/normalizer
   unit tests and offline toolbox manifest/source checks. Resolve the inherited
   anchor debt tracked in [open questions](../../../docs/open-questions.md#s7cmd-historical-deep-leaf-concurrency-and-anchor-debt).
   These checks do not establish that the candidate executable loads or works.
2. **After subject execution is reopened:** use the existing shared Docker
   toolbox build path from a clean reviewed revision. Capture actual version
   and help, inspect build metadata and complete intended mode/output smoke
   checks in the container. A Docker build may itself execute subjects; it is
   therefore part of this later stage, not static preparation.
3. **Future benchmark preparation:** follow the harness
   [running guide](../../../benchmark/docs/running.md) and
   [publishing rules](../../../benchmark/docs/publishing.md) for digest-pinned
   images, exact argv/config/resources, retained failures and content
   verification. Qualify hierarchical/deep-leaf, flat and skewed fixtures
   before drawing any comparison; source fixes do not establish a ranking.

No Docker build, subject command or cloud run is part of this source update.

## Pinned source index

These links highlight the primary sources for key findings; complete pinned
references accompany the individual findings and research-ledger entries.

| Finding | Pinned primary source |
| --- | --- |
| s7cmd version and exact listing-engine dependency | [Cargo.toml, lines 1–25](https://github.com/nidor1998/s7cmd/blob/1e8c7d0644f30cb5da927b87277376620fcd0554/Cargo.toml#L1-L25) |
| 1.1.0 fix announcement | [s3ls-rs changelog](https://github.com/nidor1998/s3ls-rs/blob/2993e4f8fd23316c63f63d04a6b6f2102cfdd41b/CHANGELOG.md#L75-L85) |
| Leaf scan retains its permit | [storage/s3/mod.rs, lines 582–595](https://github.com/nidor1998/s3ls-rs/blob/2993e4f8fd23316c63f63d04a6b6f2102cfdd41b/src/storage/s3/mod.rs#L582-L595) |
| Parent releases permit before child tasks | [storage/s3/mod.rs, lines 685–738](https://github.com/nidor1998/s3ls-rs/blob/2993e4f8fd23316c63f63d04a6b6f2102cfdd41b/src/storage/s3/mod.rs#L685-L738) |
| Default width, depth, queue and sort threshold | [config/args/mod.rs, lines 23–26](https://github.com/nidor1998/s3ls-rs/blob/2993e4f8fd23316c63f63d04a6b6f2102cfdd41b/src/config/args/mod.rs#L23-L26) |
| Parallel dispatch conditions | [storage/s3/mod.rs, lines 402–432](https://github.com/nidor1998/s3ls-rs/blob/2993e4f8fd23316c63f63d04a6b6f2102cfdd41b/src/storage/s3/mod.rs#L402-L432) |
| Exact rate-limit refill schedule | [storage/s3/mod.rs, lines 863–895](https://github.com/nidor1998/s3ls-rs/blob/2993e4f8fd23316c63f63d04a6b6f2102cfdd41b/src/storage/s3/mod.rs#L863-L895) |
| JSON metadata fields | [display/json.rs, lines 27–67](https://github.com/nidor1998/s3ls-rs/blob/2993e4f8fd23316c63f63d04a6b6f2102cfdd41b/src/display/json.rs#L27-L67) |
| Caught Ctrl-C returns 130 | [ls_bin/mod.rs, lines 72–81](https://github.com/nidor1998/s7cmd/blob/1e8c7d0644f30cb5da927b87277376620fcd0554/src/ls_bin/mod.rs#L72-L81) |
