# syntax=docker/dockerfile:1
# Official s7cmd v1.5.0 Linux x86_64 glibc release archive. The archive is
# pinned by the digest GitHub records for the upstream release asset; no source
# compiler or upstream container is needed for the uniform study image.
FROM base AS fetch
ADD --checksum=sha256:34ecf3a725e835e88ad115a73d6e7b3a324c95677439c43f5baec536d8a6b3ad \
    https://github.com/nidor1998/s7cmd/releases/download/v1.5.0/s7cmd-1.5.0-linux-glibc2.28-x86_64.tar.gz \
    /tmp/s7cmd.tar.gz
RUN mkdir -p /out \
    && tar xzf /tmp/s7cmd.tar.gz -C /out \
    && test -x /out/s7cmd

FROM scratch
COPY --from=fetch /out/s7cmd /tool-root/usr/local/bin/s7cmd
