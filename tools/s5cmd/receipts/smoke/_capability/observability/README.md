# Capability probe — s5cmd listing observability

`_capability/` carries no verifier verdict and is exempt from the every-mode
expectation. These **[OBS]** probes establish what request-level observability
s5cmd exposes for *listing*. They are not `smoke-run.sh` receipts: s5cmd's log
level is an argv flag, not an environment variable, so it cannot be injected
through the wrapper's observability-only `--env` passthrough. All probes ran
unsigned (`--no-sign-request`) against `noaa-normals-pds` in
`peakcom/s5cmd@sha256:2ff939e2ee3c76adcadd78dbfc3e2569b18a3743ed9dcfccb1ec589af7fb9903`
(v2.3.0-991c9fb, self-reported by `s5cmd version` — see `version.stdout.txt`;
the version string embeds the pinned commit `991c9fb`), arch arm64, `TZ=UTC`.

## Correction (Stage E)

An earlier draft concluded "no per-request logging at any level." **That was
wrong** — it checked only stderr. `--log trace` writes the AWS SDK's full
request/response records to **stdout**, interleaved with the listing. Corrected
findings:

| Probe | Where records go | Result |
| --- | --- | --- |
| `--stat` | stdout | **Operation** tally only: `ls 1 0 1` (one `ls` op, not API calls). `stat.stdout.txt`. |
| `--log debug` | — | No per-request records (`logdebug.stdout.txt` = the listing only; `logdebug.stderr.txt` = 0 bytes). |
| `--log trace` | **stdout** | **Full AWS SDK request/response records**, one per API call. `logtrace.stdout.txt`, `logtrace-multipage.requests.txt`. s5cmd enables `aws.LogDebugWithHTTPBody` at trace level [SRC storage/s3.go:1284 @ 991c9fb]. |

## API call count IS obtainable (via `--log trace`)

Counting `DEBUG: Request s3/<Op>` lines on stdout yields the exact API-call
count. Measured on `normals-hourly/` (2,549 keys):

- **1 × HeadBucket** — the anonymous region probe (`s3manager.GetBucketRegion`)
  that precedes every listing; the response carries `X-Amz-Bucket-Region: us-east-1`.
- **3 × ListObjectsV2** — three pages at the 1,000-key page cap, walked
  **sequentially**: the request URLs progress
  `GET /?list-type=2&prefix=normals-hourly%2F` →
  `…&continuation-token=<tok>&list-type=2…` → `…&continuation-token=<tok>…`.

The sequential continuation-token chain is runtime corroboration that a single
`ls` walks one serial page chain (no concurrent LIST requests in flight for one
`ls`). The requests carry no `Authorization` header — unsigned, as expected.
Raw records: `logtrace-multipage.requests.txt` (request/response header lines
only; the 2,549 listing lines are dropped to keep the artifact small). A
full-bucket trace (~149 pages) was **not** captured: at trace level it
interleaves the request records with 148,917 listing lines (>32 MB), which the
no-data-in-repo rule keeps out; the 3-page prefix proves the mechanism.

## Consequence for the receipts

Each mode `receipt.md`'s "API call count" field is corrected from "not exposed"
to: **obtainable via `--log trace` (s5cmd surfaces no built-in numeric counter);
the wrapper runs did not pass `--log trace` — it would bloat every payload with
the full request dump — so page counts were captured in this probe instead.**
Request-shape capture at scale still defers to the replay-server phase.
