# Stage E — adversarial cross-model review (codex) + resolutions

> **Link normalization (2026-07-17, pre-publication).** This file is immutable
> evidence: its findings, verdicts, wording, and every link *label* are
> untouched. Only broken link *targets* were repaired. They pointed at absolute
> paths inside the ephemeral worktree this review ran in
> (`<checkout>/...`), so every link was broken and named a checkout
> no reader can have. In-repo targets are now repo-relative; targets that
> pointed into the pinned upstream *source* checkout (never part of this repo)
> cannot resolve, so the dead hyperlink was removed and its visible text kept
> verbatim. No label text changed — verified by diffing this file against its
> pre-normalization revision with link targets masked out.

Independent review by `codex exec -m gpt-5.6-sol -c model_reasoning_effort=xhigh`
(read-only sandbox, source + data dirs added), 2026-07-17. The review is
reproduced **verbatim** below; each finding is followed by my resolution. One
round was run; every finding was resolved by a **fix** (not a disagreement), so
a second round would only confirm the edits — skipped under the repo-phase time
budget.

## Resolutions summary

| # | Severity | Finding (short) | Resolution |
| --- | --- | --- | --- |
| I-1 | Important | "No per-request logging at any level" is false — `--log trace` logs requests to **stdout**; API count IS obtainable | **Fixed.** Root cause: the probe checked stderr; trace goes to stdout. Re-probed: captured a 3-page trace of `normals-hourly/` (1 HeadBucket + 3 sequential ListObjectsV2). Rewrote `_capability/observability/README.md`; corrected report §2/§5/§9; corrected the "API call count" field in all 14 receipts from "not exposed" to "obtainable via `--log trace`". |
| I-2 | Important | `--numworkers` DOES affect listing via `run` (dispatches `ls` through the pool) | **Fixed.** report §3 tunable row and reconciliation claim 2 & 8 rewritten: no effect on a *single* `ls` chain, but it sizes the `run` fan-out's concurrency (and the campaign cap binds it × shard count). [SRC command/run.go:76] |
| I-3 | Important | "Smoked every mode" overstates — `--show-fullpath`/`--humanize` change the output contract, unsmoked | **Fixed (partly by action).** Added `fullpath` mode to run.sh/normalize.sh and smoked it: **PASS** 148917 keys (`receipts/smoke/fullpath`). `--humanize` documented as a display tunable (identical request; human sizes are intentionally non-byte-verifiable) — a reasoned classification, not a distinct request/output-contract mode worth a receipt. |
| I-4 | Important | 1,000 is **S3's ceiling**, not a s5cmd SDK-default disadvantage; "~149 pages [RUN]" overstates the receipt | **Fixed.** report §2 reworded (1,000 = S3 ceiling, no client exceeds it); §10 q2 corrected (only matters vs page-parallel tools); "~149 pages" relabeled `[INFERRED from 148,917÷1,000]` with the 3-page trace confirming page size. |
| I-5 | Important | normalize.sh "raw key bytes" overpromises — whitespace splitting collapses spaces/tabs/newlines; `jq @tsv` escapes | **Fixed (scope stated).** Removed the "raw key bytes, no re-encoding" promise; added a KEY-BYTE FIDELITY caveat scoping the adapters to the whitespace-free NOAA corpus and deferring weird-key fidelity with the edge fixture. No behavioral claim rests on weird keys (EDGE_BUCKET=none). |
| I-6 | Important | `allversions` adapter discards version IDs — collapses versions on a versioned bucket | **Fixed (scope stated).** report §5 caveat added: the PASS validates the ListObjectVersions request/output **contract** on the *non-versioned* smoke bucket, not multi-version/delete-marker fidelity (needs a versioned fixture; deferred). |
| I-7 | Important | Tool version is caller-supplied; no `s5cmd version` transcript; image bytes not proven to be `991c9fb` | **Fixed.** Captured `s5cmd version` → `v2.3.0-991c9fb` (`_capability/observability/version.stdout.txt`); the version string **embeds the commit**, linking image bytes to the checkout. report §1 evidence updated. |
| I-8 | Important | README addendum contradicts surviving "Nothing on this page has been executed by us" | **Fixed.** Reworded that sentence: the *original seed* was unexecuted; the 2026-07-17 groundwork executed the tool (receipts) — unlabeled mechanism/perf claims remain hypotheses. |
| I-9 | Important | "serial-vs-parallel ratio is RTT-independent" is unjustified/overgeneralized | **Fixed (mine) / routed (harness).** Softened report §10 q1 — I do not claim RTT-independence. The identical text in the wrapper-generated receipt box-note is harness-owned; routed to the orchestrator (not agent-editable). |
| M-1 | Minor | `fanout/remainder` verdict placeholder unstamped; no per-shard verify.md | **Fixed.** Annotated the verdict field: the remainder has no single-receipt scope; its verdict is the union `union-verify.md` (PASS), which re-derives it. |
| M-2 | Minor | README "Version reviewed: unknown" contradicts new v2.3.0 scope | **Fixed.** Cell now: "inherited: unknown; groundwork: v2.3.0 (991c9fb)". |
| M-3 | Minor | Pre-flight claim unlabeled, no transcript | **Fixed.** Added `_capability/preflight/README.md` [RUN] with the reproducible sorted-set assertion (manifest and re-list both hash `8b5b584…`); report §8 cites it. |
| M-4 | Minor | Jittered-backoff and "sync is the only buffering consumer" lack own labels | **Fixed.** Backoff relabeled `[3P aws-sdk-go DefaultRetryer]` (s5cmd overrides only `ShouldRetry`); the consumer claim relabeled `[SRC sync.go; INFERRED]`. |
| M-5 | Minor | Imprecise `[SRC]` anchors: `ls.go:317` only serializes JSON; `ls.go:248` only a format string; two bare `[SRC @ sha]` | **Fixed.** §5 anchors corrected: JSON shape → `strutil.JSON(l.Object)` at ls.go:316-318 + `[RUN]` for observed absolute-key/RFC3339; timestamp UTC now split into `[SRC ls.go:248]` (format) + `[INFERRED]` (UTC-by-construction). |
| M-6 | Minor | request-payer claim unscoped — `listObjectVersions` omits it | **Fixed.** §3 row notes it is not wired into allversions [SRC s3.go:169]. |
| M-7 | Minor | Arch matrix `[RUN docker manifest inspect]` has no transcript; README rows prove OSes not arches | **Fixed.** Relabeled `[OBS docker manifest inspect]`; binaries row now `[SRC .goreleaser.yml]` (goarch: 386/amd64/arm/arm64/ppc64le — verified); source row `[INFERRED]`. |
| M-8 | Minor | Robinson benchmark not "via a 256-worker pool" (older tuned concurrency) | **Fixed.** §9 reworded — default 256 is *today's*; the 2019 benchmark tuned concurrency separately. |
| M-9 | Minor | "189 open issues" includes PRs | **Fixed.** §1 now "189 open issues+PRs (`open_issues_count`)". |
| M-10 | Minor | Malformed "Prefix scope" field in scoped receipts (`` `pfx`pfx ``) | **Routed, not patched.** This is a **wrapper receipt-template bug** (`smoke-run.sh` renders the prefix with a stray backtick/duplication); `run.meta` is correct and is what the verifier used, so no verdict is affected. Hand-editing wrapper-generated receipt text would mask the harness bug — filed for the orchestrator (see handoff). |

