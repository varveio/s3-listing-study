# minio-mc — running it

How the pinned image was run for every smoke receipt, the anonymous-alias wiring,
which modes were smoked and which were recorded-but-not-smoked, the drift
pre-flight, how to reproduce any receipt, and the architecture matrix. Canonical
tested identity (pinned SHAs, versions, study states) lives in
[`../data/tool.json`](../data/tool.json); evidence labels are as in
[`mechanism.md`](mechanism.md), and references of the form claim `some-id` resolve
in [`../data/claims.json`](../data/claims.json). Study state uses the canonical
status vocabulary (`supported`, `confirmed`, `unverified`, `unverifiable`).

## Image

Upstream official `minio/mc:RELEASE.2025-08-13T08-35-41Z`, pinned by digest:

```
minio/mc@sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727
```

This is upstream's own published multi-arch image (index digest above; arm64
sub-manifest `sha256:37d109dd…`), so it closely matches the setup users receive
and no self-built Dockerfile was staged [RUN `docker manifest inspect`]. Entrypoint is `["mc"]` [RUN `docker
inspect`], so every `../adapter/run.sh` argv below starts at the global flag / subcommand,
**never at `mc`**.

**Architecture:** amd64, arm64, and ppc64le are all natively present in the index
— no emulation needed for either candidate arch. Smoke ran **natively on arm64**
(runner is aarch64; image arm64), `emulated=no` in every receipt [RUN
`../receipts/smoke/*/run.meta`].

## Anonymous wiring — the `MC_HOST_s3` alias

