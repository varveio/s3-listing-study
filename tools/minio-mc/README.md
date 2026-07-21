# MinIO Client (mc)

[MinIO Client (`mc`)](https://github.com/minio/mc) is MinIO's own command-line client for S3; its `mc ls` and `mc find` subcommands list a bucket by paginating serially through the S3 list API — ListObjectsV2 on the default listing path — printing a humanized text table by default and JSON Lines under `--json`.
mc is the canonical MinIO project rather than a fork, and its repository is now archived (read-only) on GitHub, so the pinned release is effectively terminal.
This study's groundwork is complete; no benchmark comparison has been run.

## At a glance

| Question | Current answer |
| --- | --- |
| Tested subject | Upstream mc release `RELEASE.2025-08-13T08-35-41Z` at commit `7394ce0`, run anonymously from the official multi-arch image on native arm64; every LIST decision lives in the pinned minio-go `v7.0.90` SDK. Full canonical identity is in [`data/tool.json`](data/tool.json). |
| Exercised coverage | Seven listing modes across ten anonymous smoke runs — recursive and delimiter `ls`, `--versions`, and `mc find`, in both text and JSON. `--rewind`, `--incomplete`, and `--zip` were recorded but not smoked. |
| Correctness | All ten smoke receipts PASS the study verifier for the fields each mode exposes, including 148,917 keys matched byte-exact under `--json`. In [`data/claims.json`](data/claims.json): `all-smoke-modes-verified-pass`, `recursive-lists-complete-bucket`, `json-mode-exact-fields`. |
| Smoke observation | The full-bucket `--json` run exited 0 in 91.8 s with a 35.4 MB peak RSS. These are facts of single groundwork runs, not benchmark results. |
| Results | No benchmark or comparative result exists. mc exposes no page-size or concurrency knob to sweep on the listing path. |

## How it works

`mc ls` is a thin CLI front-end over the minio-go SDK's ListObjects iterator:
one goroutine issues a single ListObjectsV2 request at a time, advancing by
continuation-token, with no keyspace division and no page-size or concurrency
tuning. mc hard-wires `MaxKeys=-1`, so it takes the server default page size and
cannot change it. Anonymous access is an alias with empty keys resolving to
`SignatureAnonymous`; mc never reads `AWS_*` for signing. The default text output
humanizes sizes and prints no ETag, so `--json` is the only faithful contract.
Listing streams object-by-object with no full-keyset accumulation, and there is
no user-facing resume. Full detail: [`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

The [upstream](https://github.com/minio/mc) mode surface and what this study
actually exercised are shown in separate columns.

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| `mc ls --recursive` | List a bucket completely through serial ListObjectsV2 pagination. | Smoked anonymously in full scope and three prefixes, text and `--json`; the full bucket matched the manifest exactly. |
| `mc ls` (delimiter) | List one directory level, folders as synthetic CommonPrefixes. | Smoked at the bucket root, text and `--json`; folders carry synthetic timestamps and zero size. |
| `mc ls --versions` | List through the list-object-versions API. | Smoked on one prefix; every object returned once because the bucket is unversioned. Multi-version fidelity is deferred. |
| `mc find` | A second traversal command over the same serial List() path. | Smoked on one prefix, text and `--json`; scoped to STANDARD objects because find skips GLACIER and emits no ETag. |
| `--rewind`, `--incomplete`, `--zip` | Point-in-time listing, in-progress multipart uploads, and zip-object listing. | Recorded but not smoked: the bucket is unversioned and non-multipart, and `--zip` is MinIO-only. |

Detailed mechanism is in [`docs/mechanism.md`](docs/mechanism.md); build, smoke,
and blocked-mode coverage is in [`docs/running.md`](docs/running.md).

## What we learned

Each finding links its owning explanation and its canonical claim ID; claim IDs
resolve in [`data/claims.json`](data/claims.json).

- **Listing is a serial single-goroutine paginator with zero tunable knobs.** mc
  issues one ListObjectsV2 at a time by continuation-token, hard-wires
  `MaxKeys=-1`, and exposes no page-size or concurrency flag, so there is nothing
  on the listing path for a benchmark to sweep — it is a serial baseline.
  [`docs/mechanism.md`](docs/mechanism.md#listing-is-one-serial-single-goroutine-paginated-stream)
  · `listing-is-serial-single-goroutine`, `maxkeys-hardwired-no-page-knob`, `concurrency-is-one-not-tunable`

- **The listing engine is minio-go, not mc.** Every meaningful LIST decision —
  V1/V2 selection, delimiter, pagination, retry, encoding, and the
  truncated-without-token guard — lives in the pinned SDK; mc contributes the
  CLI, the alias/credential model, and the output formatting.
  [`docs/mechanism.md`](docs/mechanism.md#the-listing-engine-is-minio-go-not-mc)
  · `listing-logic-in-minio-go`

- **mc ignores `AWS_*` and treats anonymous access as an empty-credential alias.**
  Unlike aws-cli and rclone, mc reads credentials only from its own
  alias/`MC_HOST_*`/STS chain, so anonymous listing means an alias with empty keys
  resolving to `SignatureAnonymous` — a real capability distinction.
  [`docs/mechanism.md`](docs/mechanism.md#credential-and-alias-model)
  · `no-aws-env-signing`, `anonymous-is-empty-cred-alias`

- **`mc find` is not a faithful full lister on archived buckets.** find drives the
  same serial path as `ls --recursive` but unconditionally skips GLACIER objects
  and emits no ETag even under `--json`, so its smoke PASS is scoped to the
  all-STANDARD bucket only.
  [`docs/mechanism.md`](docs/mechanism.md#modes-and-tunables)
  · `find-lists-standard-objects`, `find-skips-glacier`, `find-emits-no-etag`

- **Upstream is archived, so the pinned release is effectively terminal.** The
  canonical minio/mc repository is read-only as of the 2026-07-17 snapshot, which
  contradicts the inherited "actively maintained" claim; an archived repo can be
  unarchived, so this is a maintenance-posture inference, not a permanence proof.
  [`docs/running.md`](docs/running.md#image)
  · `upstream-is-archived`, `pinned-release-terminal-inference`

## Limitations and open questions

### Coverage gaps

- No MinIO-server endpoint was exercised, so the inherited "must test both
  endpoints" comparison and any MinIO-specific advantage remain unaddressed
  (claims `minio-server-endpoint-untested`, `minio-server-advantage-untested`).
- `--versions` exercised only the versions-API contract on an unversioned bucket;
  `--rewind`, `--incomplete`, and `--zip` were recorded but not smoked.
- Edge-key fidelity is deferred (`EDGE_BUCKET=none`); the text adapter's key parse
  is best-effort, so `*-json` modes are authoritative (claim
  `text-key-parse-best-effort`).

### Benchmark questions

- What is mc's serial full-bucket throughput ceiling in-region, and the relative
  CPU/output cost of text versus `--json` at scale?
- Does the streaming memory footprint stay bounded at millions of keys and under
  `--versions`?
- How do retries and backoff behave under 503 throttling?

### Tool risks to test

- Whether an interrupted listing can leave incomplete results without a resume
  path · `interrupt-resume-behavior-untested`
- Whether `mc find`'s GLACIER skip silently drops archived objects on a bucket
  that contains them.

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand the serial listing, credential, memory, and output model | [`docs/mechanism.md`](docs/mechanism.md) |
| Reproduce the image and see every smoked and recorded-not-smoked mode | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity, study states, and claim status data | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness | [`adapter/`](adapter/) |
| Audit how every old ledger row and status-bearing prose claim became atomic current claims | [`research/claims-migration.md`](research/claims-migration.md) and the preserved reconciliation in [`research/`](research/) |
| Read the historical pre-restructure landing page | [`research/tool-page.md`](research/tool-page.md) — frozen historical research, not the current entry point |
| Inspect the observations and immutable run records | [`receipts/`](receipts/) |

## Provenance

**Mixed provenance.** This page combines firsthand source, GitHub-API, and smoke
work with an inherited secondhand seed compiled from public prior-art notes; even
the original seed's Language and License cells were read firsthand from GitHub.
The seed was not a run record. See [`research/tool-page.md`](research/tool-page.md)
and [`research/reconciliation.md`](research/reconciliation.md).

## Evidence boundary

Source and documentation explain mechanisms and risks; only a committed receipt
confirms run-dependent study behavior. The `[OBS]` debug-trace and pre-flight are
capability probes, not run receipts, and smoke observations are not benchmark
results.
