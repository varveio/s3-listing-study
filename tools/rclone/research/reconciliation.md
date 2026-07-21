# rclone — reconciliation of the inherited dossier

Walks **every claim** in the inherited dossier (`tools/rclone/README.md`) against
this phase's independent findings (`research/report.md` + committed smoke
receipts). Verdicts: **Corroborated** (independent work found the same) /
**Contradicted** (found otherwise, both sides shown) / **Unaddressed** (not
touched — stays an open hypothesis) / **Settled by smoke run** (a committed
receipt decides it). Evidence labels as in the report. Pinned checkout
`rclone` @ `v1.74.4` / `5bc93a2a7ab0ebd0a11352bc4968eabeffb18027`.

**Receipt-promotion rule honoured:** source reading never promotes a claim past
`VERIFIED: no`; only a committed smoke receipt does, and only at smoke scale.
Anything scale-dependent (OOM, 7 GB RSS, 100M-object behaviour) is **not**
settleable here and stays an open hypothesis regardless of how much source I read.

## Mechanism claims

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| M1 | "Listing is an internal precursor to sync, not a product surface" (framing) | Unaddressed | Editorial framing, not a falsifiable behavioural claim. rclone does expose listing as first-class subcommands (`lsjson`/`lsf`/`ls`/`lsl`/`lsd`) [DOC], which sits in mild tension with the framing, but nothing to verify. |
| M2 | Two distinct listing modes: delimiter-recursive walk **and** flat `ListR` | **Settled by smoke run** (both ran) + Corroborated — **but the mode *selector* is corrected** | Two distinct S3 request patterns both traced: flat `ListR` [RUN receipts/smoke/recursive-fastlist PASS, _capability/debug trace] and the genuine per-directory walk [RUN receipts/smoke/recursive-walk PASS 9841/9841, _capability/walk-debug trace: 13 `delimiter=%2F` requests]. **Correction (round 2):** for `ls*` commands the two are NOT selected by `--fast-list`. `lsjson -R` calls `walk.ListR` directly [SRC fs/operations/lsjson.go:248 @ 5bc93a2a7], which uses the flat backend `ListR` whenever `maxLevel<0` regardless of `--fast-list` [SRC fs/walk/walk.go:149-163 @ 5bc93a2a7]; the walk is reached only via `--disable ListR`/`--max-depth`. The old `recursive-hierarchical` receipt (`lsjson -R` no `--fast-list`) was therefore a THIRD flat listing, not a walk — annotated, not counted as the walk. |
| M3 | `--fast-list` omits the delimiter, whole tree as **one flat, still-serial** call chain | **Settled by smoke run** | Delimiter left nil when recursing [SRC :2428-2432]; `-vv --dump headers` shows a single serial `list-type=2` chain paged by `continuation-token` [RUN receipts/smoke/_capability/debug]. (The flat shape is right; note `--fast-list` is not what selects it for `lsjson -R` — see M2.) |
| M4 | "fewer API calls, more RAM, because nothing is discarded until the listing finishes" | Corroborated (trade-off, docs) / Unaddressed (accumulate-vs-stream at scale) | Docs state "uses more memory but fewer transactions" [DOC `--fast-list`]. But the strong "nothing discarded until finish" is a memory-model-at-scale claim: the S3 `ListR` streams via `list.NewHelper` tranche flush [SRC :2745-2764], while `--fast-list` retains a walker `dirMap` [SRC fs/walk/walk.go:256-346]. Whether that means "whole listing resident" only bites at scale — **not settleable at smoke** (~70 MB for 148,917 keys [RUN recursive-fastlist]). |
| M5 | Neither mode shards the keyspace; "fast" = fewer round trips, not parallelism | **Settled by smoke run** (both patterns now traced) | Flat `ListR` is a single serial `list-type=2` chain, no sharding — traced [RUN _capability/debug]. The genuine walk's "parallelism only across directories, none intra-prefix" is now **also traced**: 13 `delimiter=%2F` requests, one per directory, `access/` itself paged serially in 10 `continuation-token` requests — no intra-directory sharding [RUN _capability/walk-debug]; the checker-bounded cross-directory fan-out is [SRC fs/walk/walk.go:380,393 @ 5bc93a2a7]. (Round 1 had this as `[SRC]`-only because the sole "hierarchical" receipt was the mislabeled flat run; the genuine walk receipt closes that gap.) |
| M6 | The pacer is **"AIMD on delay"** — additive-increase/multiplicative-decrease reacting to **response latency, not error signals** | **Contradicted** — **anchors corrected (round 2)** | The S3 backend uses the **`S3` pacer calculator**, `pacer.NewS3(...)` [SRC lib/pacer/pacers.go:220 (struct), :233 (NewS3) @ 5bc93a2a7], whose `Calculate` is at [SRC lib/pacer/pacers.go:270-294 @ 5bc93a2a7] — NOT the `Default` calculator at `:42-101` that round 1 mis-cited. `S3.Calculate` keys entirely on **error/retry state** (`state.LastError` via `IsRetryAfter`, and `state.ConsecutiveRetries`), never on latency. On a retry it **multiplicatively increases** sleep (`SleepTime<<attackConstant / (2^attackConstant−1)`, attackConstant=1, capped at `maxSleep`=2s); on success it **decays** sleep and, crucially, **drops it to ZERO once the decayed value falls below `minSleep`** — `if sleepTime < c.minSleep { sleepTime = 0 }` [SRC :289-293 @ 5bc93a2a7]. This is the S3-specific "no delay at all between successful calls" behaviour [SRC :212-217]; it is NOT the `Default` calculator's "floor at `minSleep`". So it reacts to *explicit error signals* — the exact opposite of "on delay / not error" — and is not additive-increase. |
| M7 | The pacer is an "adaptive **concurrency** mechanism … almost nothing else has one" | Contradicted (in part) / Corroborated (it exists, is distinctive) | It adapts **inter-request sleep** (a rate/backoff control), not concurrency; for listing the calls are serial regardless [SRC :2476, pacers.go:270-294]. That a per-call adaptive backoff exists and is somewhat distinctive is fair; "concurrency" is the wrong word. |

