# Receipt — can each subject speak to the swath replay server at all

RECEIPT. Direct container observations, not a measurement: every run below is a
compatibility probe against a local replay server with **no latency injection**,
so nothing here carries a timing or comparative claim. The design this
de-risks is the replay-backed phase-1 benchmark; this is its §7 Stage 0 step 3
(local smoke), run *before* the harness gains an endpoint concept, so each tool
was driven by hand with its capsule's own argv plus the endpoint additions
under test.

Date (UTC)   : 2026-08-18T19:50Z–19:56Z
Box          : linux/amd64, 2 vCPU, 7 GB, Linux 6.17.0-1022-gcp, Docker 29.7.2
Image        : `s3ls-toolbox:pilot`, toolbox_manifest_sha256
               `85884a99c9214f4140df428de481bca51eb5b7823b239a38f4f47ea04ebba888`
               (built locally from harness revision `7447ced`)
Server       : `swath-replay-server` built from swath `89269050` (clean tree),
               Temurin JDK 25.0.4+7
Server argv  : `serve --fixture sorted --bucket noaa-ghcn-pds --host 0.0.0.0
               --port 19090 --parquet-connections 4`
Networking   : `docker run --network host`, endpoint `http://127.0.0.1:19090`
Fixture      : `swath list s3://noaa-ghcn-pds/parquet/by_station/` (0.2.4,
               anonymous, concurrency 8) → 788,903 objects in 38.7 s, then
               `swath-replay-server sort-fixture` → 1 part, 21,212,292 bytes
Scope        : prefix `parquet/by_station/STATION=ACW00011604/` — **11 objects**,
               ground truth read off the fixture itself
Credentials  : dummy static SigV4 (`AKIAIOSFODNN7EXAMPLE`/…`EXAMPLEKEY`),
               `AWS_EC2_METADATA_DISABLED=true`; no AWS-directed traffic

## What the server answers

| Request | Response |
| --- | --- |
| `GET /bucket?list-type=2` | 200, `ListBucketResult` |
| `HEAD /bucket` (HeadBucket) | **405 Method Not Allowed** |
| `GET /bucket?location` (GetBucketLocation) | **400 InvalidArgument** |
| `GET /bucket` (legacy v1 ListObjects) | **400 InvalidArgument**, "only ListObjectsV2 is supported" |

## Verdict per subject

Every "works" row returned exactly the 11 objects of the scope, with the field
set its mode normally emits. `Rows` counts objects, not output lines — rclone's
JSON array and swath's TSV header are not rows.

| Tool | Exit | Rows | Endpoint mechanism used | Notes |
| --- | --- | --- | --- | --- |
| swath | 0 | 11 | `--endpoint-url` | `--checkpoint none` already in the capsule's argv |
| s3p | 0 | 11 | `S3_ENDPOINT` env | dummy env creds, no I/O |
| s3-fast-list | 0 | 11* | `--endpoint-url` | writes Parquet, not stdout: count read from its own "object count 11"; logs "using path-style addressing" itself |
| s7cmd | 0 | 11 | `--target-endpoint-url` + `--target-force-path-style` | |
| rclone | 0 | 11 | `endpoint="http://…"` in the connection string | **the value must be quoted** — see below |
| aws-cli | 0 | 11 | `--endpoint-url` | path-style config file turned out unnecessary here |
| minio-mc | 0 | 11 | `MC_HOST_s3` + `MC_REGION` | `MC_REGION` is required — see below |
| s3kor | 0 | 11 | `--custom-endpoint-url` | prints the endpoint it chose |
| s5cmd | 0 | 11 | `--endpoint-url` | **unblocked** — see below |
| ps3 | — | — | `--endpoint-url` | **unblocked**, but no `--prefix` — see below |
| s4cmd | 1 | 0 | `--endpoint-url` (accepted) | **incompatible**: v1 `ListObjects` only |

### The two "blocked pending test" tools both resolve to working

- **s5cmd** lists correctly against a server that answers HeadBucket with 405.
  Its mandatory region probe is not fatal: the failure falls back rather than
  aborting the listing. It also works signed (dummy creds) as well as with
  `--no-sign-request`.
- **ps3** speaks path-style to a custom endpoint and returns correct rows; its
  unconditional `GetBucketLocation` takes the server's 400 and continues, which
  is the documented swallow. It has no `--prefix`, so the probe listed the whole
  788,903-object fixture and was cut off by the harness timeout at 120 s having
  emitted 7,992 correct rows. Compatibility: yes. (The rate that implies is not
  a measurement — no injection, and a server sharing two cores with it.)

### Corrections to the audit's inferred cells

- **rclone**: `endpoint=http://127.0.0.1:19090` inside a connection string fails
  with "Custom endpoint `http` was not a valid URI" — the connection-string
  parser splits on the URL's own colons. `endpoint="http://127.0.0.1:19090"`
  (either quote style) works. This is a capsule-wiring detail, not a tool gap.
- **aws-cli** listed correctly *without* the `AWS_CONFIG_FILE`
  `addressing_style = path` the audit predicted it would need, and **rclone**
  without `force_path_style=true`. Both SDKs force path-style for an IP-literal
  endpoint on their own. Keep the explicit settings anyway — they cost nothing
  and the inference does not carry to a DNS-named endpoint — but neither is a
  blocker.

### Mode gates, confirmed by observation rather than inference

Every mode the audit proposed excluding fails exactly as predicted:

| Excluded mode | Observed |
| --- | --- |
| aws-cli `s3api-v1-text` | `InvalidArgument … only ListObjectsV2 is supported` |
| rclone `listv1` (`list_version=1`) | same, from `ListObjects` |
| s5cmd `--use-list-objects-v1` | same |
| minio-mc root-scope `find` | `405 Method Not Allowed` on the bucket stat |
| minio-mc without `MC_REGION` | `Unable to list folder` — its GetBucketLocation is rejected |
| s4cmd (entire listing path) | `InvalidArgument` on `ListObjects`; no v2 path exists |

## Output hashes (sha256, first 16 hex)

`swath b29d19eba0cf7316` · `s3p 2fa49f4acd7d28e7` · `s7cmd b3f07395d2001637` ·
`aws-cli a47ac51bc854caff` · `minio-mc 7f0dd7cb6bd99509` · `s3kor 2a3c0ab0944902ec` ·
`s5cmd 2ddb61e3417db81c` · `rclone 6f3ebda33e0ff9f5`

Raw stdout is not committed (owner's rule on listing data); `run.sh` beside this
file reproduces every row against the same fixture.

## What this receipt does not establish

- No capacity claim. One incidental datapoint: swath listing the full 788,903-key
  fixture over the loopback endpoint took 2 m 27 s with the server sharing the
  same two cores — which is the harness-saturation regime the design's headroom
  rule exists to detect, not a server capacity result.
- No latency injection was configured, so no run here discriminates between
  tools in the way phase 1 intends.
- Path-style was confirmed only for an IP-literal endpoint.
