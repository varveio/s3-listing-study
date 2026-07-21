# s5cmd — reconciliation with the inherited dossier

Stage D. This table walks **every inherited claim** in `tools/s5cmd/README.md`
(and the cross-cutting claims naming s5cmd) against my independent groundwork
(`research/report.md` + committed receipts). Verdicts: **Corroborated** (my
independent work found the same), **Contradicted** (I found otherwise, both
sides shown), **Unaddressed** (my research didn't touch it — stays an open
hypothesis), **Settled by smoke run** (a committed receipt genuinely decides it).

Independent pin: s5cmd **v2.3.0**, commit `991c9fb`, image digest
`sha256:2ff939e2ee3c76adcadd78dbfc3e2569b18a3743ed9dcfccb1ec589af7fb9903`.
Reminder (AGENTS.md): **source reading is not a receipt** — mechanism claims
corroborated only from source stay hypotheses; only a run settles a claim.

## Mechanism

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| 1 | s5cmd does **not parallelize listing**; `ls` is one serial `ListObjectsV2` continuation chain, no keyspace discovery, no prefix sharding | **Corroborated** (source) — *not* settled by smoke: serial-vs-parallel is not decidable at smoke scale, and s5cmd exposes no request log to show it | Single unbuffered channel + one `ListObjectsV2PagesWithContext` per URL [SRC storage/s3.go:309,317]; serial consume [SRC command/ls.go:197]; no request visibility at any log level [OBS _capability/observability]. Stays a hypothesis for the benchmark (scale). |
| 2 | All s5cmd parallelism is transfer-side (worker pool over objects **after** listing) | **Corroborated with a caveat** (source) | For a **single `ls`**, yes — the `--numworkers` pool (default 256) is consumed by cp/rm/mv, not the ls/du listing [SRC command/app.go:18, command/ls.go:197]. **But `s5cmd run` dispatches its command lines — `ls` included — through that same pool** [SRC command/run.go:76], so the batch fan-out's prefix listings run concurrently. "All parallelism is transfer-side" holds for a lone `ls`, not for `run`. |
| 3 | The "5–10× faster" reputation describes **transfers, not `ls`** | **Corroborated** (docs, transfer half) / **Unaddressed** (the `ls` magnitude) | README's speed quote is Robinson's upload/download benchmark [DOC README.md § Overview]. Whether `ls` is faster/slower than a Python client is a timing comparison this study forbids at smoke — benchmark phase. |
| 4 | Believed responsible file is `command/ls.go` | **Contradicted (anchor correction)** | `command/ls.go` only **consumes** the object channel [SRC command/ls.go:197]; the LIST request + pagination are issued in **`storage/s3.go`** (`List` at :299-341, `ListObjectsV2PagesWithContext` at :317). Editorial correction below. |

