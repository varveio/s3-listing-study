# syntax=docker/dockerfile:1
# Official s3kor v0.0.37 Linux amd64 GoReleaser archive. Upstream publishes no
# container, but it does publish this binary distribution; use it instead of
# rebuilding the same tag from source.
FROM base AS fetch
ADD --checksum=sha256:47047e8a40a84992914f02bb5c8dfc525abff0a42c3ed76a4ed042a8ef976103 \
    https://github.com/sethkor/s3kor/releases/download/v0.0.37/s3kor_0.0.37_Linux_amd64.tar.gz \
    /tmp/s3kor.tar.gz
RUN mkdir -p /out \
    && tar xzf /tmp/s3kor.tar.gz -C /out s3kor \
    && test -x /out/s3kor

FROM scratch
COPY --from=fetch /out/s3kor /tool-root/usr/local/bin/s3kor
