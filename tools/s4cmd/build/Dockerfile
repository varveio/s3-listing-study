# s4cmd 2.1.0 — containerized for the s3-listing-study smoke phase.
#
# Upstream (bloomreach/s4cmd) ships neither a published image nor a Dockerfile,
# so this recipe is written per the brief's "neither image nor Dockerfile" case.
#
# Pinning note (reproducibility, NOT necessity): boto3/botocore are pinned to a
# 2018-era line for a deterministic environment. s4cmd 2.1.0 references
# `botocore.vendored.requests` (s4cmd.py:274); an earlier draft assumed this
# broke import under current botocore (removed 2019) — that was WRONG and is
# retracted: s4cmd 2.1.0 installs, imports, and runs under the latest
# botocore 1.43.50 (see ../receipts/smoke/_build/modern-boto3-import/). The pin is a
# choice; the benchmark phase should reconsider it and use a current boto3.
FROM python@sha256:b53f496ca43e5af6994f8e316cf03af31050bf7944e0e4a308ad86c001cf028b

ENV TZ=UTC \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime deps pinned for reproducibility (see note above), then s4cmd itself
# installed from the exact pinned commit (tag 2.1.0) with --no-deps so the pinned
# boto3 line is the one actually used.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && pip install "boto3==1.9.253" "botocore==1.12.253" "pytz==2018.5" \
 && pip install --no-deps \
      "git+https://github.com/bloomreach/s4cmd@80059bfa4451f513a8f314fb6300e5ecc51587b2"

ENTRYPOINT ["s4cmd"]
