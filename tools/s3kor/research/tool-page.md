# s3kor

> **Historical landing page (2026-07-20, capsule migration).** This is the full
> pre-restructure landing page. Any `current-state` wording below is historical
> as of the date it records and is superseded by the root README and `data/`.
> Only this banner and link targets changed; body prose and evidence
> qualifications are preserved.

**Status: current-state page, consolidated 2026-07-17.** Rebuilt from the
`groundwork/s3kor` pass (v0.0.37, commit `844fe3d`) into the owner-adopted
three-document shape — this is transcription of already-reviewed groundwork
(two codex rounds, all findings resolved), not new research. Full detail lives
in the companions:

- [`mechanism.md`](../docs/mechanism.md) — the source-anchored architecture (serial
  `ListObjectsV2Pages` chain, the page-vs-format goroutine race, no keyspace
  division, retry model, memory model, the no-unsigned-listing mechanism, the
  `--detect-region`/`--auto-region` doc-drift).
- [`running.md`](../docs/running.md) — the from-source study Dockerfile, the blocked
  smoke state with both capability receipts and the panic output, what a
  credentialed run would need, and the architecture matrix.
- [`research/`](.) — `report.md` (the source-and-run groundwork report),
  `reconciliation.md` (every inherited observation, status, and supporting record), and
  `codex-review.md` (the two-round critical cross-check, all findings resolved).
  These preserved files may use the project's older terminology; this page is
  the current summary.

## What we saw

**We could not run s3kor unsigned.** The *listing client* always signs — the two
`AnonymousCredentials` paths in the codebase (region detection, S3-to-S3 copy
download) are not the listing client (see `mechanism.md`). Under the campaign's
`CREDS=none`, credential starvation is a **startup panic before any S3 request
is issued** —
receipt-backed, exit 2, 0 S3 calls [RUN
`receipts/smoke/_capability/list/receipt.md`,
`list-versions/receipt.md`]. Its **"parallel" reputation is transfer-side**:
`ls` is **one serial `ListObjectsV2Pages` paginator** with no keyspace
division, and it carries a **source-visible output-ordering / concurrency
race** in the page-formatting goroutines. Upstream is **dormant since 2022**.
Each observation below is scoped to **v0.0.37** and linked to its source or run
record (SHA, file:line, receipt); nothing about listing at scale is
receipt-settled, so all scale/performance readings stay `VERIFIED: no`.

|  |  |
|---|---|
| **Repo** | <https://github.com/sethkor/s3kor> |
| **Language** | Go (module `github.com/sethkor/s3kor`, `go 1.14`; `aws-sdk-go v1.30.16`) [SRC `go.mod:1-9` @ 844fe3d] |
| **License** | GPL-3.0 [SRC `LICENSE:1-2` @ 844fe3d] |
| **Version reviewed** | v0.0.37 (commit `844fe3d7931fcca415c8b8a4e22f048886e6b82b`) — firsthand, groundwork branch |
| **Upstream health** | **Dormant.** Last commit 2022-06-14 (docs-only); last release `v0.0.37` tagged 2021-10-02; 4 open issues, two 2023–2024 with no maintainer response [3P `commits/master`, `releases`, `issues` — accessed 2026-07-17] |
| **Tier** | 2 — included when the setup permits |
| **Testability** | Install is trivial (goreleaser binaries + `go install` both work). **Listing is untestable in this campaign**: no unsigned mode, `CREDS=none`, so every listing mode is **blocked, not skipped** [RUN `receipts/smoke/_capability/`] |

## What we tried and saw

- **No unsigned path for the listing client — every listing mode is blocked.**
  s3kor exposes no `--no-sign-request` equivalent, no anonymous config, no env
  for `ls`; its session is the standard AWS SDK-for-Go v1 credential chain
  wrapped in `session.Must` [SRC `s3kor.go:179-197` @ 844fe3d]. Under
  `CREDS=none` both listing modes are blocked — **CONFIRMED**, receipt-backed
  [RUN `receipts/smoke/_capability/list`, `list-versions`]. See
  `mechanism.md` § "No unsigned path for listing".
