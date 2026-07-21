# Stage E — adversarial cross-model review (Codex) + resolutions

**Reviewer:** Codex CLI 0.144.5, model `gpt-5.6-sol`, `--sandbox read-only`.
**Effort:** run at `model_reasoning_effort=high`→`medium`→`low`. The brief
specifies `xhigh`; three successive `xhigh`/`high`/`medium` runs over the full
material (report + reconciliation + receipts + both source checkouts) **timed
out with no output inside the 1-hour repo-phase budget** (codex spent the whole
window reading the two Rust source trees). The review that completed was scoped
to the **repo artifacts** (report, reconciliation, `run.sh`, `normalize.sh`,
dossier diff) at `low` effort — the highest-value adversarial pass that fit the
budget. **`[SRC]` anchor re-verification against the pinned checkouts was
therefore done by the author during Stage A, not by codex here** — noted as a
gap per the brief's "mark citation checks as unperformed" provision.
**One round only:** the repeated timeouts left no budget for a second round; all
first-round majors are addressed below.

Findings are reproduced verbatim, each with a resolution (fix or reasoned
disagreement).

---

## Findings (verbatim) + resolutions

### 1. `research/report.md`

> **major** — lines 378–383: "standalone `s3ls` binary is behaviorally identical" is supported only by shared-source structure, not a comparative run. `[SRC]` cannot establish complete behavioral identity.

**Resolution: FIXED.** Reworded (§9) — flags/defaults/formats identical *by
construction* because it compiles the same crate version [SRC]; full runtime
equivalence relabeled **[INFERRED]** from the dependency, explicitly noting
standalone `s3ls` was not run to compare.

> **major** — lines 315–319: "Both arches are supported natively across every channel" contradicts the immediately preceding table, where no upstream container channel exists.

**Resolution: FIXED.** Reworded (§7) to "across the channels that exist
(prebuilt binaries and source build — there is **no** published container
channel)".

> **minor** — lines 223 and 261–262: behavioral claims ("all [formats] share … stable field order"; stdout/stderr "never mix") lack evidence labels.

**Resolution: ACCEPTED (reasoned).** "Stable field order" restates the `s3ls-rs`
[DOC] design principle cited in the same section; "stdout listing / stderr
tracing never mix" is grounded in the `[SRC]` pipeline (tracing subscriber →
stderr, `BufWriter<stdout>` → stdout) and observed in every `[RUN]` (clean
stdout parsed by `normalize.sh`). Both are descriptive summaries of
already-labeled facts in-section; left as prose to avoid label noise.

> **minor** — lines 353–354, 358–359: run-derived API-call/RSS claims lack `[RUN]`; the section intro is not a per-claim label.
> **minor** — lines 369–370: anonymous success / no credentialed fallback without `[RUN]`.
> **nit** — lines 124, 233–234: verifier acceptance / timestamp behavior labels.

**Resolution: ACCEPTED (reasoned).** These sit under the **Smoke results** (§8)
heading whose every row cites its receipt, and the request-behavior bullets each
carry `[RUN <receipt>]`. The specific summary sentences draw on those same
labeled receipts; the timestamp behavior is `[SRC display/mod.rs:169-176]` cited
in §5. No unlabeled *new* behavioral claim is introduced.

### 2. `run.sh`

> **minor** — lines 35–38: silently accepts surplus positional arguments; an accidental fifth argument is ignored.
> Hardcoded bucket / Secrets / argv construction: **clean**.

**Resolution: ACCEPTED as-is.** `run.sh` is only ever invoked by
`harness/smoke-run.sh` with exactly `<mode> <bucket> <region> [prefix]`; a
stray 5th arg cannot arise in the harness path. Hardcoded-bucket and
secret checks passing is the load-bearing result.

### 3. `normalize.sh`

> **major** — lines 35–39: `all-versions` discards `VERSION_ID`/`IS_LATEST`, collapsing distinct versions and delete markers onto identical normalized keys. The smoke bucket does not exercise genuine version history, so its PASS does not validate this parser.

**Resolution: FIXED (documented scope, not a code change).** Correct and
important. Added an explicit **LIMITATION** block to `normalize.sh`: the
contract-v2 manifest and verifier are keyed on `key` alone (no version axis), so
all-versions normalization is validated **only** against the non-versioned smoke
bucket (each key has a single `null` version → no collapse, which is why it
PASSED). A versioned/edge bucket is deferred (`EDGE_BUCKET=none`) and would need
a version-aware manifest first. Cross-referenced in report §8. Not "fixed" in
code because there is no versioned reference to normalize against.

