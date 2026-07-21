# MinIO Client (`mc`)

> **Historical landing page (2026-07-20, capsule migration).** This is the full
> pre-restructure landing page. Any `current-state` wording below is historical
> as of the date it records and is superseded by the root README and `data/`.
> Only this banner and link targets changed; body prose and evidence
> qualifications are preserved.

`mc` is MinIO's own multi-command CLI for S3 and MinIO servers; its listing
surface is `mc ls` (and, as a second traversal command, `mc find`). This page
consolidates a source-and-run groundwork pass (mc `RELEASE.2025-08-13T08-35-41Z`,
commit `7394ce0`, listing SDK `minio-go v7.0.90` @ `68fb5ee`, 2026-07-17) that
read the tool's own source and the pinned SDK, and smoked its listing modes (10
runs across seven modes) anonymously against the registered smoke bucket. See
`research/report.md` for the full report, `research/reconciliation.md` for how
every inherited claim was checked, and `research/codex-review.md` for the two
critical cross-checks (Stage E, 16 findings — I1–I7, M1–M9 — all fixed; and the
consolidation review below) that stand behind it.

**What we saw.** `mc` listed AWS S3 **anonymously and correctly** at smoke — **10/10
smoke receipts PASS**, including `mc find`, with the full 148,917-key bucket
matched **byte-exact** on `size`/`etag`/`mtime`/`storage_class` under `--json`
[RUN `receipts/smoke/recursive-json`]. Anonymous access is an **empty-credential
alias** (`MC_HOST_s3` env, no embedded keys) resolving to `SignatureAnonymous`;
`mc` **does not read `AWS_ACCESS_KEY_ID`/`_SECRET_ACCESS_KEY` for signing** — a
genuine capability distinction from aws-cli/rclone, not a cosmetic one. But
listing is **strictly serial**: concurrency is **1** and the page size is fixed at
S3's server default (**≤1000**) — mc hard-wires `MaxKeys=-1` (so the SDK never
sends `max-keys`) and exposes no flag or env to change either the page size or the
concurrency, so there is **nothing for the benchmark to sweep** on mc's listing
path. And **upstream is ARCHIVED (read-only) on GitHub** [DOC GitHub API
`repos/minio/mc`, `archived:true`, accessed 2026-07-17]: the inherited "actively
maintained" is **Contradicted**, and the pinned release is effectively terminal
[INFERRED] (an archived repo *can* be unarchived — a maintenance-posture
inference, not a permanence proof).

|  |  |
|---|---|
| **Repo** | <https://github.com/minio/mc> — canonical AGPLv3 MinIO project, not a fork [DOC] |
| **Language** | Go (`go 1.23.0` in `go.mod`; binary built with go1.24.6 [RUN `mc --version`]) |
| **License** | GNU AGPL v3 [SRC `LICENSE:1` @ 7394ce0], [DOC GitHub API `license.spdx_id=AGPL-3.0`] |
| **Version reviewed** | `RELEASE.2025-08-13T08-35-41Z` (commit `7394ce0`), latest release tag; the pinned image digest `sha256:a7fe349…` ran in all 10 receipts (the release/commit identity of that digest is caller-supplied, not independently measured) |
| **Listing SDK** | `github.com/minio/minio-go/v7 v7.0.90` (commit `68fb5ee`) — all LIST HTTP behavior lives here |
| **Upstream health** | **ARCHIVED / read-only** on GitHub (`archived:true`, last push `2025-11-20`, 3522 stars) [DOC GitHub API, accessed 2026-07-17]. Pinned release effectively terminal [INFERRED] — an archived repo *can* be unarchived, so this is a maintenance-posture inference, not a permanence proof |
| **Tier** | 2 — included when the setup permits |
| **Testability** | Trivial — official multi-arch image (`minio/mc`, amd64/arm64/ppc64le) + `dl.min.io` prebuilt binaries [DOC `docker manifest inspect`]; the arm64 image ran 10 smoke runs to PASS [RUN] |

## What we tried and saw

- **`mc ls` is a serial paginator with zero listing knobs.** The two dimensions a
  large-scale lister usually exposes — page size and concurrency — do not exist as
  mc options: `MaxKeys=-1` is hard-wired (so the S3 default of 1000/page is taken
  and never overridden), and the SDK loop is a single goroutine advancing by
  `continuation-token`, one request at a time [SRC minio-go `api-list.go:100-165`
  @ 68fb5ee]. For the benchmark, mc is a useful serial baseline. Zero concurrent
  LISTs were seen at the wire [OBS `receipts/smoke/_capability/debug-trace/`]. See
  `mechanism.md`.
