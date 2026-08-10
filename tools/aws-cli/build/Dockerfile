# syntax=docker/dockerfile:1
# Official aws-cli v2 installer archive, installed on the common study base.
FROM base AS install
USER root
ADD --checksum=sha256:6b36b85980ab783db7ba0ff24ebb85924682fa6da1db7ca3c2fa6f27083c0f52 \
    https://awscli.amazonaws.com/awscli-exe-linux-x86_64-2.36.1.zip /tmp/awscliv2.zip
RUN /opt/s3-listing-study/python/bin/python3 -m zipfile -e /tmp/awscliv2.zip /tmp/aws \
    && chmod 0755 /tmp/aws/aws/install /tmp/aws/aws/dist/aws /tmp/aws/aws/dist/aws_completer \
    && /tmp/aws/aws/install --install-dir /opt/aws-cli --bin-dir /usr/local/bin \
    && /usr/local/bin/aws --version \
    && mkdir -p /tool-root/usr/local/bin \
    && ln -s /opt/aws-cli/v2/current/bin/aws /tool-root/usr/local/bin/aws \
    && ln -s /opt/aws-cli/v2/current/bin/aws_completer \
      /tool-root/usr/local/bin/aws_completer \
    && test "$(readlink /tool-root/usr/local/bin/aws)" \
      = /opt/aws-cli/v2/current/bin/aws \
    && test "$(readlink /tool-root/usr/local/bin/aws_completer)" \
      = /opt/aws-cli/v2/current/bin/aws_completer
WORKDIR /aws

FROM scratch
COPY --from=install /opt/aws-cli/ /tool-root/opt/aws-cli/
COPY --from=install /tool-root/usr/local/bin/ /tool-root/usr/local/bin/
