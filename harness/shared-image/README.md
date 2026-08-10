# Shared study runtime

[`Dockerfile`](Dockerfile) is the controlled parent inherited by every tool
image. It installs Debian's Python 3.11 from the signed 2026-08-03 Bookworm
snapshot, verifies the complete installed `package:arch=version` closure against
[`debian-packages.lock`](debian-packages.lock), and installs the checksum-locked
DuckDB 1.5.5 wheel into `/usr/local/lib/python3.11/dist-packages`.

- a digest-pinned Debian/glibc userspace;
- the Debian CA trust store and minimal runtime libraries;
- Debian's own Python 3.11, version-pinned from one signed snapshot;
- one checksum-verified DuckDB runtime for that interpreter; and
- one checksum-verified ijson 3.5.1 runtime with its compiled `yajl2_c` backend.

The image contains no tool, adapter, worker, manager, plan, or receipt. Its
Debian packages resolve from one signed snapshot and are version-pinned, and the
whole installed set is compared against a committed lock file; the DuckDB and
ijson wheels are checksum-verified before they enter the build.

This is a common base filesystem, not a claim that every tool uses the same
runtime internals. Static binaries and bundled Node, Python, or JRE payloads may
retain their own resolver, TLS, allocator, and language-runtime behavior.

It ends as fixed user `10001:10001`, with writable `HOME=/home/s3study`.
Root-owned markers under `/opt/s3-listing-study/` record the source-input hash,
package closure, and runtime identity. This is content-pinned and recorded for
reuse; package installation is not claimed to produce a bit-identical OCI digest
on every rebuild.

Build it from the repository root:

```sh
uv run s3-listing-study build-shared-image --tag study/runtime:candidate
```

Before building tool children, export or push the image and resolve its
immutable `REGISTRY/IMAGE@sha256:<digest>` reference. The tool command rejects
mutable parent tags:

```sh
uv run s3-listing-study build-tool-image \
  --tool rclone \
  --shared-base-image "$SHARED_RUNTIME_IMAGE" \
  --tag study/tool-rclone:candidate
```

See [`../derived-image/README.md`](../derived-image/README.md) for the complete
three-layer flow.
