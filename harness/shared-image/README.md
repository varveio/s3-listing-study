# Shared study base

[`Dockerfile`](Dockerfile) defines the base inherited by every comparative
image. It contains only:

- a digest-pinned Debian/glibc userspace;
- the Debian CA trust store and minimal runtime libraries;
- one checksum-verified python-build-standalone CPython 3.12 distribution; and
- one checksum-verified DuckDB runtime for that interpreter; and
- one checksum-verified ijson 3.5.1 runtime with its compiled `yajl2_c` backend.

It contains no tool, worker, adapter, manager, benchmark plan, or receipt. Its
Debian packages resolve from one signed snapshot and are version-pinned; the
Python archive and the DuckDB and ijson wheels are checksum-verified before they
enter the build.

This is a common base filesystem, not a claim that every tool uses the same
runtime internals. Static binaries and bundled Node, Python, or JRE payloads may
retain their own resolver, TLS, allocator, and language-runtime behavior.

Build it once from the repository root:

```sh
BASE_TAG=us-east1-docker.pkg.dev/PROJECT/REPOSITORY/shared-base:2026-08-10
uv run s3-listing-study build-shared-image --tag "$BASE_TAG"
```

The command builds a local tag. It does not publish or resolve a registry
digest, so the operator must do both explicitly:

```sh
docker push "$BASE_TAG"
docker image inspect --format '{{json .RepoDigests}}' "$BASE_TAG"
```

Every subsequent `build-derived-image` or `publish-derived-image` invocation
receives the same complete immutable reference through
`--shared-base-image REGISTRY/...@sha256:<digest>`. A canonical
`shared_base_source_sha256` records the source inputs, but it does not substitute
for the actual OCI URI and digest; image sets and results retain both.

See [`../derived-image/README.md`](../derived-image/README.md) for tool payload,
final assembly, registration, and publication details.
