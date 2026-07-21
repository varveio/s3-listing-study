# Pre-flight — drift check before smoke [RUN]

Before any smoke run (brief § Stage C pre-flight), the smoke bucket was re-listed
**anonymously with the pinned harness client** (never a host CLI) and diffed
against the registry manifest, whose sha256 was verified first. This is the
drift gate: a mismatch would `DRIFT`-stop the campaign (orchestrator re-baselines,
never the agent). Result: **no drift**.

## Method (reproducible)

Manifest identity (verified before use):
`<data>/manifests/noaa-normals-pds.2026-07-17.tsv.gz`,
sha256 `c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb`
(matches `docs/smoke-bucket.md`).

Re-list with the pinned harness client (per `docs/smoke-bucket.md` § Harness client):

```sh
docker run --rm --network host -e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC \
  amazon/aws-cli@sha256:eb85b2c72442c9eab0bdbe608095b9b909bc2a7136924124d63fe0c03b2ec334 \
  s3api list-objects-v2 --bucket noaa-normals-pds --region us-east-1 --no-sign-request \
  --query 'Contents[].[Key,Size,ETag,LastModified,StorageClass]' --output text
# then canonicalize: strip ETag quotes, rewrite +00:00 -> Z
```

## Assertion

Both the manifest and the canonicalized re-list, compared as **sorted sets**,
hash identically:

| | sha256 of `... | LC_ALL=C sort` |
| --- | --- |
| Manifest (contract-v2, 148,917 rows) | `8b5b584ed989f25fbb7043266ea9453c77be60bd4536108c8499610156983aac` |
| Anonymous re-list (canonicalized) | `8b5b584ed989f25fbb7043266ea9453c77be60bd4536108c8499610156983aac` |

`diff` over the sorted sets = **0 lines**; 148,917 = 148,917 keys. The bucket did
**not** drift from its 2026-07-17 snapshot. The manifest sorted-set hash is
reproducible from the committed registry digest at any time:
`zcat <manifest> | LC_ALL=C sort | sha256sum`. The full re-list output (148,917
lines) is not committed (no-data-in-repo rule); the sorted-set hash is the
binding, exactly as for the manifest itself.