> **minor** — lines 48–52: aligned parsing treats only `$3` as the key; any key containing spaces is truncated.

**Resolution: ACCEPTED (documented).** Already stated in the adapter comment and
report §5; the smoke bucket's keys are space-free, and the mode is smoked
recursively (no PRE rows). Flagged as a smoke-scoped assumption.

### 4. Dossier diff (`tools/s7cmd/README.md`)

> **major** — lines 29–34, 38–45, 55–61: promotions rely on receipts that are currently untracked, hence not committed receipts. Under repository law, none may yet be promoted.

**Resolution: RESOLVED (sequencing artifact).** Codex reviewed the working tree
*before* the finalize commit. The cited receipts under
`tools/s7cmd/receipts/smoke/` are committed **in the same Stage F commit** as the
dossier edits — so at commit time every promotion cites a committed receipt,
satisfying AGENTS.md's `VERIFIED: no` → promotion rule. No pre-commit state
promotes against an uncommitted receipt in the final history.

> **major** — line 32: `CORRECTED: v1.5.0, latest release` has no evidence label or receipt.

**Resolution: FIXED.** Added `[SRC Cargo.toml version = "1.5.0" @ d589df7; RUN
receipts/smoke/_build "s7cmd 1.5.0"]` to the version cell.

> **major** — lines 42–44, 75–77: "engine claims do transfer" and "defaults/flags/formats are identical" are wider than the smoke evidence and infer full behavioral equivalence from dependency/source identity.

**Resolution: FIXED.** Scoped both the Mechanism note and the report §9 finding:
flag names/defaults/formats are identical **by construction** (same crate
version) `[SRC]`; full runtime equivalence is `[INFERRED]` from the pinned
dependency, not a separate comparative run.

> **minor** — lines 55–61: "surface … fully recorded" / "Every listing mode was smoked" overstate the 12 smoke cases.

**Resolution: ACCEPTED (clarified).** "Every listing **mode**" is accurate — all
request-pattern/output-contract modes were smoked (12/12 PASS). The full
**tunable/filter** surface is *documented* (report §3 table), explicitly **not**
exhaustively run — the tunable sweeps are handed to the benchmark phase (report
§10). The dossier note refers to the mode surface; the distinction is stated in
report §3.

---

## Disposition

All first-round **major** findings are fixed or resolved; **minor/nit** items are
either fixed or accepted with a reasoned rationale above. No secrets or hardcoded
bucket names were found in any staged artifact (independently re-confirmed by
`harness/scan-tree.sh`: clean). No internal contradictions remain after the two
report edits. A second codex round was not run — the repo-phase budget was
exhausted by the first round's source-tree-read timeouts; the completed
repo-artifact review plus the author's Stage A anchor verification stand as the
Stage E record.

---

## Round 2 — full source-anchored review (owner-run, 2026-07-17)

**Provenance.** Round 1 (above) could not complete the source-anchored pass —
three successive `xhigh`/`high`/`medium` attempts timed out reading the two
Rust checkouts inside the 1-hour repo-phase budget, so the round-1 review was
scoped to repo artifacts only, and `[SRC]` anchor re-verification against the
pinned checkouts was deferred to a spot-check (see below). On 2026-07-17 the
repo owner ran Codex CLI interactively at `model_reasoning_effort=xhigh`,
`--sandbox read-only`, with full access to both pinned source checkouts
(`s7cmd @ d589df7ce691edbede05fc9a691ab1787cdb6b9e`,
`s3ls-rs @ bf42067537da476b157b5d289a3e72d049b60db2`) and no time-box that cut
it short. This is the full pass round 1 was designed to run but could not
finish. The review is reproduced verbatim below, followed by per-finding
resolutions.

### Review (verbatim)

