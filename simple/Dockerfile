# Single stage, one image for every tool. Compare tools/<tool>/build/ in the
# real repo: eleven separate multi-stage Dockerfiles, each pinned to a base
# image digest, each producing a "derived image" whose sha256 the campaign
# fingerprints and pins in the rendered Batch job. None of that here.
#
# Owner correction: gsutil/gcloud subprocesses are gone (see gcs.py,
# campaign.py), so the google-cloud-cli apt package goes with them --
# google-cloud-storage is a pip install now, alongside DuckDB (the capsule
# normalizers' own dependency) and PyYAML.
#
# adapters.py's command_adapter and campaign.py's Plan.load need the real
# s3_listing_study package importable, which this Dockerfile does not stage:
# its build context is simple/ alone (`docker build -f simple/Dockerfile
# simple`, see README.md), so the actual package isn't reachable from here.
# A real build would COPY src/ from a repo-rooted context and `pip install -e .`
# (or install a published wheel) -- left as a comment, not solved, since this
# image is never actually run.
# RUN pip install -e /repo

FROM python:3.12-slim

RUN pip install --no-cache-dir duckdb pyyaml google-cloud-storage google-cloud-batch

# Real per-tool binaries (aws-cli, s5cmd, rclone, mc, s7cmd, s3-fast-list,
# s3kor, s4cmd, s3p, ps3, swath) would each be COPY'd in or installed here --
# omitted since this image is never actually run.

WORKDIR /app
COPY . /app

ENTRYPOINT ["python", "/app/measure.py"]
