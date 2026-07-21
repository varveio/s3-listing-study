# minio-mc claim-ledger migration

This is the current human-auditable conservation map for the `minio-mc` capsule.
It maps every status-bearing origin on the pre-restructure landing page
([`tool-page.md`](tool-page.md)) to the atomic records in
[`../data/claims.json`](../data/claims.json). Each legacy origin appears exactly
once below. An origin may map to several atomic claims, and one atomic claim may
conserve propositions from more than one origin.

## Labeling scheme

- The landing page carries one status-bearing ledger, its "Notes, questions, and
  observations" table. Its typed rows keep their existing labels — `M1`-`M3`
  (mechanism), `T1`-`T4` (modes to exercise), `N1` (claimed numbers), `S1`-`S2`
  (strengths), `W1`-`W3` (weaknesses), and `V1`-`V2` (verify-first). Its five
  numbered identity rows (Repo, Language, License, Version reviewed, Tier and
  Testability) take deterministic `META-1`..`META-5` top-to-bottom.
- Genuinely table-external status-bearing propositions take `PROSE-1`..`PROSE-12`.
- The unlabeled two-column identity table above the ledger restates the `META`
  rows and gets no separate label; the "What we saw" and "What we tried" prose
  bullets restate ledger rows and are conserved by those rows. Two open-hypothesis
  items are likewise conserved without a new label: "AWS-vs-MinIO-server axis"
  restates `T3`/`S1`/`W2`/`V2`, and "common-denominator arch" restates the
  multi-arch fact in `META-5`.

## Status re-mapping

The migration does not promote evidence. `status` records evidence strength:
`confirmed` requires a receipt-backed run, `supported` requires source,
documentation, observation, or run evidence, `unverified` is testable but not
settled (only `none` evidence), and `unverifiable` cannot be settled from
surviving public evidence. `disposition` separately records whether inherited
wording was `retained`, `corrected`, or `contradicted`.

The legacy vocabulary reserved `CONFIRMED` for receipts and marked every
source-, documentation-, or `[OBS]`-corroborated fact `VERIFIED: no`. Those
corroborated rows become `supported` here (evidence exists, just not a receipt),
while genuinely unaddressed or benchmark-routed rows become `unverified`.
Specific judgments to note:

- The serial-paginator finding (`M3`, `V1`, `W1`) is `supported` on source and an
  `[OBS]` debug probe. The inherited "server-internal parallelism" framing is
  demoted, not adopted: `server-internal-parallelism-unverified` is `unverified`
  with a `corrected` disposition, because client-side evidence cannot settle
  server-internal behavior. No claim is promoted above `supported` on the
  strength of the `[OBS]` probe alone.
- The `META-4` version identity is filled from the inherited "unknown"
  (`corrected`), while `version-identity-caller-supplied` is `confirmed` — the
  run.meta records that the version identity itself is caller-supplied metadata.
- `META-5` carries three conserved subjects. The multi-arch image availability and
  the arm64 native run map to `multiarch-image-available` and
  `arm64-image-ran-natively`; the explicit "Trivial" testability judgment maps to
  `testability-is-trivial` (the aws-cli `MD6` pattern — a `supported` operability
  judgment backed by the official image running to a verifier PASS). The `Tier 2`
  classification is a study-design decision, not a testable property of the tool,
  so it is conserved in [`../../README.md`](../../README.md) rather than as an
  empirical claim.
- `S1` ("designed to work well against MinIO servers specifically") is an
  inherited design-intent assertion; it is conserved as its own
  `designed-for-minio-servers` claim, distinct from `W2`'s untested
  MinIO-specific performance advantage (`minio-server-advantage-untested`).
- The "actively maintained" half of `S2` is `contradicted` (the repository is
  archived); the "mature" half is `retained`.

## Conservation map

