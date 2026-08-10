# s3kor — built from source at the pinned release tag v0.0.37.
# Upstream ships no Docker image and no Dockerfile (distribution is a Homebrew
# tap + goreleaser GitHub-release binaries), so per the study brief Stage B we
# write our own recipe. The built image's digest — recorded in the receipt — is
# the run's identity; this Dockerfile is the best-effort recipe.
# Build decision and the no-image/no-Dockerfile evidence: ../research/report.md § 7.
#
# Pinned commit: 844fe3d7931fcca415c8b8a4e22f048886e6b82b (tag v0.0.37)
# Language/build: Go 1.14 module, aws-sdk-go v1.30.16, CGO disabled (matches
# upstream .goreleaser.yml: CGO_ENABLED=0).

# --- build stage --------------------------------------------------------------
FROM golang@sha256:77f25981bd57e60a510165f3be89c901aec90453fd0f1c5a45691f6cb1528807 AS build
RUN apk add --no-cache git
ENV CGO_ENABLED=0 GOFLAGS=-mod=mod
# go install <module>@<commit> fetches the pinned revision via the module proxy.
RUN go install github.com/sethkor/s3kor@844fe3d7931fcca415c8b8a4e22f048886e6b82b \
    && cp "$(go env GOPATH)/bin/s3kor" /out-s3kor

# --- runtime stage ------------------------------------------------------------
# glibc, not Alpine, and the tool is indifferent: CGO_ENABLED=0 above makes the
# binary static, so it runs on either. The harness is not indifferent. Our
# payload's CPython links the base image's libc, and C-extension wheels are
# published per libc — DuckDB and google-crc32c ship manylinux only, with no
# musllinux build at any version. On a musl base the worker therefore cannot
# run the study's own code, and the fix is not to trim what the worker needs
# but to stop the subject's packaging from deciding it.
#
# A uniform base also removes a difference from the comparison itself: libc,
# malloc, the DNS resolver and the TLS stack all differ between Alpine and
# Debian, and all four sit in the path of a listing benchmark.
FROM debian@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241
# CA certificates so HTTPS to the S3 endpoint can be established (the tool still
# needs credentials to sign; TLS trust is orthogonal).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /out-s3kor /usr/local/bin/s3kor
ENTRYPOINT ["/usr/local/bin/s3kor"]
