# Single stage, one image for every tool. Compare tools/<tool>/build/ in the
# real repo: eleven separate multi-stage Dockerfiles, each pinned to a base
# image digest, each producing a "derived image" whose sha256 the campaign
# fingerprints and pins in the rendered Batch job. None of that here.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        gnupg \
    && curl -sSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | apt-key add - \
    && echo "deb https://packages.cloud.google.com/apt cloud-sdk main" \
        > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && apt-get update && apt-get install -y --no-install-recommends google-cloud-cli \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir duckdb pyyaml

# Real per-tool binaries (aws-cli, s5cmd, rclone, mc, s7cmd, s3-fast-list,
# s3kor, s4cmd, s3p, ps3, swath) would each be COPY'd in or installed here --
# omitted since this image is never actually run.

WORKDIR /app
COPY . /app

ENTRYPOINT ["python", "/app/measure.py"]