- **Credential starvation is a startup panic, not a clean error.** Under the
  harness's starved env (`AWS_WEB_IDENTITY_TOKEN_FILE` set while `AWS_ROLE_ARN`
  is emptied — an SDK **session-build** error), `session.Must` turns the error
  into a **Go panic at session construction**, exit 2, before any S3 request
  [SRC `s3kor.go:190` @ 844fe3d][RUN same receipts]. **Scope:** the *panic* is
  specific to this session-build-error condition; a bare empty-credential env
  would instead fail at **request** time — either way `ls` cannot run unsigned.
- **Listing is one serial `ListObjectsV2Pages` paginator — the "threads" only
  format pages.** `ls` calls the SDK auto-paginator with `Bucket`+`Prefix`
  only; no keyspace division, no `Delimiter`, no `MaxKeys`; per-page goroutines
  drain/format already-fetched pages and issue no LIST requests [SRC
  `list.go:172-213,177-194` @ 844fe3d]. Source confirms the **mechanism**;
  whether serial listing is a **performance** weakness at scale is
  `VERIFIED: no` (no benchmark receipt; smoke produced no listing). See
  `mechanism.md` § "Listing is one serial paginator".
- **`ls` has a source-visible concurrency race, not just non-deterministic
  order.** One goroutine per page races a shared channel (non-deterministic
  output order), and `List` starts the printer goroutine *before* reassigning
  the channel it ranges over and sequences `wg.Add` after the work that
  `Done`s it — a data race with two source-visible failure shapes: the printer
  binding the abandoned channel and hanging, or a `Done`-before-`Add` panic
  [SRC `list.go:187-194,216-234` @ 844fe3d][INFERRED]. **Unobserved at
  runtime** — every listing was credential-blocked. See `mechanism.md` §
  "The page-vs-format goroutine race".
- **Doc-drift and dormancy.** The README documents a nonexistent
  `--auto-region` flag; the actual flag is `--detect-region` [SRC
  `s3kor.go:29` @ 844fe3d vs DOC `README.md:71`; live `--help`
  `receipts/smoke/_build/first-exec.txt`]. The project is ~4 years dormant, so
  we found no later change when we checked the repository on 2026-07-17.

## Notes, questions, and observations

Every claim in `research/reconciliation.md` mapped to a status here. Status
values, **routed by receipt-backed status — never the reconciliation status
alone**:

- **CONFIRMED** — a committed receipt in this repo decides it.
- **CORRECTED** — the inherited claim stated a **factually wrong static value**
  (a name, version, or flag) and the correct value is shown both-sides, settled
  by source / docs / live `--help`. Correcting a wrong value is **not** a
  promotion: it makes no new behavioral claim about the tool, so the "source is
  not a receipt" rule below does not bar it.
- **VERIFIED: no** — not settled by any committed receipt. Per `AGENTS.md`,
  **source reading is not a receipt and never promotes a corroborated claim to
  `CONFIRMED`**, so a behavioral / mechanism / scale claim that is only
  *corroborated by source* stays here, with the corroboration noted in
  Evidence. This is the campaign's "no run proves it" bucket, not a statement of
  doubt about a static fact.
- **UNVERIFIABLE** — cannot be tested with the resources on hand.

Evidence labels: `[SRC file:line @ sha]` pinned source · `[DOC]` docs ·
`[RUN receipt]` committed smoke run · `[OBS]` observed directly, not
wrapper-recorded · `[3P]` third-party · `[INFERRED]`.

