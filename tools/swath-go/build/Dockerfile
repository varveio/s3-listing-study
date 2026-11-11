# syntax=docker/dockerfile:1
#
# Swath, Go port — built from upstream source at a pinned revision, on the study's
# uniform glibc base.
#
# Built from source because the subject is a commit on upstream's main, not a
# release: 8c8f870801c700f22413581f35683096782329a0. The port's first release
# candidate, v0.4.0-rc.1, is an ancestor of it; the study pins the later commit.
#
# The source is GitHub's archive of that commit, fetched by URL and verified by
# digest before anything is extracted. The build line is upstream's own
# (go/build.sh at the pinned revision: CGO_ENABLED=0, `-trimpath -ldflags="-s -w
# -X github.com/varveio/swath/go/internal/buildinfo.Version=$SWATH_VERSION
# -X github.com/varveio/swath/go/internal/buildinfo.Revision=$rev"`), with
# $SWATH_VERSION unset, as for any build that is not a release, so --version
# says devel, and $rev the pinned commit, so `--version` and the run report
# name the revision the archive was cut from. An archive has no git metadata,
# so the revision is passed in rather than read; upstream CI does the same
# through SWATH_REVISION. The module graph is held to the committed go.sum
# (-mod=readonly; the seekparquet module and the in-tree reader module, which
# go.mod replaces with ./reader, resolve under it) and the toolchain to the
# image's own Go (GOTOOLCHAIN=local: no toolchain download). The image's Go is
# go.mod's `toolchain go1.27.4` line, the release upstream's own Dockerfile.go
# and CI compile with; go.mod's `go 1.26.0` directive is only the minimum.
#
# The binary is static (CGO disabled), so it needs nothing from the base but a
# kernel and the CA bundle the base already carries.

ARG SHARED_BASE_IMAGE
FROM golang:1.27.4-bookworm@sha256:4fa7c968ba93f1635670522dbb01a2c78150443e1385746924566079f36570ea AS swath-go-build
ADD --checksum=sha256:8aa439ac53df3f8a704851f9c9e1eaee49b52f51a3c2dfa0d958160ceb7e5f4d \
    https://github.com/varveio/swath/archive/8c8f870801c700f22413581f35683096782329a0.tar.gz \
    /tmp/swath.tar.gz
RUN mkdir -p /src /out \
    && tar xzf /tmp/swath.tar.gz -C /src --strip-components=1
WORKDIR /src/go
RUN GOTOOLCHAIN=local GOFLAGS=-mod=readonly CGO_ENABLED=0 \
    go build -trimpath \
      -ldflags="-s -w -X github.com/varveio/swath/go/internal/buildinfo.Version= -X github.com/varveio/swath/go/internal/buildinfo.Revision=8c8f870801c700f22413581f35683096782329a0" \
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
