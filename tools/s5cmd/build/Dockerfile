# syntax=docker/dockerfile:1
#
# s5cmd — upstream's published release binary, on the study's uniform glibc base.
#
# NOT a build from source. The official `Linux-64bit` release archive is fetched
# by URL and verified by digest before anything is extracted from it, so what
# runs is the artifact upstream ships to any Linux user.
#
# Why this repository packages s5cmd at all. Upstream's own image
# (`peakcom/s5cmd`) is Alpine, and the study's payload carries its own CPython
# which links the base image's libc. Compiled Python wheels are published per
# libc, and DuckDB — imported by every `normalize.py` adapter — publishes
# manylinux wheels only, with no musllinux build at any version. On a musl base
# the worker cannot run the study's own code. Rather than trim what the worker
# needs, the subject's packaging stops deciding the harness runtime.
#
# Why the release archive rather than upstream's image. They are not the same
# artifact: the binary inside `peakcom/s5cmd@sha256:2ff939e2…` hashes
# 6a645f4f53ffe03911e531586c167b35e36e2d33e0f10a9404cb1f665eeaaa98, while this
# release binary hashes
# 672299fea8941281702bd52a4e51c330a4e39c1540f4bdfc3b4e737823ac2878. Both report
# `v2.3.0-991c9fb`, so they are the same commit built in different environments.
# The release archive is preferred because it is a content-addressed artifact a
# reader can fetch and verify without pulling and unpacking someone's container
# image.
#
# Safe on a glibc base because the binary is static: Go with CGO disabled needs
# nothing from the base but a kernel. Verified by running it — `v2.3.0-991c9fb`.

ARG SHARED_BASE_IMAGE
FROM ${SHARED_BASE_IMAGE}
USER root
# --checksum makes the digest a precondition of the fetch: a substituted or
# corrupted archive fails the build rather than being unpacked.
ADD --checksum=sha256:de0fdbfa3aceae55e069ba81a0fc17b2026567637603734a387b2fca06c299b4 \
    https://github.com/peak/s5cmd/releases/download/v2.3.0/s5cmd_2.3.0_Linux-64bit.tar.gz \
    /tmp/s5cmd.tgz
RUN tar xzf /tmp/s5cmd.tgz -C / s5cmd \
    && chmod 0755 /s5cmd \
    && install -d -o 10001 -g 10001 /aws
ARG TOOL_BUILD_SHA256
LABEL io.varve.s3-listing-study.tool-build-sha256="${TOOL_BUILD_SHA256}"
USER 10001:10001
WORKDIR /aws