- **The listing implementation is in minio-go, not mc.** Every meaningful LIST decision
  (V1/V2, delimiter, pagination, retry, encoding, the truncated-without-token
  guard) lives in the SDK; mc contributes the CLI, the alias/credential model, and
  the output formatting. Auditing mc's listing means auditing `minio-go v7.0.90`.
- **No AWS-env signing — a real capability distinction.** Unlike aws-cli/rclone,
  mc ignores `AWS_ACCESS_KEY_ID`/`_SECRET_ACCESS_KEY`; credentials come only from
  its own alias / `MC_HOST_*` / STS chain. Anonymous access = an alias with empty
  keys, resolving to `SignatureAnonymous` and skipping signing [SRC mc
  `cmd/client.go:269-311` @ 7394ce0; minio-go `pkg/credentials/static.go:55-58`,
  `api.go:904-912` @ 68fb5ee]. This changes what "anonymous" even means for mc.
- **Text output omits detail; JSONL preserves it.** The default human text
  format humanizes sizes (`1006KiB`) and prints **no ETag at all**; exact bytes and
  ETag are available **only** via `--json`, which emits one compact JSON object per
  line (JSONL) [SRC mc `cmd/ls.go:60-95` @ 7394ce0]. Scripts that need those
  fields should use `--json`; see `mechanism.md` § Footguns.
- **`mc find` skips GLACIER objects.** `mc find` is a second listing surface that
  drives the *same* serial `List()` path as `ls --recursive`, but it
  **unconditionally skips `GLACIER` objects** [SRC mc `cmd/find.go:304` @ 7394ce0]
  and emits **no ETag even under `--json`**. So it is not a faithful full lister on
  buckets with archived objects. The all-`STANDARD` smoke bucket cannot expose this
  limitation, so the `mc find` PASS receipts are scoped to `STANDARD` objects only.
- **Upstream archived.** The canonical repo is read-only; the community `mc` line
  is effectively frozen at this release [DOC GitHub API, accessed 2026-07-17]. A
  useful maintenance detail for anyone choosing tooling.
  (Archived repos *can* be unarchived, so this is a maintenance-posture inference,
  not a permanence proof [INFERRED].)

## Notes, questions, and observations

Every inherited observation from the original (secondhand) tool page is shown
alongside the 2026-07-17 groundwork (`research/report.md`) and row-by-row review in
`research/reconciliation.md` (row IDs below match that file). Status values:
**CONFIRMED** (receipt-backed), **CORRECTED** (found different; both sides shown),
**VERIFIED: no** (not settled by any receipt — a hypothesis, however corroborated
by source reading or an `[OBS]` probe), **UNVERIFIABLE** (cannot be tested with
resources on hand). Per `AGENTS.md`, source reading and an `[OBS]` debug probe are
**not** receipts — neither promotes a claim past `VERIFIED: no`.

