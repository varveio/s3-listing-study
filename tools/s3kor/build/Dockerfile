# syntax=docker/dockerfile:1
# Official s3kor v0.0.37 Linux amd64 GoReleaser archive. Upstream publishes no
# container, but it does publish this binary distribution; use it instead of
# rebuilding the same tag from source.
ARG SHARED_BASE_IMAGE
FROM ${SHARED_BASE_IMAGE}
USER root
ADD --checksum=sha256:47047e8a40a84992914f02bb5c8dfc525abff0a42c3ed76a4ed042a8ef976103 \
    https://github.com/sethkor/s3kor/releases/download/v0.0.37/s3kor_0.0.37_Linux_amd64.tar.gz \
    /tmp/s3kor.tar.gz
RUN mkdir -p /out \
    && tar xzf /tmp/s3kor.tar.gz -C /out s3kor \
    && install -m 0755 /out/s3kor /usr/local/bin/s3kor
ARG TOOL_BUILD_SHA256
ARG SELECTION_SHA256
LABEL io.varve.s3-listing-study.tool-build-sha256="${TOOL_BUILD_SHA256}" \
      io.varve.s3-listing-study.selection-sha256="${SELECTION_SHA256}"
USER 10001:10001
WORKDIR /
