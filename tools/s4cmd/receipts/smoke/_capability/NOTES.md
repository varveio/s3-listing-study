# Capability probe — s4cmd anonymous / unsigned access

**Finding:** s4cmd 2.1.0 has **no unsigned/anonymous access path**. It cannot
list a public bucket without AWS credentials. `CREDS=none` for this campaign, so
every listing mode is **blocked, not skipped**.

## Why (source)

`BotoClient.__init__` (s4cmd.py:375-386) builds the boto3 S3 client and passes
`signature_version=UNSIGNED` **nowhere** — it only supplies credentials when both
an access key and a secret key are present, otherwise calls
`self.boto3.client('s3', endpoint_url=opt.endpoint_url)` with defaults. boto3
therefore resolves/uses SigV4 credentials, and with none available the run dies.
There is no `--no-sign-request` flag, config setting, or env convention anywhere
in `--help` or the source. Upstream issue #139 asks for exactly this and it was
never added.

## Two observed failure shapes (same root cause)

1. **Under the wrapper (canonical receipt):** `anon-nocredentials/` —
   `auth=anonymous`, exit 1, 0.212s. Fails at **client construction**
   (`BotoClient.__init__`, s4cmd.py:386) with
   `botocore.exceptions.InvalidConfigError: ... configured to assume role with
   web identity but has no role ARN configured`. This exact text is an
   interaction with the wrapper's credential neutralization, which sets
   `AWS_WEB_IDENTITY_TOKEN_FILE=/nonexistent-by-harness` and `AWS_ROLE_ARN=`
   (empty): that activates botocore's web-identity provider, which then has no
   role ARN. The point stands regardless — with no `UNSIGNED` config, s4cmd
   cannot even build its client credential-starved, let alone issue a LIST.

2. **Direct, bare no-cred env (`[OBS]`, not a wrapper receipt):**
   `direct-bare-env.stderr.txt` — same image, only `AWS_EC2_METADATA_DISABLED=true`
   and `TZ=UTC`, no web-identity vars. Here the client constructs, then the
   `list_objects` paginator call inside the s3walk worker thread fails with
   `Unable to locate credentials` (botocore `NoCredentialsError`), surfaced as
   `[Thread Failure] Unable to locate credentials`, exit 1. This is the general
   credential-starved case. Note the error is caught by the worker thread's
   generic `except Exception` (s4cmd.py:540 → `[Thread Failure]` at 469); it does
   **not** reach the main-thread `except BotoClient.NoCredentialsError` at
   s4cmd.py:1933, which only fires for main-thread errors. Recorded as `[OBS]`
   because it was not produced through `smoke-run.sh` (binding metadata in
   `OBS-probes.md`).

Both confirm the bucket is not at fault: an anonymous scoped list of the same
prefix with the pinned harness client (`--no-sign-request`) succeeds (see the
report's Smoke results).