| # | Claim | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Repo `github.com/minio/mc` | **VERIFIED: no** (corroborated) | Canonical AGPLv3 upstream, not a fork [DOC github.com/minio/mc] |
| 2 | Language: Go (firsthand) | **VERIFIED: no** (corroborated) | [SRC mc `go.mod:3` @ 7394ce0]; binary self-reports go1.24.6 [RUN `mc --version`] |
| 3 | License: AGPL-3.0 (firsthand) | **VERIFIED: no** (corroborated) | [SRC mc `LICENSE:1`]; [DOC GitHub API `license.spdx_id=AGPL-3.0`] |
| 4 | Version reviewed: **unknown** | **VERIFIED: no** (filled editorially; pinned) | Pinned `RELEASE.2025-08-13T08-35-41Z` @ 7394ce0 (latest release); the pinned image digest `sha256:a7fe349…` ran in all 10 receipts [RUN `receipts/smoke/*`], but the release/commit **identity** of that digest is caller-supplied [RUN `run.meta` `tool_version_source=caller-supplied`], not independently measured — so the version *identity* is not receipt-proven |
| 5 | Tier 2; Testability "Trivial — prebuilt binaries/packages" | **VERIFIED: no** (corroborated) | Official multi-arch image (amd64/arm64/ppc64le) + `dl.min.io` binaries [DOC `docker manifest inspect`]; the **arm64** image did run 10 smoke runs to PASS [RUN `receipts/smoke/*`], proving it is trivially runnable, but the other arches and the `dl.min.io` binaries were not each exercised |
| M1 | MinIO's own client; works against MinIO **and** generic S3 incl. AWS | **Split — generic-S3/AWS: CONFIRMED; MinIO-server: VERIFIED: no** | Anonymous AWS S3 listing PASS [RUN `receipts/smoke/recursive-json`]. No MinIO-server endpoint exercised — that half untested (routed, see below) |
| M2 | Listing surface is `mc ls --recursive` | **CONFIRMED** (+ nuance) | Full bucket listed completely [RUN `recursive`/`recursive-json` PASS]. Nuance: `mc find` is a *second* listing surface (T4) |
| M3 | "Serial client-side iterator — parallelism is **server-internal**, not client fan-out" | **VERIFIED: no** (corroborated, framing-corrected) | Serial single-goroutine paginator, one `ListObjectsV2` at a time by `continuation-token` [SRC minio-go `api-list.go:100-165` @ 68fb5ee]; serial at the wire [OBS `_capability/debug-trace`]. **Framing note:** evidence establishes only that the **client** issues no concurrent LISTs / no keyspace fan-out; it says **nothing** about how AWS or a MinIO server serves each request internally — neither confirmed nor refuted. An `[OBS]` probe is not a run receipt → stays `VERIFIED: no` |
| T1 | `mc ls --recursive alias/bucket` (baseline) | **CONFIRMED** | Full-bucket 148,917 keys, 0 missing/extra/dup [RUN `recursive-json` PASS; `recursive` PASS] |
| T2 | `mc ls --recursive --json` | **CONFIRMED** | JSONL; exact size/etag/mtime/storageClass all matched manifest [RUN `recursive-json` PASS] |
| T3 | Endpoint **AWS S3 vs MinIO server** — "must test both" | **Split — AWS: CONFIRMED; MinIO-server: VERIFIED: no** | AWS side fully exercised [RUN]. No MinIO-server endpoint registered in the study — standing one up is a benchmark-phase infra decision (routed) |
| T4 | `mc find` — alternate traversal | **CONFIRMED** (scoped `STANDARD` only) | `find` + `find-json` PASS on `normals-hourly/` [RUN `find-hourly`, `find-json-hourly`]. Same serial `List()` path as `ls --recursive` [SRC mc `cmd/find.go:275-284`]; distinct output contract (alias-prefixed absolute keys, **no ETag even in `--json`**). **Completeness caveat:** unconditionally skips `GLACIER` [SRC mc `cmd/find.go:304`] — the all-`STANDARD` smoke bucket cannot expose this, so the PASS is scoped to `STANDARD` objects only |
| N1 | Claimed numbers: "None inherited" | **VERIFIED: no** (corroborated) | No throughput figures inherited; groundwork adds none — smoke durations are non-comparative facts about each run (per methodology) |
| S1 | "Designed to work well against MinIO servers specifically" | **VERIFIED: no** (unaddressed) | Design-intent claim, not exercisable against the AWS-only smoke bucket. Routed to the AWS-vs-MinIO axis |
| S2 | "Mature, **actively maintained**, part of the MinIO ecosystem" | **Split — "mature": VERIFIED: no (corroborated); "actively maintained": CORRECTED (Contradicted)** | Mature: 317 release tags — corroborated. **Actively maintained is CONTRADICTED:** `minio/mc` is **ARCHIVED (read-only)** — `archived:true`, last push `2025-11-20` [DOC GitHub API, accessed 2026-07-17]. [INFERRED] pinned release effectively terminal (an archived repo *can* be unarchived — maintenance-posture inference, not permanence proof) |
| W1 | Serial client-side iterator, **no client-side keyspace sharding**; parallelism server-internal | **VERIFIED: no** (corroborated, same framing correction as M3) | Single-goroutine serial paginator, no fan-out flag exists; `MaxKeys=-1` hard-wired [SRC minio-go `api-list.go:100-165` @ 68fb5ee; mc `cmd/client-s3.go:2351,2404,2420` @ 7394ce0]; serial at the wire [OBS `_capability/debug-trace`] |
| W2 | Any performance advantage may be **MinIO-server-specific**, not generalize to AWS S3 | **VERIFIED: no** (unaddressed) | Not run against a MinIO server (benchmark-phase). Routed |
| W3 | "No Parquet / no crash-resume / no key-range sharding" (marked inferred) | **Split — Parquet/sharding: VERIFIED: no (corroborated); resume: VERIFIED: no (unaddressed)** | No Parquet/columnar or key-range/sharding flag in **either** `mc ls` or `mc find` [live `--help` both; SRC mc `cmd/ls-main.go:34-63`]. Resume: **source-corroborated, no receipt** — no user-facing resume flag [live `--help`]; the `continuation-token` lives entirely inside the SDK loop and is never surfaced [SRC minio-go `api-list.go:100-165` @ 68fb5ee — this anchor, which the reconciliation had left "Unaddressed-locally" because the SDK was absent from the checkout, is now re-verified against the pinned SDK; see consolidation review]. No receipt exercised an interrupt/resume, so it stays `VERIFIED: no` |
| V1 | "Whether `mc ls --recursive` shows **any concurrent LIST calls** at all (network trace)" | **VERIFIED: no** (corroborated: NO concurrent LISTs) | `--debug` trace shows one `GET /?location` then serial `list-type=2` pages, each carrying the prior response's `continuation-token` — zero concurrency [OBS `_capability/debug-trace`; SRC minio-go `api-list.go:100-165` @ 68fb5ee]. Definitive packet-level proof deferred to the replay-server phase; not receipt-promoted |
| V2 | AWS-S3-vs-MinIO-server comparison | **VERIFIED: no** (unaddressed) | Benchmark-phase; routed |