| Recon | Inherited claim | Status | Evidence |
| --- | --- | --- | --- |
| M1 | Repo `github.com/sethkor/s3kor` | **VERIFIED: no** (source/clone-corroborated identity) | Canonical home, cloned and pinned [SRC repo @ 844fe3d] |
| M2 | Language **Go** | **VERIFIED: no** (source-corroborated) | `go.mod` module `github.com/sethkor/s3kor`, `go 1.14` [SRC `go.mod:1-5` @ 844fe3d]. (The dispatch note's "believed Rust" premise was mistaken; the tool page already said Go — corroboration, not contradiction.) |
| M3 | License **GPL-3.0** | **VERIFIED: no** (source-corroborated) | `LICENSE` is GPLv3 [SRC `LICENSE:1-2` @ 844fe3d] |
| M4 | Version reviewed **unknown** | **CORRECTED** — reviewed **v0.0.37 @ `844fe3d`** | Pinned release tag; self-reports `dev-local-version` (see `running.md`) [SRC `go.mod` @ 844fe3d; OBS `_build/first-exec.txt`] |
| M5 | Testability "**Trivial** — prebuilt binaries and `go install` both available" | **CORRECTED** — trivial to **install**; **untestable to list here** | Install trivial: goreleaser binaries + `go install …@844fe3d` [SRC `.goreleaser.yml`, `Makefile` @ 844fe3d]. But listing is blocked this campaign (no unsigned mode, `CREDS=none`) — "trivial to install" ≠ "trivial to test here" [RUN `receipts/smoke/_capability/`] |
| X1 | "fast … multiparts and multiple threads for fast parallel actions," `aws s3` replacement with cp/sync/rm/ls | **VERIFIED: no** (doc/source-corroborated) | Exact README framing; four subcommands present [DOC `README.md:1-2,29-36`][SRC `s3kor.go:32-94` @ 844fe3d] |
| X2 | "mostly transfer-focused, in the same shape as s5cmd" — multipart, multithreaded | **VERIFIED: no** (transfer side **doc-attested**, not source-verified) | cp/sync expose concurrency flags (`--concurrent` 30/20) [SRC `s3kor.go:48,76` @ 844fe3d] and the README describes concurrent multipart transfers [DOC `README.md:126-133`]; the transfer **worker code was not read** (out of listing scope), so no source claim about worker spawning |
| X3 | Listing (`ls`) **believed likely serial** — single continuation-token chain, no keyspace discovery — *flagged as the weakest-provenance claim, an extrapolation from "same shape as s5cmd," nobody read s3kor's listing source* | **VERIFIED: no** (mechanism now source-confirmed; scale/perf **not** receipt-settled) | Read directly: `ls` calls SDK `ListObjectsV2Pages` serially with only `Bucket`+`Prefix`; no `Delimiter`, no `MaxKeys`, no keyspace division; per-page goroutines only format output [SRC `list.go:172-213,177-194` @ 844fe3d]. The inference holds and is no longer an extrapolation — but whether serial listing is a *performance weakness at scale* needs a benchmark receipt; source confirms the mechanism, not the scale cost |
| X4 | "If that inference holds, the finding **collapses into the s5cmd finding**, report as a one-line corollary" | **CORRECTED** — does **not** collapse; distinct findings exist | The serial *mechanism* mirrors the s5cmd hypothesis, but s3kor has non-shared findings: (a) no unsigned listing path [RUN], (b) session-build panic under the starved env [RUN], (c) a source-visible `ls` concurrency race [SRC `list.go:187-194` @ 844fe3d][INFERRED]. None follow from "same shape as s5cmd." Stays thin, but not folded into s5cmd |
| D1 | `s3kor ls s3://bucket` — primary listing surface, believed serial | **VERIFIED: no** (primary+serial source-corroborated; scale not settled) | The primary and (with `--all-versions`) only listing surface; serial (see X3) [SRC `s3kor.go:39-41`, `list.go:172-213` @ 844fe3d] |
| D2 | `s3kor ls --versions` — "lists version history" | **CORRECTED** — the flag is **`--all-versions`**; `--versions` does not exist | Real mode uses `ListObjectVersionsPages`, output `<versionId> <key>` [SRC `s3kor.go:40`, `list.go:109-160` @ 844fe3d; live `--help` `_build/first-exec.txt`] |
| D3 | cp/sync multipart/multithread flags — transfer-side parallelism; establishes the listing-serial/transfer-parallel split | **VERIFIED: no** (flag split **source-visible**; transfer worker behavior doc-attested) | cp/sync carry `-c/--concurrent` (30/20) while `ls` has no concurrency flag [SRC `s3kor.go:48,76` vs `39-41` @ 844fe3d]; `ls` confirmed serial by source, but the transfer worker code was not read |
| S1 | Multipart, multithreaded transfer (cp/sync) | **VERIFIED: no** (out of listing scope; doc/flag-corroborated) | [DOC `README.md:126-133`][SRC `s3kor.go:48,76` @ 844fe3d] |
| S2 | Cross-account bucket-to-bucket copy support | **VERIFIED: no** (doc-corroborated; not run — transfer, mutating, forbidden) | Documented `--dest-profile` feature [DOC `README.md:122-124`][SRC `s3kor.go:69` @ 844fe3d] |
| S3 | "Go performance characteristics, similar to s5cmd and rclone" | **VERIFIED: no** (Unaddressed — no performance measured) | Smoke is not a benchmark, and listing was blocked; vague comparative claim — carried to Open hypotheses below |
| W1 | "Listing is likely serial, by analogy to s5cmd … the weakest-provenance claim … Falsifiable by reading s3kor's listing source and by network trace during `ls`" | **VERIFIED: no** (source-confirmed serial, not falsified; scale/perf not settled) | Source read (see X3): serial confirmed. The network-trace check was blocked (no unsigned listing); source settles the mechanism only |
| W2 | "If serial listing confirmed, not distinct from s5cmd — one-line corollary" | **CORRECTED** — distinct tool-specific findings exist (same as X4) | No unsigned listing path; session-build panic under the starved env; a source-visible `ls` concurrency race [SRC `list.go:187-194` @ 844fe3d][RUN][INFERRED] |
| V1 | "Read the listing command's source before running, since it may resolve the uncertainty cheaply" | **VERIFIED: no** (guidance followed; resolved by source) | Done; source resolved serial-vs-parallel (serial) and surfaced findings the tool page never anticipated (no unsigned path, panic, race) [SRC `list.go`, `s3kor.go`, `common.go` @ 844fe3d] |
| N1 | *(net-new)* No anonymous/unsigned listing capability | **CONFIRMED** — the tool cannot list unsigned | `AnonymousCredentials` appears twice — region detection [SRC `common.go:49` @ 844fe3d] and the S3-to-S3 copy download path [SRC `multicopy.go:513` @ 844fe3d] — **neither is the listing client** [SRC `s3kor.go:179-197`, `list.go:264-273` @ 844fe3d]; settled by smoke [RUN `_capability/list`, `list-versions`] |
| N2 | *(net-new)* Session-build panic under the harness's starved env | **CONFIRMED**, scoped | `session.Must` turns the session-construction error into a Go panic; exit 2, 0 S3 calls [SRC `s3kor.go:190` @ 844fe3d][RUN same receipts]. Scope: a bare empty-credential env would fail at request time instead — the panic is specific to the session-build condition |
| N3 | *(net-new)* Non-deterministic `ls` output order / concurrency race | **VERIFIED: no** (source-visible, **unobserved** at runtime) | Per-page goroutines race a shared channel; printer-before-reassignment + `wg` mis-sequencing → abandoned-channel hang or `Done`-before-`Add` panic [SRC `list.go:187-194,216-234` @ 844fe3d][INFERRED]. Every listing was credential-blocked |
| N4 | *(net-new)* README documents a nonexistent flag `--auto-region` | **CORRECTED** — the real flag is **`--detect-region`** | [SRC `s3kor.go:29` @ 844fe3d vs DOC `README.md:71`; live `--help` `_build/first-exec.txt`] |
| N5 | *(net-new)* `--verbose` logs go to a temp file inside the container | **VERIFIED: no** (source-corroborated, unobserved) | `zap` DevelopmentConfig to a temp file, not stdout/stderr — no visible request trace [SRC `s3kor.go:110-151` @ 844fe3d] |
| N6 | *(net-new)* Upstream dormant since 2022-06-14 | **VERIFIED: no** (3P, dated — not a receipt) | Last commit 2022-06-14 (docs-only); last release v0.0.37 (2021-10-02); 4 open issues [3P `commits`/`releases`/`issues` — 2026-07-17] |
| N7 | *(net-new)* No upstream Docker image or Dockerfile | **VERIFIED: no** (absence-sweep + source — not a receipt) | Distribution is a Homebrew tap + goreleaser binaries [SRC `.goreleaser.yml`, `Makefile` @ 844fe3d]; built from source [INFERRED from a Docker Hub/GHCR search returning no `sethkor/s3kor` image on 2026-07-17 — absence of a found result, not a cited document] |

**Notes kept on other pages.** Two
reconciliation entries under "Claims about OTHER tools / S3 itself" are not
claims about s3kor and are **not** ledger rows here, but they have a
destination: (1) the tool page's repeated "by analogy to **s5cmd**" framing and
the assertion that s5cmd's listing is "confirmed-by-source-reading serial"
belong to **s5cmd's page** and `docs/open-questions.md` — the s3kor
groundwork neither confirms nor denies the s5cmd claim; (2) the **owner-routing
item** "s3kor cannot participate in an anonymous-only (`CREDS=none`) benchmark
— it needs a scoped, list-only credential to run at all" is the direct
consequence of N1, carried into Open hypotheses below as an **owner decision**.
The reconciliation's "Dossier edits made" section uses the project's older
terminology and documents the groundwork's
edits to the *previous* README and is superseded by this consolidation; it
needs no ledger row.

**Additive rows:** none. Every ledger row above traces to a
`research/reconciliation.md` row (M/X/D/S/W/V, or a net-new N-item); no row was
introduced from outside the reconciliation.

## Open hypotheses for the benchmark

Every `Unaddressed` / unrun claim, carried forward **in full** with
provenance. None is settleable at smoke scale, and none is in this
consolidation's scope to resolve.

- *[reconciliation S3]* "Go performance characteristics, similar to s5cmd and
  rclone" — **Unaddressed**: no performance measured (smoke is not a benchmark;
  and listing was blocked). Vague comparative claim.
