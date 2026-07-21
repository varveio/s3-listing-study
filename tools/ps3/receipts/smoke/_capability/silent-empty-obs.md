# [OBS] pS3 credential-absent behaviour — two paths

NOT a wrapper receipt (the wrapper always applies its full credential-starve
env). Recorded as an honest observation of the shipped binary under emulation
(amd64 on arm64, qemu). Image
`ps3-study@sha256:c0d7b655163832bf769af0dd5da037c17f6b7b1b519724b8291297b5ae539663`,
`pS3 version 0.1.16`. All runs `--network host`, no credentials anywhere on the
box (GCP runner, AWS_EC2_METADATA_DISABLED=true).

## Path 1 — harness canonical starvation (also captured as the wrapper receipt in the sibling `list-anon/` dir)
Env includes `AWS_CONFIG_FILE=/nonexistent-by-harness`,
`AWS_SHARED_CREDENTIALS_FILE=/nonexistent-by-harness`.
Command: `pS3 list-objects-v2 --bucket noaa-normals-pds --region us-east-1`
Result: **exit 1**, stderr `error: S3 session creation failed`, no stdout.
Deterministic 5/5 (non-trace) + 3/3 (--trace).
[INFERRED from SRC] aws-sdk-go v1 session.NewSessionWithOptions with
SharedConfigState=SharedConfigEnable errors when the explicitly-pointed config
file path does not exist; pS3 fatals on that first session-build error before
issuing any S3 API call.

## Path 2 — bare no-credentials (only AWS_EC2_METADATA_DISABLED=true; no file redirect)
Command: `pS3 list-objects-v2 --bucket noaa-normals-pds --region us-east-1`
Result: **exit 0, ZERO objects printed, no error** (3/3). With `--trace`, one
early sample showed the full flow: getBucketLocation returns
`NoCredentialProviders: no valid providers in chain`, region is then set empty,
findPrefixes discovers 0 prefixes, `debug: item count: 0`, exit 0.
Adding `AWS_REGION=us-east-1` (still no creds): same — exit 0, empty (2/2).

## Finding
pS3 has NO unsigned/anonymous request path (no AnonymousCredentials, no
--no-sign-request; source uses session.SharedConfigEnable only, [SRC
cmd/listObjectsV2.go:90-99 @ 9428492]). With no credentials it EITHER fatals at
session creation (exit 1) OR — more dangerously — returns **exit 0 with an
empty listing and no error**, which a caller cannot distinguish from a genuinely
empty bucket. Either way it cannot list anonymously; with CREDS=none every
listing mode is BLOCKED (untested-for-this-reason), not skipped.
