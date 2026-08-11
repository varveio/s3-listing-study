# Groundwork build recipe for s3-fast-list.
ARG SHARED_BASE_IMAGE
#
# This is upstream's own Dockerfile (aws-samples/s3-fast-list @ 6c72f59, the
# feat/no-sign-request branch = upstream b11e385 + the 2-commit anonymous-access
# patch) with ONE deviation, documented here because the study forbids silent
# reconfiguration:
#
#   Upstream pins `FROM rust:1.86-slim`. It does NOT build at the pinned SHA today.
#   Cargo.lock is .gitignored (no committed lockfile), and Cargo.toml uses loose
#   semver requirements (e.g. `aws-config = "1.1.1"`, `aws-sdk-s3 = "1.11.0"`), so
#   `cargo build --release` resolves the newest semver-compatible transitive deps,
#   which now demand rustc >= 1.94.1 (aws-smithy-runtime-api-macros@1.1.0,
#   aws-smithy-types@1.6.1, aws-types@1.4.0). Under rust:1.86 the build fails with
#   exit 101 ("requires rustc 1.94.1"). See ../research/report.md § Container.
#
#   The minimal faithful fix is to advance the *builder toolchain* to satisfy the
#   floated deps — exactly what an upstream user running this Dockerfile today
#   would have to do — rather than hand-pin dozens of transitive crates to a
#   version set upstream never recorded. The builder is kept on the SAME Debian
#   release as upstream's (bookworm → glibc 2.36) so the binary still links
#   against the distroless/cc-debian12 runtime; a trixie-based `rust:1.94-slim`
#   builds a binary needing GLIBC_2.39 that the debian12 runtime cannot load.
#   Nothing else is changed. The BUILT IMAGE'S DIGEST (recorded in every receipt)
#   is the run's identity; this file is the best-effort recipe.
FROM rust@sha256:cf9dd0ec73e75f827fe59123fff9dc65af1a1c8363c3c31ee8d7f8ad0b6a5fb2 AS builder

WORKDIR /usr/src/app

ADD --checksum=sha256:43bebce8cc64c85c25ff4d7b5fe497b46849825b4ff37fb0c7c230dcdf4bb6a0 \
    https://github.com/sagiba/s3-fast-list/archive/6c72f596e2ffe7311dec8cb7de29b114c0251207.tar.gz \
    /tmp/source.tar.gz
RUN tar xzf /tmp/source.tar.gz --strip-components=1
COPY --from=tool_build Cargo.lock /usr/src/app/Cargo.lock

# Cache Cargo deps and build with optimal caching
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    cargo build --release --locked


FROM ${SHARED_BASE_IMAGE}
USER root
COPY --from=builder /usr/src/app/target/release/s3-fast-list /usr/bin/s3-fast-list
COPY --from=builder /usr/src/app/target/release/ks-tool /usr/bin/ks-tool
ARG TOOL_BUILD_SHA256
LABEL io.varve.s3-listing-study.tool-build-sha256="${TOOL_BUILD_SHA256}"
USER 10001:10001
WORKDIR /