- *[report §10]* "**s3kor cannot participate in an anonymous-only benchmark.**
  If the benchmark keeps `CREDS=none`, s3kor is untestable for listing; it
  needs a scoped, list-only credential to run at all. **Decision for the
  owner.**"
- *[report §10]* "**Common-denominator arch:** amd64 is supported natively on
  every channel; smoke ran arm64 native. Confirm amd64 for the comparative
  phase (no emulation needed either way)."
- *[report §10]* "**Memory at scale (unsettled):** is the streaming
  buffered-channel design actually bounded on million-object listings, or does
  the one-goroutine-per-page spawn (no pool) grow goroutine/heap unboundedly if
  the single printer lags? Proposed: measure RSS + goroutine count across a
  size sweep." *(The "bounded-memory" reading was updated in groundwork — the
  design is streaming but **not** back-pressured; see `mechanism.md`.)*
- *[report §10]* "**Serial-list latency:** with list concurrency fixed at 1 and
  `MaxRetries 30`, how does wall-clock scale with page count vs tools that
  shard prefixes? Proposed sweep: bucket/prefix sizes spanning ~3 to ~149+
  pages. No internal tunable to sweep — the only lever is `--detect-region`
  on/off (pre-list RTTs)."
- *[report §10]* "**Output-order non-determinism** could matter if any
  downstream consumer assumes sorted output; worth a note in the comparison
  setup."