## Modes and tunables

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| 5 | `s5cmd ls 's3://bucket/*'` native listing runs and is the baseline | **Settled by smoke** | recursive full-bucket **PASS** 148917/148917 [RUN receipts/smoke/recursive]. |
| 6 | Output flags `-etag`, `-humanize`, `--json` are formatting-only, don't change the mechanism | **Corroborated** / json **Settled by smoke** | Flags exist as `-e/--etag`, `-H/--humanize`, global `--json` [SRC command/ls.go:71,75; --help]. `--json` is the same ListObjectsV2 request, output-only — smoked **PASS** 148917 [RUN receipts/smoke/json]. (Note: `-etag`/`-humanize` single-dash spellings in the dossier work because urfave/cli accepts both; canonical are `-e`/`-H`.) |
| 7 | **Mandatory:** hand-generated per-prefix `ls` piped to `s5cmd run -f <file>` (the fair fan-out) must be measured | **Settled by smoke** (completeness) + **Contradicted (flag)** | Smoked as `fanout` mode: 4 prefix shards + remainder, `--scope union` **PASS** 148917, 0 dups [RUN receipts/smoke/fanout/union]. In-process `s5cmd run <file>` also clean (148917 keys) [OBS _capability/run-fanout]. **Flag correction:** v2.3.0 `run` takes the file **positionally** (`run [file]`) or on stdin — there is **no `-f`** (`run -f` → `Incorrect Usage: flag provided but not defined: -f`, exit 1). *Competitiveness* of the fan-out (dossier #5) stays Unaddressed — a timing/scale question. |
| 8 | `-numworkers N` on `run` — sweep it | **Corroborated** (exists) / sweep **Unaddressed** | `--numworkers` default 256 [SRC command/app.go:18; --help]; governs `run`/transfer concurrency, **not** a single `ls`'s LIST. Sweep flagged for the benchmark [report § 10]. |
| 9 | `s5cmd cp`/`sync` transfer context, for separating "fast" from "lists fast" | **Unaddressed** | Out of listing scope and mutating — guardrail forbids running cp/sync here. Not smoked. |

## Claimed numbers & failure reports

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| 10 | 15M objects, 733 s, ~20.5K obj/s (swath survey table) | **Unaddressed** | Scale + timing; smoke bucket is 148,917 keys and produces no comparative numbers. Benchmark phase. |
| 11 | `ls` ≈ 1.5× `aws s3api` (not 5–10×) | **Unaddressed** | Timing comparison, forbidden at smoke. Benchmark phase, fair-timing harness. |
| 12 | 11 GB RAM syncing 400k files ([#441](https://github.com/peak/s5cmd/issues/441)) | **Unaddressed** | `sync` (not listing), memory-at-scale. Not reproduced. Streaming `ls` at smoke used ~40–53 MB [RUN recursive, allversions] — a listing-path datum, not a rebuttal of a `sync` report. |
| 13 | Killed at 15M objects ([#447](https://github.com/peak/s5cmd/issues/447)) | **Unaddressed** | Scale; not attempted (300 s / politeness guardrails, 148,917-key bucket). |
| 14 | Independent 2025 re-testing (HN thread vs BigGo aggregator) of s5cmd's speed claims | **Unaddressed** | Not read (concerns *transfer* speed claims, outside listing scope). Remains a pointer, as the dossier itself states. Flagged for the publishing/benchmark phase. |

## Strengths / weaknesses

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| 15 | Genuinely fast at transfers (worker-pool parallelism) | **Unaddressed** | Transfer-side; not measured (out of scope, mutating). Docs context only. |
| 16 | Glob/wildcard support | **Corroborated** / **Settled by smoke** | Client-side wildcard filter [SRC storage/url/url.go:259-285]; scoped globs smoked **PASS** [RUN recursive-hourly etc.]. |
| 17 | Low resource use (transfer side); 400k-file RAM report complicates listing-adjacent `sync` | **Unaddressed** | Transfer/`sync` at scale; not measured. |
| 18 | Weakness #5: the fan-out is competitive when done well | **Settled by smoke** (works & covers) / **Unaddressed** (competitive speed) | Union PASS proves correctness/coverage [RUN fanout/union]; speed vs a native parallel lister is a benchmark question [report § 10 q6]. |

## Cross-cutting claims naming s5cmd (routed, not edited here)

Per the brief, I do **not** edit other tools' or shared pages. For the
orchestrator to route:

- **`docs/open-questions.md` § 2 (client language is the bottleneck)** lists
  s5cmd among Go tools. My groundwork is relevant: s5cmd's listing is a serial
  single-stream paginator, so per-page client CPU is the language-sensitive cost
  — but the cross-internet runner (harness known-limit #1) may mask it. No edit
  made; noted for the language-tier analysis.
- **`docs/open-questions.md` (2025 re-testing pointer)** duplicates dossier
  claim #14 and stays Unaddressed for the same reason (transfer scope, unread).

## Dossier edits made (conservative — see `git diff tools/s5cmd/README.md`)

1. **Editorial correction** — `s5cmd run -f <file>` → `s5cmd run <file>`
   (positional / stdin; no `-f` flag in v2.3.0). Evidence `[OBS _capability/run-fanout]`.
2. **Editorial correction** — code anchor: the serial LIST is *issued* in
   `storage/s3.go` (`List`/`ListObjectsV2PagesWithContext`); `command/ls.go` is
   the serial *consumer*. Evidence `[SRC]`.
3. **Receipt-backed note** — the hand-rolled per-prefix fan-out lists the
   registered smoke bucket (148,917 keys) **completely, no duplicates**, for
   v2.3.0 against that snapshot (union receipt cited). Scoped to smoke; the
   fan-out's *speed* stays `VERIFIED: no`.
4. **Receipts section** — replaced "None yet" with the committed receipt index.
5. **Status / Provenance** — noted the page now carries firsthand runs (mixed
   lineage); behavioral *mechanism* claims (serial listing) remain source-level
   hypotheses, not receipt-settled.

No inherited claim was deleted; every one appears above with a verdict.
