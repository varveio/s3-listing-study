# s3kor — reconciliation with the inherited dossier

Walks every inherited claim in `tools/s3kor/README.md` (the dossier, seeded from
swath's secondhand prior-art notes) against my independent groundwork
(`research/report.md`, pinned checkout `844fe3d7931fcca415c8b8a4e22f048886e6b82b`
= tag `v0.0.37`, and the smoke receipts under `receipts/smoke/`).

Verdicts: **Corroborated** (independent work found the same) · **Contradicted**
(found otherwise, both sides shown) · **Unaddressed** (my work didn't touch it) ·
**Settled by smoke run** (a committed receipt genuinely decides it).

Evidence labels as in the report: `[SRC file:line @ sha]`, `[DOC url]`,
`[RUN receipt]`, `[3P url]`, `[INFERRED]`.

> **Scope discipline (methodology § Receipts):** source reading is **not** a
> receipt and never promotes a claim past `VERIFIED: no`. Several mechanism
> claims below are marked **Corroborated** on `[SRC]` evidence — that raises my
> confidence and fixes the provenance, but it does **not** promote them to
> `CONFIRMED`; only a receipt does, and my smoke produced no listing (every mode
> was credential-blocked). "Settled by smoke run" is therefore used only where a
> committed receipt genuinely decides the point.

## Metadata claims

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| M1 | Repo `github.com/sethkor/s3kor` | **Corroborated** | Canonical home; cloned and pinned [SRC repo @ 844fe3d] |
| M2 | Language **Go** | **Corroborated** | `go.mod` module `github.com/sethkor/s3kor`, `go 1.14`; all sources `.go` [SRC go.mod:1-5 @ 844fe3d]. **Note to orchestrator:** the dispatch note said "the dossier's language row (believed Rust) gets Contradicted" — the dossier text actually says **Go**, so this is a corroboration, not a contradiction. The dispatch premise was mistaken; reconciled against the dossier as written. |
| M3 | License **GPL-3.0** | **Corroborated** | `LICENSE` is GPLv3 [SRC LICENSE:1-2 @ 844fe3d] |
| M4 | Version reviewed **unknown** | **Contradicted (editorial)** | I reviewed a specific version: **v0.0.37 @ `844fe3d`**. The dossier's "unknown" is corrected in the dossier's Version cell. |
| M5 | Testability "**Trivial** — prebuilt binaries and `go install` both available" | **Corroborated (install) / Contradicted (test-in-this-campaign)** | Install is trivial — goreleaser binaries + `go install` both work; I built via `go install …@844fe3d` [SRC .goreleaser.yml, Makefile @ 844fe3d][RUN §7]. But *testing listing in this campaign* is **not** trivial: s3kor has no anonymous mode and the campaign is `CREDS=none`, so every listing mode is blocked [RUN receipts/smoke/_capability/]. "Trivial to install" ≠ "trivial to test here." |

## Mechanism claims

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| X1 | "fast … built in Go using multiparts and multiple threads for fast parallel actions," `aws s3` replacement with cp/sync/rm/ls | **Corroborated** | Exact README framing; four subcommands present [DOC README.md:1-2, 29-36][SRC s3kor.go:32-94 @ 844fe3d] |
| X2 | "mostly transfer-focused, in the same shape as s5cmd" — multipart, multithreaded | **Corroborated (transfer side, docs+flags)** | cp/sync expose concurrency flags (`--concurrent` 30/20) [SRC s3kor.go:48,76 @ 844fe3d] and the README describes concurrent multipart transfers [DOC README.md:126-133]. I did **not** read the transfer worker code (out of listing scope), so I make no source claim about worker spawning — the transfer-parallel shape is doc-attested, not source-verified here. |
| X3 | Listing (`ls`) is **believed likely serial** — single continuation-token chain, no keyspace discovery — *explicitly flagged as the weakest-provenance claim, an extrapolation from "same shape as s5cmd," nobody read s3kor's listing source* | **Corroborated (now source-confirmed, provenance upgraded)** | I read the listing source directly: `ls` calls the SDK `ListObjectsV2Pages` serial paginator with only `Bucket`+`Prefix`; no `Delimiter`, no `MaxKeys`, no keyspace division; per-page goroutines only format output, they issue no requests [SRC list.go:172-213, 177-194 @ 844fe3d]. The inference holds and is no longer an extrapolation. **Stays `VERIFIED: no`**: whether serial listing is a *performance weakness at scale* is a behavioral claim needing a benchmark receipt — source confirms the mechanism, not the scale cost. |
| X4 | "If that inference holds, the finding **collapses into the s5cmd finding**, report as a one-line corollary" | **Contradicted (in part)** | The serial-listing *mechanism* does mirror the s5cmd hypothesis, but s3kor has **distinct, non-shared findings** that make it more than a corollary: (a) **no unsigned path for the listing client** [SRC s3kor.go:179-197, list.go:264-273 @ 844fe3d][RUN]; (b) **session-build panic under the harness's starved env** (web-identity token file set, role ARN emptied) via `session.Must` [RUN receipts/smoke/_capability/list/receipt.md]; (c) **non-deterministic output order** across pages (per-page goroutines race a shared channel) [SRC list.go:187-194 @ 844fe3d][INFERRED]. None of these follow from "same shape as s5cmd." The page should stay thin but not fold into s5cmd. |

## Modes / tunables to exercise

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| D1 | `s3kor ls s3://bucket` — primary listing surface, believed serial | **Corroborated** | Confirmed the primary and (with `--all-versions`) only listing surface; serial (see X3) [SRC s3kor.go:39-41, list.go:172-213 @ 844fe3d] |
| D2 | `s3kor ls --versions` — "lists version history" | **Contradicted (editorial)** | The flag is **`--all-versions`**, not `--versions`. `--versions` does not exist. The mode is real (uses `ListObjectVersionsPages`, output `<versionId> <key>`) [SRC s3kor.go:40, list.go:109-160 @ 844fe3d; live `--help` report §7]. Corrected in the dossier's Modes table. |
| D3 | cp/sync multipart/multithread flags — transfer-side parallelism; establishes the listing-serial/transfer-parallel split | **Corroborated (flag surface only)** | The *flag* split is real and source-visible: cp/sync carry `-c/--concurrent` (defaults 30/20) while `ls` has no concurrency flag [SRC s3kor.go:48,76 vs 39-41 @ 844fe3d]. Consistent with X2: I confirmed `ls` is serial by source, but did **not** read the transfer worker code, so the transfer side is doc-attested, not source-verified. |

## Claimed strengths

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| S1 | Multipart, multithreaded transfer (cp/sync) | **Corroborated** (out of listing scope) | [DOC README.md:126-133][SRC s3kor.go:48,76 @ 844fe3d] |
| S2 | Cross-account bucket-to-bucket copy support | **Corroborated (by docs) / Unaddressed (by run)** | Documented `--dest-profile` feature [DOC README.md:122-124][SRC s3kor.go:69 @ 844fe3d]; a transfer feature, not exercised (out of listing scope, and mutating — forbidden) |
| S3 | "Go performance characteristics, similar to s5cmd and rclone" | **Unaddressed** | No performance measured (smoke is not a benchmark; and listing was blocked). Vague comparative claim — stays an open hypothesis for the benchmark phase. |

## Claimed weaknesses (hypotheses)

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| W1 | "Listing is likely serial, by analogy to s5cmd … the weakest-provenance claim … Falsifiable by reading s3kor's listing source and by network trace during `ls`" | **Corroborated (source-confirmed; not falsified)** | Read the source (see X3): serial confirmed. The suggested network-trace check was blocked (no anonymous listing), but the source settles the mechanism. Stays `VERIFIED: no` for the scale/perf reading. |
| W2 | "If serial listing confirmed, not distinct from s5cmd — one-line corollary" | **Contradicted (in part)** | Same as X4: distinct tool-specific findings exist (no unsigned listing path; session-build panic under the harness's starved env; a source-visible `ls` concurrency race). Not merely an s5cmd corollary. |

## "What to verify first"

| # | Inherited guidance | Verdict | Evidence |
| --- | --- | --- | --- |
| V1 | Read the listing command's source before running, since it may resolve the uncertainty cheaply | **Corroborated (followed; resolved)** | Did exactly this; the source resolves the serial-vs-parallel question (serial) and surfaced findings the dossier never anticipated (anonymous gap, panic, ordering) [SRC list.go, s3kor.go, common.go @ 844fe3d] |

## Findings NOT present in the dossier (net-new, for benchmark/owner routing)

These are not reconciliation of inherited claims — the dossier is silent on them:

1. **No anonymous/unsigned listing capability.** No `--no-sign-request`, no
   config, no env. `AnonymousCredentials` appears twice — region detection
   [SRC common.go:49 @ 844fe3d] and the S3-to-S3 copy download path
   [SRC multicopy.go:513 @ 844fe3d] — but **neither is wired into the listing
   client**, which always uses the signing session [SRC s3kor.go:179-197,
   list.go:264-273 @ 844fe3d]. **Settled by smoke run** (capability): the tool
   cannot list unsigned [RUN receipts/smoke/_capability/list/receipt.md,
   list-versions/receipt.md].
2. **Session-build panic under the harness's starved env** (which sets a
   web-identity token file while emptying the role ARN). `session.Must` turns
   that session-construction error into a Go panic before any request; exit 2,
   0 S3 calls [SRC s3kor.go:190 @ 844fe3d][RUN same receipts]. Scope: a bare
   empty-credential env would instead fail at request time — the panic is
   specific to this session-build-error condition; either way `ls` cannot run
   unsigned.
3. **Non-deterministic `ls` output order** across page boundaries (per-page
   goroutines race a shared channel) [SRC list.go:187-194 @ 844fe3d][INFERRED].
4. **README documents a nonexistent flag** `--auto-region`; the real flag is
   `--detect-region` [SRC s3kor.go:29 @ 844fe3d vs DOC README.md:71].
5. **`--verbose` logs go to a temp file inside the container**, not to the
   terminal — no visible request trace [SRC s3kor.go:110-151 @ 844fe3d].
6. **Upstream dormant** since 2022-06-14 (last commit, docs-only); last release
   v0.0.37 (2021-10-02); 4 open issues [3P commits/releases/issues — 2026-07-17].
7. **No upstream Docker image or Dockerfile** — built from source [SRC
   .goreleaser.yml, Makefile @ 844fe3d; 3P Docker Hub/GHCR — 2026-07-17].

## Claims about OTHER tools / S3 itself (for orchestrator routing, not edited here)

- The dossier repeatedly frames s3kor "by analogy to **s5cmd**" and asserts
  s5cmd's listing is "confirmed-by-source-reading (elsewhere) serial." I did not
  investigate s5cmd; that cross-tool claim lives on s5cmd's page and in
  `docs/open-questions.md` (language-bottleneck hypothesis) — **not edited
  by me.** My s3kor work neither confirms nor denies the s5cmd claim; it only
  shows s3kor's own listing is serial by its own source.
- **Owner routing item:** s3kor cannot participate in an anonymous-only
  (`CREDS=none`) benchmark — it needs a scoped, list-only credential to run at
  all. Same decision class as other signed-only tools. No credentials were
  attempted.

## Dossier edits made (this branch)

- Header status: note that smoke was attempted and the capability finding
  (receipt-backed), replacing the blanket "not yet run."
- Metadata: **Version reviewed** `unknown` → `v0.0.37 (844fe3d)` (editorial).
- Modes table: `--versions` → **`--all-versions`** (editorial correction, [SRC]).
- Added a **Receipts** entry citing the two capability receipts.
- Added a receipt-backed **capability finding** section (no anonymous listing
  path; session-build panic under the harness's starved env) scoped to v0.0.37 /
  the registered smoke bucket.
- Added an **Update callout** at the top of the Mechanism section noting the
  serial-listing claim is now source-confirmed and does not collapse into
  s5cmd — the original hypothesis prose is **left verbatim** as the
  pre-registration record (not rewritten), with verdicts pointing here. The
  Claimed-strengths / Claimed-weaknesses hypothesis rows are likewise left
  verbatim; their verdicts live in this file (S1–S3, W1–W2), not inline in the
  dossier. `VERIFIED: no` is kept for all scale/performance readings.
- Provenance section: updated to mixed lineage (secondhand notes + firsthand
  source read + smoke receipts).

Mechanism claims that are scale-dependent (listing as a *performance* weakness)
remain `VERIFIED: no` — not settleable at smoke scale, and my smoke produced no
listing at all.