mc has **no `--no-sign-request` flag**; it resolves credentials from a named
alias, and anonymous access is an alias whose keys are empty (see
[`mechanism.md`](mechanism.md#credential-and-alias-model)). The harness passes:

```
--env MC_HOST_s3=https://s3.amazonaws.com
```

This defines an ad-hoc alias **`s3`** that names real AWS S3 and carries **no
embedded credentials**; minio-go resolves `SignatureAnonymous` and issues unsigned
requests. It is endpoint configuration mc structurally requires (it has no default
endpoint like aws-cli) — **not** a traffic redirect or credential injection: the
alias name `s3` is not a bucket name (the bucket is always the run argument), and
`auth=anonymous` stays enforced because the alias carries no keys and no config is
mounted. The wrapper's `--env` guard accepts the name (not credential-shaped) and
the value has no credential shape [RUN `../receipts/smoke/*/run.meta`,
`passed_env=MC_HOST_s3=https://s3.amazonaws.com`]. Region (`us-east-1`) is accepted
for interface parity but is **not** placed on the mc argv — mc/minio-go
auto-resolve it via an anonymous `GET ?location` before the first LIST [OBS
`_capability/debug-trace`].

## Every smoked mode

All 10 smoke runs below (across seven distinct mode names — `recursive-json`
appears at four scopes) ran **anonymous**, native arm64, via
`harness/smoke-run.sh`, against
`noaa-normals-pds` (us-east-1) at its 2026-07-17 snapshot (148,917 keys, manifest
sha256 `c78a827…2adb`). All verdicts via `harness/verify-listing.sh`. `CREDS=none`
(no credentialed pass) and `EDGE_BUCKET=none` (unicode/weird-key/multipart-ETag
fidelity deferred). Invocation column is the argv appended to the `["mc"]`
entrypoint.

| Mode | Invocation (argv after `mc`) | Scope | Exit | Wall | Verdict | Receipt |
| --- | --- | --- | --- | --- | --- | --- |
| recursive-json | `--json ls --recursive s3/b/` | full (148,917) | 0 | 91.8s | **PASS** 0 miss/extra/dup, all fields | `../receipts/smoke/recursive-json/` |
| recursive (text) | `ls --recursive s3/b/` | full (148,917) | 0 | 75.4s | **PASS** key+mtime | `../receipts/smoke/recursive/` |
| shallow (text) | `ls s3/b/` | delimiter `/` (5) | 0 | 0.27s | **PASS** | `../receipts/smoke/shallow/` |
| shallow-json | `--json ls s3/b/` | delimiter `/` (5) | 0 | 0.21s | **PASS** | `../receipts/smoke/shallow-json/` |
| recursive-json | `--json ls --recursive s3/b/normals-hourly/` | prefix (2,549) | 0 | 1.6s | **PASS** | `../receipts/smoke/recursive-json-hourly/` |
| recursive-json | `--json ls --recursive s3/b/normals-monthly/1991-2020/` | prefix (15,625) | 0 | 9.7s | **PASS** | `../receipts/smoke/recursive-json-monthly1991/` |
| recursive-json | `--json ls --recursive s3/b/normals-annualseasonal/1981-2010/access/` | prefix (9,839) | 0 | 4.7s | **PASS** | `../receipts/smoke/recursive-json-annualaccess/` |
| versions-json | `--json ls --versions --recursive s3/b/normals-hourly/` | prefix (2,549) | 0 | 1.7s | **PASS** | `../receipts/smoke/versions-json-hourly/` |
| find-json | `--json find s3/b/normals-hourly/` | prefix (2,549) | 0 | 1.6s | **PASS** (key+size+mtime; no etag) | `../receipts/smoke/find-json-hourly/` |
| find (text) | `find s3/b/normals-hourly/` | prefix (2,549) | 0 | 1.5s | **PASS** (key only) | `../receipts/smoke/find-hourly/` |

Every verifier verdict is `dups=0 missing=0 extra=0 fields=0` for the fields the
mode exposes. Notes:

- **`recursive-json` full-bucket is the fidelity anchor:** 148,917 distinct keys,
  with `size`+`etag`+`mtime`+`storage_class` all matched to the manifest exactly
  [RUN `recursive-json/verify.md`] (claims `recursive-lists-complete-bucket`,
  `json-mode-exact-fields`). `peak_rss` 35.4 MB / `cgroup_peak` 16.1 MB and 91.8 s
  wall-clock on this run are `confirmed` single-run facts (claims
  `full-bucket-smoke-peak-rss`, `full-bucket-smoke-wall-clock`), not comparative
  numbers.
- **`shallow` delimiter modes** returned exactly the expected 4 CommonPrefixes +
  `index.html` (the verifier's derived delimiter-scope set).
- **`versions-json`** on the unversioned bucket returned each object once with the
  same key/size/etag/mtime as list-type=2 — the versions API is a distinct request
  pattern that produced the same object set here (claim
  `versions-mode-ran-on-unversioned-bucket`; multi-version fidelity deferred, claim
  `multi-version-fidelity-untested`).
- **`mc find`** prints keys as **alias-prefixed absolute paths** (`s3/<bucket>/<key>`)
  and emits **no ETag even under `--json`**; `../adapter/normalize.sh` strips the
  `<alias>/<bucket>/` prefix and marks etag `-`. It traverses the same serial
  `List()` path as `ls --recursive` [SRC mc `cmd/find.go:275-284`]. The PASS is
  scoped to `STANDARD` objects (find skips `GLACIER`; the bucket is all `STANDARD`) —
  claims `find-lists-standard-objects`, `find-skips-glacier`, `find-emits-no-etag`,
  `find-shares-serial-list-path`.
- Durations are recorded facts about each run, **not** comparative numbers (per
  methodology).

### Adapter provenance note

The first `recursive` (text) verify returned **FAIL missing=2** — an adapter bug,
not a tool defect: two keys had a size string exactly 7 chars wide (`1006KiB`),
which `%7s` prints with no separating space after `]`, and the text regex required
whitespace there. Fixed (`\s+`→`\s*`) in `../adapter/normalize.sh`; the mode was re-run (fresh
receipt) and PASSES. The `recursive-json` PASS on the same key set separately
shows that the tool listed all 148,917 keys throughout.

## Recorded, not smoked

Three modes exist on the mc listing surface but were deliberately not smoked; each
is recorded here with its reason (not silently skipped):

- **`--incomplete, -I`** — lists in-progress multipart uploads
  (`ListMultipartUploads`), not objects [SRC mc `cmd/client-s3.go:2016-2102`].
  Out of scope for an object-listing study, and returns none on this bucket.
- **`--rewind <t>`** — point-in-time listing via the versions API at a timestamp
  [SRC mc `cmd/ls-main.go:127-158`, `cmd/client-s3.go:1904-1905,1566`]. Not
  exercisable: the smoke bucket is unversioned, so a rewind has nothing to resolve.
- **`--zip`** — lists entries inside a zip object via the `x-minio-extract` header,
  **MinIO servers only** [SRC mc `cmd/client-s3.go:1576-1582`]. N/A on AWS S3.

`--versions` and `--rewind` (and multi-version fidelity generally) belong to a
versioned/edge fixture (`EDGE_BUCKET=none` this pass). These recorded-but-not-smoked
modes stay `unverified` — claim `rewind-incomplete-modes-untested`.

## Drift pre-flight

Before smoke, the bucket was re-listed anonymously with the pinned harness client
and diffed against the registry manifest as sorted sets — **byte-identical**
(decompressed sha256 `8b5b584…`), 0 lines of diff, no drift [OBS
`../receipts/smoke/_capability/preflight/preflight.md`].

## Request-shape probe

The `mc --debug` excerpt on the hourly prefix shows one `GET ?location` then serial
`list-type=2` request lines, each after the first carrying the prior
`continuation-token`, and **no `Authorization` header** [OBS
`../receipts/smoke/_capability/debug-trace`]. The excerpt is redacted and unhashed (a
probe, not a wrapper receipt); the 1000/1000/549 page split is [INFERRED] from the
2,549-key count, not read off the trace. This is the richest observability surface
mc offers for `ls` (no built-in API-call counter or progress metric exists) and is
what the study's replay-server phase can key off.

## Reproduction via `harness/smoke-run.sh`

Every receipt above was produced by the shared wrapper, never a bare `docker run`.
`../adapter/run.sh` only *prints* the argv (NUL-delimited) that the wrapper appends to the
pinned image's entrypoint; the wrapper owns `docker run`, the `--env MC_HOST_s3`
anonymous-alias injection, mounts, credential starving, the timeout, and
measurement. To reproduce any row:

```sh
harness/smoke-run.sh \
  --tool minio-mc --mode recursive-json \
  --image minio/mc@sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727 \
  --run-script tools/minio-mc/adapter/run.sh \
  --bucket noaa-normals-pds --region us-east-1 \
  --auth anonymous \
  --out tools/minio-mc/receipts/smoke/recursive-json
```

`<mode>` is one of `recursive`, `recursive-json`, `shallow`, `shallow-json`,
`versions-json`, `find`, `find-json` — see `tools/minio-mc/adapter/run.sh`'s `case`
statement for the exact argv each maps to. Add `--prefix <p>` for a scoped listing
(e.g. `normals-hourly/`). `../adapter/run.sh`, `../adapter/normalize.sh`, and everything under
[`../research/`](../research/) and [`../receipts/`](../receipts/) are **immutable**
inputs to this page — they were not modified for this consolidation; a rerun adds
a new receipt rather than editing an existing one.

## Architecture matrix

| Channel | amd64 | arm64 | other |
| --- | --- | --- | --- |
| Upstream image `minio/mc` (this tag) | yes native (`sha256:eb4ea988…`) | yes native (`sha256:37d109dd…`) | yes ppc64le |
| Prebuilt binaries `dl.min.io/client/mc/...` | yes | yes | per-arch archives; `Dockerfile.release` fetches `linux-${TARGETARCH}` |
| Source build (Go) | yes | yes | any Go target |

amd64 (the campaign's expected common denominator) is natively supported — no
emulation needed for either candidate arch. Smoke ran on arm64 natively; smoke
produces no comparative numbers, so the arch choice here is immaterial to anything
in this tool page. The single-arch benchmark choice is unconstrained by mc.
