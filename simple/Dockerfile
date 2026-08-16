# syntax=docker/dockerfile:1
# Build from a selected tool image by immutable digest. The parent already
# contains the subject binary, locked DuckDB/ijson runtimes, Python, and uid 10001.
ARG TOOL_PARENT
FROM ${TOOL_PARENT}

USER root
ARG TOOL_PARENT
ARG TOOL
ARG TOOL_BUILD_SHA256
ARG HARNESS_REVISION
RUN printf '%s\n' "${TOOL_PARENT}" | grep -Eq '@sha256:[0-9a-f]{64}$' \
    && test -n "${TOOL}" \
    && printf '%s\n' "${TOOL_BUILD_SHA256}" | grep -Eq '^[0-9a-f]{64}$' \
    && printf '%s\n' "${HARNESS_REVISION}" | grep -Eq '^[0-9a-f]{40}$' \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       python3-pip=23.0.1+dfsg-1 \
    && rm -rf /var/lib/apt/lists/*
# Exact direct and transitive versions are constrained by the checked-in lock
# projection. The immutable parent pins the Debian/Python ABI underneath them.
COPY simple/requirements-worker.txt /tmp/requirements-worker.txt
RUN /usr/bin/python3 -m pip install --break-system-packages --no-cache-dir \
      -r /tmp/requirements-worker.txt \
    && /usr/bin/python3 -m pip check \
    && /usr/bin/python3 -c 'import google.cloud.storage'

COPY src/s3_listing_study/__init__.py /opt/simple/s3_listing_study/__init__.py
COPY src/s3_listing_study/common/ /opt/simple/s3_listing_study/common/
COPY tools/${TOOL}/adapter/ /opt/s3-listing-study/tool/
COPY tools/${TOOL}/build/image.json /opt/simple/tool-image.json
COPY simple/adapters.py simple/contract.py simple/gcs.py simple/measure.py /opt/simple/
# Capsules retain historical manager.* imports. These aliases preserve the
# stable common modules without staging the manager implementation.
RUN mkdir -p /opt/simple/s3_listing_study/manager \
    && touch /opt/simple/s3_listing_study/manager/__init__.py \
    && ln -s ../common/contract.py /opt/simple/s3_listing_study/manager/contract.py \
    && ln -s ../common/duckdb_adapter.py /opt/simple/s3_listing_study/manager/duckdb_adapter.py \
    && ln -s ../common/normalizer_cli.py /opt/simple/s3_listing_study/manager/normalizer_cli.py \
    && /usr/bin/python3 -c 'import json,os,sys; p=json.load(open("/opt/simple/tool-image.json")); required=("tool","tool_version","tool_build_sha256","adapter_bundle_sha256","subject_workdir","executable"); missing=[k for k in required if k not in p]; assert not missing, missing; assert p["tool"] == sys.argv[1]; assert p["tool_build_sha256"] == sys.argv[4]; assert p["subject_workdir"] == os.getcwd(); executable=p["executable"]; assert isinstance(executable,list) and executable; assert os.path.isfile(executable[0]) and os.access(executable[0],os.X_OK); assert all(not value.startswith("/") or os.path.exists(value) for value in executable[1:]); json.dump({"schema_version":1,"tool":p["tool"],"tool_version":p["tool_version"],"tool_build_sha256":p["tool_build_sha256"],"adapter_bundle_sha256":p["adapter_bundle_sha256"],"subject_workdir":p["subject_workdir"],"harness_revision":sys.argv[2],"tool_parent_image":sys.argv[3]},open("/opt/simple/image-metadata.json","w"),sort_keys=True,separators=(",",":"))' "${TOOL}" "${HARNESS_REVISION}" "${TOOL_PARENT}" "${TOOL_BUILD_SHA256}" \
    && chown -R root:root /opt/simple /opt/s3-listing-study/tool \
    && find /opt/simple /opt/s3-listing-study/tool -type f -exec chmod 0444 {} + \
    && find /opt/simple /opt/s3-listing-study/tool -type d -exec chmod 0555 {} +

ENV PYTHONPATH=/opt/simple
USER 10001:10001
ENTRYPOINT ["/usr/bin/python3", "/opt/simple/measure.py"]
CMD []
