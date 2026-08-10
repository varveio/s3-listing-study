# s4cmd 2.1.0 — containerized for the s3-listing-study smoke phase.
#
# Upstream (bloomreach/s4cmd) ships neither a published image nor a Dockerfile,
# so this recipe is written per the brief's "neither image nor Dockerfile" case.
#
# The dependency closure uses current official PyPI wheels queried on
# 2026-08-10. The former 2018 botocore pin cannot import on the shared modern
# Python because it still references collections.Mapping.
FROM base AS install
USER root

ENV TZ=UTC \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime dependencies are installed from hash-locked wheels. The official PyPI
# sdist contains the tool as one script; extract that exact payload instead of
# invoking a Python package build, which would require unpinned build tooling.
ADD --checksum=sha256:42566058a74d3e1e553351966efaaffa08e4b6ac28a19e72a51be21151ea9534 \
    https://files.pythonhosted.org/packages/42/b4/0061f4930958cd790098738659c1c39f8feaf688e698142435eedaa4ae34/s4cmd-2.1.0.tar.gz \
    /tmp/s4cmd.tar.gz
COPY tools/s4cmd/build/requirements.txt /tmp/requirements.txt
RUN /opt/s3-listing-study/python/bin/python3 -m venv /opt/s4cmd-venv \
    && /opt/s4cmd-venv/bin/python -m pip install \
      --require-hashes --no-deps -r /tmp/requirements.txt \
    && mkdir -p /opt/s4cmd \
    && tar xzf /tmp/s4cmd.tar.gz -C /opt/s4cmd --strip-components=1 \
      s4cmd-2.1.0/s4cmd.py \
    && test -f /opt/s4cmd/s4cmd.py \
    && printf '#!/bin/sh\nexec /opt/s4cmd-venv/bin/python /opt/s4cmd/s4cmd.py "$@"\n' \
      > /usr/local/bin/s4cmd \
    && chmod 0755 /usr/local/bin/s4cmd

FROM scratch
COPY --from=install /opt/s4cmd-venv/ /tool-root/opt/s4cmd-venv/
COPY --from=install /opt/s4cmd/ /tool-root/opt/s4cmd/
COPY --from=install /usr/local/bin/s4cmd /tool-root/usr/local/bin/s4cmd
