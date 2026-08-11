# syntax=docker/dockerfile:1
# One controlled Debian runtime inherited by every tool image. The Debian root
# is digest-pinned; the DuckDB and ijson contexts are fetched and
# checksum-verified by the host, never resolved by a package manager in here.
FROM debian@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241

USER root
COPY harness/shared-image/debian-packages.lock /tmp/debian-packages.lock
RUN test -f /etc/apt/sources.list.d/debian.sources \
    && sed -i \
        -e 's|http://deb.debian.org/debian-security|http://snapshot.debian.org/archive/debian-security/20260803T000000Z|g' \
        -e 's|http://deb.debian.org/debian|http://snapshot.debian.org/archive/debian/20260803T000000Z|g' \
        /etc/apt/sources.list.d/debian.sources \
    && ! grep -F 'deb.debian.org' /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' 'Acquire::Check-Valid-Until "false";' \
        > /etc/apt/apt.conf.d/99snapshot \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates=20230311+deb12u1 \
        libssl3=3.0.20-1~deb12u2 \
        openssl=3.0.20-1~deb12u2 \
        python3=3.11.2-1+b1 \
        python3-minimal=3.11.2-1+b1 \
    && dpkg-query -W -f='${Package}:${Architecture}=${Version}\n' \
        | LC_ALL=C sort > /tmp/installed-packages.lock \
    && cmp /tmp/debian-packages.lock /tmp/installed-packages.lock \
    && install -D -m 0444 /tmp/installed-packages.lock \
        /opt/s3-listing-study/debian-packages.lock \
    && rm -rf /var/lib/apt/lists/* /tmp/installed-packages.lock
ENV TZ=UTC HOME=/home/s3study

ARG SHARED_BASE_SOURCE_SHA256
RUN printf '%s\n' "${SHARED_BASE_SOURCE_SHA256}" | grep -Eq '^[0-9a-f]{64}$' \
    && install -d -m 0755 /opt/s3-listing-study \
    && printf '%s\n' "${SHARED_BASE_SOURCE_SHA256}" \
        > /opt/s3-listing-study/shared-base-source.sha256
LABEL io.varve.s3-listing-study.shared-base-source-sha256="${SHARED_BASE_SOURCE_SHA256}"

COPY --from=duckdb . /usr/local/lib/python3.11/dist-packages/
COPY --from=ijson . /usr/local/lib/python3.11/dist-packages/
RUN /usr/bin/python3 -I -B -c "import duckdb; assert duckdb.__version__ == '1.5.5'" \
    && /usr/bin/python3 -I -B -c \
      "import ijson, ijson.backends._yajl2; assert ijson.__version__ == '3.5.1', ijson.__version__; assert ijson.backend == 'yajl2_c', ijson.backend" \
    && /usr/bin/python3 -I -B -c \
      'import json,platform,sys; n,v=platform.libc_ver(); print(json.dumps({"architecture":platform.machine(),"implementation":platform.python_implementation(),"libc":f"{n}-{v}" if n else None,"package_manifest_sha256":sys.argv[1],"running_version":platform.python_version(),"source":"debian-snapshot"},sort_keys=True,separators=(",",":")))' \
      "$(sha256sum /opt/s3-listing-study/debian-packages.lock | cut -d' ' -f1)" \
      > /opt/s3-listing-study/runtime-identity.json \
    && chmod 0444 /opt/s3-listing-study/*.lock /opt/s3-listing-study/*.json \
        /opt/s3-listing-study/*.sha256 \
    && useradd --uid 10001 --user-group --home-dir /home/s3study --create-home \
        --shell /usr/sbin/nologin s3study \
    && chmod 0755 /home/s3study \
    && chown -R root:root /opt/s3-listing-study

USER 10001:10001
WORKDIR /home/s3study
