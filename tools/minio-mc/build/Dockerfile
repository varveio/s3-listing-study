# syntax=docker/dockerfile:1
# Official MinIO client release binary, installed on the common study base.
FROM base AS install
USER root
ADD --checksum=sha256:01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891 \
    https://dl.min.io/client/mc/release/linux-amd64/archive/mc.RELEASE.2025-08-13T08-35-41Z \
    /usr/bin/mc
RUN chmod 0755 /usr/bin/mc

FROM scratch
COPY --from=install /usr/bin/mc /tool-root/usr/bin/mc