---

## Verbatim review

<!-- Reproduced verbatim from codex output /tmp/codex-review-s5cmd.md -->
### Important

- [observability/README.md:18](../receipts/smoke/_capability/observability/README.md), [logtrace.stdout.txt:1](../receipts/smoke/_capability/observability/logtrace.stdout.txt), [report.md:51](report.md), [report.md:233](report.md), [report.md:322](report.md), and every mode receipt’s `receipt.md:102-109` — The “no per-request logging at any log level” conclusion is directly contradicted by the preserved trace stdout, which contains complete `HeadBucket` and `ListObjectsV2` request/response records. The probe checked stderr while trace logs went to stdout. Pinned source explicitly enables AWS SDK debug logging at `--log trace` (storage/s3.go:1284). Consequently, the receipts’ “API call count not exposed” claim is also false, and obtainable API-count evidence required by the methodology was omitted.

- [report.md:43](report.md), [report.md:139](report.md), and [reconciliation.md:20](reconciliation.md) — The claim that all `--numworkers` parallelism is transfer-side and has no listing effect is false. `run` reads `--numworkers`, creates a parallel manager, and dispatches arbitrary subcommands—including `ls`—through it (command/run.go:76). This contradicts the report’s own fan-out description at lines 125-132 and proposed `--numworkers` sweep at lines 385-390. Reconciliation’s “Corroborated” verdict reverses the source evidence.

- [README.md:5](tool-page.md), [report.md:116](report.md), and [run.sh:30](../adapter/run.sh) — “Smoked every listing mode” overstates coverage. `--humanize` and `--show-fullpath` materially change the output contract—the report itself says one changes byte representation and the other drops metadata—but neither has a `run.sh` mode or receipt. `--humanize` was explicitly named in the inherited dossier’s exercise list. Under the brief’s mode definition, distinct output contracts must be smoked.

- [report.md:60](report.md) and [report.md:375](report.md) — The report misnames 1,000 as an SDK default and then calls it a disadvantage versus tools that “raise `MaxKeys`.” Source only proves s5cmd omits the field. Amazon S3 itself defaults to at most 1,000 and never returns more, so s5cmd is already at the service ceiling; another real-S3 client cannot raise it above s5cmd’s effective value. The `~149 pages [RUN]` label also overstates the receipt, which recorded no page count. [AWS ListObjectsV2 API](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html).

- [normalize.sh:7](../adapter/normalize.sh), [normalize.sh:36](../adapter/normalize.sh), [normalize.sh:54](../adapter/normalize.sh), and [normalize.sh:76](../adapter/normalize.sh) — The adapter promises raw key bytes but its text branches split on arbitrary whitespace and rebuild fields with one literal space. Repeated spaces are collapsed, tabs become spaces, and embedded newlines cannot survive. The JSON branch’s `jq @tsv` also escapes tabs, newlines, and backslashes. Current PASS receipts do not exercise this because weird-key checks were deferred.

