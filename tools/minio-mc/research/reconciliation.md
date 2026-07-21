# Reconciliation — inherited `minio-mc` dossier vs independent groundwork

Walks **every** claim on the inherited dossier (`tools/minio-mc/README.md`,
seeded from swath's secondhand prior-art notes) against this agent's independent
groundwork (`research/report.md` + Stage C smoke receipts). Verdicts:
**Corroborated** / **Contradicted** / **Unaddressed** / **Settled by smoke run**.
Evidence labels as in the report; `[SRC]` anchors are against mc @
`7394ce0dd2a80935aded936b09fa12cbb3cb8096` and minio-go @ `v7.0.90`
(`68fb5ee339f2e3a798c14d12ca0e04c51f304d58`).

**Provenance reminder:** the dossier already carried a "Mixed provenance" callout
— its Language/License cells were firsthand (read from GitHub), everything else
secondhand. That labeling is preserved and extended below, not overwritten.

## Metadata cells

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| 1 | Repo `github.com/minio/mc` | **Corroborated** | Canonical upstream confirmed (AGPLv3 MinIO project, not a fork) [DOC github.com/minio/mc] |
| 2 | Language: Go (firsthand) | **Corroborated** | `go 1.23.0` [SRC go.mod:3]; binary go1.24.6 [RUN `mc --version`] |
| 3 | License: AGPL-3.0 (firsthand) | **Corroborated** | [SRC LICENSE:1 @ 7394ce0], [DOC GitHub API `license.spdx_id=AGPL-3.0`] |
| 4 | Version reviewed: **unknown** | **Corroborated (filled — editorial)** | Now pinned: `RELEASE.2025-08-13T08-35-41Z` @ `7394ce0` (latest release tag reachable from `master`) [DOC/SRC] |
| 5 | Tier 2; Testability "Trivial — prebuilt binaries/packages" | **Corroborated** | Official multi-arch image (`minio/mc`, amd64/arm64/ppc64le) + `dl.min.io` prebuilt binaries [RUN docker manifest inspect] |

## Mechanism

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| M1 | MinIO's own client, works against MinIO **and** generic S3 incl. AWS | **Generic-S3/AWS side Corroborated; MinIO-server side Unaddressed** | Anonymous listing of an AWS S3 bucket succeeded [RUN receipts/smoke/recursive-json]. No MinIO-server endpoint was exercised — that half is untested (see T3/routed items). |
| M2 | Listing surface is `mc ls --recursive` | **Settled by smoke run** (+ nuance) | `mc ls --recursive` lists the bucket completely [RUN recursive/recursive-json PASS]. Nuance: `mc find` is a *second* listing surface (see T4). |
| M3 | "Serial client-side iterator — whatever parallelism it benefits from is **server-internal**, not client fan-out" | **Corroborated (source + observation); not receipt-promoted** | Serial single-goroutine paginator, one `ListObjectsV2` at a time advanced by `continuation-token` [SRC minio-go api-list.go:100-165]; the curated `--debug` excerpt shows serial `list-type=2` request lines, each after the first carrying the prior `continuation-token` [OBS receipts/smoke/_capability/debug-trace]. **Framing note:** the evidence establishes only that the **client** issues no concurrent LIST requests and does no keyspace fan-out; it says **nothing** about how AWS or a MinIO server serves each request internally, so the dossier's "server-internal parallelism" claim is neither confirmed nor refuted here. Rigorous proof stays a replay-phase job → dossier stays `VERIFIED: no` for the parallelism character. |

## Modes / tunables to exercise

| # | Inherited item | Verdict | Evidence |
| --- | --- | --- | --- |
| T1 | `mc ls --recursive alias/bucket` (baseline) | **Settled by smoke run** | Full-bucket 148,917 keys, 0 missing/extra/dup [RUN recursive-json PASS; recursive PASS] |
| T2 | `mc ls --recursive --json` | **Settled by smoke run** | JSONL, exact size/etag/mtime/storageClass all matched manifest [RUN recursive-json PASS] |
| T3 | Endpoint **AWS S3 vs MinIO server** — "must test both" | **AWS side Settled; MinIO-server side Unaddressed** | AWS S3 fully exercised [RUN]. No MinIO-server endpoint exists in the study's registered buckets; standing up one is a benchmark-phase infra decision → **routed as open** (see below). |
| T4 | `mc find` — alternate traversal | **Settled by smoke run (dossier-prompted addition)** | Added at Stage D because the dossier named it. Smoked `find` + `find-json` on `normals-hourly/` (2,549 keys) — both PASS [RUN find-hourly, find-json-hourly]. Uses the **same serial List path** as `ls --recursive` [SRC cmd/find.go:275-284]; distinct output contract: alias-prefixed absolute keys, **no ETag even in `--json`**. **Completeness caveat:** `mc find` unconditionally skips `GLACIER` objects [SRC cmd/find.go:304] — the all-`STANDARD` smoke bucket cannot expose this, so the PASS is scoped to `STANDARD` objects only. |

## Claimed numbers

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| N1 | "None inherited" | **Corroborated** | No throughput figures inherited; this groundwork adds none — smoke durations are non-comparative facts about each run (per methodology). |

## Claimed strengths

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| S1 | "Designed to work well against MinIO servers specifically" | **Unaddressed** | A design-intent claim; not exercisable against the study's AWS-only smoke bucket. Routed to the AWS-vs-MinIO open question. |
| S2 | "Mature, **actively maintained**, part of the MinIO ecosystem" | **Split: Corroborated (mature) / Contradicted (actively maintained)** | Mature: 317 release tags, long history — corroborated. **Actively maintained is CONTRADICTED:** the `minio/mc` repository is **ARCHIVED (read-only)** on GitHub — `archived:true`, last push `2025-11-20` [DOC GitHub API repos/minio/mc, accessed 2026-07-17]. [INFERRED] the pinned release is effectively terminal — though an archived repo *can* be unarchived, so this is a maintenance-posture inference, not a permanence proof. |

## Claimed weaknesses (hypotheses)

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| W1 | Serial client-side iterator, **no client-side keyspace sharding**; parallelism server-internal | **Corroborated (source + observation)** | Single-goroutine serial paginator, no fan-out flag exists [SRC api-list.go:100-165; cmd/client-s3.go:2351,2404,2420 MaxKeys=-1]; strictly serial at the wire [OBS debug-trace]. Same framing correction as M3. |
| W2 | Any performance advantage may be **MinIO-server-specific**, not generalize to AWS S3 | **Unaddressed** | Not run against a MinIO server (benchmark-phase). Routed. |
| W3 | "No Parquet / no crash-resume / no key-range sharding" (marked inferred) | **Split: Parquet/sharding Corroborated (help); resume Unaddressed-locally** | No Parquet/columnar output flag in `mc ls` or `mc find` [live `--help` both; SRC cmd/ls-main.go:34-63]; no key-range/sharding flag in either help output [live `--help`]. Resume: no user-facing resume flag in the help, but the "continuation token is SDK-internal" part rests on minio-go `api-list.go`, **absent from the checkout** → that sub-claim is Unaddressed-locally, not receipt-backed. |

## What to verify first

| # | Inherited item | Verdict | Evidence |
| --- | --- | --- | --- |
| V1 | "Whether `mc ls --recursive` against AWS S3 shows **any concurrent LIST calls** at all (network trace)" | **Corroborated: NO concurrent LISTs** (source + observation) | `--debug` trace shows one `GET /?location` then serial `list-type=2` pages, each request carrying the prior response's `continuation-token` — zero concurrency [OBS debug-trace; SRC api-list.go:100-165]. Definitive packet-level proof deferred to the replay-server phase; not receipt-promoted. |
| V2 | AWS-S3-vs-MinIO-server comparison | **Unaddressed** | Benchmark-phase; routed. |

## Verdict counts

Several rows carry a **split** verdict (counted under each half). Primary tally:

- **Corroborated (source/doc, incl. source+observation):** metadata 1-5, N1, V1,
  W1, M3 (framing-noted), the generic-S3 half of M1, the Parquet/sharding half of
  W3, and the "mature" half of S2.
- **Settled by smoke run:** 4 (M2, T1, T2, T4 — T4 scoped to `STANDARD` objects).
- **Contradicted:** 1 (the "actively maintained" half of S2 — repo archived).
- **Unaddressed:** T3 (MinIO-server side), S1, W2, V2, the MinIO-server half of
  M1, and the resume sub-claim of W3 — the whole AWS-vs-MinIO-server axis plus one
  SDK-internal detail.

No inherited claim was found *wrong on the mechanism*; the one contradiction is a
maintenance-status fact, and the main correction is a framing nuance (there is no
listing parallelism at all, not merely "server-internal"). The dossier's central
hypothesis — serial iterator, no client sharding — is corroborated.

## Items routed to the orchestrator (claims about other tools / S3 / infra)

1. **Cross-cutting claim §6 (S3-compatible endpoints), truncated-without-token
   guard.** The inherited note attributes the defensive bail-out on a truncated
   response carrying no continuation token to **`s3ls-rs` specifically** ("no
   other tool is named as guarding it") `[docs/open-questions.md §6]`.
   Independent source reading shows **minio-go (hence `mc`, and every minio-go
   based tool) guards the identical case**: it emits an explicit "S3 server is
   incompatible with S3 API" error rather than looping
   `[SRC minio-go api-list.go:157-163 @ v7.0.90]`. The s3ls-rs-exclusive framing
   is therefore too narrow. Not edited here (other tools' pages / cross-cutting
   doc are out of this branch's scope) — flagged for routing.
2. **Cross-cutting §"language bottleneck" roster** lists `mc` under Go
   `[docs/open-questions.md §2]` — corroborated (mc is Go). No edit needed.
3. **AWS-vs-MinIO-server fairness axis (T3/S1/W2/V2).** mc's one genuinely
   tool-specific benchmark question needs a MinIO-server endpoint the study does
   not currently register. An infra/methodology decision for the orchestrator.
4. **New capability finding not in the dossier:** mc has **no `--no-sign-request`
   flag** and **does not read `AWS_ACCESS_KEY_ID`/`_SECRET_ACCESS_KEY` for
   signing** — anonymous access is an empty-credential *alias* (`MC_HOST_<alias>`
   with no user-info, or `mc alias set ... "" ""`) resolving to
   `SignatureAnonymous` [SRC cmd/client.go:269-311; minio-go static.go:55-58]
   [RUN]. Benchmark-relevant (auth setup differs from aws-cli/rclone); recorded
   in the report, noted here so the owner can decide if it belongs in a
   cross-cutting auth note.
