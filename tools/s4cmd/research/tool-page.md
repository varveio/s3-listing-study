# s4cmd

> **Historical landing page (2026-07-20, capsule migration).** This is the full
> pre-restructure landing page. Any `current-state` wording below is historical
> as of the date it records and is superseded by the root README and `data/`.
> Only this banner and link targets changed; body prose and evidence
> qualifications are preserved.

**Status: current-state page, consolidated 2026-07-17.** This page was rewritten
from the mixed-provenance groundwork tool page into a clean current-state summary
(not new research — every claim below was already source- or receipt-backed
before this rewrite, through a source-and-run report, a row-by-row
reconciliation, and a critical cross-check that resolved 13 findings). What we
saw first: **s4cmd cannot make unsigned requests, so every listing mode is *blocked,
not skipped* under this `CREDS=none` campaign** — the mechanism claims below rest
on pinned source reading and stay `VERIFIED: no`; the one committed listing-side
receipt settles the capability block itself. Detail lives in three companion
documents:

- [`mechanism.md`](../docs/mechanism.md) — source-anchored architecture (delimiter-
  recursion parallelism, memory model, retry set, the `S3APICALL` observability
  limit, no unsigned path, no resume).
- [`running.md`](../docs/running.md) — the study Dockerfile and its reproducibility-only
  boto3 pin, the local-registry image ref, the blocked smoke state with its
  capability receipts, the concurrency-cap guard, and the architecture matrix.
- [`research/`](.) — `report.md` (the source-and-run report), `reconciliation.md`
  (every inherited claim walked), `codex-review.md` (the 13 resolved findings +
  the consolidation review). These preserved files may use the project's older
  terminology; this page is the current summary.

|  |  |
|---|---|
| **Repo** | <https://github.com/bloomreach/s4cmd> |
| **Language** | Python (single file `s4cmd.py`, ~1,950 lines) |
| **License** | Apache-2.0 |
| **Version reviewed** | ~~unknown~~ → **2.1.0** (latest release tag, commit `80059bf`) [SRC git tag] |
| **Tier** | 2 — included when the setup permits |
| **Testability** | Trivial — `pip install s4cmd`. Installs, imports, and runs under a current boto3 (botocore 1.43.50, Py 3.12: `s4cmd --version` exit 0) [RUN `receipts/smoke/_build/modern-boto3-import/`] |

## What we tried and saw

- **s4cmd cannot make unsigned requests — every listing mode is blocked, not
  skipped.** There is no `--no-sign-request` equivalent and no
  `signature_version=UNSIGNED` anywhere [SRC `s4cmd.py:380-386 @ 80059bf`]
  [3P `github.com/bloomreach/s4cmd/issues/139`, opened 2018-10], so a
  credential-starved run cannot list. **Where** it fails depends on the
  environment: under the harness's credential neutralization (an empty
  `AWS_ROLE_ARN` + web-identity token file) it fails at `BotoClient.__init__`
  **before any S3 request** — the committed `recursive` capability receipt (exit
  1); in a plain bare environment the client *does* construct and the failure
  moves to the first `list_objects` call in the worker thread
  (`NoCredentialsError` → `[Thread Failure]`, `[OBS]`). Neither path produces
  keys. `shallow`/`show-directory`/`du`
  share the same constructor path and are blocked by `[INFERRED]` source
  extension [RUN `receipts/smoke/_capability/anon-nocredentials/`]. Benchmarking
  s4cmd needs credentials — routed to the owner. See `running.md`.
- **The source showed a different unit of parallelism than the inherited page.** The inherited
  page modeled s4cmd as a "threadpool across distinct CLI-supplied prefixes,
  serial per prefix." Source shows the opposite unit of parallelism: **client-side
  delimiter recursion**. `ls` always sends `Delimiter='/'` and re-queues each
  discovered `CommonPrefix` as a new thread-pool task — **one paginated legacy
  `list_objects` (v1) per pseudo-directory**, with concurrency bounded by the
  tree's branching, not a caller-supplied shard count. `ls` also takes exactly
  one path argument, so the believed "multiple prefixes on one invocation" mode
  does not exist [SRC `s4cmd.py:1176,1184-1185,1625-1632 @ 80059bf`]
  [OBS `_capability/obs-multiprefix`]. Full trace in `mechanism.md`.
