# syntax=docker/dockerfile:1
# MinIO client release binary mc.RELEASE.2025-08-13T08-35-41Z (linux-amd64), installed
# on the common study base. It was fetched from
# https://dl.min.io/client/mc/release/linux-amd64/archive/mc.RELEASE.2025-08-13T08-35-41Z
# under ADD --checksum=sha256:01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891
# until dl.min.io began answering 410 Gone for every community release (checked
# 2026-11-07). The same bytes are now taken from the published
# 2026-09-scale-diagnostics toolbox image, pinned by digest, and checked against
# that binary digest before use.
ARG SHARED_BASE_IMAGE
FROM us-east1-docker.pkg.dev/varve-oss/s3-listing-study/benchmark-toolbox@sha256:0f376b395cce0029b0bbf68a71e0414df72fa0a3b172edf601aa848809997f8f AS released
FROM ${SHARED_BASE_IMAGE}
USER root
COPY --from=released /usr/bin/mc /usr/bin/mc
RUN printf '%s  %s\n' 01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891 /usr/bin/mc | sha256sum -c - \
    && chmod 0755 /usr/bin/mc
ARG TOOL_BUILD_SHA256
LABEL io.varve.s3-listing-study.tool-build-sha256="${TOOL_BUILD_SHA256}"
USER 10001:10001
WORKDIR /
