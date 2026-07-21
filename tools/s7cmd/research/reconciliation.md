# Reconciliation — inherited dossiers vs independent groundwork

Walks **every inherited claim** touching `s7cmd` against my independent Stage
A-C work (report.md + committed receipts). Verdicts per the brief:

| Verdict | Meaning |
| --- | --- |
| **Corroborated** | Independent work found the same (evidence cited) |
| **Contradicted** | Found otherwise (both sides + evidence) |
| **Unaddressed** | My work didn't touch it — stays an open hypothesis |
| **Settled by smoke run** | A committed smoke receipt genuinely decides it |

**Scope & routing.** Three inherited sources name my subject: (A) my dossier
`tools/s7cmd/README.md` — I edit it conservatively (see §A verdicts). (B) the
neighbor `tools/s3ls-rs/README.md` — because `s7cmd`'s `ls` **is** the `s3ls-rs`
crate (v1.0.3), its engine claims are inherited claims about my subject, so I
reconcile them here; **but edits to that page are routed to the orchestrator, not
applied by me** (per the brief's routing rule — it is another tool's page). (C)
`docs/open-questions.md` — likewise routed. Evidence labels and anchors are
defined in `report.md`; `s7cmd` anchors are `@ d589df7`, `s3ls-rs` anchors are
`@ bf42067` (v1.0.3).

**Version note (load-bearing for §B).** The `s3ls-rs` dossier was reviewed at
**v1.0.1**; `s7cmd 1.5.0` depends on **`s3ls-rs =1.0.3`**, which is what I pinned
and read. Anchors below were re-checked against v1.0.3; most land within a few
lines, a few drifted (flagged as editorial corrections to route).

---

## A. `tools/s7cmd/README.md` (my dossier — thin; edited conservatively)