## Modes / tunables table

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| T1 | `lsf`/`lsjson` default (no `--fast-list`) = delimiter-recursive walk, a **distinct** mode | **Corrected** — the dossier's premise is wrong | `lsjson`/`lsf` default (no `--fast-list`) is **NOT** a delimiter-recursive walk; a plain `-R` is the flat `ListR` [SRC fs/operations/lsjson.go:248, fs/walk/walk.go:149-163 @ 5bc93a2a7]. The genuine delimiter-recursive walk IS a distinct mode but must be forced with `--disable ListR`/`--max-depth` — smoked as recursive-walk [RUN recursive-walk PASS, _capability/walk-debug: distinct `delimiter=%2F` per-directory pattern]. |
| T2 | `lsf`/`lsjson --fast-list` = flat `ListR` | **Settled by smoke run** | [RUN recursive-fastlist, lsf] |
| T3 | `--transfers`/`--checkers` "mainly transfer-oriented"; the no-sharding claim "predicts they shouldn't" affect pure listing | **Contradicted** (for `--checkers`, on the genuine walk) / Corroborated (for `--transfers`) / **evidence re-anchored (round 2)** | `--checkers` **does** affect pure listing — but only the **genuine hierarchical walk**, where it bounds the concurrent-directory fan-out (`chan listJob, ci.Checkers`, default 8) [SRC fs/walk/walk.go:380,393, fs/config.go:60-61], now RUN-traced as genuine per-directory fan-out [RUN recursive-walk, _capability/walk-debug]. It is **inert on the flat `ListR`** — which is what a plain `lsjson -R` runs, so `--checkers` does nothing there. **Round-2 correction:** the prior evidence was the `recursive-hierarchical` receipt, but that run was a flat `ListR` where `--checkers 4` was inert, so it supported nothing. Verdict stands, re-anchored to the genuine walk receipt + [SRC]; that a *non-default checker count* moves wall-clock/request timing is a benchmark-phase sweep item, not yet run. `--transfers` (default 4) governs transfers only [SRC fs/config.go:65-66]. See W1. |
| T4 | `--s3-list-chunk` = page size for `ListObjectsV2` | **Corroborated** (default observed; tunability **not** smoked) | Default 1000 [SRC :426-434]; the wire trace confirms the **default** `max-keys=1000` was sent [RUN _capability/debug] — no non-default value was run, so the receipt supports the observed parameter, **not** the flag's effect on request count. Flagged for the benchmark sweep. |
| T5 | `--low-level-retries`, `--tpslimit`, `--tpslimit-burst` = pacer/rate controls | Corroborated (flags exist) + clarification | All three are real global flags [DOC `rclone help flags`]. Clarification: `--tpslimit`/`-burst` is a **separate** token-bucket transactions-per-second limiter, distinct from the S3 backoff pacer of M6; `--low-level-retries` bounds retry attempts. Not smoked (rate-limiting behaviour is a benchmark/replay concern). |
| T6 | Output `lsf` vs `lsjson`; **no Parquet** either way (capability gap) | **Settled by smoke run** (formats) / Corroborated (no Parquet) | `lsjson`, `lsf` (incl. `--csv`) exercised/observed [RUN lsf, delimiter-shallow; _build help]; `lsf --help` shows CSV, no Parquet output exists in any list command [DOC]. |
| T7 | Constrained-memory run (`--fast-list` under a cgroup cap, scaled bucket) | Unaddressed | Explicitly a scale test — deferred to the benchmark phase. |