| Legacy origin | Conserved subject | Atomic claim IDs |
| --- | --- | --- |
| META-1 | Canonical minio/mc repository, not a fork | `repo-is-canonical-mc` |
| META-2 | Language is Go | `language-is-go` |
| META-3 | License is AGPL-3.0 | `license-is-agpl3` |
| META-4 | Pinned version identity and its caller-supplied provenance | `pinned-version-identity`, `version-identity-caller-supplied` |
| META-5 | Multi-arch image and testability, arm64 exercised | `multiarch-image-available`, `arm64-image-ran-natively`, `testability-is-trivial` |
| M1 | MinIO's client works against generic S3/AWS and MinIO servers | `recursive-lists-complete-bucket`, `minio-server-endpoint-untested` |
| M2 | Listing surface is mc ls --recursive, with find as a second surface | `recursive-lists-complete-bucket`, `find-shares-serial-list-path`, `all-smoke-modes-verified-pass` |
| M3 | Serial client-side iterator and the server-internal-parallelism framing | `listing-is-serial-single-goroutine`, `server-internal-parallelism-unverified` |
| T1 | Baseline full-bucket recursive listing | `recursive-lists-complete-bucket`, `all-smoke-modes-verified-pass` |
| T2 | Exact fields under --json | `json-mode-exact-fields`, `all-smoke-modes-verified-pass` |
| T3 | AWS-vs-MinIO-server, must test both endpoints | `recursive-lists-complete-bucket`, `minio-server-endpoint-untested` |
| T4 | mc find traversal, shared path, GLACIER skip, and no ETag | `find-lists-standard-objects`, `find-shares-serial-list-path`, `find-skips-glacier`, `find-emits-no-etag`, `all-smoke-modes-verified-pass` |
| N1 | No inherited or added throughput numbers | `no-inherited-or-added-throughput-numbers` |
| S1 | Designed to work well against MinIO servers | `designed-for-minio-servers` |
| S2 | Mature and formerly "actively maintained" (now archived) | `upstream-is-mature`, `upstream-is-archived`, `pinned-release-terminal-inference` |
| W1 | Serial iterator, no keyspace sharding, hard-wired page size and concurrency | `server-internal-parallelism-unverified`, `no-client-keyspace-sharding`, `maxkeys-hardwired-no-page-knob`, `concurrency-is-one-not-tunable` |
| W2 | Any advantage may be MinIO-server-specific | `minio-server-advantage-untested` |
| W3 | No Parquet, no crash-resume, no key-range sharding | `no-client-keyspace-sharding`, `no-parquet-or-columnar-output`, `no-user-facing-resume`, `interrupt-resume-behavior-untested` |
| V1 | Whether any concurrent LIST calls occur | `listing-is-serial-single-goroutine` |
| V2 | AWS-S3-vs-MinIO-server comparison | `minio-server-endpoint-untested` |
| PROSE-1 | No AWS-env signing and the empty-credential anonymous alias | `no-aws-env-signing`, `anonymous-is-empty-cred-alias` |
| PROSE-2 | The listing implementation lives in minio-go, not mc | `listing-logic-in-minio-go` |
| PROSE-3 | Text output is lossy while --json preserves exact fields | `json-mode-exact-fields`, `text-output-lossy` |
| PROSE-4 | Folders are fabricated client-side | `folders-are-synthetic` |
| PROSE-5 | Text-mode key parsing is best-effort in the adapter | `text-key-parse-best-effort` |
| PROSE-6 | --versions exercised the versions-API contract only, on an unversioned bucket | `versions-mode-ran-on-unversioned-bucket`, `multi-version-fidelity-untested`, `all-smoke-modes-verified-pass` |
| PROSE-7 | The truncated-without-token guard is a shared minio-go behaviour | `truncated-without-token-guard` |
| PROSE-8 | Streaming bounded memory, the smoke peak, and unconfirmed scaling | `memory-streaming-bounded`, `full-bucket-smoke-peak-rss`, `memory-scaling-unconfirmed` |
| PROSE-9 | SDK retry policy and untested throttling behavior | `sdk-retry-policy`, `retry-throttle-behavior-untested` |
| PROSE-10 | Text-vs-JSON formatting cost at scale | `text-vs-json-cost-untested` |
| PROSE-11 | Serial throughput ceiling and the recorded full-bucket wall-clock | `full-bucket-smoke-wall-clock`, `serial-throughput-ceiling-untested` |
| PROSE-12 | Recorded-but-not-smoked --rewind, --incomplete, and --zip modes | `rewind-incomplete-modes-untested` |

The validator compares this table with every `legacy_origins` value in
[`../data/claims.json`](../data/claims.json) in both directions. Reviewers should
additionally compare it with the preserved tables in [`tool-page.md`](tool-page.md),
because where a compound sentence splits remains a human judgment.
