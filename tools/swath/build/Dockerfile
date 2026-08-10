# syntax=docker/dockerfile:1
ARG SHARED_BASE_IMAGE
# The official release jar and the same digest-pinned Temurin 25 JRE used by
# upstream, copied onto the common study base. The JRE stage is discarded.
FROM eclipse-temurin:25-jre-noble@sha256:2f1da100788559b397bcf48c736169ea5b070bde84e55f203bbee8e83d87a175 AS jre

FROM ${SHARED_BASE_IMAGE}
USER root
COPY --from=jre /opt/java/openjdk/ /opt/java/openjdk/
ADD --checksum=sha256:01a55d46a7173094f5e66959c24347f85e87f28edeeb4652a535795ac97f2b52 \
    https://github.com/varveio/swath/releases/download/v0.2.4/swath-0.2.4.jar \
    /opt/swath/swath.jar
RUN chown -R root:root /opt/java /opt/swath
ARG TOOL_BUILD_SHA256
ARG SELECTION_SHA256
LABEL io.varve.s3-listing-study.tool-build-sha256="${TOOL_BUILD_SHA256}" \
      io.varve.s3-listing-study.selection-sha256="${SELECTION_SHA256}"
USER 10001:10001
WORKDIR /opt/swath