## Claimed numbers & weaknesses (the high-stakes block)

| # | Inherited claim | Verdict | Evidence / provenance preserved |
| --- | --- | --- | --- |
| W1 / #1 | Neither mode shards the keyspace — no intra-prefix key-range parallelism; the walk only parallelizes across discovered directories | **Settled by smoke run** (both patterns traced) | Flat `ListR`: serial single chain per prefix, no sharding, traced [RUN _capability/debug]. The genuine walk's across-directory-only parallelism is now **also traced**: one `delimiter=%2F` LIST per directory, and the big `access/` directory paged serially in 10 `continuation-token` requests — no intra-directory sharding [RUN recursive-walk, _capability/walk-debug]. The checker-bounded cross-directory fan-out is [SRC fs/walk/walk.go:380,393]. (Round 1 was `[SRC]`-only for the walk because the sole receipt was the mislabeled flat run; the genuine walk receipt closes it.) Scoped to v1.74.4 at smoke scale. |
| W2 / #2, N1 | `--fast-list` holds the entire listing in RAM and **OOMs at ~100M objects at root** | **Unaddressed** (scale) — **experiment design corrected (round 2)** | Needs a cgroup-capped, scaled-bucket run. Smoke peak was ~70 MB for 148,917 keys [RUN recursive-fastlist] — a data point, **not** a refutation. **Design caveat (round 2):** the cited failures (#7966/#7974) are about the **`sync`** path on **v1.67-era** code, where the whole directory was scanned into RAM before transferring. The pinned **v1.74.4 carries the v1.70 `--list-cutoff` fix**, which sorts directory listings **on disk** (external sort) once a directory exceeds 1,000,000 entries [SRC fs/config.go:281 (`list_cutoff`, default 1e6), fs/list/sorter.go:26 + `github.com/lanrat/extsort` @ 5bc93a2a7; DOC faq.md]. So a pure-listing cgroup test of THIS version tests a code path that has already been changed relative to the allegation. What such a test **can** establish: whether `lsjson --fast-list` (a streaming path — entries flushed via `list.NewHelper`, see M4) stays memory-bounded at scale, and its RSS/exit-code under a cap. What it **cannot** establish: the original sync-path OOM or its exit behaviour — that needs a **sync-shaped** workload (not `lsjson`), and even then it speaks to v1.74.4, not the v1.67 the reporter ran. Claim stays `VERIFIED: no` with this design caveat attached. |
| W3 / #3, N2 | **OOM-killed runs allegedly exit 0** — silent failure reported as success. The study's highest-stakes claim. | **Unaddressed** (scale) — **not settleable by smoke** | Requires reproduction under a cgroup memory cap with a recorded exit code (benchmark phase). Provenance, **corrected exactly (round 2)**: the two cited issues are **two distinct issue records by the same reporter (`zackees`), filed five days apart about the same S3 datalake / directory-reorganization scenario, and BOTH allege exit-0.** #7966 "rclone returning exit 0 for out of memory OS kill" (closed, 2024-07-20, on rclone v1.67.0) is the primary exit-0 report; #7974 "Excess memory use when syncing millions of files in one directory" (closed, 2024-07-25) is chiefly an excess-memory/OOM report **but explicitly repeats the exit-0 allegation** ("when the rclone process gets a kill signal, it will **exit 0**") and links #7966 [3P https://api.github.com/repos/rclone/rclone/issues/7966 & /7974, accessed 2026-07-17]. So they are neither "one report described twice" (round-1 error in the opposite direction resolved this) nor "#7974 does not allege exit 0" (round-1 overcorrection — **wrong**): they are two distinct records, same reporter and scenario, both alleging exit-0. Both concern the **`sync`** path on **v1.67-era** code; the pinned v1.74.4 postdates that and carries the v1.70 `--list-cutoff` external-sort fix (see W2). This corrects the citation only; it does **not** settle the behavioural claim, which stays `VERIFIED: no`. Every run this phase exited 0 legitimately (no OOM induced); nothing here reproduces or refutes the allegation. |
| W4 / #4, N3 | **>3h stall before transfers start** at 100M objects (listing completes before transfer begins) | **Unaddressed** | About the sync/copy pipeline, not a pure-listing command. [3P] #5859 is an **open feature request** ("Start transfer of objects while paginating"), i.e. the behaviour is acknowledged-by-design, not a bug report of a stall [3P /issues/5859]. Timing-to-first-transfer at scale is a benchmark test. |
| W5 / #5, N4 | **~7 GB resident** on large listings | **Unaddressed** (scale) | RSS at scale — benchmark. [3P] #2157 "rclone using too much memory" (closed). Smoke RSS ~70 MB is not comparable. |
| W6 / #6, N5 | **No LIST crash-resume** — a killed listing restarts from zero | **Unaddressed** (source observation attached) | No checkpoint/cursor state exists in `Fs.list` [SRC :2419-2609 — observation, not proof]; positive proof needs the `SIGKILL`-and-resume protocol (benchmark). Left an open hypothesis with the source observation recorded. |
| W7 / #7 | No Parquet output (CSV/JSON only) | Corroborated | [DOC `lsf --help` shows `--csv`; no Parquet path in any list command] — capability gap, not a behavioural claim. |
| W8 / #8 | The pacer is "AIMD on delay" (falsifiable by a latency/503 sawtooth trace) | **Contradicted** | See M6 — it is error/retry-driven, not delay-driven [SRC lib/pacer/pacers.go:270-294 (`S3.Calculate`), :233 (`NewS3`)]. A replay-proxy trace would still be worth capturing to characterise the exact sawtooth, but the "on delay" mechanism is already wrong from source. |