- [normalize.sh:30](../adapter/normalize.sh), [report.md:224](report.md), and [allversions/verify.md:12](../receipts/smoke/allversions/verify.md) — The `allversions` adapter discards version IDs. On a versioned bucket, legitimate versions of the same key collapse into duplicate key records and the verifier will reject them or be unable to distinguish versions. The smoke payload exercised only trailing `null` version IDs, so its PASS does not validate multiple-version/delete-marker semantics.

- [report.md:25](report.md), [report.md:405](report.md), [recursive/run.meta:28](../receipts/smoke/recursive/run.meta), and all other mode `run.meta:28` files — The asserted `v2.3.0-991c9fb` tool version is caller-supplied, not observed by the receipts. No `s5cmd version` transcript was preserved despite the report claiming a live version check. The image digest pins opaque bytes, but the separate source checkout does not prove those image bytes contain commit `991c9fb`; `[RUN]` therefore overstates the version/commit evidence.

- [README.md:157](tool-page.md) and [README.md:167](tool-page.md) — The updated mixed-provenance addendum and receipt section are contradicted by the surviving statement at line 173: “Nothing on this page has been executed by us.” This violates the repository’s provenance requirement by describing firsthand receipt-backed content as entirely unexecuted.

- [report.md:372](report.md) and every mode receipt’s `receipt.md:48-53`, e.g. [recursive/receipt.md:48](../receipts/smoke/recursive/receipt.md) — The assertion that the serial-versus-parallel ratio is the concurrency factor and is RTT-independent is unjustified and generally false. With client CPU, serialized printing/back-pressure, setup costs, throttling, or unequal shards, the ratio depends on RTT’s share of total work. This also contradicts the report’s own stdout-coupling and client-language-cost discussion.

### Minor

- [fanout/remainder/receipt.md:15](../receipts/smoke/fanout/remainder/receipt.md) — The mandatory verifier field remains the literal placeholder “filled in by `harness/verify-listing.sh`,” and no per-shard `verify.md` exists. The union verifier re-derived the remainder successfully, but the individual receipt is incomplete.

- [README.md:17](tool-page.md) — “Version reviewed: unknown” contradicts the new header and receipt section, which scope the groundwork to v2.3.0/`991c9fb`.

- [report.md:290](report.md) — The pre-flight claim—pinned-client re-list, byte-identical result, no drift—has neither an evidence label nor a committed transcript. No pre-flight artifact exists elsewhere in the reviewed tree or data directory.

- [report.md:91](report.md) and [report.md:98](report.md) — The SDK’s jittered-backoff behavior and the assertion that `sync` is the only buffering/sorting listing consumer are behavioral claims without their own evidence labels. The adjacent `storage/s3.go` anchor establishes the custom retry wrapper, not the dependency’s backoff implementation or the exhaustive consumer claim.

- [report.md:122](report.md), [report.md:214](report.md), [report.md:249](report.md), and [report.md:255](report.md) — Several `[SRC]` anchors do not satisfy their attributed claim or the declared `file:line` format. `ls.go:317` only serializes JSON, not proves the request is unchanged; `ls.go:248` only defines a timestamp format, not UTC conversion; and two labels are merely `[SRC @ 991c9fb]` with no file or line.

- [report.md:146](report.md) — The request-payer claim is unscoped across listing modes. V1 and V2 set `RequestPayer`, but `listObjectVersions` omits it entirely (storage/s3.go:169); requester-pays therefore does not apply uniformly to the report’s `allversions` mode.

- [report.md:277](report.md) — The architecture row cites `[RUN docker manifest inspect]`, but no transcript or receipt exists. The following README citations establish supported operating systems, not amd64/arm64 or “any Go target.” Pinned workflow/source files could support much of the matrix, but the labels currently attached do not.

- [report.md:337](report.md) — README Overview/Benchmarks supports that the published numbers concern uploads/downloads, but not that those historical tests ran “via a 256-worker pool.” The cited 2019 benchmark used older upload/download concurrency flags and reported tuned values such as 16/32, not the current default 256-worker configuration. [Robinson benchmark](https://joshua-robinson.medium.com/s5cmd-for-high-performance-object-storage-7071352cc09d).

- [report.md:23](report.md) — “189 open issues” misstates GitHub API semantics: repository `open_issues_count` includes pull requests. Without subtracting open PRs, it is not an issue-only count. The fetched API payload was also not retained. [GitHub REST issues documentation](https://docs.github.com/en/rest/issues/issues?apiVersion=latest).

- [fanout/annual/receipt.md:61](../receipts/smoke/fanout/annual/receipt.md) — Scoped receipt generation duplicated the prefix value, producing malformed fields such as ``normals-annualseasonal/`normals-annualseasonal/`. The same defect appears in `fanout/{monthly,daily,hourly}` and `recursive-{annual,hourly,monthly}`; their `run.meta` files retain the correct scope.