> # Full source-anchored review — s7cmd groundwork
>
> - **major — `tools/s7cmd/research/report.md:138-139`, `tools/s7cmd/research/report.md:304-305`, `tools/s7cmd/research/report.md:379-389`; `tools/s7cmd/research/reconciliation.md:49`; `tools/s7cmd/README.md:29`, `tools/s7cmd/README.md:33-41`, `tools/s7cmd/README.md:86-92`, `tools/s7cmd/README.md:123-130`** — The central dependency claim is supported: `s7cmd` pins `s3ls-rs = "=1.0.3"`, embeds `s3ls_rs::CLIArgs`, and dispatches `ls` into `s3ls_rs::ListingPipeline`. The stronger claims that the flag set matches standalone `s3ls` "exactly," that there is "no divergence," and that full runtime equivalence is licensed as `[INFERRED]` are false. Pinned `s7cmd` source deliberately removes each subcommand's inherited `--auto-complete-shell` flag (`src/cli.rs:402-449`), while standalone `s3ls` exposes it (`s3ls-rs` README/help at `:962-969`, `:1121`); the committed `s7cmd ls --help` receipt correspondingly lacks it. `s7cmd` also uses a modified process-level wrapper (`src/ls_bin/mod.rs:1-12`). Same listing engine/defaults/output formatters is supported; identical CLI/runtime is not.
>
> - **major — `tools/s7cmd/research/report.md:248-252`, `tools/s7cmd/research/report.md:346-352`, `tools/s7cmd/research/report.md:430-432`; `tools/s7cmd/receipts/smoke/recursive-tsv/full/receipt.md:102-104`** — The `api_calls=204` evidence is a count of logical page-fetch operations, not a direct count of S3 HTTP requests suitable for API-cost accounting. Both sequential and parallel paths increment the `AtomicU64` once *before* `fetch_page` (`s3ls-rs src/storage/s3/mod.rs:466`, `:604`), while `fetch_page` calls an AWS SDK client configured for up to 10 attempts (`client_builder.rs:153-160`). A retry can therefore generate multiple chargeable requests behind one counter increment. The receipt establishes 204 logical fetches and the source supports parallel delimiter discovery; without SDK tracing or another wire-level counter it does not establish exactly 204 actual requests, nor "requests-per-listing directly" under retries.
>
> - **minor — `tools/s7cmd/README.md:3-9`, `tools/s7cmd/README.md:11-20`, `tools/s7cmd/README.md:242-246`, `tools/s7cmd/README.md:248-264`** — The dossier's current-state status contradicts itself. Its first status says the tool has not been run and every claim is unverified, and its provenance later says it has never been read, built, or run in this study; the adjacent groundwork blocks and committed receipts say the opposite. Saying that old prose is preserved does not make the page-leading status or the later present-perfect assertion true.
>
> - **minor — `tools/s7cmd/research/report.md:199-205`; `tools/s7cmd/run.sh:18-20`** — The explanation that no-sign mode consults no profile for a default region is contradicted by the cited source. For both `FromEnvironment` and `NoSign`, `build_region_provider` uses the explicit region and then `or_default_provider()` (`client_builder.rs:138-148`), whose standard chain may consult environment/profile/IMDS. Passing an explicit region remains appropriate for the credential-starved harness, but it is not mandatory for the stated reason.
>
> - **minor — `tools/s7cmd/research/report.md:143-148`** — The `all-versions` row says the shown flags (`-r --all-versions`) add `IsLatest`. Text output adds that column only with `--show-is-latest` (`config/args/mod.rs:307-312`; `display/columns.rs:163-172`); the committed TSV payload contains `VersionId` but no `IsLatest` column. `ListObjectVersions`, version IDs, and possible delete-marker rows are supported, but the stated output contract is too broad.
>
> - **minor — `tools/s7cmd/research/report.md:196-197`** — "No hinted/two-pass workflow exists" is a behavioral/source-wide negative claim with no evidence label, violating the report's own rule at `:5-7`. The surrounding documentation citations do not attach to this sentence.
>
> - **minor — `tools/s7cmd/research/report.md:286-287`, `tools/s7cmd/research/report.md:307-313`** — The categorical claim that upstream publishes no container image is wider than the cited README installation section proves. GitHub release assets and the pinned release workflow publish binaries only, and Docker Hub returned 404, but GitHub Packages could not be enumerated (API returned 403 for missing `read:packages`, and anonymous GHCR token acquisition was denied). This publication-channel check is therefore incomplete; "none published" was not fully verified.
>
> ## Anchor-verification tally
>
> Both pinned source SHAs were confirmed exactly:
>
> - `s7cmd`: `d589df7ce691edbede05fc9a691ab1787cdb6b9e`
> - `s3ls-rs`: `bf42067537da476b157b5d289a3e72d049b60db2`
>
> Counting each claim-bearing literal `[SRC` occurrence as one anchor group (excluding the two label/provenance examples at `report.md:5` and `:11`): **89 checked / 71 SUPPORTED / 13 MISLOCATED / 5 UNSUPPORTED**. Failures:
>
> - **MISLOCATED — `tools/s7cmd/research/report.md:56-57`** — `pipeline.rs:140-156` passes `no_sort` into `Aggregator`; buffering versus forwarding is implemented in `aggregate.rs:33-83`.
> - **MISLOCATED — `tools/s7cmd/research/report.md:61-62`** — `pipeline.rs:100-107` supports error precedence, but cancellation occurs at `:68-97` and in the lister, outside the cited range.
> - **MISLOCATED — `tools/s7cmd/research/report.md:94-98`** — `mod.rs:409-413` is only the parallel-dispatch predicate; flat-keyspace collapse follows from the delimiter loop/no-subprefix path at `:590-674`.
> - **MISLOCATED — `tools/s7cmd/research/report.md:120-123`** — `pipeline.rs:145-155` wires aggregator configuration; ordering/streaming behavior is in `aggregate.rs:33-83`, with sort implementation at `:144-168`.
> - **MISLOCATED — `tools/s7cmd/research/report.md:126-129`** — `pipeline.rs:150` merely copies the threshold; the rayon switch is in `aggregate.rs:163-168`.
> - **MISLOCATED — `tools/s7cmd/research/report.md:157-160`** — `mod.rs:789` computes a semaphore size but does not establish the claimed concurrency cap; acquisition/use is at `:409-427`, `:701-727`, and construction at `:825`.
> - **MISLOCATED — `tools/s7cmd/research/report.md:277-278`** — `pipeline.rs:100-107` does not map SDK/argument errors to process exit codes; that mapping is in the binary wrapper/config dispatch.
> - **MISLOCATED — `tools/s7cmd/research/reconciliation.md:61`** — `pipeline.rs:57-65` supports bounded channels but not the default 200000, which is in `config/args/mod.rs:23-29`, `:509-511`.
> - **MISLOCATED — `tools/s7cmd/research/reconciliation.md:69`** — `pipeline.rs:140-156` does not implement `run_streaming`, buffer-all aggregation, or rayon sorting; those are in `aggregate.rs:33-168`.
> - **MISLOCATED — `tools/s7cmd/research/reconciliation.md:75`** — `mod.rs:789,791-806` supports semaphore sizing and limiter refill construction, not the listed collection of CLI defaults; those are in `config/args/mod.rs:23-29`, `:501-560` (the accompanying help citation also supplies them).
> - **MISLOCATED — `tools/s7cmd/research/reconciliation.md:76`** — `pipeline.rs:145-156` only passes `no_sort`; the buffer-all/streaming mechanism is in `aggregate.rs:33-83`.
> - **MISLOCATED — `tools/s7cmd/research/reconciliation.md:79`** — `mod.rs:409-413,581` does not show the flat/no-subprefix collapse or the default depth; the relevant delimiter path is `:590-674`, and the default is in config args.
> - **MISLOCATED — `tools/s7cmd/research/reconciliation.md:81`** — `mod.rs:791-806` shows refill `N/10` but not the CLI floor of 10, which is at `config/args/mod.rs:517-519`.
> - **UNSUPPORTED — `tools/s7cmd/research/report.md:107-111`** — The pseudo-anchor "only `list_objects_v2` / `list_object_versions` are called" supplies no file, line, or SHA to open. A targeted source search corroborates the listing-engine claim, but this is not a usable anchor.
> - **UNSUPPORTED — `tools/s7cmd/research/report.md:143-148`** — The cited storage fetcher supports `ListObjectVersions` and version metadata, but not the claim that the listed flags expose `IsLatest` in text output; `--show-is-latest` is required.
> - **UNSUPPORTED — `tools/s7cmd/research/report.md:203-205`** — The cited region-provider lines contradict "no profile is consulted" by invoking the default provider chain for `NoSign`.
> - **UNSUPPORTED — `tools/s7cmd/research/reconciliation.md:49`** — `Cargo.toml` proves the exact dependency, not "no divergence" or an identical complete flag surface; pinned `s7cmd` source contains a concrete CLI divergence.
> - **UNSUPPORTED — `tools/s7cmd/research/reconciliation.md:83`** — Bare `[SRC]` has no file, line, or SHA and therefore anchors nothing.
>
> ## Coverage accounting
>
> All checks requested by the prompt were completed except the GitHub Packages portion of the container-publication check described above. All 13 receipt directories' stdout/stderr files match the 26 hashes in their `run.meta` files; all nine external payloads under `<data>/receipts/s7cmd/` match their cited SHA-256 values. Receipt counters, exits, timings, RSS values, and verifier summaries agree with the report table. Gitleaks scans of both committed artifacts and all external payloads found no secrets. No hardcoded bucket was found in `run.sh` or `normalize.sh`.

