# Final per-tool attempt image

[`Dockerfile`](Dockerfile) is the common final assembly recipe. It combines the
exact published shared base, one capsule-owned `/tool-root` payload, and current
worker/common code with one adapter and selection record. The output is one
image and digest per tool, scheduled directly by Batch.

Tool payloads prefer checksum-pinned official distributions; `s3-fast-list` is
the sole native source-build exception. The common base fixes shared filesystem
inputs but does not erase bundled or static runtime differences. Full capsule
and registration rules live in
[`../../docs/operating/tool-structure.md`](../../docs/operating/tool-structure.md)
§ Executable integration and builds.

Worker or adapter edits do not invalidate the separate tool target. Reusing it
requires retained or restored BuildKit cache; registry-backed cache for fresh
builders is not implemented yet.

## Build and publication flow

First build the shared base once, tag it in the target registry namespace, push
it, and resolve the registry's immutable digest. There is no publication helper
for this step yet:

```sh
BASE_TAG=us-east1-docker.pkg.dev/PROJECT/REPOSITORY/shared-base:2026-08-10
uv run s3-listing-study build-shared-image --tag "$BASE_TAG"
docker push "$BASE_TAG"
docker image inspect --format '{{json .RepoDigests}}' "$BASE_TAG"
```

Select the one returned reference for that repository and keep the complete
`REGISTRY/...@sha256:<64 lowercase hex>` value:

```sh
BASE_IMAGE=us-east1-docker.pkg.dev/PROJECT/REPOSITORY/shared-base@sha256:BASE_DIGEST
```

Build one final image locally from that exact base:

```sh
uv run s3-listing-study build-derived-image \
  --tool swath \
  --shared-base-image "$BASE_IMAGE"
```

Or build, push, inspect, and atomically add it to a campaign image set:

```sh
uv run s3-listing-study publish-derived-image \
  --tool swath \
  --repository us-east1-docker.pkg.dev/PROJECT/REPOSITORY \
  --image-set build/images.json \
  --shared-base-image "$BASE_IMAGE"
```

The production commands reject a mutable base tag. Reuse the same
`BASE_IMAGE` for every tool in an image set; image-set validation rejects mixed
base digests or source identities.

With no explicit `--tag`, the readable local name is:

```text
s3-listing-study/swath:0.2.2-h0.1.0-5dbe7637c089
                └tool  └release └harness └tool-build prefix
```

That tag is not an identity. Publication records the final image's own immutable
URI and digest, the shared-base URI/digest/source identity, tool build and
artifact identities, adapter bundle, and harness revision. The worker result
records the same execution-relevant provenance.

## Running

The final image fixes this entry point; schedulers append typed logical request
arguments and never replace it:

```text
/opt/s3-listing-study/python/bin/python3 -I /opt/s3-listing-study/attempt.pyz
```

The child process receives a minimal allowlisted environment rather than the
worker's Python or TLS settings. Runtime, security, output, and authentication
details are documented in [`../README.md`](../README.md) and
[`../../docs/operating/runner-security.md`](../../docs/operating/runner-security.md).