The inherited mechanism description mostly lined up with the source. The one
different maintenance-status fact is S2 (repo archived), and the main correction is a framing
nuance — the **client** does no listing parallelism and no keyspace fan-out, so
the inherited "server-internal parallelism" framing is set aside as
unverifiable-here (how the server serves each request internally is neither
confirmed nor refuted), not replaced by a broader conclusion. The tool page's central
hypothesis — serial iterator, no client sharding — is corroborated.

**Questions tracked elsewhere** (about other tools, S3, or shared infrastructure;
recorded in `research/reconciliation.md` § "Items routed"):

1. **Truncated-without-token guard.** The cross-cutting note attributes the
   defensive bail-out on a truncated response with no continuation token to
   `s3ls-rs` specifically; minio-go (hence `mc` and every minio-go-based tool)
   guards the identical case — the reachable guard is inside `listObjectsV2Query`,
   returning a `NotImplemented` "Truncated response should have continuation token
   set" error [SRC minio-go `api-list.go:253-257` @ 68fb5ee] (with a defensive
   backstop in the outer loop [SRC minio-go `api-list.go:157-163`]). The
   s3ls-rs-exclusive framing is too narrow — flagged for `docs/open-questions.md`.
2. **AWS-vs-MinIO-server environment axis (T3/S1/W2/V2).** This benchmark
   question needs a **registered MinIO-server endpoint**
   the study does not currently have — an infra/methodology decision for the owner.
3. **New capability finding:** mc has **no `--no-sign-request` flag** and does not
   read `AWS_*` for signing (empty-cred alias mechanism). Benchmark-relevant (auth
   setup differs from aws-cli/rclone); noted for a possible cross-cutting auth note.

## Open hypotheses for the benchmark phase

Every claim below is unrun at smoke and carried forward from `research/report.md`
§ 10 and the routed items, with provenance. None is settled here; all stay
`VERIFIED: no`.

1. **AWS-vs-MinIO-server axis.** Whether any performance
   advantage is MinIO-server-specific and fails to generalize to AWS S3 (S1/W2),
   and the "must test both endpoints" comparison itself (T3/V2). **Blocked on a
   registered MinIO-server endpoint** — an owner infrastructure decision; the study
   registers only AWS buckets today. This is a tool-specific question for mc.
2. **Serial throughput ceiling.** With page size fixed at the server default
   (≤1000) and strict serial pagination, mc's full-bucket time is ≈
   `ceil(N/1000) × (RTT + page-parse)`; at 148,917 keys this was ~76–92s
   cross-internet. In-region it will be RTT-bound and **much faster**, but still
   serial. Measure the serial wall-clock and per-page CPU, not knobs that don't
   exist.
3. **Text vs JSON CPU/output cost.** `--json` marshals every entry; text humanizes
   sizes. At scale the formatting cost may be measurable — capture CPU for both
   output contracts (same request pattern).
