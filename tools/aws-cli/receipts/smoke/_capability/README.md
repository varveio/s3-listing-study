# Capability probe — request behavior via `--debug` (NOT a measured receipt)

Ran outside the wrapper (a `--debug` capability probe, not a timed mode run):

    docker run --rm --network host -e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC \
      amazon/aws-cli@sha256:406ca32d31e640a56e8d52921b40528cc64bfa59ec9cb4ee1456db6746cb7292 \
      s3api list-objects-v2 --bucket noaa-normals-pds --region us-east-1 \
      --no-sign-request --prefix normals-hourly/ --debug --output text

Full debug stderr: `debug-hourly.stderr.raw.gz` (secret-scanned clean before compression).

## Observations [OBS --debug, aws-cli 2.36.1]

- **Serial pagination.** 3 `ListObjectsV2` requests for the 2,549-key `normals-hourly/`
  prefix = ceil(2549/1000). All emitted from a single `MainThread`; no worker
  threads appear in the log. Request N+1 carries the prior response's
  `NextContinuationToken` as its `ContinuationToken` (token `14965Nw5dul2klI+…`
  is returned by response 1 and sent in request 2), and request timestamps are
  strictly increasing (49.647 → 49.998 → 50.179), so each request is issued only
  after the previous response returns. This is the botocore paginator loop, not
  concurrency.
- **Unsigned confirmed at request level.** All 3 requests log `'auth_type': 'none'`;
  zero `Authorization:` / `AKIA…` / `Signature=` lines in the whole debug stream.
- **URL shape.** `GET https://noaa-normals-pds.s3.us-east-1.amazonaws.com/?list-type=2&prefix=normals-hourly%2F&encoding-type=url`
  (virtual-hosted-style, `list-type=2`, `encoding-type=url`).
- **No built-in API-call counter.** The count above is derived from the debug log;
  aws-cli prints no request tally on stdout/stderr in normal operation.
