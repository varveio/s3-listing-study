# AWS CLI (`aws s3` / `aws s3api`)

> **Historical landing page (2026-07-20, capsule migration).** This is the full
> pre-restructure landing page. Any `current-state` wording below is historical
> as of the date it records and is superseded by the root README and `data/`.
> Only this banner and link targets changed; body prose and evidence
> qualifications are preserved.

|  |  |
|---|---|
| **Repo** | <https://github.com/aws/aws-cli> |
| **Language** | Python |
| **License** | Apache-2.0 |
| **Version reviewed** | **2.36.1** (SHA `12d962d2…`, smoke 2026-07-17) |
| **Tier** | 1 — included in the planned comparative runs; our familiar reference point |
| **Testability** | Trivial. Already installed on most boxes; otherwise `pip install awscli` or the official installer. |

aws-cli is AWS's official command-line client and a familiar reference point
for the runs in this project. It offers two separate S3
listing surfaces — the high-level `aws s3 ls` and the low-level `aws s3api
list-objects-v2` (plus the legacy V1 `list-objects` and
`list-object-versions`) — both built on the same serial, single-threaded
`ListObjectsV2` continuation-token paginator. There is no concurrency knob
anywhere in listing; the only parallelism aws-cli has at all is on the
transfer side (`s3 cp`/`sync`), never in LIST.

## What we saw

