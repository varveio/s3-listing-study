# syntax=docker/dockerfile:1
# One execution image, assembled from the eleven independently pinned tool
# payloads. Tool copies form the stable lower toolbox layers; worker code and
# adapters are added only afterwards.
ARG AWS_CLI_PARENT
ARG SHARED_BASE_PARENT
ARG MINIO_MC_PARENT
ARG PS3_PARENT
ARG RCLONE_PARENT
ARG S3_FAST_LIST_PARENT
ARG S3KOR_PARENT
ARG S3P_PARENT
ARG S4CMD_PARENT
ARG S5CMD_PARENT
ARG S7CMD_PARENT
ARG SWATH_PARENT

FROM ${AWS_CLI_PARENT} AS aws_cli
FROM ${MINIO_MC_PARENT} AS minio_mc
FROM ${PS3_PARENT} AS ps3
FROM ${RCLONE_PARENT} AS rclone
FROM ${S3_FAST_LIST_PARENT} AS s3_fast_list
FROM ${S3KOR_PARENT} AS s3kor
FROM ${S3P_PARENT} AS s3p
FROM ${S4CMD_PARENT} AS s4cmd
FROM ${S5CMD_PARENT} AS s5cmd
FROM ${S7CMD_PARENT} AS s7cmd
FROM ${SWATH_PARENT} AS swath

# Every payload is copied onto the explicitly recorded shared study base.
FROM ${SHARED_BASE_PARENT} AS toolbox
USER root
COPY --from=aws_cli /opt/aws-cli/ /opt/aws-cli/
COPY --from=minio_mc /usr/bin/mc /usr/bin/mc
COPY --from=ps3 /usr/local/bin/pS3 /usr/local/bin/pS3
COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone
COPY --from=s3_fast_list /usr/bin/s3-fast-list /usr/bin/s3-fast-list
COPY --from=s3_fast_list /usr/bin/ks-tool /usr/bin/ks-tool
COPY --from=s3kor /usr/local/bin/s3kor /usr/local/bin/s3kor
COPY --from=s3p /usr/local/bin/node /usr/local/bin/node
COPY --from=s3p /usr/local/bin/s3p /usr/local/bin/s3p
COPY --from=s3p /opt/s3p/ /opt/s3p/
COPY --from=s4cmd /usr/local/bin/s4cmd /usr/local/bin/s4cmd
COPY --from=s4cmd /opt/s4cmd/ /opt/s4cmd/
COPY --from=s4cmd /opt/s4cmd-venv/ /opt/s4cmd-venv/
COPY --from=s5cmd /s5cmd /s5cmd
COPY --from=s7cmd /usr/local/bin/s7cmd /usr/local/bin/s7cmd
COPY --from=swath /opt/java/openjdk/ /opt/java/openjdk/
COPY --from=swath /opt/swath/ /opt/swath/
ARG TOOLBOX_MANIFEST_SHA256
RUN printf '%s\n' "${TOOLBOX_MANIFEST_SHA256}" | grep -Eq '^[0-9a-f]{64}$' \
    && ln -s /opt/aws-cli/v2/current/bin/aws /usr/local/bin/aws \
    && ln -s /opt/aws-cli/v2/current/bin/aws_completer /usr/local/bin/aws_completer \
    && install -d -o 10001 -g 10001 /aws /data /home/s7cmd /home/s3study
LABEL io.varve.s3-listing-study.toolbox-manifest-sha256="${TOOLBOX_MANIFEST_SHA256}"

# Harness runtime and source are deliberately above the stable toolbox stage.
FROM toolbox AS runtime
USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       python3-pip=23.0.1+dfsg-1 \
    && rm -rf /var/lib/apt/lists/*
COPY simple/requirements-worker.txt /tmp/requirements-worker.txt
RUN /usr/bin/python3 -m pip install --break-system-packages --no-cache-dir \
      -r /tmp/requirements-worker.txt \
    && /usr/bin/python3 -m pip check \
    && /usr/bin/python3 -c 'import google.cloud.storage'

COPY src/s3_listing_study/__init__.py /opt/simple/s3_listing_study/__init__.py
COPY src/s3_listing_study/common/ /opt/simple/s3_listing_study/common/
# Dockerfile.dockerignore admits only each adapter's Python files under tools/;
# research, receipts, and build inputs never enter this context layer.
COPY tools/ /opt/simple/tools/
COPY simple/adapters.py simple/contract.py simple/gcs.py simple/measure.py /opt/simple/
ARG IMAGE_METADATA_B64
ARG TOOLBOX_MANIFEST_SHA256
ARG HARNESS_REVISION
RUN printf '%s\n' "${HARNESS_REVISION}" | grep -Eq '^[0-9a-f]{40}$' \
    && printf '%s' "${IMAGE_METADATA_B64}" | base64 -d > /opt/simple/image-metadata.json
RUN TOOLBOX_MANIFEST_SHA256="${TOOLBOX_MANIFEST_SHA256}" \
    HARNESS_REVISION="${HARNESS_REVISION}" \
    /usr/bin/python3 <<'PY'
import json
import os

with open("/opt/simple/image-metadata.json", encoding="utf-8") as source:
    metadata = json.load(source)
assert metadata["schema_version"] == 2
assert metadata["toolbox_manifest_sha256"] == os.environ["TOOLBOX_MANIFEST_SHA256"]
assert metadata["harness_revision"] == os.environ["HARNESS_REVISION"]
assert len(metadata["tools"]) == 11
for tool, registered in metadata["tools"].items():
    executable = registered["executable"]
    assert os.path.isdir(registered["subject_workdir"]), tool
    assert os.path.isfile(executable[0]) and os.access(executable[0], os.X_OK), tool
    assert all(not token.startswith("/") or os.path.exists(token) for token in executable[1:]), tool
PY
RUN mkdir -p /opt/simple/s3_listing_study/manager \
    && touch /opt/simple/s3_listing_study/manager/__init__.py \
    && ln -s ../common/contract.py /opt/simple/s3_listing_study/manager/contract.py \
    && ln -s ../common/duckdb_adapter.py /opt/simple/s3_listing_study/manager/duckdb_adapter.py \
    && ln -s ../common/normalizer_cli.py /opt/simple/s3_listing_study/manager/normalizer_cli.py \
    && chown -R root:root /opt/simple \
    && find /opt/simple -type f -exec chmod 0444 {} + \
    && find /opt/simple -type d -exec chmod 0555 {} +
LABEL io.varve.s3-listing-study.tool-build-sha256="" \
      io.varve.s3-listing-study.toolbox-manifest-sha256="${TOOLBOX_MANIFEST_SHA256}" \
      io.varve.s3-listing-study.harness-revision="${HARNESS_REVISION}"

ENV PYTHONPATH=/opt/simple
USER 10001:10001
WORKDIR /home/s3study
ENTRYPOINT ["/usr/bin/python3", "/opt/simple/measure.py"]
CMD []
