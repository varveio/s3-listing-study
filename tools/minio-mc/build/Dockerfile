# syntax=docker/dockerfile:1
# MinIO client release binary mc.RELEASE.2025-08-13T08-35-41Z (linux-amd64), installed
# on the common study base. It was fetched from
# https://dl.min.io/client/mc/release/linux-amd64/archive/mc.RELEASE.2025-08-13T08-35-41Z
# until dl.min.io began answering 410 Gone for every community release (checked
# 2026-11-07). The same bytes, with the same digest, are the GitHub release asset.
ARG SHARED_BASE_IMAGE
FROM ${SHARED_BASE_IMAGE}
USER root
ADD --checksum=sha256:01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891 \
    https://github.com/minio/mc/releases/download/RELEASE.2025-08-13T08-35-41Z/mc.linux-amd64.RELEASE.2025-08-13T08-35-41Z \
    /usr/bin/mc
RUN chmod 0755 /usr/bin/mc
ARG TOOL_BUILD_SHA256
LABEL io.varve.s3-listing-study.tool-build-sha256="${TOOL_BUILD_SHA256}"
USER 10001:10001
WORKDIR /