- **An earlier "won't import under modern boto3" note was wrong.** A Stage A
  note asserted s4cmd 2.1.0 could not import
  under a current boto3 (the `botocore.vendored.requests` reference at
  `s4cmd.py:274`, "removed 2019"). It was **never tested and does not hold** — the
  attribute path still resolves and s4cmd imports and runs under botocore
  **1.33.13** (Py 3.7) and the latest **1.43.50** (Py 3.12) [RUN
  `receipts/smoke/_build/modern-boto3-import/`]. The image's 2018-era boto3 pin is
  therefore **reproducibility-only, not a necessity**. (Provenance precision: this
  false "won't import" claim was a **Stage A study-draft note, not an
  inherited tool-page observation** — the inherited testability observation was the opposite,
  "Trivial — `pip install s4cmd`", which held. It is exactly the kind of untested
  assumption the study exists to catch, logged as a self-correction, not erased.
  Codex review finding 2.)
- **Upstream is dormant.** The `2.1.0` tag is dated **2018-08-14**; the default
  branch is only 14 commits further (newest 2024-07-21, a mix of dependabot bumps
  and small edits) [SRC git log @ 80059bf]. The last *released* version is ~8
  years old — legacy/maintenance-mode, though it still installs and runs.

## Notes, questions, and observations

Every claim inherited from the original (secondhand) tool page, checked against the
2026-07-17 groundwork (`research/report.md`) and reconciled row-by-row in
`research/reconciliation.md`. **Every reconciliation row appears here.** Status
values: **CONFIRMED** (receipt-backed); **CONTRADICTED** (the inherited claim is
false — source, or a committed `[OBS]` receipt, establishes the opposite, both
sides shown; the *corrected mechanism* it points to is itself `VERIFIED: no` at
runtime, since no listing mode could execute); **CORRECTED** (a metadata/editorial
fact found different, both sides shown); **VERIFIED: no** (not settled by any
receipt — a hypothesis, however corroborated by source/doc reading);
**Unaddressed** (not a behavioral claim). Per `AGENTS.md`, source reading alone
never promotes a claim to **CONFIRMED** — CONTRADICTED falsifies an inherited
claim but does **not** promote its replacement past `VERIFIED: no`.

