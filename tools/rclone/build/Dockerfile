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

FROM debian@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241 AS fetch
# --checksum makes the digest a precondition of the fetch: a substituted or
# corrupted archive fails the build rather than being unpacked.
ADD --checksum=sha256:fe435e0c36228e7c2f116a8701f01127bb1f694005fc11d1f27186c8bca4115d \
    https://downloads.rclone.org/v1.74.4/rclone-v1.74.4-linux-amd64.zip /tmp/rclone.zip
RUN apt-get update \
    && apt-get install -y --no-install-recommends unzip \
    && unzip -j /tmp/rclone.zip 'rclone-v1.74.4-linux-amd64/rclone' -d /out \
    && rm -rf /var/lib/apt/lists/*

FROM debian@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241
# CA certificates so HTTPS to the S3 endpoint can be established (the tool still
# needs credentials to sign; TLS trust is orthogonal).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=fetch /out/rclone /usr/local/bin/rclone
# Upstream's workdir, preserved so the registered subject_workdir keeps meaning
# what it meant against the published image.
WORKDIR /data
ENTRYPOINT ["/usr/local/bin/rclone"]
