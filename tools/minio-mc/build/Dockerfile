# syntax=docker/dockerfile:1
# Official MinIO client release binary, installed on the common study base.
ARG SHARED_BASE_IMAGE
FROM ${SHARED_BASE_IMAGE}
USER root
ADD --checksum=sha256:01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891 \
    https://dl.min.io/client/mc/release/linux-amd64/archive/mc.RELEASE.2025-08-13T08-35-41Z \
    /usr/bin/mc
RUN chmod 0755 /usr/bin/mc
ARG TOOL_BUILD_SHA256
ARG SELECTION_SHA256
LABEL io.varve.s3-listing-study.tool-build-sha256="${TOOL_BUILD_SHA256}" \
      io.varve.s3-listing-study.selection-sha256="${SELECTION_SHA256}"
USER 10001:10001
WORKDIR /
