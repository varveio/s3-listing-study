# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc AS uv
FROM docker.io/library/python@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff AS build

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
COPY experiments/orchestration/snakemake/runtime/pyproject.toml experiments/orchestration/snakemake/runtime/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
    && mkdir -p /runtime \
    && cp -a .venv/lib/python3.11/site-packages /runtime/site-packages \
    && /usr/local/bin/python -c 'import sys; sys.path.insert(0, "/runtime/site-packages"); import snakemake, snakemake_storage_plugin_gcs; assert snakemake.__version__ == "9.25.1"'

FROM docker.io/library/python@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff
COPY --from=build /runtime /runtime
COPY experiments/orchestration/snakemake/runtime/stage-runtime /usr/local/bin/stage-runtime
RUN chmod 0555 /usr/local/bin/stage-runtime \
    && find /runtime -type d -exec chmod 0555 {} + \
    && find /runtime -type f -exec chmod 0444 {} +
ENTRYPOINT ["/usr/local/bin/stage-runtime"]