4. **Memory at scale.** Smoke showed a flat ~28–35 MB RSS (28.1/9.0 MB shallow vs
   35.4/16.1 MB full), scale-independent by design (streaming). Confirm no
   accumulation at millions of keys and under `--versions` (per-key version
   grouping).
5. **Retry/throttle behavior under 503s.** The SDK's 10×/200ms-jitter policy is
   untestable politely at smoke scale; a replay-server fault-injection run would
   settle it.
6. **`--versions` / `--rewind` / `--incomplete`** on a *versioned* and a
   *multipart-heavy* bucket — distinct request patterns not exercisable on this
   tame, unversioned bucket. Proposed for an edge/versioned fixture (`EDGE_BUCKET=none`
   this pass).
7. **Common-denominator arch** (report § 10 item 4). amd64 is natively supported by
   the upstream image (and arm64/ppc64le), so the benchmark's single-arch choice is
   unconstrained by mc — flagged for that phase; see `running.md` § Architecture
   matrix.

## Known caveats carried forward

- **Text-mode key parsing is best-effort.** The `normalize.sh` text adapter
  consumes exactly one separator space (and one after a recognised storage-class
  token), so genuine leading spaces in a key survive the raw-key contract; but a
  key literally beginning `"<SC> …"` is indistinguishable from the SC column in
  text mode. `*-json` modes are authoritative. Exact for the whitespace-free NOAA
  corpus; general weird-key fidelity is deferred (`EDGE_BUCKET=none`). See
  `mechanism.md`.
- **`--versions` validates the versions-API request/output contract only, on an
  unversioned bucket.** On `noaa-normals-pds` every object has one `null` version,
  so `--versions` mode never exercised genuine multi-version collapse or
  delete-marker rows. Deferred with the edge-case fixture. See `mechanism.md`.
- **The `mc find` PASS is scoped to `STANDARD` objects.** Because `mc find` skips
  `GLACIER` unconditionally and the smoke bucket is all `STANDARD`, the receipts
  cannot exercise that completeness hole. See `mechanism.md`.
- **Folders are fabricated client-side** with `time.Now()` timestamps and zero
  size — a delimiter listing's "directories" are synthetic CommonPrefixes, not
  objects; `normalize.sh` marks their mtime/size as `-`. See `mechanism.md`.

## Provenance

**Mixed provenance — read the metadata table carefully.** This tool page's lineage
is mixed in two ways, and the "Mixed provenance" callout it has always carried is
preserved and extended here:

- The **inherited seed** was Swath's private prior-art notes: a one-line "Others"
  catalog entry ("serial iterator; server-internal parallelism only"), restated in
  this study's pre-existing tool inventory with the added caveat about
  MinIO-server-specific advantages. No dedicated investigation existed; treat every
  inherited hypothesis as unverified regardless of its confident phrasing. Those
  notes are out of reach (per `AGENTS.md`) and are **not** consulted to resolve
  ambiguity — the answers come from running the tool or reading its public source.
- Even in the *original* inherited page, the **Language** and **License** cells did
  *not* come from Swath's notes (which never stated them) — they were read directly
  from the public GitHub repo. So firsthand metadata sat inside an otherwise
  secondhand page from the start.
- Everything this current page marks as **CONFIRMED / CORRECTED / VERIFIED: no**
  is **firsthand**: the pinned source read (mc @ `7394ce0`, minio-go @ `v7.0.90` @
  `68fb5ee`), the GitHub REST API snapshot, and 10 committed anonymous smoke runs
  against `noaa-normals-pds`. The original secondhand hypotheses are preserved in
  the notes table beside their statuses. The source-and-run derivation lives in
  `research/report.md`; the row-by-row audit in `research/reconciliation.md`.

**Additive findings (not from the inherited notes), named with their origins:**

- **The `mc find` results** (10th–9th receipts, the GLACIER-skip caveat, the
  no-ETag-under-`--json` contract) are additive: the inherited Modes table *named*
  `mc find` as worth a check, but the smoke runs, the receipts, and the
  `cmd/find.go:304` GLACIER finding are all firsthand groundwork [RUN
  `find-hourly`, `find-json-hourly`; SRC mc `cmd/find.go:275-284,304` @ 7394ce0].
