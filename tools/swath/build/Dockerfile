# syntax=docker/dockerfile:1
ARG SHARED_BASE_IMAGE
# The official main image, pinned by immutable index digest. Its tested jar and
# JRE are copied onto the common study base without recompiling Swath.
FROM ghcr.io/varveio/swath@sha256:5528ac5c238d5d0ca51bd221e43c398b3a0ac5464dcb30bef9691c94d10fa735 AS upstream

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