The page is almost entirely open questions. My work resolves the identity/
mechanism questions and fills the empty metadata.

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| A1 | Repo `github.com/nidor1998/s7cmd` (not independently confirmed) | **Corroborated** | Cloned, canonical (crates.io `s7cmd`, author nidor1998) [DOC][RUN receipts/_build] |
| A2 | Language unconfirmed (presumed Rust) | **Corroborated** | Rust, edition 2024, `rust-version 1.91.1` [SRC s7cmd Cargo.toml @ d589df7] |
| A3 | License unconfirmed | **Corroborated** | Apache-2.0 [DOC LICENSE] |
| A4 | Version reviewed unknown | **Corrected** | Pinned **v1.5.0** (`d589df7`), latest release |
| A5 | Testability unknown; no build/invocation known | **Settled by smoke run** | Builds from upstream `Dockerfile` at pinned SHA; runs; `ls` surface = `s3ls` flags; **12/12 smoke PASS** [RUN receipts/smoke/*] |
| A6 | "possibly redundant with `s3ls-rs` rather than a distinct target" | **Settled by smoke run (partly)** | `s7cmd`'s `ls` **depends on** `s3ls-rs =1.0.3` (not a reimplementation); behaviorally the same engine. Whether to benchmark *both* is a scope decision → **routed** [SRC s7cmd Cargo.toml; src/ls_bin/mod.rs @ d589df7] |
| A7 | Mechanism "almost nothing known"; "bundles s3ls-rs"; "could be separate reimplementation" | **Settled by smoke run + source** | Cargo dep `s3ls-rs =1.0.3`; `ls_bin::run` builds `s3ls_rs::ListingPipeline`. It is a **crate dependency, not a reimplementation** [SRC s7cmd src/ls_bin/mod.rs:15-88, dispatch.rs:61-72 @ d589df7]. Full mechanism in report §2 |
| A8 | Author now points users to `s7cmd` over `s3ls-rs` | **Corroborated** | `s3ls-rs` README: "please file new issues in the s7cmd repository" [DOC s3ls-rs README] |
| A9 | Umbrella/multi-tool CLI (subcommands), listing one capability among several | **Corroborated** | `ls`/`cp`/`mv`/`rm`/`sync`/`clean` + bucket admin, composing 4 sibling crates [DOC s7cmd README][SRC Cargo.toml] |
| A10 | Modes/tunables unknown (empty table) | **Settled by smoke run** | Full modes+tunables tables in report §3; every mode smoked [RUN] |
| A11 | Open-Q1: successor vs separate project | **Corroborated** | Umbrella successor; `s3ls-rs` still released standalone (v1.0.3 tag) [DOC] |
| A12 | Open-Q2: is standalone `s3ls-rs` still viable/maintained | **Corroborated** | v1.0.3 tagged, actively released; not frozen [3P git] — *page belongs to s3ls-rs; routed* |
| A13 | Open-Q3: does bundled listing differ from standalone `s3ls-rs` | **Contradicted (partly)** | `s7cmd` depends on the crate directly (`=1.0.3`) [SRC s7cmd Cargo.toml @ d589df7], so the listing engine, defaults, and output formats are identical — the `s3ls-rs` dossier's engine claims **do** transfer. The CLI flag surface is **not** fully identical: `s7cmd` hides `--auto-complete-shell` per subcommand [SRC s7cmd src/cli.rs:400-449 @ d589df7] |
| A14 | Open-Q4: benchmark s3ls-rs standalone, s7cmd, or both | **Unaddressed** | Genuine scope decision (same engine) → **routed to orchestrator** |

## B. `tools/s3ls-rs/README.md` (engine claims about my subject — RECONCILED, edits routed)

Reviewed at v1.0.1; I read v1.0.3. Engine mechanism claims:

| # | Inherited claim (s3ls-rs dossier) | Verdict | Evidence (@ bf42067 = v1.0.3) |
| --- | --- | --- | --- |
| B1 | Mode split on target-bucket presence (list-buckets vs list-objects) | **Corroborated** | [SRC ls_bin/mod.rs:34-49 (s7cmd wrapper); bucket_lister vs ListingPipeline] |
| B2 | Async producer/consumer pipeline, bounded mpsc, queue default 200000 | **Corroborated** | Bounded channels: [SRC pipeline.rs:57-59]; default 200000: [SRC config/args/mod.rs:25,511] |
| B3 | Await terminal-stage-first (writer→aggregator→lister); writer error flips cancel token | **Corroborated** | [SRC pipeline.rs:68-107] |
| B4 | `list_dispatch` parallel iff (max_parallel>1) & (delimiter None/recursive) & (not-express or opt-in) | **Corroborated** | [SRC mod.rs:409-413] |
| B5 | Sequential path: cancel→rate-limit→bump counter→fetch→send→loop on truncated | **Corroborated** | [SRC mod.rs:443-555] |
| B6 | Parallel path: JoinSet, delimiter `/`, drop permit before spawning children, depth>max→sequential | **Corroborated** | [SRC mod.rs:558-746, drop at :677, fallback at :581] |
| B7 | Two depth concepts: fan-out `max_parallel_listing_max_depth` vs content `--max-depth` (synthesizes CommonPrefix at boundary) | **Corroborated** + **Settled by smoke run** | [SRC mod.rs:333-350,682-699][RUN max-depth/root: 1 API call, PRE at boundary] |
| B8 | Express One Zone via `--x-s3` suffix; parallel gated behind opt-in flag | **Corroborated (source)** | [SRC mod.rs:26,302-303,409-413]; not runtime-tested (no Express bucket) |
| B9 | ObjectLister drops `list_rx` before joining storage task (deadlock avoidance); named regression test | **Corroborated** | [SRC lister.rs:70-74; test `lister_does_not_deadlock_when_cancelled_with_full_queue` :161] |
| B10 | Aggregator run_streaming (`--no-sort`) vs run_aggregate (buffer-all-then-sort); rayon `par_sort_by` past threshold 1,000,000 | **Corroborated** + **Settled by smoke run** | [SRC aggregate.rs:33-83 (streaming/buffering), :144-168 (sort/rayon threshold)][RUN recursive-tsv/normals-hourly stderr: `sort_entries started entry_count=2549 parallel_sort_threshold=1000000`] |
| B11 | DisplayWriter: formatter selection once; `BufWriter<Stdout>` sink | **Corroborated** | [SRC pipeline.rs:170-186] |
| B12 | Control-char escaping `\x00-\x1f`,`\x7f`→hex; `Cow::Borrowed` fast path; `--raw-output` opt-out; JSON exempt | **Corroborated** | [SRC display/mod.rs:78-108; json.rs uses serde] |
| B13 | Bucket-listing mode: single async, `list_buckets`, `max_buckets(1000)`, `--bucket-name-prefix`, `use_max_buckets` for custom endpoints | **Partly: Settled by smoke run (capability) + Unaddressed (internals)** | Anonymous ListBuckets **blocked** (307, exit 1) [RUN _capability/bucket-list]; the `max_buckets`/`use_max_buckets` internals not source-read |
| B14 | Stuck-continuation-token bail (twice-in-a-row) + no-token-on-truncated bail | **Corroborated (source)** | [SRC mod.rs:506-522,637-666]; not runtime-triggered (real S3 well-behaved) |
| B15 | Retries: no self-retry; SDK `RetryConfig::standard()` (exp backoff + jitter); defaults 10 attempts / 100 ms | **Corroborated** | [SRC client_builder.rs:154-160][DOC help] |
| B16 | Tunable defaults (64, 2, 200000, 1M, 1000, 10, 100ms, rate-limit floor/refill N/10) | **Corroborated** | Declared: [SRC config/args/mod.rs:23-29 (consts), :501-560 (fields)]; consumed (semaphore sizing / limiter refill construction): [SRC mod.rs:789,791-806]; [DOC help] — all still current at v1.0.3 |
| B17 | Weakness H1: default mode buffers all (RSS ~linear); `--no-sort` flat | **Corroborated (mechanism); Unaddressed (scale)** | [SRC aggregate.rs:33-83]; smoke 120.8 MB @ 149k consistent; 9x gap needs ≥1M keys [RUN] |
| B18 | H2: billion-object whole-bucket is worst regime | **Unaddressed** | Scale-only; not settleable at smoke |
| B19 | H3: no crash-resume | **Corroborated** | One-shot CLI, no persisted state [SRC ls_bin/mod.rs:52-88][DOC Non-Goals] |
| B20 | H4: fan-out depth 2 → flat/shallow layout gets little speedup | **Corroborated (mechanism); Unaddressed (runtime)** | Collapse-to-sequential path: [SRC mod.rs:590-674]; default depth 2: [SRC config/args/mod.rs:24,506-507][DOC]; noaa is hierarchical — flat-bucket test deferred to benchmark |
| B21 | H5: stuck-token bail yields incomplete listing on bad endpoints | **Corroborated (mechanism); Unaddressed (runtime)** | [SRC mod.rs:515,637]; real S3 only |
| B22 | H6: `--rate-limit-api` floor 10 req/s, refill N/10 | **Corroborated** | Floor of 10 (CLI validator): [SRC config/args/mod.rs:517-519]; refill N/10 construction: [SRC mod.rs:791-806] |
| B23 | H7: Express parallelism opt-in = fairness trap | **Corroborated (mechanism)** | [SRC mod.rs:409-413] |
| B24 | H8: sort-threshold step at 1M (stdlib vs rayon) | **Corroborated (mechanism); Unaddressed (latency curve)** | Threshold default 1,000,000: [SRC config/args/mod.rs:26,523]; stdlib/rayon switch: [SRC aggregate.rs:163-168][DOC]; runtime step not measured |
| B25 | Version reviewed **v1.0.1** | **Corrected** | Current v1.0.3; `s7cmd` depends on `=1.0.3` → **routed** |
| B26 | Code anchors (v1.0.1 line numbers) | **Mostly Corroborated; some drifted** | Re-checked @ v1.0.3: most land ±a few lines; e.g. semaphore-size at :789 (dossier `:825`), max-depth boundary at :682 (dossier `:679`). **Editorial corrections routed** |
| B27 | Library inventory (tokio, aws-sdk-s3, rayon, leaky-bucket, fancy-regex, zeroize, …) | **Corroborated (deps); zeroize Unaddressed** | [SRC s3ls-rs Cargo.toml]; `zeroize`-on-drop of access keys not source-verified |

## C. `docs/open-questions.md` (claims naming my subject — RECONCILED, edits routed)

| # | Claim | Verdict | Evidence |
| --- | --- | --- | --- |
| C1 | §4: `s3ls-rs` guards Express One Zone (rejects parallel on `*--x-s3` unless opted in) | **Corroborated (source)** | [SRC mod.rs:302-303,409-413]; applies equally to `s7cmd` (same engine) |
| C2 | §6: stuck-token/no-token bail attributed to `s3ls-rs` specifically | **Corroborated (source)** | [SRC mod.rs:506-522,637-666] |
| C3 | §2: language table lists `s3ls-rs` (Rust); `s7cmd` absent | **Corroborated + gap** | `s7cmd` is the **same Rust engine**; table omits it → routing note |
| C4 | §0/§1: `IsTruncated` is the only valid stop signal; page size 1000 | **Corroborated (source)** | The engine stops strictly on `is_truncated` + token [SRC mod.rs:502-551]; `max_keys` default 1000 |

---

## Routing list (for the orchestrator — I did NOT edit these pages)

1. **`tools/s3ls-rs/README.md` — version + anchors.** Update "Version reviewed
   v1.0.1" to note the current release **v1.0.3** (what `s7cmd 1.5.0` ships), and
   correct the drifted anchors (semaphore-size `:825`→`:789`; max-depth boundary
   `:679`→`:682`) against `s3ls-rs @ bf42067`. All B-row verdicts above are the
   receipt/source backing.
2. **`tools/s3ls-rs/README.md` — engine claims are now largely source-corroborated
   at v1.0.3** (rows B1-B16, B19, B22). The orchestrator may promote status where a
   receipt settles it (B7, B10 via smoke; B13 capability). `s7cmd`'s receipts live
   under `tools/s7cmd/receipts/`, so any promotion should cite across tools.
3. **`docs/open-questions.md §2`** language table: consider adding `s7cmd`
   (Rust) beside `s3ls-rs`, or a note that they share one engine, so the
   language-bottleneck experiment isn't double-counted.
4. **Benchmark scope (A14/Open-Q4):** decide whether to benchmark `s3ls-rs`
   standalone, `s7cmd`, or both. Finding: they are the **same engine** (`s7cmd`
   depends on `s3ls-rs =1.0.3`); benchmarking both measures one implementation
   twice unless the wrapper overhead is itself of interest.
5. **`s3ls-rs` is a going concern** (A12/Open-Q2) — actively released (v1.0.3),
   not frozen — belongs on the s3ls-rs page.

No claims about *other* tools surfaced from my work beyond the above.
