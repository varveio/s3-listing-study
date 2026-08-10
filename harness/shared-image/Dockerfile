# syntax=docker/dockerfile:1
# One stable runtime for every measured tool. Worker and adapter bytes are
# deliberately added by the final assembly stage, after the independently
# cached tool payload, so changing Python harness code never rebuilds a tool.
# The Debian root is digest-pinned; the Python and DuckDB contexts are fetched
# and checksum-verified by the host.
FROM debian@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241

RUN test -f /etc/apt/sources.list.d/debian.sources \
    && sed -i \
        -e 's|http://deb.debian.org/debian-security|http://snapshot.debian.org/archive/debian-security/20260803T000000Z|g' \
        -e 's|http://deb.debian.org/debian|http://snapshot.debian.org/archive/debian/20260803T000000Z|g' \
        /etc/apt/sources.list.d/debian.sources \
    && ! grep -F 'deb.debian.org' /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' 'Acquire::Check-Valid-Until "false";' \
        > /etc/apt/apt.conf.d/99snapshot \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates=20230311+deb12u1 \
        libssl3=3.0.20-1~deb12u2 \
        openssl=3.0.20-1~deb12u2 \
    && test "$(dpkg-query -W -f='${Version}' ca-certificates)" = '20230311+deb12u1' \
    && test "$(dpkg-query -W -f='${Version}' libssl3)" = '3.0.20-1~deb12u2' \
    && test "$(dpkg-query -W -f='${Version}' openssl)" = '3.0.20-1~deb12u2' \
    && test "$(dpkg-query -W -f='${Version}' libstdc++6)" = '12.2.0-14+deb12u1' \
    && test "$(dpkg-query -W -f='${Version}' tzdata)" = '2026b-0+deb12u1' \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=UTC

ARG SHARED_BASE_SOURCE_SHA256
RUN printf '%s\n' "${SHARED_BASE_SOURCE_SHA256}" | grep -Eq '^[0-9a-f]{64}$' \
    && mkdir -p /opt/s3-listing-study \
    && printf '%s\n' "${SHARED_BASE_SOURCE_SHA256}" \
        > /opt/s3-listing-study/shared-base-source.sha256
LABEL io.varve.s3-listing-study.shared-base-source-sha256="${SHARED_BASE_SOURCE_SHA256}"

COPY --from=python . /opt/s3-listing-study/python/
COPY --from=duckdb . /opt/s3-listing-study/python/lib/python3.12/site-packages/
RUN ["/opt/s3-listing-study/python/bin/python3", "-I", "-B", "-c", "import duckdb; assert duckdb.__version__ == '1.5.5', duckdb.__version__"]