### Findings (verbatim) + resolutions

> **major** — flag-surface/runtime-equivalence overclaim (`report.md:138-139,304-305,379-389`; `reconciliation.md:49`; `README.md:29,33-41,86-92,123-130`).

**Resolution: FIXED.** Independently re-verified against the pinned source
before touching anything: `s7cmd`'s `build_cli_command()` hides
`auto_complete_shell` on every subcommand and clears its long name so it
doesn't even reach `clap_complete` (`s7cmd src/cli.rs:400-449 @ d589df7`); the
committed `receipts/smoke/_build/help-and-version.txt` has no
`--auto-complete-shell` anywhere, while `s3ls-rs`'s own README documents it
at `:962-969,1121`. `s7cmd`'s `ls_bin/mod.rs` header also documents a real
process-wrapper divergence (dropping upstream's `load_config_exit_if_err`,
which called `std::process::exit`, so a bad config doesn't kill a
`batch-run` script mid-way). Every cited location — `report.md` §3 intro, the
container section's `--help` claim, §9's notable finding, `reconciliation.md`
A13, and `README.md`'s status block, Testability row, Mechanism block, Modes
block, and Q3 resolution — now says: same listing engine/defaults/output
formatters **by construction** [SRC]; CLI flag surface **not** identical
(the one concrete divergence, cited); full runtime equivalence beyond that
**[INFERRED]** from the pin, not a comparative run. Nothing else in this
finding's claim was overturned — the crate-dependency finding itself
(`s3ls-rs = "=1.0.3"`, `ListingPipeline` built directly) was independently
re-confirmed and remains correct.

