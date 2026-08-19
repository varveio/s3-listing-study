# Replay-server sidecar image for the colocation spike.
#
# Pilot artifact, not a campaign artifact: it is assembled from a locally built
# `:swath-replay-server:installDist` rather than from a published swath release,
# because the server it must carry is swath PR #113, which is green but not yet
# merged. The commit is recorded as a label so a receipt can name what ran.
#
# The fixture is baked in rather than staged from GCS. One fixture, one image
# digest: the bytes the server answered from are then part of the image identity
# the attempt already records, and the Batch worker service account needs no
# object-read grant it does not already have (it can pull from Artifact Registry
# and nothing else). Staging from a bucket is the right answer once there are
# many fixtures; it is not worth an IAM change for one.
#
# Build context is assembled outside the repo — see `build.sh` beside this file.
# Neither the fixture nor the server distribution is committed.

FROM eclipse-temurin:25-jre-noble@sha256:d062925a8ef2d71547b3ef558ee10037b60cf8a325d6b4575c6b0c4e9acabd08

ARG SWATH_COMMIT
ARG FIXTURE_SHA256
ARG FIXTURE_BUCKET
LABEL io.varve.swath.commit="${SWATH_COMMIT}" \
      io.varve.replay.fixture_sha256="${FIXTURE_SHA256}" \
      io.varve.replay.fixture_bucket="${FIXTURE_BUCKET}"

# A fixture is served by seeking into it, and a timestamp is rendered per row:
# the box's zone must not be a variable the results carry.
ENV TZ=UTC

COPY dist/ /opt/swath-replay-server/
COPY fixture/ /fixtures/

# The spike's whole question is whether Batch honors per-runnable cgroup
# options, so the entrypoint reads its own cgroup back before it serves
# anything. It costs one line of log per start and settles the fact from
# inside the container, which is the only place it can be settled.
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
