# syntax=docker/dockerfile:1
ARG SHARED_BASE_IMAGE
# The official main image, pinned by immutable index digest. Its tested jar and
# JRE are copied onto the common study base without recompiling Swath.
FROM ghcr.io/varveio/swath@sha256:6595c2f18da68cee4c1b1cdaf5335d993fea7b8f2f8aaf2f26ef3c81058a9ffd AS upstream

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
