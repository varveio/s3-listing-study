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
FROM alpine@sha256:6baf43584bcb78f2e5847d1de515f23499913ac9f12bdf834811a3145eb11ca1
# CA certificates so HTTPS to the S3 endpoint can be established (the tool still
# needs credentials to sign; TLS trust is orthogonal).
RUN apk add --no-cache ca-certificates
COPY --from=build /out-s3kor /usr/local/bin/s3kor
ENTRYPOINT ["/usr/local/bin/s3kor"]