**The key observation: listing memory behavior splits by output
*format*, not by which command you used.** `s3 ls`, `s3api --output text`,
and `s3api --output yaml-stream` all **stream** page-by-page and hold no key
set in memory. `s3api --output json` (the default), `--output yaml`, and
`--output table` all **buffer the entire result set** in memory via
`build_full_result()` before printing anything. When we describe aws-cli's
memory use, we need to name both the surface and the output format. Full
mechanism, with source anchors:
[`mechanism.md` § Memory behavior by output format](../docs/mechanism.md#memory-behavior-by-output-format).

Other things we saw in the 2026-07-17 groundwork pass (version 2.36.1,
smoke bucket `noaa-normals-pds`, 148,917 keys, all anonymous, every mode
**PASS**):

- **Serial pagination confirmed, scope-limited.** A `--debug` probe of one
  `s3api` invocation shows single-`MainThread`, continuation-token-chained
  requests — no concurrency observed. `s3 ls`'s serial-ness rests on source
  reading, not a separate probe. See
  [`mechanism.md` § Concurrency](../docs/mechanism.md#concurrency--serial-listing-supported-by-source-and-one-probe).
- **The only "parallel" path is manual prefix fan-out**, entirely
  caller-owned: 4 shards + a remainder shard reconstructed the full
  148,917-key bucket with 0 cross-shard duplicates and 0 missing/extra.
  [`receipts/smoke/fanout/union/union-verify.md`](../receipts/smoke/fanout/union/union-verify.md);
  procedure in [`running.md` § Fan-out union procedure](../docs/running.md#fan-out-union-procedure).
- **Resume primitive round-trips clean** (`--max-items` → `NextToken` →
  `--starting-token`, 0 gap, 0 duplicate) — but this is deliberate chunking,
  **not** a kill-and-resume crash test; no process was killed.
  [`receipts/smoke/_capability/resume-README.md`](../receipts/smoke/_capability/resume-README.md).
- **`--output` has more members than inherited**: also `yaml-stream` (the
  only *streaming* s3api format) and `off`. "No Parquet" still holds.

**Scale-dependent observations remain unverified.** Throughput numbers (the
15M-objects/1110s figure, the ~8h billion-object extrapolation) and the
reported ~12M-object mid-run failure are **not settled by smoke** — smoke
scale (148,917 keys) cannot speak to them. See the ledger below.

## Errata

> **Merge commit `85e561e` misstates the format-split finding above —
> reversed.** Its commit message lists `text` among the *buffering* formats
> and calls the pilot's text-streams claim wrong. Both are backwards. The
> correct, source-anchored record is the one in the What we saw section and in
> [`mechanism.md`](../docs/mechanism.md#memory-behavior-by-output-format): `s3 ls`,
> `s3api --output text`, and `s3api --output yaml-stream` **stream**;
> `s3api --output json`/`yaml`/`table` **buffer** via `build_full_result()`.
> The original pilot research had this right; a draft of
> `research/report.md` briefly had it backwards; a separate Codex review caught
> the draft (see
> [`research/codex-review.md`](codex-review.md) finding 1), and the
> fix landed in the report and this page — but the merge commit's message
> was already written against the pre-fix draft and is now immutable
> history. For this detail, use this page and `research/report.md` rather than
> the merge message.

## Notes, questions, and observations

Every inherited observation from the original tool page is shown here alongside
the source-and-run groundwork (`research/report.md`) and smoke records. IDs
match [`research/reconciliation.md`](reconciliation.md) exactly, so
any row here can be traced back to its full reconciliation entry. Status
values: **CONFIRMED** (a committed receipt or source-and-probe combination
settles it, scope noted where partial) · **CORRECTED** (found different;
both versions shown) · **VERIFIED: no** (scale-dependent or otherwise not
exercised — still an open hypothesis) · **UNVERIFIABLE** (not a testable
observation, or a third-party report we cannot test with the resources on hand).

### Metadata

| # | Claim | Status | Receipt / reason |
| --- | --- | --- | --- |
| MD1 | Repo `github.com/aws/aws-cli` | CONFIRMED | Canonical AWS-owned repo `[3P github]` |
| MD2 | Language: Python | CONFIRMED | `[SRC pyproject.toml]` requires-python >=3.9 |
| MD3 | License: Apache-2.0 | CONFIRMED | `[SRC LICENSE.txt]` |
| MD4 | Version reviewed: *unknown* | CORRECTED | Pinned **2.36.1** @ SHA `12d962d2` `[SRC git tag][RUN receipts/smoke/_build/version-help.md]` |
| MD5 | Tier 1 — primary baseline | UNVERIFIABLE | Study-design tier assignment, not a testable claim about the tool |
| MD6 | Testability: trivial | CONFIRMED | Ran unmodified in the official upstream image `[RUN]` |

### Mechanism

| # | Claim | Status | Receipt / reason |
| --- | --- | --- | --- |
| M1 | Both `s3 ls` and `s3api list-objects-v2` run a paginated `ListObjectsV2` continuation-token chain | CONFIRMED | `[SRC subcommands.py:852,917; clidriver.py:1105-1107]` + `[OBS --debug]` (one s3api invocation). Every-mode PASS proves completeness, not concurrency |
| M2 | One thread, one call outstanding at a time | CONFIRMED — scope-limited | `[OBS --debug]` single `MainThread` on **one** s3api invocation `receipts/smoke/_capability/README.md`; `s3 ls` rests on `[SRC subcommands.py:852,865]`, not separately probed |
| M3 | 1,000 keys per page | CONFIRMED | Server `MaxKeys` cap `[DOC ListObjectsV2]`; `[OBS]` 3 requests for 2,549 keys = ceil(2549/1000) |
| M4 | No parallelism anywhere in either command | CONFIRMED — source-primary | No threads around the paginator `[SRC subcommands.py:852-889]`; only transfers parallelize `[SRC factory.py]`. "Regardless of flags" rests on source, not an exhaustive per-flag smoke |
| M5 | 1B objects ≈ 1M sequential round trips | VERIFIED: no | `[INFERRED]` arithmetic from M1–M3; not run at scale |
| M6 | Resume is manual: `--starting-token` accepted, nothing persists it, lost if the process dies | CONFIRMED (partial) | Token emitted only to stdout `[SRC paginate.py:155-165]`; the round-trip primitive is confirmed `[RUN _capability/resume-README.md]` (chunked continuation). The "lost if it dies" half rests on source only — no crash was performed |
| M7 | AWS docs redirect large-scale listing to S3 Inventory | CONFIRMED | Found: AWS re:Post KB `[3P repost.aws/knowledge-center/s3-troubleshoot-unresponsive-list]` — the CLI user guide `[DOC cli-services-s3-commands]` covers only the narrower "pagination knobs are the in-CLI lever" point |

### Modes and tunables

| # | Claim | Status | Receipt / reason |
| --- | --- | --- | --- |
| T1 | `s3 ls s3://b/ --recursive` — high-level listing | CONFIRMED | `[RUN receipts/smoke/s3-ls-recursive]` full-bucket 148,917 PASS |
| T2 | `s3api list-objects-v2` — low-level pagination | CONFIRMED | `[RUN receipts/smoke/s3api-v2-text]` full 148,917 PASS + scoped prefixes |
| T3 | `--page-size` (server caps 1000); confirm 1000 is the ceiling | CONFIRMED (doc) / VERIFIED: no (sweep) | Maps to `PaginationConfig.PageSize`→`MaxKeys` `[SRC paginate.py:182-193]`; ceiling is `[DOC]`, no >1000 test run — flagged for benchmark sweep |
| T4 | `--max-items`/`--starting-token` — resume, round-tripped (kill a run, resume, confirm no gap/duplicate) | CONFIRMED (chunked continuation, not the crash test) | `[RUN _capability/resume-README.md]`: 1000 + 1549 = 2549 distinct, 0 dup, no gap. No process was killed — this is deliberate chunking, not the tool page's kill-and-resume scenario |
| T5 | `--output json\|text\|table\|yaml` | CORRECTED | Also `yaml-stream` and `off` exist `[SRC cli.json:23-33][RUN receipts/smoke/_build/help-*]` |
| T6 | `--no-sign-request` — anonymous access | CONFIRMED | Global flag → botocore `UNSIGNED` `[SRC globalargs.py:99-104]`; every smoke mode ran unsigned, `[OBS]` `auth_type:'none'`, zero Authorization headers |
| T7 | `--cli-read-timeout`/`--cli-connect-timeout`/retry config | CONFIRMED (flags/defaults) / VERIFIED: no (behavior) | Defaults 60s `[SRC globalargs.py:117; cli.json:61]`, retries `max_attempts=3` `[SRC configprovider.py:153]`; no fault injection performed at smoke |
| T8 | No concurrency mode to sweep — the absence is the finding | CONFIRMED | No internal listing concurrency `[SRC]`; only path is manual prefix fan-out `[RUN fanout/union PASS]`, caller-owned |

### Published numbers and estimates

| # | Claim | Status | Receipt / reason |
| --- | --- | --- | --- |
| N1 | 15M objects → 1110s → ~13.5K objects/s (Swath survey table; PS3-author baseline, secondhand) | VERIFIED: no | Smoke makes no comparative timing claim. Benchmark-phase target; the figure is from the PS3 author's own comparison table (aws-cli 1110 s vs. s5cmd 733 s vs. PS3 160 s), so we treat it as the author's result for that setup until we run ours |
| N2 | ~12M objects → 1–2h, then failed mid-run ([aws/aws-cli#1118](https://github.com/aws/aws-cli/issues/1118)) | VERIFIED: no | Not reproduced — scale + fault-injection scope. This is a third-party report; we need to reproduce the exact invocation before presenting it as something we saw |
| N3 | 1B ÷ 1000 ≈ 1M calls, ~30ms RTT → ~8h best case | VERIFIED: no | `[INFERRED]` arithmetic derivation from M1–M3, correct as arithmetic; not run at scale |

### Where the approach may fit

| # | Claim | Status | Receipt / reason |
| --- | --- | --- | --- |
| S1 | Universal — official, always available, no install friction | CONFIRMED | Official image + installers, both arches `[RUN]` — see `running.md` § Container/install |
| S2 | Simple, well-documented, predictable output formats | CONFIRMED — with caveat | True at the surface; the memory model (S3, below) is *not* evident from surface docs |
| S3 | "No surprises: what you see in the docs is what it does. Nothing hidden." | CORRECTED | Two non-obvious behaviors found: (a) `s3api --output json` buffers the entire result while `--output yaml-stream` streams — a large, undocumented-at-a-glance memory difference `[SRC formatter.py:76,154]`; (b) `s3 ls s3://b/` without `--recursive` lists only the first delimiter level `[SRC subcommands.py:853][RUN s3-ls-delimiter]`. Neither is malicious, but "nothing hidden" overstates it |

### Tradeoffs and questions to test

| # | Claim | Status | Receipt / reason |
| --- | --- | --- | --- |
| W1 | No parallelism anywhere; single serial token chain regardless of flags | CONFIRMED | `[SRC]` + `[OBS --debug]` (single thread, one probed invocation) + `[RUN fanout]` (parallelism only via external fan-out) |
| W2 | ~12M objects took 1–2h then failed mid-run (#1118) | VERIFIED: no | Not reproduced — scale/fault scope. This is a third-party report; we need an exact-invocation reproduction before presenting it as something we saw |
| W3 | Resume is entirely manual and easy to lose | CONFIRMED | No persistence layer `[SRC paginate.py:155]`; round-trip primitive confirmed `[RUN _capability/resume-README.md]` (chunked continuation, not a crash-kill test) |
| W4 | No Parquet output; json/yaml/text/table only | CORRECTED | "No Parquet" holds; the format list is not exhaustive — also `yaml-stream`, `off` `[SRC cli.json:23-33][RUN help]` |
| W5 | AWS's own docs allegedly redirect large-scale listing to S3 Inventory — find the citation | CONFIRMED | AWS re:Post KB article `[3P repost.aws/knowledge-center/s3-troubleshoot-unresponsive-list]` |

**Benchmark-phase checks for W1/M4** (carried from the original
tool page so they aren't reinvented when the benchmark harness is designed):
(a) plot wall-clock vs. object count across bucket sizes — a strictly serial
lister must be linear; (b) capture a network trace and confirm zero concurrent
LIST requests ever in flight. Both generalize to every tool on the roster that
claims serial listing.

**Cross-cutting claims naming aws-cli** (language bottleneck, crash-resume in
`docs/open-questions.md`) are routed there, not re-litigated here — see
[`research/reconciliation.md`](reconciliation.md) § Cross-cutting
claims for what was found and how it's routed.

## Provenance

**Mixed provenance.** Two kinds of content on this page did *not* come from
the inherited secondhand notes:

- **Metadata cells.** The **Language** and **License** cells were not in the
  inherited notes; added and confirmed against the pinned source
  (MD2/MD3 above).
- **The mechanism, output-format, anonymous-access, and resume claims** were
  re-derived from the pinned source
  (`12d962d239b9fd0669951c4d27dc366388abba2d`, tag `2.36.1`) and checked
  against committed smoke receipts on version 2.36.1, 2026-07-17. That work
  is [`research/report.md`](report.md); its critical cross-check is
  [`research/codex-review.md`](codex-review.md); its row-by-row
  check against this page's inherited predecessor is
  [`research/reconciliation.md`](reconciliation.md).
- **The modes table (T1–T8).** The inherited tool page flagged its own modes
  table as partly "added from general knowledge … not checked against the
  tool's own documentation" and told readers to verify it before any sweep.
  That caveat is now discharged, not dropped: every T-row was checked against
  the pinned source and smoke receipts during groundwork (statuses in the
  ledger above).

Everything not covered by the ledger above or by the two bullets is
inherited and secondhand: originally sourced from Swath's private prior-art
research — a compiled reference note on LIST mechanics and tool-by-tool
receipts, and a separate survey document's tool catalog and
throughput-comparison table. Both were built from AWS's own documentation
and one GitHub issue, not from running the tool, and Swath's own private
notes are now out of reach (this repo is the only surviving copy — see
`AGENTS.md` § Provenance discipline). The scale-dependent claims in the
ledger above (N1, N2, N3, M5, W2) remain inherited and unexecuted.

## Further reading

- [`mechanism.md`](../docs/mechanism.md) — architecture: the two S3 surfaces,
  request patterns, pagination/continuation, concurrency (with its scope
  caveat), memory behavior by output format, failure surface, source
  anchors.
- [`running.md`](../docs/running.md) — install/image, every smoked mode with its
  invocation, how to reproduce a receipt via `harness/smoke-run.sh`, the
  fan-out union procedure, and the capability probes.
- [`research/`](./) — the immutable evidence basement:
  [`report.md`](report.md) (source-and-run groundwork),
  [`reconciliation.md`](reconciliation.md) (row-by-row check
  against the inherited tool page), [`codex-review.md`](codex-review.md)
  (separate cross-model review). These preserved research files may use the
  project's older terminology; this page is the current summary.

## Receipts

Groundwork pass 2026-07-17, aws-cli 2.36.1, image
`amazon/aws-cli@sha256:406ca32d…`, bucket `noaa-normals-pds`, all anonymous,
all verifier **PASS**. Under [`receipts/smoke/`](../receipts/smoke/):

- **Recursive full-bucket (148,917):** `s3api-v2-text/`, `s3-ls-recursive/`
- **Scoped (designated prefixes):** `s3api-v2-text-hourly/`,
  `s3api-v2-text-monthly1991/`, `s3api-v2-text-annualaccess/`
- **Output formats / APIs:** `s3api-v2-json-hourly/`,
  `s3api-v2-yamlstream-hourly/`, `s3api-v1-text-hourly/` (V1),
  `s3api-versions-text-hourly/` (ListObjectVersions)
- **Delimiter (root):** `s3-ls-delimiter/`, `s3api-v2-delimiter/`
- **Fan-out (manual parallel):** `fanout/{shard-*,remainder,union/union-verify.md}`
- **Capability probes (non-mode):** `_capability/` (`--debug` request
  behavior; `--max-items`/`--starting-token` resume round-trip); `_build/`
  (version + help capture)
