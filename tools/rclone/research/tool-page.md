# rclone

> **Historical landing page (2026-07-20, capsule migration).** This is the full
> pre-restructure landing page. Any `current-state` wording below is historical
> as of the date it records and is superseded by the root README and `data/`.
> Only this banner and link targets changed; body prose and evidence
> qualifications are preserved.

rclone is a general-purpose multi-cloud sync/transfer tool ("rsync for cloud
storage"); S3 is one of ~70 backends, and listing is exposed through the `ls*`
command family (`lsjson`/`lsf`/`ls`/`lsl`/`lsd`) over a common backend
`List`/`ListR` interface. This page consolidates a source-and-run groundwork pass
(v1.74.4, commit `5bc93a2a7`, 2026-07-17) that read the pinned source and smoked
every listing mode that changes the S3 **request pattern** (flat `ListR`, the
genuine hierarchical walk, delimiter-shallow, legacy v1, and `lsf`) against the
registered smoke bucket, plus a round-2 cross-model anchor audit that ran the
genuine hierarchical walk. The `ListObjectVersions` API was not smoked (the bucket
is unversioned); `ls`/`lsl`/`lsd` are output-format variants over the same requests
and were not separately timed. Full report:
`research/report.md`; row-by-row reconciliation: `research/reconciliation.md`;
critical cross-checks and their resolutions: `research/codex-review.md`.

|  |  |
|---|---|
| **Repo** | <https://github.com/rclone/rclone> |
| **Language** | Go (`go 1.25.0` per `go.mod`; upstream image builds with go1.26.5) |
| **License** | MIT |
| **Version tested** | `v1.74.4` (commit `5bc93a2a7ab0ebd0a11352bc4968eabeffb18027`) |
| **Tier** | 1 — included in the planned comparative runs |
| **Testability** | Trivial to install; the memory and exit-code questions need a constrained-memory harness (cgroup cap) and a large bucket. Groundwork ran the pinned container image (v1.74.4 by digest, see `running.md`). |

## What we saw

**rclone lists anonymously and correctly at smoke scale.** Every verifier-checked
mode PASSed (0 duplicates / 0 missing / 0 extra / 0 field mismatches), and the full
bucket re-listed **byte-exact** against the manifest (148,917 keys). What the
groundwork established, receipt-backed:

- **Two S3 request patterns — but for the `ls*` commands they are NOT selected by
  `--fast-list`.** A plain `lsjson -R` calls `walk.ListR` directly, which uses the
  **flat, undelimited `ListR`** whenever the recursion is unbounded and **never
  consults `--fast-list`** [SRC `fs/operations/lsjson.go:248`, `fs/walk/walk.go:149-163`
  @ 5bc93a2a7]. The genuine **per-directory hierarchical walk** exists but must be
  **forced** with `--disable ListR` (or `--max-depth`). Both are now run and traced
  — see `mechanism.md`.
- **Pagination is a serial cursor-chained loop** within any one prefix: page N+1
  needs page N's `continuation-token` (v2) / `NextMarker` (v1). No keyspace
  sharding in either pattern [SRC `backend/s3/s3.go:2472` @ 5bc93a2a7] [RUN
  `_capability/debug`, `_capability/walk-debug`].
- **`--fast-list` is a flat undelimited `ListR`** (one continuation chain, no
  delimiter) — traced [RUN `_capability/debug`].
- **The genuine hierarchical walk is VERIFIED: yes at smoke scale** — run as
  `recursive-walk` (`--disable ListR`), **PASS 9841/9841**, with a wire trace of
  **13 `delimiter=%2F` requests, one per directory** [RUN
  `receipts/smoke/recursive-walk`, `_capability/walk-debug`].
- **The HEAD-per-object `lsjson` footgun.** Default `lsjson`/`lsl` compute
  `ModTime`/`MimeType`, each of which does a **HEAD per object** unless suppressed —
  turning a ~149-page listing into 148,917 HEADs. Proper listing **must** pass
  `--use-server-modtime --no-mimetype` [SRC `backend/s3/s3.go` ModTime/MimeType,
  `fs/operations/lsjson.go:181-185` @ 5bc93a2a7]. See `running.md`.
- **Anonymous = the absence of credentials.** rclone has **no `--no-sign-request`
  flag**; it installs `aws.AnonymousCredentials{}` when no keys are set and
  `env_auth` is false [SRC `backend/s3/s3.go:1508-1511` @ 5bc93a2a7]. Every smoked
  mode ran unsigned [RUN all receipts].

**The reported exit-0-on-OOM behavior stays `VERIFIED: no`.** The corrected
source history and version caveat are below:

> The two cited issues are **two distinct issue records by the same reporter
> (`zackees`), filed five days apart about the same S3 datalake /
> directory-reorganization scenario, and both report exit 0.** #7966 "rclone
> returning exit 0 for out of memory OS kill" (closed, 2024-07-20, on rclone
> v1.67.0) is the primary exit-0 report; #7974 "Excess memory use when syncing
> millions of files in one directory" (closed, 2024-07-25) is chiefly an
> excess-memory/OOM report but **explicitly repeats the exit-0 report** ("when
> the rclone process gets a kill signal, it will **exit 0**") and links #7966 [3P
> api.github.com/repos/rclone/rclone/issues/7966 & /7974, accessed 2026-07-17].
> They are neither "one report described twice" nor "#7974 does not allege exit 0"
> — both earlier framings were wrong. Both concern the **`sync`** path on
> **v1.67-era** code; the pinned v1.74.4 postdates that and carries the v1.70
> `--list-cutoff` external-sort fix, so a pure-`lsjson` cgroup test of this version
> cannot speak to the sync-path report. **This corrects the citation and
> attaches the version-delta design caveat, nothing more: it does not settle the
> reported behavior, which stays `VERIFIED: no`.** Every run this phase exited 0
> normally (no OOM induced); nothing here reproduces or refutes the report. Until
> we reproduce it under a cgroup memory cap **with a scaled bucket** and record the
> exit code, we describe it only as a third-party report about v1.67-era `sync`.

