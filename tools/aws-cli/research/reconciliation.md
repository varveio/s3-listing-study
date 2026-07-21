# aws-cli — reconciliation with the inherited dossier

Walks **every claim** in `tools/aws-cli/README.md` (the inherited dossier) and in
the aws-cli-naming entries of `docs/open-questions.md`, against my
independent groundwork (`research/report.md` + committed receipts). This table is
the complete inventory of inherited claims, so a later README consolidation can
prove nothing was silently dropped.

Verdicts: **Corroborated** (independent work agrees) · **Contradicted** (found
otherwise, both sides shown) · **Unaddressed** (my research didn't settle it —
stays an open hypothesis) · **Settled by smoke run** (a committed receipt
genuinely decides it, scoped to version 2.36.1 / the invocation / noaa-normals-pds
at its 148,917-key snapshot).

Evidence anchors use the pinned SHA `12d962d239b9fd0669951c4d27dc366388abba2d`
(tag 2.36.1).

## Metadata cells

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| MD1 | Repo `github.com/aws/aws-cli` | Corroborated | Canonical AWS-owned repo `[3P github]` |
| MD2 | Language Python | Corroborated | `[SRC pyproject.toml `requires-python>=3.9`]`; 1215 `.py` files |
| MD3 | License Apache-2.0 | Corroborated | `[SRC LICENSE.txt]` |
| MD4 | Version reviewed: **unknown** | Corrected (editorial) | Pinned **2.36.1** @ SHA `12d962d2` `[SRC git tag][RUN _build/version-help.md]` |
| MD5 | Tier 1 — primary baseline | Unaddressed | Study-design assignment, not a testable claim |
| MD6 | Testability trivial | Corroborated | Ran unmodified in the official image `[RUN]` |

## Mechanism — "what we believe it does"

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| M1 | Both `s3 ls` and `s3api list-objects-v2` run a paginated `ListObjectsV2` continuation-token chain | Corroborated | `s3 ls` calls `get_paginator('list_objects_v2')` `[SRC subcommands.py:852,917]`; s3api paginates via `paginator.paginate` `[SRC clidriver.py:1105-1107]`; the `[OBS --debug]` probe (one **s3api** invocation) shows `ListObjectsV2` with `ContinuationToken` chaining. Every-mode `[RUN]` PASS proves *completeness*, not request concurrency |
| M2 | One thread, one call outstanding at a time | Corroborated (source + one s3api probe) | `[OBS --debug]` single `MainThread`, timestamps strictly increasing, request N+1 carries response N's token (`_capability/README.md`) — this is **one s3api invocation**; the `s3 ls` surface is serial by `[SRC subcommands.py:852,865]` (synchronous paginator loop), not separately debug-probed |
| M3 | 1000 keys per page | Corroborated | Server `MaxKeys` cap `[DOC ListObjectsV2]`; `[OBS]` 3 requests for 2,549 keys = ceil(2549/1000) |
| M4 | No parallelism anywhere in either command | Corroborated ([SRC]-primary) | No threads around the paginator `[SRC subcommands.py:852-889]`; the only parallelism is in transfers `[SRC customizations/s3/factory.py]`; `[OBS]` single thread on the one s3api probe. "Regardless of flags" rests on source, not an exhaustive per-flag smoke |
| M5 | 1B objects ≈ 1M sequential round trips | Corroborated (arithmetic) / **Unaddressed at scale** | `[INFERRED]` from M1–M3; not run at 1B — a benchmark-phase question |
| M6 | Resume is manual: `--starting-token` accepted, nothing persists it; lost if process dies | Corroborated | Token emitted only to stdout, nothing persists it `[SRC paginate.py:155-165]`. The `[RUN _capability/resume]` probe shows the **resume primitive** round-trips (chunked continuation via `--max-items`→`NextToken`→`--starting-token`, no gap/dup). It does **not** demonstrate *crash* recovery: no process was killed, and a killed *unbounded* run never emits a token to save — so the "lost if it dies" half stands on source, not the probe |
| M7 | AWS docs redirect large-scale listing to S3 Inventory | Corroborated | The **Inventory redirect** is in AWS re:Post `[3P https://repost.aws/knowledge-center/s3-troubleshoot-unresponsive-list]` (use Inventory; remove delete markers). The `[DOC cli-services-s3-commands userguide]` supports only the *narrower* point (pagination knobs are the in-CLI lever); it is **not** the Inventory citation |

## Modes and tunables table

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| T1 | `s3 ls s3://b/ --recursive` — high-level listing | **Settled by smoke** | `[RUN s3-ls-recursive]` full-bucket 148,917 PASS |
| T2 | `s3api list-objects-v2` — low-level pagination | **Settled by smoke** | `[RUN s3api-v2-text]` full 148,917 PASS + scoped |
| T3 | `--page-size` (server caps 1000); "confirm 1000 is the ceiling" | Corroborated / **Unaddressed** (no >1000 test) | Maps to `PaginationConfig.PageSize`→`MaxKeys` `[SRC paginate.py:182-193]`; ceiling is `[DOC]`, not directly re-tested. Sweep flagged for benchmark |
| T4 | `--max-items` / `--starting-token` — resume, must be round-tripped | Settled by smoke (chunked continuation) | `[RUN _capability/resume-README.md]`: max-items 1000 → NextToken → starting-token → 2549 distinct, 0 dup, no gap. This is deliberate **chunked continuation**, not the dossier's "kill a run" crash-resume (no kill performed) |
| T5 | `--output json\|yaml\|text\|table` | **Corrected** | Also `yaml-stream` and `off` exist `[SRC cli.json:23-33][RUN _build/help-*]`. The dossier's own Provenance already flags this table as unverified-added |
| T6 | `--no-sign-request` — anonymous access | **Settled by smoke** | Global flag → botocore `UNSIGNED` `[SRC globalargs.py:99-104]`; every mode ran unsigned, `[OBS]` `auth_type:'none'`, zero Authorization headers |
| T7 | `--cli-read-timeout` / `--cli-connect-timeout` / retry config | Corroborated / **Unaddressed** (behavior) | Flags default 60s `[SRC globalargs.py:117; cli.json:61]`; retries botocore standard, `max_attempts=3` `[SRC configprovider.py:153]`. No fault injection at smoke — benchmark/replay-phase |
| T8 | "No concurrency mode to sweep — the absence is the finding" | **Settled by smoke** / Corroborated | No internal listing concurrency `[SRC]`; the only parallel path is manual prefix fan-out `[RUN fanout/union PASS]`, caller-owned |

