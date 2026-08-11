# Tool and execution images

This file describes the recipe. For how the layers fit together, what a change
rebuilds, how CI decides what to build, and how to find a published digest, see
[`docs/operating/image-builds.md`](../../docs/operating/image-builds.md).

The runnable image chain is explicit OCI parentage:

```text
shared Debian/Python runtime -> tool image -> execution image
```

Each `tools/<tool>/build/Dockerfile` consumes an immutable shared-runtime
reference and produces a real runnable tool image. The generic
[`Dockerfile`](Dockerfile) then
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
its exact tool parent, canonical selection hash, and canonical worker source
hash in `/opt/s3-listing-study/image-provenance.json`. New provenance uses
schema 2; schema-1 provenance remains readable. The worker source hash is an
image-assembly identity, not a field in an individual attempt's `result.json`.

Benchmark results and campaign image sets have separate compatibility
contracts: historical result and image-provenance schemas remain readable, but
retired campaign image-set inputs do not. Current campaign image sets require
split-layer schema 3. Publication ledgers avoid that vocabulary; they carry a
kind-specific `format_version` instead.

## GHCR tags and publication

The public package uses four immutable version-tag families:

- `shared-python3.11-<source12>` identifies canonical shared-runtime inputs.
- `tool-<tool>-v<version>-base-<source12>-build-<build12>` identifies a tool
  parent independently of adapters and worker code.
- `execution-<tool>-v<version>-base-<source12>-build-<build12>-worker-v<version>-src-<source12>`
  identifies the complete worker/tool assembly.
- `set-v2-<manifest12>` stores a durable publication manifest at
  `/manifest.json`.

Those are guarded version tags: publication adopts an existing tag only after
validating its identity and never intentionally moves it. Execution
`*-main`/`*-branch-*` tags and `set-main`/`set-branch-*` tags are movable
channels. The set channel advances last and is the authority that all eleven
execution channels form one ready publication. The `set-v2` suffix hashes the
exact manifest bytes, including checkout and reuse metadata, so a later
publication of the same image digests can intentionally create a different
ledger entry. Historical `*-run-*` and `*-sha256-*` tags are left untouched
until a separate retention audit decides their fate.

A relevant push to `main` publishes automatically. Pull requests remain
build-only unless a same-repository maintainer applies the `publish-images`
label; `workflow_dispatch` with `publish_to_ghcr=true` is the other explicit
publication path, and it accepts a `tools` filter so one subject can be
rebuilt without the other ten.

Applying the label always starts a publication, whether or not that pull
request changed an image input. The label states an intent — publish this
branch's set — and an explicit action that silently does nothing is worse than
one that quickly confirms the set is already published. Where nothing has
changed, the run resolves every reference, finds the whole set present, and
advances the branch channels onto the digests that already exist.

What a run actually does is decided by what the registry is missing, not by
which files changed. `s3-listing-study ci plan` resolves every shared, tool,
and execution reference over the registry API — no layers, a few hundred
milliseconds — and each tool lands in one of three buckets: build the parent
and its child together, bake the worker layer onto a published parent, or adopt
what is already published. A bucket that comes back empty produces no job, so
the shape of the run is the answer to what changed. The common case, a change
under `worker/` or `common/` or an adapter, is one `docker buildx bake` over
every tool: the parents are fetched by digest, and pushing a child into the
same repository as its parent uploads only the new layer.

Schedulers append typed logical-request arguments to the fixed entry point:

```text
/usr/bin/python3 -I /opt/s3-listing-study/attempt.pyz
```
