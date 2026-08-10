# Tool and execution images

The runnable image chain is explicit OCI parentage:

```text
shared Debian/Python runtime -> tool image -> execution image
```

Each `tools/<tool>/build/Dockerfile` consumes an immutable shared-runtime
reference and produces a real runnable tool image. It no longer exports a
scratch `/tool-root` overlay. The generic [`Dockerfile`](Dockerfile) then
consumes the immutable tool image and adds only the current worker zipapp,
selected adapter, selection metadata, and root-owned provenance.

Build and resolve each layer separately:

```sh
uv run s3-listing-study build-shared-image --tag study/runtime:candidate
# export/push and resolve SHARED_RUNTIME_IMAGE=...@sha256:...

uv run s3-listing-study build-tool-image \
  --tool swath \
  --shared-base-image "$SHARED_RUNTIME_IMAGE" \
  --tag study/tool-swath:candidate
# export/push and resolve TOOL_IMAGE=...@sha256:...

uv run s3-listing-study build-derived-image \
  --tool swath \
  --tool-image "$TOOL_IMAGE" \
  --tag study/execution-swath:candidate
```

A worker-only change therefore rebuilds only the final layer when the tool
parent is supplied by digest; it does not depend on retained BuildKit cache.
The execution image runs as `10001:10001`, uses `/usr/bin/python3`, and records
its exact tool parent and canonical selection hash in
`/opt/s3-listing-study/image-provenance.json`. New results use schema 3;
historical schema-2 results and image sets remain readable.

Schedulers append typed logical-request arguments to the fixed entry point:

```text
/usr/bin/python3 -I /opt/s3-listing-study/attempt.pyz
```