- *[inherited D2 mode row]* `ls --all-versions` was flagged in the pre-groundwork
  tool page as "worth a quick check for different perf characteristics than plain
  listing" — **unrun**: the mode switches the API to `ListObjectVersionsPages`
  [SRC `list.go:109-160` @ 844fe3d], but both listing modes were
  credential-blocked, so no perf comparison against plain `list` exists. Its
  flag-name and output-contract corrections are settled (ledger D2); the
  perf-characteristics sub-hypothesis is a benchmark-phase question.

## Known caveats carried forward

- **`list-versions` is only manifest-comparable on an *unversioned* bucket.**
  `ListObjectVersions` returns every version *and* every delete marker [SRC
  `list.go:109-151` @ 844fe3d], and `normalize.sh` drops the version id to emit
  a bare key. On an unversioned bucket the emitted key set equals the
  current-object set; on a versioned bucket the same mode legitimately emits
  duplicate keys and delete-marker-only keys. The registry does not record the
  smoke bucket's versioning state and `normalize.sh` does not enforce it — so
  this precondition is unestablished [INFERRED — not a registry/receipt fact],
  and the mode was credential-blocked anyway. See `mechanism.md`.
- **Memory is streaming but NOT back-pressured — the "bounded-memory" claim was
  updated.** The paginator callback spawns a goroutine per page and returns
  immediately, so the 50-slot channel does not back-pressure the fetch loop;
  page goroutines accumulate if the single printer lags, and peak memory can
  grow with in-flight pages rather than staying bounded [SRC `list.go:187-194`
  @ 844fe3d][INFERRED]. A structural read, not a measured memory claim — scale
  OOM is a benchmark question. See `mechanism.md`.