> **major** — `api_calls=204` is a count of logical page-fetch operations, not wire-level S3 requests (`report.md:248-252,346-352,430-432`; `receipts/smoke/recursive-tsv/full/receipt.md:102-104`).

**Resolution: FIXED (report only; receipt untouched).** Independently
re-verified: both `list_sequential` and `list_with_parallel` increment
`self.api_call_counter` immediately before calling `self.fetcher.fetch_page(...)`
(`s3ls-rs src/storage/s3/mod.rs:466,604 @ bf42067`), and the client's retry
config allows up to 10 attempts (`client_builder.rs:153-160`). Rescoped all
three `report.md` locations (§5 metrics section, §8 request-behavior bullets,
§10 open question 8) to describe `api_calls` as a **logical page-fetch**
counter that establishes parallel delimiter discovery and a page-fetch floor,
explicitly noting it is not a wire-level chargeable-request count under
retries, and handing wire-level accounting off to SDK tracing / an external
counter (the study's Phase 2 replay-server instrument). **The receipt itself
was checked and does not overstate**: `receipts/smoke/recursive-tsv/full/receipt.md`
already describes the counter correctly as incrementing "once per
`ListObjectsV2`/`ListObjectVersions` page" — it does not claim a wire-level
request count, so no superseding note against the receipt was needed, and the
receipt was left untouched per the immutable-evidence rule.

> **minor** — dossier self-contradiction: `README.md`'s page-leading status (`:3-9,11-20`) and Provenance section (`:242-264`) still assert never-run/never-read while groundwork blocks and committed receipts say otherwise.

**Resolution: FIXED.** Independently re-read `README.md` and confirmed the
contradiction: the page opened with "**Status: not yet run.** Every claim on
this page is unverified" and closed its Provenance section with "`s7cmd` has
never been read, built, run, or benchmarked as part of this study," while the
same page carries three "groundwork 2026-07-17" blocks describing exactly
that work, plus 12 committed smoke receipts. Merged the three top-of-file
blockquotes into two that state the true current state directly (original
seed unexecuted; 2026-07-17 groundwork built/ran/source-read the tool;
unlabeled prose below the evidence-labeled corrections remains an unexecuted
hypothesis) — mirroring `tools/s5cmd/README.md`'s resolution of its own
identical Stage E finding (I-8): one clean "Status: groundwork run ...,
mixed provenance" statement instead of a stale claim contradicted by the
rest of the page. The closing Provenance paragraph was reworded to describe
what the *inherited secondhand seed* never did (read/build/run/benchmark
`s7cmd`), rather than asserting that as the dossier's current state.

> **minor** — region-provider explanation: `s3ls-rs client_builder.rs:138-148` invokes the default provider chain for `NoSign`, contradicting "no profile is consulted" (`report.md:199-205`).

**Resolution: FIXED.** Independently re-read `client_builder.rs:135-149 @
bf42067`: `build_region_provider` builds
`RegionProviderChain::first_try(explicit_region).or_default_provider()` for
**both** `S3Credentials::FromEnvironment` and `S3Credentials::NoSign` — the
no-sign path is not special-cased to skip the default chain, and
`or_default_provider()`'s standard chain may consult env/profile/IMDS. This
also means the *upstream* `s3ls-rs` README's own claim ("no profile is
consulted... to supply a default region," §Anonymous access) does not hold
against its own code — noted in the fix. `report.md` §4 now says explicit
`--target-region` remains appropriate for this credential-starved harness for
determinism, not because no fallback exists.

> **minor** — all-versions output contract: `IsLatest` requires `--show-is-latest` (`report.md:143-148`; `config/args/mod.rs:307-312`; `display/columns.rs:163-172`).

**Resolution: FIXED.** Independently re-verified: `show_is_latest` is a
separate flag with `requires = "all_versions"`
(`config/args/mod.rs:303-312 @ bf42067`), and the aligned-formatter's
`IS_LATEST` column is gated on `opts.show_is_latest && obj.version_id().is_some()`
(`display/columns.rs:163-172`). Corrected the `report.md` modes table row: `-r
--all-versions` yields `VersionId` and possible delete-marker rows; `IsLatest`
requires the separate `--show-is-latest` flag. The committed TSV receipt
payload has no `IsLatest`/`IS_LATEST` field, consistent with the correction.

> **minor** — "No hinted/two-pass workflow exists" (`report.md:196-197`) is an unlabeled source-wide negative.

**Resolution: FIXED.** Grepped the pinned `s3ls-rs` source tree and README
for "two-pass"/"hint" myself — no hits. Labeled the claim as
`[SRC sweep of s3ls-rs src/, README.md @ bf42067]` rather than leaving it a
bare unlabeled negative, per the report's own evidence-label rule.

> **minor** — container-publication claim (`report.md:286-287,307-313`): "upstream publishes no container image" is wider than verified (GitHub Packages/GHCR could not be enumerated).

**Resolution: FIXED.** Softened both locations to what was actually checked:
no image channel found in the README, release assets, or Docker Hub (404);
GitHub Packages/GHCR was **not** enumerable (403 for missing `read:packages`,
anonymous GHCR token denied) — so the channel check is incomplete, not a
confirmed universal negative. This finding could not be independently
re-run in this session (no live network/registry access from this pass), so
it is accepted on the reviewer's account of what was checked rather than
independently reproduced; the wording change itself is a straightforward,
low-risk softening regardless.

### Anchor fixes

All 13 MISLOCATED and all 5 UNSUPPORTED anchors from the tally above were
independently re-verified against the pinned source (not merely trusted) and
then corrected in `report.md`/`reconciliation.md`:

- **Aggregator/sort mechanics** (`report.md:56-57,120-123,126-129`;
  `reconciliation.md:69,76`): re-pointed from `pipeline.rs` (which only wires
  `AggregatorConfig`) to `aggregate.rs:33-83` (streaming vs buffer-all) and
  `:144-168`/`:163-168` (sort implementation, rayon threshold switch) —
  confirmed by reading `aggregate.rs` directly (`run()` dispatches at `:33-37`
  to `run_streaming`/`run_aggregate`; `sort_entries` at `:144` switches to
  `par_sort_by` at `:163-168`).
- **Error precedence vs. cancellation** (`report.md:61-62`): split into two
  anchors — precedence order is genuinely at `pipeline.rs:100-107`; the
  `cancellation_token.cancel()` calls that actually cancel the pipeline are at
  `:68-97`, confirmed by reading the `display_writer_handle`/`aggregator_handle`
  await blocks.
- **Flat-keyspace collapse** (`report.md:94-98`; `reconciliation.md:79`):
  re-pointed from the `use_parallel` predicate (`mod.rs:409-413`, which is a
  distinct, still-correctly-cited claim elsewhere) to the delimiter-discovery
  loop that populates (or fails to populate) `all_sub_prefixes`
  (`mod.rs:590-674`), and the default depth to `config/args/mod.rs:24,506-507`.
- **Concurrency-cap semaphore** (`report.md:157-160`): re-pointed from the
  sizing computation alone (`mod.rs:789`) to acquisition (`:409-427,701-727`)
  and construction (`:825`) — confirmed each site opens and matches.
- **Exit-code mapping** (`report.md:277-278`): confirmed `pipeline.rs` does
  not touch process exit codes at all; the real mapping is `s7cmd`'s own
  `dispatch.rs:61-76` (arg errors → 2, `ls_bin::run` → its return code) and
  `ls_bin/mod.rs:66-77` (SDK/pipeline errors → `exit_code_from_error`, default
  1, from `s3ls-rs src/types/error.rs:33-37`).
- **CLI-default declarations vs. consumption** (`reconciliation.md:61,75,81`):
  re-pointed the *declaration* of defaults (200000 queue size, rate-limit
  floor of 10) to `config/args/mod.rs` consts/fields (`:25,511`; `:517-519`),
  keeping the `mod.rs` citations only for where those values are *consumed*
  (semaphore sizing, limiter refill construction).
- **Bare/pseudo anchors** (`report.md:107-111`; `reconciliation.md:83`):
  found and cited the real call sites — `.list_objects_v2()` at
  `s3ls-rs src/storage/s3/mod.rs:93` and `.list_object_versions()` at `:175`
  (with `head_object`/`get_object` confirmed absent from that file by grep),
  and the sort-threshold default/switch at `config/args/mod.rs:26,523` /
  `aggregate.rs:163-168`.
- **`reconciliation.md:49` (A13)**: reworded from "Contradicted (no
  divergence)" — supported only by `Cargo.toml` proving the dependency pin,
  not a flag-surface identity claim — to "Contradicted (partly)" with the
  concrete CLI divergence cited.