## Claimed strengths

| Claim | Verdict | Evidence |
| --- | --- | --- |
| Mature, 58k+ stars | Corroborated | 58,380 stars [3P api.github.com/repos/rclone/rclone, 2026-07-17] |
| Broad backend support | Corroborated (context) | ~70 backends [DOC] |
| Structured output `lsf`/`lsjson` | **Settled by smoke run** | [RUN lsf, delimiter-shallow] |
| Genuine adaptive rate-control (pacer) | Corroborated (exists) / see M6 for the mischaracterised mechanism | [SRC lib/pacer/pacers.go] |

## Independent findings NOT in the dossier (divergence to record)

1. **HEAD-per-object modtime/mimetype footgun (new).** Default `lsjson`/`lsl` on
   S3 call `ModTime`/`MimeType`, each of which does a **HEAD per object**
   (`readMetaData`) unless suppressed — turning a ~149-LIST listing into
   148,917 HEADs [SRC backend/s3/s3.go ModTime & MimeType, fs/operations/lsjson.go:181-185 @ 5bc93a2a7].
   Proper listing needs `--use-server-modtime --no-mimetype`. The dossier does not
   mention it; it materially affects any "listing" benchmark of rclone. Mechanism
   is [SRC] (I never ran rclone the wrong way — no receipt for the HEAD storm); the
   suppressed-path correctness is [RUN all lsjson receipts, fields=0].
2. **Anonymous = absence of credentials (new).** rclone has **no
   `--no-sign-request` flag**; it goes unsigned when no keys are set and
   `env_auth` is false (installs `aws.AnonymousCredentials{}`)
   [SRC backend/s3/s3.go:1508-1511]. Settled by smoke: every mode ran unsigned
   under the wrapper's credential-starved `auth=anonymous` [RUN all receipts].