- **The panic is scoped to the session-build-error condition.** The
  receipt-backed panic proves s3kor cannot list under the harness's starved
  env; the *root* capability finding (no unsigned listing path) is stated
  independently of the panic and holds under any credential-starved condition.

## Provenance

**Mixed provenance.** Original lineage: Swath's private prior-art research — a
one-line "Others" catalog entry in a survey document ("mostly transfer-focused,
similar shape to s5cmd"), plus the tool's own public GitHub repository (name,
author, language/license, confirmed directly against the repo). No dedicated
source-level investigation of s3kor existed in Swath's research.

The `groundwork/s3kor` branch (2026-07-17) adds **firsthand** material derived
independently of those notes: a full read of the listing source at v0.0.37
(`844fe3d`), a from-source container build, and two capability smoke receipts.
Firsthand facts on this page — the version reviewed, the `--all-versions`
correction, the capability finding (no unsigned listing; session-build panic),
the serial-listing source confirmation, the concurrency race, and the doc-drift
— come from that branch, not the inherited notes. The dormancy finding is also
groundwork-derived, but it is a **dated third-party observation** ([3P]
commits/releases/issues, 2026-07-17), not a source read — labeled as such in the
ledger (N6), not counted among the firsthand *source* facts. The inherited
mechanism/analogy claims (serial-by-analogy-to-s5cmd, "collapses into s5cmd")
are reconciled row-by-row in `research/reconciliation.md`; the source-and-run
write-up is `research/report.md`.

## Receipts

- `receipts/smoke/_capability/list/receipt.md` (+ `run.meta`, `stdout.txt`,
  `stderr.txt`) — anonymous `ls`, credential-starved: startup panic, exit 2,
  0 S3 requests. Image
  `s3kor@sha256:b021869dfa78b7af85506a5d566ec6c7e7ed49d940b20d9e110a04fa5006f37c`,
  bucket `noaa-normals-pds`, manifest snapshot 2026-07-17.
- `receipts/smoke/_capability/list-versions/receipt.md` (+ payloads) —
  anonymous `ls --all-versions`, identical panic.

Both are **capability** receipts (underscore dir): they settle that s3kor
cannot list anonymously; they carry no listing verifier result (no output was
produced to verify — `n/a — capability probe`). Non-mode evidence
(`_build/first-exec.txt`, `_adapter/self-test.txt`) is `[OBS]` — direct runs,
not wrapper-recorded. See `running.md` for the full reproduction detail.
