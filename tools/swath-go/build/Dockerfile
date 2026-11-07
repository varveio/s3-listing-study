# syntax=docker/dockerfile:1
#
# Swath, Go port — built from upstream source at a pinned revision, on the study's
# uniform glibc base.
#
# Built from source because there is nothing else to select: the port has no
# release, no tag and no published binary or image. The revision is the
# merge commit of upstream pull request varveio/swath#255 on main,
# a2efe16e45d66088f6447e9723a119e5aebd4a91.
#
# The source is GitHub's archive of that commit, fetched by URL and verified by
# digest before anything is extracted. The build line is upstream's own
# (go/build.sh at the pinned revision: CGO_ENABLED=0, `-trimpath -ldflags="-s -w
# -X github.com/varveio/swath/go/internal/listing.revision=$rev"`), with $rev the
# pinned commit, so `--version` and the run report name the revision the archive
# was cut from. An archive has no git metadata, so the revision is passed in
# rather than read; upstream CI does the same through SWATH_REVISION. The module
# graph is held to the committed go.sum (-mod=readonly) and the toolchain to the
# image's own Go, the go.mod directive's version (GOTOOLCHAIN=local: no
# toolchain download).
#
# The binary is static (CGO disabled), so it needs nothing from the base but a
# kernel and the CA bundle the base already carries.

ARG SHARED_BASE_IMAGE
FROM golang:1.26.0-bookworm@sha256:2a0ba12e116687098780d3ce700f9ce3cb340783779646aafbabed748fa6677c AS swath-go-build
ADD --checksum=sha256:7f83c4e8845e14f68fe173f06a9525bfd28e513c38b408c43edc250a4a2770f4 \
    https://github.com/varveio/swath/archive/a2efe16e45d66088f6447e9723a119e5aebd4a91.tar.gz \
    /tmp/swath.tar.gz
RUN mkdir -p /src /out \
    && tar xzf /tmp/swath.tar.gz -C /src --strip-components=1
WORKDIR /src/go
RUN GOTOOLCHAIN=local GOFLAGS=-mod=readonly CGO_ENABLED=0 \
    go build -trimpath \
      -ldflags="-s -w -X github.com/varveio/swath/go/internal/listing.revision=a2efe16e45d66088f6447e9723a119e5aebd4a91" \
      -o /out/swath ./cmd/swath

FROM ${SHARED_BASE_IMAGE}
USER root
COPY --from=swath-go-build /out/swath /opt/swath-go/swath
RUN chown -R root:root /opt/swath-go \
    && chmod 0555 /opt/swath-go/swath
ARG TOOL_BUILD_SHA256
LABEL io.varve.s3-listing-study.tool-build-sha256="${TOOL_BUILD_SHA256}"
USER 10001:10001
WORKDIR /opt/swath-go
