# Image builds and publication

Every measurement runs inside a container image, so an image whose provenance is
uncertain invalidates the numbers taken from it. This is how those images are
built, named, and published, and what causes each layer to be rebuilt.

## Three layers, each pinned to its parent's digest

```text
debian@sha256:…            digest-pinned Debian root
      │
      ▼
shared runtime             one for the whole study
      │                    Debian python3.11, DuckDB, ijson; packages installed
      │                    from a frozen snapshot and compared against
      │                    harness/shared-image/debian-packages.lock
      ▼
tool parent  ×11           exactly one subject tool, fetched by
      │                    ADD --checksum. No adapter, no worker code.
      ▼
execution image ×11        attempt.pyz + one adapter + selection metadata
                           + image-provenance.json naming its parent
```

A parent is always named `…@sha256:…`, never by tag: `build_selection.py` refuses
a reference that is not a digest. The execution image bakes its parent's digest
into `/opt/s3-listing-study/image-provenance.json`, and `validate_selection.py`
checks that record — and imports the worker from the zipapp — *during the build*,
so a payload that cannot import fails the build rather than a benchmark run.

## What a change rebuilds

Each layer's identity is a hash over exactly the repository bytes that reach it,
so what a tag names and what invalidates it are the same statement.

| You change | Shared runtime | Tool parents | Execution images | Images built |
| --- | --- | --- | --- | --- |
| `src/…/worker/**` or `src/…/common/**` | — | — | all 11 | 11 |
| one tool's `adapter/**` | — | — | 1 | 1 |
| one tool's `build/**` | — | 1 | 1 | 2 |
| `harness/shared-image/**`, `.dockerignore`, DuckDB or ijson pin | 1 | 11 | 11 | 23 |

The first row is the ordinary case: editing Python rebuilds only the thin worker
layer, on top of tool parents that are fetched by digest and never rebuilt.
Pushing a child into the same repository as its parent uploads only the new
layer, because the parent's blobs are already there.

Rebuilds cascade downwards, never sideways. A tool recipe change rebuilds that
tool's execution image even though the worker bytes are identical, because the
parent's hash is part of the child's name — and it leaves the other ten subjects
untouched.

## What CI does

`s3-listing-study ci plan` resolves every shared, tool, and execution reference
over the registry API — no layers pulled, a few hundred milliseconds — and sorts
each tool into one bucket:

- **chain** — the tool parent is absent, so it and its execution child are built
  together in one job. A tool recipe change, or a first build on a branch.
- **bake** — the parent is published and only the worker layer is new. Every such
  tool is one target in a single `docker buildx bake`.
- **adopt** — both are published. Nothing is built; the existing digest is used.

A bucket that comes back empty produces **no job at all**, so the shape of a run
on the Actions page is the answer to "what changed". Nothing decides this from
which files a commit touched; it is decided by what the registry is missing.

Run the same command yourself to see the plan in a second, without spending a
CI run:

```sh
uv run s3-listing-study ci plan \
  --repository ghcr.io/varveio/s3-listing-study --ref-name "$(git branch --show-current)"
```

## What happens automatically, and what does not

| Event | Builds and validates | Publishes |
| --- | --- | --- |
| Pull request touching an image input | automatically | no |
| Push to `main` touching an image input | automatically | yes, to the `main` channel |
| Push to a branch with no pull request | no | no |
| `publish-images` label on a same-repository pull request | automatically | yes, to that branch's channel |
| `workflow_dispatch` with `publish_to_ghcr` | automatically | yes, to the selected branch's channel |

**Branch publication is always opt-in.** Ordinary branch work builds and
validates through its pull request but publishes nothing; a branch image set
reaches GHCR only through the label or a manual dispatch. A dispatch also accepts
a `tools` filter, so one subject can be rebuilt without the other ten.

Publication requires `packages: write`, and that permission is granted in exactly
one file — `.github/workflows/images-publish.yml`. GitHub does not allow
permissions to be computed from an expression, so the pull-request caller and the
publishing caller are separate files that call one shared workflow. A
pull-request run cannot obtain a write token however it is triggered, and a fork
cannot reach the publishing caller at all.

## Tags

```text
shared-python3.11-<shared12>
tool-<tool>-v<toolver>-base-<shared12>-build-<build12>
execution-<tool>-v<toolver>-base-<shared12>-build-<build12>-worker-v<wver>-src-<worker12>
```

Each twelve-character component is the prefix of the content hash of the inputs
named in the table above, so the tag is a readable statement of what the image is
made of. Version tags are immutable: publication adopts an existing one after
validating it and never moves it.

Movable channels are `execution-<tool>-<suffix>` and `set-<suffix>`, where the
suffix is `main` or `branch-<slug>-<hash>`. `set-v2-<manifest12>` is an image
whose only content is the publication manifest — the durable ledger, outliving
Actions artifact retention. Individual execution channels may move one at a time,
but the set channel moves **last**, after every promotion has been verified, so it
never advertises an incomplete set.

## Boundaries

- CI never invokes a subject tool. It proves an image is structurally correct —
  non-root, right entrypoint, provenance consistent, worker payload imports — not
  that the tool runs. That is what the smoke campaign and Batch runs are for.
- Published images are plain manifests, never attestation indexes: BuildKit's
  default would wrap an image in an index and the promoted digest would stop
  naming the image. Attestations are disabled explicitly and the published shape
  is asserted. Standard SLSA provenance is attached separately, where it cannot
  change a digest.
- Retention and deletion are out of scope. Historical `*-run-*` and `*-sha256-*`
  tags are left untouched pending a separate audit.
