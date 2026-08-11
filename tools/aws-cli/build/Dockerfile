# syntax=docker/dockerfile:1
# Official aws-cli v2 installer archive, installed on the common study base.
ARG SHARED_BASE_IMAGE
FROM ${SHARED_BASE_IMAGE} AS install
USER root
ADD --checksum=sha256:6b36b85980ab783db7ba0ff24ebb85924682fa6da1db7ca3c2fa6f27083c0f52 \
    https://awscli.amazonaws.com/awscli-exe-linux-x86_64-2.36.1.zip /tmp/awscliv2.zip
RUN /usr/bin/python3 -m zipfile -e /tmp/awscliv2.zip /tmp/aws \
    && chmod 0755 /tmp/aws/aws/install /tmp/aws/aws/dist/aws /tmp/aws/aws/dist/aws_completer \
    && /tmp/aws/aws/install --install-dir /opt/aws-cli --bin-dir /usr/local/bin \
    && /usr/local/bin/aws --version \
    && test "$(readlink /usr/local/bin/aws)" \
      = /opt/aws-cli/v2/current/bin/aws \
    && test "$(readlink /usr/local/bin/aws_completer)" \
      = /opt/aws-cli/v2/current/bin/aws_completer
RUN install -d -o 10001 -g 10001 /aws
ARG TOOL_BUILD_SHA256
LABEL io.varve.s3-listing-study.tool-build-sha256="${TOOL_BUILD_SHA256}"
USER 10001:10001
WORKDIR /aws