## Claimed numbers

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| N1 | 15M objects → 1110 s → ~13.5K obj/s (swath survey table; PS3-author baseline, secondhand) | **Unaddressed** | Smoke is not a benchmark (no comparative timings). A benchmark-phase target; the least-neutral provenance is noted in the dossier and carried forward |
| N2 | ~12M objects → 1–2h then failed mid-run (aws/aws-cli#1118) | **Unaddressed** | Scale + failure reproduction is benchmark/fault-injection scope, not exercised at smoke |
| N3 | 1B ÷ 1000 ≈ 1M calls, ~30 ms RTT → ~8h best case | Corroborated (arithmetic) / **Unaddressed at scale** | `[INFERRED]` from M1–M3 |

## Claimed strengths

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| S1 | Universal / official / always available | Corroborated (context) | Official image + installers, both arches `[RUN §7]` |
| S2 | Simple, well-documented, predictable output | Corroborated **with caveat** | True at the surface, but the memory model is *not* evident from surface docs (see S3) |
| S3 | "No surprises — what you see in docs is what it does. Nothing hidden." | **Contradicted (mildly)** | Two non-obvious behaviors: (a) `s3api --output json` buffers the *entire* result via `build_full_result()` while `--output yaml-stream` streams — a large memory difference invisible in surface docs `[SRC formatter.py:76,154]`; (b) `s3 ls s3://b/` without `--recursive` lists only the first delimiter level `[SRC subcommands.py:853][RUN s3-ls-delimiter]`. Neither is "hidden" maliciously, but "nothing hidden" overstates |

## Claimed weaknesses — hypotheses to test

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| W1 | No parallelism anywhere; single serial token chain regardless of flags | **Settled by smoke** / Corroborated | `[SRC]` + `[OBS --debug single thread]` + `[RUN fanout]` (parallelism only via external fan-out) |
| W2 | ~12M objects took 1–2h then failed mid-run (#1118) | **Unaddressed** | Not reproduced — scale/fault scope. Negative third-party claim; would need exact-invocation reproduction before it ships as a finding (AGENTS.md § Evidence) |
| W3 | Resume manual and easy to lose | Corroborated | No persistence layer `[SRC paginate.py:155]`; the resume primitive round-trips `[RUN _capability/resume]` (chunked continuation, not a crash-kill test) |
| W4 | No Parquet; json/yaml/text/table only — verify no undocumented mode | **Corrected** (partly Corroborated, partly Contradicted) | "No Parquet" **Corroborated** (capability gap holds); "json/yaml/text/table only" **Contradicted** — also `yaml-stream`, `off` `[SRC cli.json:23-33][RUN help]` |
| W5 | AWS docs redirect large-scale listing to S3 Inventory — find the page | Corroborated | Inventory-redirect page: `[3P repost.aws/knowledge-center/s3-troubleshoot-unresponsive-list]` (AWS re:Post). The `[DOC cli-services-s3-commands userguide]` covers pagination knobs only — cited separately, not as the Inventory source |

## Cross-cutting claims naming aws-cli (for the orchestrator to route — not edited here)

- **`docs/open-questions.md` §2 (language bottleneck), Python tier lists
  aws-cli.** My groundwork adds mechanism support: aws-cli's `s3api` JSON/text
  path has real per-key CPU cost (botocore model deserialization +
  `build_full_result` buffering + `--query`/jq) that is scale- and
  format-dependent `[SRC formatter.py:76]`. This *supports* the hypothesis being
  worth measuring but does not settle it (scale-only). Route as supporting
  context; still `VERIFIED: no`.
- **`docs/open-questions.md` §3 (crash-resume): "aws-cli's
  `--starting-token` exists but the caller must persist it."** **Corroborated** —
  `[SRC paginate.py:155-165]` (token emitted, never persisted) and `[RUN
  _capability/resume]` (round-trip clean). The continuation token being *opaque*
  and the listing *not a consistent snapshot* is a `[DOC]` property I did not
  independently exercise (would need mid-list mutation). Route the corroboration;
  the "not a consistent snapshot" half stays `VERIFIED: no`.

## Modes the dossier named that I had to add to Stage C

- The dossier's modes table demanded a `--max-items`/`--starting-token` **resume
  round-trip** ("kill a run, resume from a saved token, confirm no gap and no
  duplicate"). My workspace-phase smoke recorded these as tunables but did not
  round-trip them. I went back and added the round-trip as a capability probe
  (`receipts/smoke/_capability/resume-README.md`) — honest mixed provenance,
  prompted by the dossier. No *listing mode* (distinct request pattern / output
  contract) named in the dossier was missing from my Stage C set; every one was
  already smoked.
