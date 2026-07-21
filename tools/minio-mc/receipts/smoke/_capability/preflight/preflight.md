# Pre-flight drift check — noaa-normals-pds (2026-07-17) [OBS]

Mandatory Stage C pre-flight: independent anonymous re-list of the smoke bucket
with the **pinned harness client**, diffed against the registry manifest, to
detect drift before any smoke run. This is an `[OBS]` artifact (run directly, not
through the smoke-run wrapper — the harness has no dedicated pre-flight receipt
mode); it records the command and the hash comparison, not the 148,917-line
listing body (no-data-in-repo rule).

**Manifest sha256 verified first:**
`c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb` matches the
registry entry for `noaa-normals-pds.2026-07-17.tsv.gz`.

**Independent re-list command (anonymous, pinned harness client):**

```sh
docker run --rm --network host -e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC \
  amazon/aws-cli@sha256:eb85b2c72442c9eab0bdbe608095b9b909bc2a7136924124d63fe0c03b2ec334 \
  s3api list-objects-v2 --bucket noaa-normals-pds --region us-east-1 \
  --no-sign-request \
  --query 'Contents[].[Key,Size,ETag,LastModified,StorageClass]' --output text
# then canonicalise: strip ETag quotes; rewrite '+00:00' -> 'Z'
```

**Result:** 148,917 lines, byte-identical to the decompressed manifest.
Decompressed sha256 of BOTH the manifest body and the canonicalised re-list:
`8b5b584ed989f25fbb7043266ea9453c77be60bd4536108c8499610156983aac`.

**Verdict: no drift.** The bucket has not moved since the 2026-07-17 snapshot;
the smoke runs verify against a live-confirmed reference. Captured 2026-07-17.
