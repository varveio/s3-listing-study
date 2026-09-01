# syntax=docker/dockerfile:1
ARG SHARED_BASE_IMAGE
# The official 0.3.1 image, pinned by immutable index digest. Its tested jar and
# JRE are copied onto the common study base without recompiling Swath.
FROM ghcr.io/varveio/swath@sha256:776e788200a1e70f30206897303a34e4faabd56c591e1c9562277677085c4f60 AS upstream

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
