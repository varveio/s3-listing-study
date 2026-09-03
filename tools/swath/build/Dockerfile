# syntax=docker/dockerfile:1
ARG SHARED_BASE_IMAGE
# Upstream's published 0.3.1 release image (registry tag 0.3.1, git tag v0.3.1,
# revision 7b9a5e2), pinned by immutable index digest. Its tested jar and
# JRE are copied onto the common study base without recompiling Swath.
FROM ghcr.io/varveio/swath@sha256:0bbc96c10b4b63d184cce76734679dd4a2f54a1c81c7c94c1000f7114eab8e43 AS upstream

FROM ${SHARED_BASE_IMAGE}
USER root
COPY --from=upstream /opt/java/openjdk/ /opt/java/openjdk/
COPY --from=upstream /opt/swath/swath.jar /opt/swath/swath.jar
RUN chown -R root:root /opt/java /opt/swath \
    && chmod 0444 /opt/swath/swath.jar
ARG TOOL_BUILD_SHA256
LABEL io.varve.s3-listing-study.tool-build-sha256="${TOOL_BUILD_SHA256}"
USER 10001:10001
WORKDIR /opt/swath