| # | Inherited claim | Status | Evidence |
| --- | --- | --- | --- |
| M1 | Repo `github.com/bloomreach/s4cmd` | **VERIFIED: no** (corroborated — canonical, not a fork) | [DOC README] [SRC @ 80059bf] |
| M2 | Language: Python | **VERIFIED: no** (corroborated) | [SRC `s4cmd.py` @ 80059bf] single-file Python |
| M3 | License: Apache-2.0 | **VERIFIED: no** (corroborated) | [SRC `LICENSE` @ 80059bf] |
| M4 | Version reviewed: unknown | **CORRECTED** (editorial, non-promoting) | Reviewed **2.1.0** (latest release tag), commit `80059bf` [SRC git tag] |
| M5 | Tier 2 (study-design) | **Unaddressed** | Not a behavioral claim; study-design decision, not mine to verify |
| M6 | Testability: "Trivial — `pip install s4cmd`" | **CONFIRMED** | Installs and **imports/runs** under botocore 1.33.13 (Py 3.7) and 1.43.50 (Py 3.12): `s4cmd --version` → exit 0 [RUN `receipts/smoke/_build/modern-boto3-import/`]. (correction: an earlier draft marked this Contradicted on the untested `vendored.requests` assumption — false, reverted.) |
| C1 | "Super S3 CLI, alternative to s3cmd, for large-file / data-intensive scripted workflows" | **VERIFIED: no** (corroborated — in full positioning) | [DOC README.md] |
| C2 | "Threadpool **across distinct CLI-supplied prefixes**" | **CONTRADICTED** (runtime `VERIFIED: no`) | Parallelism is real, but its unit is the **pseudo-directory discovered by delimiter recursion**, not CLI-supplied prefixes: `ThreadUtil.s3walk` always sends `Delimiter='/'` and re-queues each `CommonPrefix` as a new pool task [SRC `s4cmd.py:1176,1184-1185 @ 80059bf`] |
| C3 | "**Serial within any single prefix** — no keyspace discovery or sharding inside one prefix" | **CONTRADICTED** (runtime `VERIFIED: no`) | A single CLI prefix **with `/`-substructure parallelizes automatically** — each sub-directory is a separate thread task [SRC `s4cmd.py:1184-1185 @ 80059bf`]. Residual truth: a **delimiter-free flat** prefix does collapse to one serial paginated scan — the claim holds only for that degenerate shape |
| C4 | Parallelism "only if the caller supplies multiple distinct prefixes … same manual-sharding burden as s5cmd's `run` fan-out" | **CONTRADICTED** (multi-prefix rejection is `[OBS]`-receipt-backed; delimiter-recursion replacement runtime `VERIFIED: no`) | s4cmd auto-discovers the keyspace by delimiter recursion — **no** caller sharding. And `ls` accepts **exactly one** path (`args[1]`, `validate('cmd\|s3')`), so "multiple distinct prefixes on one invocation" is not a supported invocation [SRC `s4cmd.py:1625-1632 @ 80059bf`] [OBS `_capability/obs-multiprefix`: "Invalid number of parameters", exit 1] |
| T1 | `ls s3://bucket/prefix` (single prefix) = "believed-serial baseline" | **CONTRADICTED** (premise; runtime `VERIFIED: no`) | Not serial when the prefix has substructure (see C3). A real listing mode, but the "serial baseline" framing is wrong [SRC @ 80059bf] |
| T2 | `ls` with "multiple distinct prefixes on one invocation" = "threadpool-across-prefixes mode … closest thing to a fair best mode" | **CONTRADICTED** (does not exist; rejection is `[OBS]`-receipt-backed) | `ls` rejects >1 path; the believed best-mode is a misconception [SRC `s4cmd.py:1625-1632`] [OBS `obs-multiprefix`] |
| T3 | Thread-count flag "if present in the installed version" — sweep | **CONFIRMED** (present) | `-c/--num-threads` exists; default `cpu_count*4`; `S4CMD_NUM_THREADS` env alias; flagged for the benchmark sweep [SRC `s4cmd.py:121,1859 @ 80059bf`] [RUN `_build/build.md` `--help`] |
| S1 | "Genuine multi-prefix parallelism out of the box … just multiple prefix arguments" | **CONTRADICTED** (misattributed; runtime `VERIFIED: no`) | Parallelism out of the box: **yes**. Mechanism "multiple prefix arguments": **no** — it comes from delimiter recursion, and `ls` takes one path [SRC @ 80059bf] [OBS `obs-multiprefix`] |
| S2 | "Positioned specifically for large-file / data-intensive scripted use" | **VERIFIED: no** (corroborated) | [DOC README.md] |
| W1 | "Serial within a single prefix — no keyspace discovery/sharding inside one prefix" | **CONTRADICTED** (runtime `VERIFIED: no`) | Same as C3: delimiter recursion **is** in-prefix keyspace discovery [SRC `s4cmd.py:1184-1185 @ 80059bf`] |
| W2 | "Parallelism only across caller-supplied prefixes = manual sharding, like s5cmd `run`" | **CONTRADICTED** (`[OBS]`-receipt-backed rejection; replacement runtime `VERIFIED: no`) | Same as C4 [SRC] [OBS] |
| W3 | "Maintenance status unconfirmed — verify release cadence & current-S3 compatibility" | **VERIFIED: no** (release-cadence half corroborated; current-S3-compatibility half still unverified) | **Release cadence:** dormant — `2.1.0` tag 2018-08-14; default branch only +14 commits (newest 2024-07-21) [SRC git log @ 80059bf]; still installs and runs `--version` [RUN `_build/build.md`]. **Current-S3 API compatibility: NOT settled** — no listing mode could execute (`CREDS=none`), so whether the legacy `list_objects` v1 path still lists correctly against live S3 is unexercised (carried to Open hypotheses). **Immutable-file note:** `reconciliation.md`'s W3 evidence cell still carries the pre-correction phrase "won't import under current boto3" — that clause is **stale**, superseded by the M6 correction [RUN `_build/modern-boto3-import/`]; the immutable research file is left unedited as the historical record |
| WV | "What to verify first: is threadpool-across-prefixes real; compare best-case to s5cmd `run`" | **CONTRADICTED** (premise false) + deferred | The premise is false (C2/C4). The real benchmark question becomes: how does **delimiter-recursion** parallelism scale with `-c` and tree shape, and its LIST-request amplification vs a flat scan — needs credentials + scale (Open hypotheses, below) |
| N1 | s4cmd has **no unsigned/anonymous access** (no `--no-sign-request`; client built without `signature_version=UNSIGNED`); under credential starvation it fails before listing | **CONFIRMED** (capability, `recursive`) | [SRC `s4cmd.py:380-386 @ 80059bf`] [RUN `_capability/anon-nocredentials/` — auth=anonymous, exit 1, fails at `BotoClient.__init__` before any request] [3P issue #139]. Other modes (shallow/show-directory/du) share the constructor path — blocked by `[INFERRED]` extension, not four receipts |

The nine **CONTRADICTED** rows are falsified by source (and, for the
multiple-prefixes cases C4/T2/S1/W2, additionally by the committed `[OBS]`
`obs-multiprefix` receipt showing `ls` rejects >1 path). The *corrected*
mechanism they point to — delimiter-recursion parallelism — is itself
`VERIFIED: no` at runtime: no listing mode could execute. So nothing here is
promoted to CONFIRMED on source alone, consistent with the Provenance note below
that the behavioral claims "were NOT promoted."

**Additive row.** `N1` is **additive** — a firsthand finding with no
inherited tool-page antecedent (it entered via the groundwork's capability probe,
not the original page). Every other row above traces to a claim in the
pre-groundwork tool page. The inherited tool page also carried a **"Claimed numbers:
None inherited"** note (no throughput figures were ever supplied for s4cmd) — it
has no ledger row because there is no numeric claim to check; it survives as this
sentence, and the benchmark phase starts from zero inherited numbers. Status
tally (matching `research/reconciliation.md`): **8 Corroborated** (M1, M2, M3, M6,
C1, S2, T3, W3 — of which M6 and T3 are receipt-backed → CONFIRMED, the rest
VERIFIED: no); **9 Contradicted** (C2, C3, C4, T1, T2, S1, W1, W2, WV →
CONTRADICTED, both sides shown, corrected mechanism runtime `VERIFIED: no`);
**1 Unaddressed** (M5); **1 Settled by run** (N1 → CONFIRMED); **1
Corrected-editorial** (M4).

## Open hypotheses for the benchmark

Carried forward in full from `research/report.md` §10 (and the reconciliation's
deferred `WV`). Answerable only with **credentials + scale** — s4cmd cannot be
benchmarked anonymously. Provenance: all `[INFERRED from SRC]`/`[DOC]` hypotheses,
none receipt-settled.

1. **Memory ceiling.** At what key count does accumulate-then-sort OOM, and how
   does peak RSS scale with N? (Docs say "memory is the limit.") Sweep listings of
   increasing prefix size; capture `peak_rss` and `cgroup_peak_mem`.
2. **Fan-out parallelism vs request amplification.** How does wall-clock scale
   with `-c` on a broad tree, and how many LIST *pages* does `ls -r` actually issue
   vs a flat scan? Sweep `-c` in {1,2,4,8,16,32} (respecting the aggregate cap).
   **Note:** `grep S3APICALL` will **not** count LIST pages (it logs
   `get_paginator` once per directory, not per-page `list_objects`; s4cmd's
   `--debug` does not raise botocore's logger) — use the replay-server phase or a
   network capture for true page counts. Also revisit the **boto3 version**: the
   smoke image pins 2018-era boto3/botocore for reproducibility only (not a
   compatibility need); the benchmark should run a current boto3, whose
   retry/pooling behavior differs.
3. **Parallelism on a flat prefix.** Confirm the hypothesis that a single
   sub-delimiter-free prefix collapses to one serial thread regardless of `-c`.
4. **Throttling behavior** under aggressive fan-out (503 SlowDown is not in
   s4cmd's own retry set).
5. **Client-CPU cost** of per-line formatting + full sort at large N (capture CPU
   time alongside wall-clock, per the harness's cross-internet caveat).
6. **Architecture:** run on amd64 (native support confirmed) for the common
   denominator; no s4cmd-specific arch risk expected (pure Python).

**Additionally, carried from claim W3 (not in report §10):** whether s4cmd
2.1.0's legacy `list_objects` **v1** listing path still works correctly against
live current S3 is **unverified** — the mode is blocked at smoke (`CREDS=none`),
so "current-S3 compatibility" (half of the inherited W3 hypothesis) is settled
only for *install/startup*, not for *listing behavior*. A credentialed run
settles it.

## Known caveats carried forward

- **Retry-induced duplication (hypothesis).** A whole-directory requeue on a
  retryable error, without rolling back already-appended objects or already-queued
  child directories, can duplicate keys and re-walk subtrees on successful retry
  [SRC `s4cmd.py:529-539,1195-1206 @ 80059bf`]. The verifier counts duplicates, so
  this would surface as a `FAIL` at benchmark scale — an open question, not observed
  at smoke (codex review finding 7). See `mechanism.md` § Retry.
- **`S3APICALL` cannot count LIST pages.** `--debug`'s `S3APICALL` lines log
  `get_paginator` (once per pseudo-directory), not the per-page `list_objects` the
  paginator issues against the raw boto3 client — so they count directories walked,
  not LIST pages (codex review finding 1). See `mechanism.md` § Observability.
- **KEY-BYTE FIDELITY.** s4cmd `rstrip()`s each output line (`s4cmd.py:1622`), so a
  key with trailing whitespace loses it and a newline-bearing key is split across
  lines — a tool-side limit, not adapter fidelity. The `normalize.sh` adapter was
  exercised on **synthetic fixtures** (a construction check, not a `[RUN]` against
  real output — no mode could execute). Weird-key/unicode fidelity is deferred
  (`EDGE_BUCKET=none`). See `mechanism.md` § Output contract.

## Provenance

Sourced originally from Swath's private prior-art research: a one-line "Others"
catalog entry ("threadpool across distinct CLI prefixes; serial per prefix"). No
dedicated investigation existed — the thinnest provenance chain in the study,
restated from a single secondhand summary.

**Mixed provenance — read the table carefully.** The **Language** and **License**
cells did *not* come from Swath's notes, which never state them; they were read
directly from the tool's public GitHub repository. They are therefore firsthand
metadata sitting in a page that was otherwise entirely secondhand. (Two tool pages,
`s4cmd` and `minio-mc`, carry this callout for exactly this reason.)

**Current lineage.** The page is no longer uniform secondhand. Firsthand,
derived from the pinned checkout (commit `80059bf`, tag 2.1.0) and
committed receipts: the corrected **Version** cell [SRC]; the **Testability** cell
[RUN]; the **capability receipt** settling no-unsigned-access; and the standalone
report + reconciliation + review under `research/`. The **behavioral
mechanism/modes/strengths/weaknesses claims were NOT promoted to CONFIRMED** — no
listing mode could execute (`CREDS=none`), so where the inherited claim was wrong
it is marked **CONTRADICTED** (falsified by source or the `[OBS]` receipt) and the
*corrected* mechanism that replaces it stays `VERIFIED: no` at runtime; where the
inherited claim held it stays `VERIFIED: no` (corroborated). Both sides are
visible in the Notes, questions, and observations above, nothing rewritten into confidence. Full
provenance detail — including which sentence came from the secondhand seed — is in
`research/reconciliation.md`.

## Receipts

Scoped to s4cmd **2.1.0**, image
`sha256:d458ef5096180e517840712e29b0b8705ec97cebf48f717cad2fea3805105813` (the
`localhost:5000/s4cmd-study` ref resolves only via a local registry — see
`running.md`), against the anonymous smoke bucket (`noaa-normals-pds`, 148,917
keys, 2026-07-17 snapshot).

- **Capability — no anonymous access** (settles N1):
  `receipts/smoke/_capability/anon-nocredentials/` — `auth=anonymous`, exit 1,
  fails at `BotoClient.__init__` before any S3 request. Corroborating `[OBS]`
  bare-env `NoCredentialsError` in `_capability/OBS-probes.md` and narrative in
  `_capability/NOTES.md`.
- **Pre-flight — bucket is anonymously listable** (`--no-sign-request` harness
  client, exit 0): `receipts/smoke/_capability/preflight-anon/`.
- **Build + first-execution + correction evidence** (`--version` 2.1.0, `--help`,
  boto3-pin rationale, modern-boto3 import runs):
  `receipts/smoke/_build/build.md`, `receipts/smoke/_build/modern-boto3-import/`.
- **Adapter fixtures** for `normalize.sh` (synthetic construction check):
  `receipts/smoke/_adapter/`.

**No listing-mode receipt exists — every mode is blocked, not skipped** (no
unsigned path, `CREDS=none`). See `running.md` for the full blocked-smoke state
and reproduction.