- **The archived-repo finding** is additive DOC evidence not present in the
  inherited notes: it came from the GitHub REST API `repos/minio/mc`
  (`archived:true`, accessed 2026-07-17) during groundwork, and it is what
  *contradicts* the inherited "actively maintained" claim (S2).

## Pointers

- **`mechanism.md`** — source-anchored architecture: the serial single-goroutine
  paginator, the alias/credential model, the hard-wired page-size/concurrency,
  streaming memory, the SDK retry model, no resume, the corrected pager/trailing-slash
  footguns, the truncated-without-token guard, and the client-only scoping of any
  parallelism statement. minio-go anchors labelled `[SRC minio-go … @ 68fb5ee]`.
- **`running.md`** — the upstream official image by digest, the `MC_HOST_s3` env
  wiring through the wrapper, every smoked mode with its exact invocation and
  receipt (10 PASS), the un-smoked modes (`--incomplete`/`--rewind`/`--zip`)
  recorded-not-smoked with reasons, the drift pre-flight, reproduction, and the
  architecture matrix.
- **`research/`** — `report.md` (the source-and-run groundwork report),
  `reconciliation.md` (every inherited observation, status, and supporting record),
  `codex-review.md` (the Stage E critical cross-check's 15 resolved findings and
  the consolidation review). These preserved files may use the project's older
  terminology; this page is the current summary.
- **`receipts/`** — the 10 committed anonymous smoke receipts plus the
  `_capability/debug-trace` (request-shape probe) and `_capability/preflight`
  (drift pre-flight). Immutable; read-only inputs to this page.

## Receipts

10 Stage C–D smoke receipts (all anonymous, native arm64, mc
`RELEASE.2025-08-13T08-35-41Z`, image
`minio/mc@sha256:a7fe349…`, bucket `noaa-normals-pds` @ 2026-07-17 snapshot,
manifest sha256 `c78a827…2adb`; each run result via `harness/verify-listing.sh`):

| Run record | Mode | Scope | Run result |
| --- | --- | --- | --- |
| [`receipts/smoke/recursive-json/`](../receipts/smoke/recursive-json/) | `mc --json ls --recursive` | full 148,917 | PASS (key/size/etag/mtime/sc) |
| [`receipts/smoke/recursive/`](../receipts/smoke/recursive/) | `mc ls --recursive` | full 148,917 | PASS (key/mtime) |
| [`receipts/smoke/shallow/`](../receipts/smoke/shallow/) | `mc ls` (delimiter) | root | PASS |
| [`receipts/smoke/shallow-json/`](../receipts/smoke/shallow-json/) | `mc --json ls` (delimiter) | root | PASS |
| [`receipts/smoke/recursive-json-hourly/`](../receipts/smoke/recursive-json-hourly/) | `mc --json ls --recursive` | `normals-hourly/` (2,549) | PASS |
| [`receipts/smoke/recursive-json-monthly1991/`](../receipts/smoke/recursive-json-monthly1991/) | `mc --json ls --recursive` | `normals-monthly/1991-2020/` (15,625) | PASS |
| [`receipts/smoke/recursive-json-annualaccess/`](../receipts/smoke/recursive-json-annualaccess/) | `mc --json ls --recursive` | `normals-annualseasonal/1981-2010/access/` (9,839) | PASS |
| [`receipts/smoke/versions-json-hourly/`](../receipts/smoke/versions-json-hourly/) | `mc --json ls --versions --recursive` | `normals-hourly/` (2,549) | PASS |
| [`receipts/smoke/find-json-hourly/`](../receipts/smoke/find-json-hourly/) | `mc --json find` | `normals-hourly/` (2,549) | PASS (no etag) |
| [`receipts/smoke/find-hourly/`](../receipts/smoke/find-hourly/) | `mc find` | `normals-hourly/` (2,549) | PASS (key only) |
| [`receipts/smoke/_capability/debug-trace/`](../receipts/smoke/_capability/debug-trace/) | `mc --debug` request-shape probe | `normals-hourly/` | (supporting record, no run result) |
| [`receipts/smoke/_capability/preflight/`](../receipts/smoke/_capability/preflight/) | drift pre-flight re-list | full | (byte-identical, no run result) |

Full-bucket stdout payloads are externalized under
`<data>/receipts/minio-mc/` (no-data-in-repo rule) with sha256
recorded in each `run.meta`. Edge-case fidelity checks (unicode/weird-key/
multipart-ETag) are **deferred** — `EDGE_BUCKET=none`.