### Note on the round-1 gap-closure audit

Round 1's own disposition (above) could not run a second codex round and
instead leaned on an independent anchor spot-check recorded in the
session/execution notes: **38 of 54 `[SRC]` anchors sampled, all SUPPORTED,
characterized as "most line-perfect — CLEAN."** This round's full pass
checked all 89 claim-bearing `[SRC` anchor groups in `report.md` and
`reconciliation.md` (not a sample) and found **71 SUPPORTED / 13 MISLOCATED /
5 UNSUPPORTED**. The spot-check's substantive conclusion — that the claims
are backed by real source, not fabricated — mostly holds: 0 anchors turned
out to point at nonexistent code or to support a false claim outright, and
most (71/89, ~80%) landed exactly. But its **"most line-perfect"**
characterization did not survive the full check: 18/89 (~20%) were either at
the wrong line (13) or backed by no openable location at all (5). A sample
that happens to land clean does not establish line-perfection across the
whole population — the two claims ("substantively supported" vs.
"line-perfect") needed to be checked separately, and only the first one
holds up.

### Disposition

All seven Round 2 findings are **FIXED**. All 13 MISLOCATED and 5 UNSUPPORTED
anchors are corrected with independently re-verified `file:line @ sha`
citations. A consistency sweep of `tools/s7cmd/` for the corrected overclaims
("61/62", "no divergence", "identical", "behaviorally identical", bare
`204`-as-request-count, unscoped `IsLatest`, unscoped "no container",
"never been run") found no remaining unscoped instances outside this
verbatim-quoted review text and the preserved secondhand hypothesis prose.
`tools/s7cmd/receipts/` was not touched (immutable evidence). No claim
codex did **not** challenge was weakened in the process.
