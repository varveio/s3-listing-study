# syntax=docker/dockerfile:1
#
# Swath, Go port — built from upstream source at a pinned revision, on the study's
# uniform glibc base.
#
# Built from source because there is nothing else to select: the port has no
# release, no tag and no published binary or image. The revision is the head of
# upstream's unmerged `spike/go-local-lister` branch,
# 63a42568dbcfb2d3cdc18e3ccf7abfd69792ad77. PROVISIONAL: the pin moves to the
# merge commit when that branch merges, and the archive checksum, image.json and
# every `source@` provenance in adapter/command.py move with it.
#
# The source is GitHub's archive of that commit, fetched by URL and verified by
# digest before anything is extracted. The build line is upstream CI's own static
# build (.github/workflows/ci.yml:340-347 and go/README.md at the pinned revision:
# CGO_ENABLED=0, `-trimpath -ldflags='-s -w'`), with the module graph held to the
# committed go.sum (-mod=readonly) and the toolchain to the image's own Go, which
# is the go.mod directive's version (GOTOOLCHAIN=local: no toolchain download).
#
# One known consequence, disclosed rather than worked around: an archive carries
# no VCS metadata, and the port reads its revision only from Go's build info
# (go/internal/listing/report.go:192-214), so this binary's `--version` and run
# report say `swath-go devel` with no revision. The revision identity is the
# checksum-pinned archive recorded in image.json, not a self-report.
#
# The binary is static (CGO disabled), so it needs nothing from the base but a
# kernel.

ARG SHARED_BASE_IMAGE
FROM golang:1.26.0-bookworm@sha256:2a0ba12e116687098780d3ce700f9ce3cb340783779646aafbabed748fa6677c AS swath-go-build
ADD --checksum=sha256:22cf5a7696bc23c2ee9582d9eb8d4f6ade139fe44221f428292ee2811aa9b9e5 \
    https://github.com/varveio/swath/archive/63a42568dbcfb2d3cdc18e3ccf7abfd69792ad77.tar.gz \
    /tmp/swath.tar.gz
RUN mkdir -p /src /out \
    && tar xzf /tmp/swath.tar.gz -C /src --strip-components=1
WORKDIR /src/go
RUN GOTOOLCHAIN=local GOFLAGS=-mod=readonly CGO_ENABLED=0 \
    go build -trimpath -ldflags='-s -w' -o /out/swath ./cmd/swath

FROM ${SHARED_BASE_IMAGE}
USER root
COPY --from=swath-go-build /out/swath /opt/swath-go/swath
RUN chown -R root:root /opt/swath-go \
    && chmod 0555 /opt/swath-go/swath
ARG TOOL_BUILD_SHA256
LABEL io.varve.s3-listing-study.tool-build-sha256="${TOOL_BUILD_SHA256}"
USER 10001:10001
WORKDIR /opt/swath-go
