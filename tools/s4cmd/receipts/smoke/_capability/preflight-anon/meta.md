# Pre-flight receipt — anonymous bucket accessibility (harness client, NOT s4cmd)

Purpose: establish that `noaa-normals-pds` is anonymously listable with a
proper unsigned client, so s4cmd's credential-starved failure is the tool's
limitation, not the bucket's. This is a harness-client run (`s3api`), evidence
in support of the capability finding — not an s4cmd smoke receipt.

| | |
| --- | --- |
| Date (UTC) | 2026-07-17T12:53:36Z |
| Tool | pinned harness client `amazon/aws-cli@sha256:eb85b2c7...ec334` |
| Invocation | `docker run --rm --network host -e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC amazon/aws-cli@sha256:eb85b2c72442c9eab0bdbe608095b9b909bc2a7136924124d63fe0c03b2ec334 s3api list-objects-v2 --bucket noaa-normals-pds --region us-east-1 --no-sign-request --prefix normals-hourly/ --max-items 10 --query Contents[].[Key,Size] --output text` |
| Auth | anonymous (`--no-sign-request`, `AWS_EC2_METADATA_DISABLED=true`) |
| Exit code | 0 |
| stdout sha256 | `2ab3933a935b06e4024c83b1c0e4ee5ee62cf8b35f7053148a2a90683141371e` (see `stdout.txt`, 565 bytes) |
| Result | returns keys under `normals-hourly/` — bucket is live and anonymously listable |

Scope: this run only proves anonymous accessibility of the bucket at this time.
It is not verified against the full manifest (no s4cmd mode could consume it).