3. **storage_class rides along free (new).** `lsjson` exposes `.Tier` straight
   from the list response (no HEAD); verified equal to the manifest's
   `StorageClass` on all 148,917 keys [RUN recursive-fastlist, fields=0].
4. **Legacy `--s3-list-version 1` mode (new).** A distinct API (`ListObjects` v1)
   the dossier's table omits; smoked PASS [RUN listv1].
5. **`lsjson`/`ls*` `-R` ignores `--fast-list` — the flat `ListR` is the default
   (new, round 2).** The dossier's model ("default = delimiter walk, `--fast-list`
   = flat") is wrong for the listing commands: `lsjson -R` calls `walk.ListR`
   directly [SRC fs/operations/lsjson.go:248], which selects the flat backend
   `ListR` whenever `maxLevel<0`, never consulting `--fast-list`
   [SRC fs/walk/walk.go:149-163]. The genuine hierarchical walk exists but must be
   forced (`--disable ListR`/`--max-depth`); smoked as recursive-walk PASS with the
   per-directory `delimiter=%2F` shape traced [RUN recursive-walk, _capability/walk-debug].
   This corrects M2/M3/T1/T3 above.
6. **Live third-party bucket drift observed (new, round 2, event-record).** NOAA
   re-uploaded `normals-hourly/` and `normals-monthly/` objects mid-session
   (mtime → today); the harness re-list returned `DRIFT` — **not a tool finding**.
   The genuine walk was verified on the still-un-drifted
   `normals-annualseasonal/1981-2010/` scope. Flagged to the manifest owner.

## Verdict counts (updated round 2)

- **Settled by smoke run:** 8 — M2 (both patterns, selector corrected), M3, M5
  (both patterns now traced), T2, **recursive-walk / genuine hierarchical walk**
  (PASS 9841/9841 + `delimiter=%2F` trace), T6-formats, W1 (both patterns traced),
  strengths-output — plus new findings 2 & 3. **T4 removed** (downgraded to
  Corroborated: only the default `max-keys=1000` was observed, no non-default value
  ran). **T1 removed** (its premise is wrong — recategorized as Corrected).
- **Corrected (round 2):** T1 (default is the flat `ListR`, not a walk), M2's mode
  *selector* (not `--fast-list`), and the pacer anchors in M6/W8 (the `S3`
  calculator at `pacers.go:270-294`, decays to zero below `minSleep`).
- **Corroborated (non-smoke):** M4-tradeoff, T4 (default observed, tunability not
  smoked), T5, W7, strengths (stars/backends/pacer-exists).
- **Contradicted:** 3 (M6, T3-for-`--checkers` — re-anchored to the genuine walk,
  W8) — all with positive [SRC] evidence.
- **Unaddressed (open hypotheses, scale/benchmark):** W2/N1 (with the round-2
  version-delta design caveat), W3/N2 (exit-0, highest-stakes), W4/N3, W5/N4,
  W6/N5, T7, M1.

## Routed to the orchestrator / owner

- **Claims about other tools or S3 itself surfaced here:** none new — the pacer,
  HEAD-footgun, and anonymous findings are rclone-specific. `docs/open-questions.md`
  lists rclone only under the Go-language grouping (line 111); no rclone-specific
  cross-cutting claim needed editing.
- **Benchmark-phase gates:** the exit-0-on-OOM reproduction (cgroup cap + scaled
  bucket + recorded exit code) remains the single highest-stakes open item — but
  its design must honour the round-2 caveats (W2/W3): the allegation is about the
  **`sync`** path on **v1.67-era** code, and the pinned v1.74.4 carries the v1.70
  `--list-cutoff` external-sort fix, so a pure-`lsjson` cgroup test cannot speak to
  it; a faithful test needs a sync-shaped workload and is still bound to v1.74.4,
  not the reporter's v1.67. The `--fast-list` memory curve, 7 GB RSS, >3h-stall,
  and SIGKILL-resume tests all require scale this smoke cannot provide.
- **Manifest re-baseline (new, round 2):** NOAA drift on `normals-hourly/` and
  `normals-monthly/` (mtime) is a manifest-owner decision; the 2026-03-16-mtime
  manifest is now stale for those prefixes. Pre-drift receipts remain valid as
  recorded; new runs on drifted prefixes will read `DRIFT` until a re-baseline.
