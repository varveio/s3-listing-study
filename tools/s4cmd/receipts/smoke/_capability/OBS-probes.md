# [OBS] direct probes — binding metadata

These two runs did **not** go through `smoke-run.sh` (they are `[OBS]`, not
receipts). Metadata bound here so the claims that cite them are auditable.

Image (both): `localhost:5000/s4cmd-study@sha256:d458ef5096180e517840712e29b0b8705ec97cebf48f717cad2fea3805105813` (arm64). Env (both): `-e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC`, no credentials.

| Probe | Invocation (argv after entrypoint `s4cmd`) | Exit | stdout sha256 | stderr sha256 |
| --- | --- | --- | --- | --- |
| direct-bare-env | `ls -r -c 4 s3://noaa-normals-pds/normals-hourly/` | 1 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | `4b6f0f3521b537880ccc0dc109f5c23963f2d1048c32f3c737a9a2531fb6254e` |
| obs-multiprefix | `ls s3://noaa-normals-pds/normals-hourly/ s3://noaa-normals-pds/normals-daily/` | 1 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | `c064f6ed2f4fd8fb1af3ea04c3f3e36db9df46a04e344a51488d2a1601a7c5ff` |

- direct-bare-env: bare no-cred env; `list_objects` inside the s3walk worker
  fails `Unable to locate credentials`, caught by the worker's generic
  `except Exception` (s4cmd.py:540) and surfaced as `[Thread Failure]`
  (s4cmd.py:469) — it does **not** reach the main-thread
  `except NoCredentialsError` at line 1933.
- obs-multiprefix: `ls` with two paths → `[Invalid Argument] Invalid number of
  parameters` at `validate('cmd|s3')` (s4cmd.py:1631), before any S3 call —
  confirms `ls` takes exactly one path (`args[1]`).