## Notes, questions, and observations

Every claim inherited from the original (secondhand) tool page, checked against the
2026-07-17 groundwork (`research/report.md`) and reconciled row-by-row in
`research/reconciliation.md`. Status values map onto the reconciliation's states:
**CONFIRMED** (receipt-backed — reconciliation "Settled by smoke run"),
**CORRECTED** (found different from the original; both sides shown, with positive
source evidence — reconciliation "Contradicted"), **CORROBORATED** (source/docs/3P
support but **not** receipt-settled — treated as `VERIFIED: no` for promotion, per
`AGENTS.md`), **VERIFIED: no** (not settled by any receipt — a hypothesis, however
corroborated; reconciliation "Unaddressed"), **UNVERIFIABLE** (cannot be tested on
hand). Per `AGENTS.md`, source reading alone never promotes a claim past
`VERIFIED: no`. Reconciliation IDs (M/T/W) are given so every row traces back.

| Claim | Status | Evidence |
| --- | --- | --- |
| M1 · "Listing is an internal precursor to sync, not a product surface" (framing) | **VERIFIED: no** — editorial, not falsifiable; rclone does expose `ls*` as first-class subcommands, in mild tension with the framing | [DOC] |
| M2 · Two distinct listing modes exist (delimiter-recursive walk **and** flat `ListR`) | **CONFIRMED** (both run + traced) / **CORRECTED** — the *selector* is not `--fast-list`: `lsjson -R` is the flat `ListR` regardless; the walk needs `--disable ListR`/`--max-depth` | [SRC `fs/operations/lsjson.go:248`, `fs/walk/walk.go:149-163`] [RUN `recursive-fastlist`, `recursive-walk`, `_capability/debug`, `_capability/walk-debug`] |
| M3 · `--fast-list` = one flat, still-serial, undelimited chain | **CONFIRMED** | [SRC `:2428-2432`] [RUN `_capability/debug`] |
| M4 · "fewer API calls, more RAM, nothing discarded until finish" | **CORROBORATED** (the fewer-calls/more-RAM tradeoff — docs) / **VERIFIED: no** (accumulate-vs-stream at scale — the S3 `ListR` streams via `list.NewHelper`) | [DOC `--fast-list`] [SRC `:2745-2764`] |
| M5 · Neither pattern shards the keyspace; "fast" = fewer round trips, not parallelism | **CONFIRMED** (both patterns traced serial/per-directory, no intra-prefix sharding) | [RUN `_capability/debug`, `_capability/walk-debug`] [SRC `fs/walk/walk.go:380,393`] |
| M6 · Pacer is "AIMD on delay" (reacts to latency, not errors) | **CORRECTED** — the S3 pacer keys entirely on **error/retry state**, never latency; on success it decays sleep **to zero below `minSleep`** (not "toward" it) | [SRC `lib/pacer/pacers.go:220,233,270-294`] |
| M7 · Pacer is an adaptive **concurrency** mechanism | **CORRECTED** — it adapts **inter-request sleep** (backoff), not concurrency; listing calls are serial regardless | [SRC `lib/pacer/pacers.go:270-294`, `:2476`] |
| T1 · `lsjson`/`lsf` default (no `--fast-list`) = delimiter-recursive walk, a distinct mode | **CORRECTED** — the premise is wrong: a plain `lsjson -R` is the **flat `ListR`**, not a walk. The walk is a distinct mode but must be forced | [SRC `fs/operations/lsjson.go:248`, `fs/walk/walk.go:149-163`] [RUN `recursive-walk`] |
| T2 · `lsjson --fast-list` = flat `ListR` | **CONFIRMED** | [RUN `recursive-fastlist`, `lsf`] |
| T3 · `--transfers`/`--checkers` shouldn't affect pure listing | **CORRECTED** (for `--checkers` — receipt-backed) / **CORROBORATED** (for `--transfers` — source) — `--checkers` bounds the **genuine walk's** per-directory fan-out (run-traced); inert on the flat `ListR`. `--transfers` touches only transfers | [SRC `fs/walk/walk.go:380,393`, `fs/config.go:60-66`] [RUN `recursive-walk`, `_capability/walk-debug`] |
| T4 · `--s3-list-chunk` = page size for `ListObjectsV2` | **CONFIRMED** (default `max-keys=1000` observed on the wire) / **VERIFIED: no** (the flag's *effect* on request count — no non-default value ran) | [SRC `:426-434`] [RUN `_capability/debug`] |
| T5 · `--low-level-retries`/`--tpslimit`/`--tpslimit-burst` = pacer/rate controls | **CORROBORATED** (flags are real — docs) + clarified: `--tpslimit`/`-burst` is a **separate** token-bucket TPS limiter, distinct from the S3 backoff pacer of M6 | [DOC `rclone help flags`] |
| T6 · Output `lsf` vs `lsjson`; no Parquet | **CONFIRMED** (formats run) / **CORROBORATED** (no Parquet — capability gap, docs) | [RUN `lsf`, `delimiter-shallow`] [DOC] |
| T7 · Constrained-memory run (`--fast-list` under a cgroup cap) | **VERIFIED: no** — a scale test, deferred to the benchmark | — |
| W1 · No intra-prefix key-range parallelism; the walk parallelizes only across directories | **CONFIRMED** (both patterns traced; `access/` paged serially in 10 requests, no intra-directory sharding) | [RUN `_capability/debug`, `_capability/walk-debug`] [SRC `fs/walk/walk.go:380,393`] |
| W2 · `--fast-list` holds the whole listing in RAM and OOMs at ~100M at root | **VERIFIED: no** — scale; smoke peaked ~70 MB for 148,917 keys, not a refutation. Design caveat: the streaming `--fast-list` path ≠ the v1.67-era `sync` path the report cites; v1.74.4 carries the v1.70 `--list-cutoff` external-sort fix | [SRC `fs/config.go:281`, `fs/list/sorter.go:26`] [RUN `recursive-fastlist`] |
| W3 · OOM-killed runs reportedly exit 0 | **VERIFIED: no** — provenance corrected (see What we saw, in full); needs a **sync-shaped** workload under a cgroup cap + recorded exit code, and even then speaks to v1.74.4, not the reporter's v1.67 | [3P issues/7966 & /7974] |
| W4 · >3h stall before transfers start at 100M | **VERIFIED: no** — about the sync/copy pipeline, not pure listing; #5859 is an **open feature request**, not a bug report | [3P issues/5859] |
| W5 · ~7 GB resident on large listings | **VERIFIED: no** — RSS at scale (benchmark); #2157, closed | [3P issues/2157] |
| W6 · No LIST crash-resume — a killed listing restarts from zero | **VERIFIED: no** — source shows no checkpoint state in `Fs.list`, but positive proof needs the SIGKILL-and-resume protocol | [SRC `:2419-2609` — observation, not proof] |
| W7 · No Parquet output (CSV/JSON only) | **CORROBORATED** — capability gap (docs) | [DOC `lsf --help`] |
| W8 · Pacer is "AIMD on delay" (latency/503 sawtooth) | **CORRECTED** — see M6: error/retry-driven, not delay-driven | [SRC `lib/pacer/pacers.go:270-294`] |
| Strength · Mature, 58k+ stars | **CORROBORATED** (58,380 stars — 3P) | [3P api.github.com/repos/rclone/rclone] |
| Strength · Broad backend support (~70 backends) | **CORROBORATED** (context — docs) | [DOC] |
| Strength · Structured output `lsf`/`lsjson` | **CONFIRMED** | [RUN `lsf`, `delimiter-shallow`] |
| Strength · Genuine adaptive rate-control (pacer) | **CORROBORATED** (it exists, is distinctive — source) / see M6 for the mischaracterised mechanism | [SRC `lib/pacer/pacers.go`] |

### Additive rows (entered via groundwork/review, not the inherited tool page)

These were not in the inherited tool page; they came from the source-and-run report and
the round-2 audit, and are named here per the "additive rows named in the footer"
rule.

| Claim (additive) | Status | Evidence |
| --- | --- | --- |
| HEAD-per-object modtime/mimetype footgun — default `lsjson`/`lsl` HEAD every object unless suppressed | **CONFIRMED** (the *suppressed correct path* — receipt-backed on all lsjson receipts, `fields=0`) / **CORROBORATED** (the HEAD-storm magnitude itself — source across three functions + inferred; never run, no receipt for the wrong way) | [SRC ModTime/MimeType/`lsjson.go:181-185`] [RUN all lsjson receipts] |
| Anonymous = absence of credentials; no `--no-sign-request` flag | **CONFIRMED** — every mode ran unsigned (no `Authorization` header) | [SRC `:1508-1511`] [RUN all receipts, `_capability/debug`, `_capability/walk-debug`] |
| storage_class (`.Tier`) rides along free from the list response (no HEAD) | **CONFIRMED** — verified equal to the manifest `StorageClass` on all 148,917 keys | [RUN `recursive-fastlist`, `fields=0`] |
| Legacy `--s3-list-version 1` (`ListObjects` v1) is a distinct API the tool page omits | **CONFIRMED** | [RUN `listv1`] |
| `lsjson`/`ls*` `-R` ignores `--fast-list` — the flat `ListR` is the default (round 2) | **CONFIRMED** (that `--disable ListR` flips a plain `-R` from flat to a delimited per-directory walk — receipt-backed) / with source that `walk.ListR` never reads `ci.UseListR`; the "plain `-R` (no `--fast-list`) is already flat" *runtime* check is `[OBS]` (an ad-hoc side-by-side trace, 0 `delimiter=` requests), not a committed `--dump headers` receipt | [SRC `fs/operations/lsjson.go:248`, `fs/walk/walk.go:149-163`] [RUN `recursive-walk`, `_capability/walk-debug`] [OBS trace of the old argv] |
| Live NOAA bucket drift observed mid-session (mtime on `normals-hourly/`+`normals-monthly/`) | **event record, not a tool claim** — harness re-list returned `DRIFT`; flagged to the manifest owner | [RUN harness re-list; `running.md`] |

No inherited claim was dropped: every M/T/W/strength row above traces to
`research/reconciliation.md`, which walks the original tool page row-by-row. The
six rows under **Additive rows** are named there as the divergences groundwork and
the round-2 audit added.

## Open hypotheses for the benchmark

Everything scale-dependent stays `VERIFIED: no` — no receipt settles it, and this
smoke bucket (148,917 broad-shallow keys) produces no comparative numbers. Each
carries its corrected experiment spec:

1. **`--fast-list` memory at scale / OOM cliff (W2).** Capture `peak_rss` +
   `cgroup_peak_mem` across a size/depth sweep on the streaming `--fast-list` path.
   **Design caveat:** this tests the *listing* path of v1.74.4, which streams
   (`list.NewHelper`) and carries the v1.70 `--list-cutoff` external-sort fix — it
   is **not** the v1.67-era `sync` path the allegation is about.
2. **Exit-0-on-OOM (W3).** Requires a **sync-shaped** workload (not
   `lsjson`) on a **scaled bucket** under a cgroup memory cap with a recorded exit
   code; and even a faithful reproduction speaks to v1.74.4, not the reporter's
   v1.67. Until we reproduce it, present it only as a third-party report with
   that version and workload context.
3. **>3h stall before first transfer at 100M (W4).** Timing-to-first-transfer at
   scale on the sync/copy pipeline — not a pure-listing command; #5859 is an
   acknowledged-by-design feature request.
4. **~7 GB RSS on large listings (W5).** RSS measurement during a scaled run.
5. **No LIST crash-resume (W6).** SIGKILL-and-resume protocol, same as every other
   tool; source shows no cursor state, but that is an observation, not a receipt.
6. **Constrained-memory `--fast-list` under a cgroup cap (T7).** The failure-mode
   harness the memory/exit-code claims live or die on.
7. **Hierarchical `--checkers` sweep.** At what `--checkers` does the genuine walk
   (`--disable ListR`) beat/lose the flat `ListR` in wall-clock and request count?
   Sweep `∈ {1,2,4,8,16,32}`. NB the walk must be **forced**; a plain `lsjson -R`
   is the flat path and ignores `--checkers`.
8. **`--s3-list-chunk` sweep `∈ {100,500,1000}`** (AWS caps at 1000): pages traded
   against per-request overhead (T4's tunability, unsmoked).
9. **v1 vs v2 API** wall-clock and request-count parity at scale (smoke saw both
   complete identically at 2,549 keys).
10. **CPU vs network** — capture CPU time alongside wall-clock to test any
    Go-language-bottleneck hypothesis for rclone's JSON/marshal path.

## Provenance

**Mixed lineage — preserved from the pre-consolidation tool page.** This page is not
uniformly secondhand, and says so per `AGENTS.md` provenance discipline:

- **Inherited (secondhand, never executed).** The behavioural hypotheses (M1–M8,
  W2–W8, the noted strengths) came from Swath's private prior-art research: a
  compiled reference note (citing rclone's own S3 docs, a forum thread on
  flat-structure list performance, and GitHub issues #7974/#5859/#2157); a separate
  survey document's tool-catalog entry on `--fast-list`; a concurrency note
  characterizing the pacer as "AIMD on delay"; and a **separate** internal note
  citing #7966 for an "80M at root" framing of the exit-0 claim — the source of the
  two-issue-number discrepancy. All secondhand; the Swath notes are now out of reach
  (this repo is the only surviving copy).
- **Not inherited — added from the tool's own docs/source.** The **Language** and
  **License** cells (Go, MIT) and the **modes/tunables flag names** were not in the
  inherited notes (which recorded only ~58.1k stars); they were added from the
  tool's docs/source and are now checked against the pinned checkout.
- **Firsthand (this groundwork + round-2 audit).** The **Version** pin, every `[SRC]`
  anchor, all `[RUN]` receipts (including the genuine walk), the pacer/`--checkers`/
  selector/exit-0 corrections, and the bucket-drift event record are firsthand —
  pinned source reading (`v1.74.4`/`5bc93a2a7`) and this repo's committed smoke runs.

Row-by-row lineage lives in `research/reconciliation.md` (every inherited observation,
its status, and supporting record) and `research/report.md` § 11 (sources). Those are the
immutable record; this page routes their conclusions.

## Pointers

- **`mechanism.md`** — the two request patterns and what actually selects them,
  serial pagination, the pacer at the corrected anchors, `--checkers`' real scope,
  `--list-cutoff` external sort (v1.70+), retry model, memory model, list APIs
  (v1/v2/versions), output contracts, source anchors.
- **`running.md`** — image pin by digest, every smoked mode with exact invocation
  and receipt (including the corrected status of the former "hierarchical" receipt
  and the new genuine-walk receipt), the HEAD-footgun flags, the mid-session drift
  event, reproduction, and the arch matrix.
- **`research/`** — `report.md` (source-and-run groundwork), `reconciliation.md`
  (every inherited observation, status, and supporting record), `codex-review.md`
  (the two separate review rounds and their resolved findings). These preserved
  files may use the project's older terminology; this page is the current summary.
- **`receipts/`** — committed smoke receipts (`recursive-fastlist` ×3,
  `recursive-walk`, the annotated `recursive-hierarchical`, `delimiter-shallow`,
  `listv1`, `lsf`) and capability probes (`_capability/debug`,
  `_capability/walk-debug`). Immutable read-only inputs to this page.
