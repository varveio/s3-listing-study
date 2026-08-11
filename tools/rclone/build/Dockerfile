# syntax=docker/dockerfile:1
#
# rclone — upstream's published release binary, on the study's uniform glibc base.
#
# NOT a build from source. The official `linux-amd64` release archive is fetched
# by URL and verified by digest before anything is extracted from it, so what
# runs is the artifact upstream ships to any Linux user.
#
# Why this repository packages rclone at all. Upstream's own image
# (`rclone/rclone`) is Alpine, and the study's payload carries its own CPython
# which links the base image's libc. Compiled Python wheels are published per
# libc, and DuckDB — imported by every `normalize.py` adapter — publishes
# manylinux wheels only, with no musllinux build at any version. On a musl base
# the worker cannot run the study's own code. Rather than trim what the worker
# needs, the subject's packaging stops deciding the harness runtime.
#
# Why the release archive rather than upstream's image. They are not the same
# artifact: the binary inside `rclone/rclone@sha256:c61954aa…` hashes
# d67c485534687d1f2d5fbe467104a8c9f82cc491796f9cd13acc33100852527f, while this
# release binary hashes
# 9f56ca5edfac24a3ed37226c2ba1de69f1ec9e05fa2526cddee5cd97e202be6b. Both report
# `rclone v1.74.4`. The release archive is preferred because it is a
# content-addressed artifact a reader can fetch and verify without pulling and
# unpacking someone's container image.
#
# Safe on a glibc base because the binary is static: Go with CGO disabled needs
# nothing from the base but a kernel. Verified by running it — `rclone v1.74.4`,
# reporting `os/version: debian 12.15`.

ARG SHARED_BASE_IMAGE
FROM ${SHARED_BASE_IMAGE}
USER root
# --checksum makes the digest a precondition of the fetch: a substituted or
# corrupted archive fails the build rather than being unpacked.
ADD --checksum=sha256:fe435e0c36228e7c2f116a8701f01127bb1f694005fc11d1f27186c8bca4115d \
    https://downloads.rclone.org/v1.74.4/rclone-v1.74.4-linux-amd64.zip /tmp/rclone.zip
RUN /usr/bin/python3 -m zipfile -e /tmp/rclone.zip /tmp/rclone \
    && install -m 0755 /tmp/rclone/rclone-v1.74.4-linux-amd64/rclone \
        /usr/local/bin/rclone \
    && install -d -o 10001 -g 10001 /data
ARG TOOL_BUILD_SHA256
LABEL io.varve.s3-listing-study.tool-build-sha256="${TOOL_BUILD_SHA256}"
USER 10001:10001
WORKDIR /data
