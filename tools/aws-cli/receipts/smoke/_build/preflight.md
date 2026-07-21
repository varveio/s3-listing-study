# Pre-flight drift check (bucket vs manifest) — 2026-07-17

Per the brief's Stage C pre-flight: re-list `noaa-normals-pds` anonymously with
the **pinned harness client** (NOT my smoke image — this is a drift check, not a
mode receipt, and runs outside `smoke-run.sh`), canonicalise identically to the
snapshot pipeline, and diff against the registry manifest.

    docker run --rm --network host -e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC \
      amazon/aws-cli@sha256:eb85b2c72442c9eab0bdbe608095b9b909bc2a7136924124d63fe0c03b2ec334 \
      s3api list-objects-v2 --bucket noaa-normals-pds --region us-east-1 --no-sign-request \
      --query 'Contents[].[Key,Size,ETag,LastModified,StorageClass]' --output text
    # then: strip ETag quotes, rewrite mtime +00:00 -> Z, LC_ALL=C sort

## Result [RUN pre-flight, harness client aws-cli 2.36.0, 2026-07-17]

| | |
| --- | --- |
| Re-list keys | 148,917 (exit 0, ~29 s) |
| Canonicalised re-list sha256 (sorted) | `8b5b584ed989f25fbb7043266ea9453c77be60bd4536108c8499610156983aac` |
| Manifest sha256 (sorted, gunzipped) | `8b5b584ed989f25fbb7043266ea9453c77be60bd4536108c8499610156983aac` |
| Registry manifest digest (gz) | `c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb` (verified before use) |
| Verdict | **byte-identical — bucket has NOT drifted** |

The two sorted-TSV sha256 values match exactly, so the live bucket equals the
2026-07-17 snapshot. This is a drift gate, not a tool measurement; it uses the
harness client (the same tool that produced the manifest) by design.
