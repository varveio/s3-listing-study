# Pre-flight — noaa-normals-pds (Stage C prerequisite)

Date (UTC): 2026-07-17T12:54:05Z
Harness client: amazon/aws-cli@sha256:eb85b2c72442c9eab0bdbe608095b9b909bc2a7136924124d63fe0c03b2ec334
Invocation: docker run --rm --network host -e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC <hc> \
  s3api list-objects-v2 --bucket noaa-normals-pds --region us-east-1 --no-sign-request \
  --query 'Contents[].[Key,Size,ETag,LastModified,StorageClass]' --output text
Canonicalization: strip surrounding ETag quotes; mtime +00:00 -> Z; sort (LC_ALL=C).

Manifest: <data>/manifests/noaa-normals-pds.2026-07-17.tsv.gz
Manifest sha256 (registry-bound): c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb  [verified OK]
Keys: 148917 relisted == 148917 manifest.

VERDICT: PASS — re-list is byte-identical to the manifest (no drift).
  sha256(sorted relist)   = 8b5b584ed989f25fbb7043266ea9453c77be60bd4536108c8499610156983aac
  sha256(sorted manifest) = 8b5b584ed989f25fbb7043266ea9453c77be60bd4536108c8499610156983aac

Large intermediate TSVs removed after diff (no-data-in-repo); reproducible from
the invocation above.
