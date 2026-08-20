# Replay-server image for replay-backed benchmark cases.
#
# The server distribution is assembled from an explicit Swath checkout. Its
# clean commit and the served fixture digest become image labels, so a plan can
# pin the resulting immutable image without duplicating either identity fact.
#
# The fixture is baked in rather than staged from GCS. One fixture, one image
# digest: the bytes the server answered from are then part of the image identity
# the attempt already records, and the Batch worker service account needs no
# object-read grant it does not already have (it can pull from Artifact Registry
# and nothing else). Staging from a bucket is the right answer once there are
# many fixtures; it is not worth an IAM change for one.
#
# `build.sh` assembles the external build context. Neither the fixture nor the
# server distribution is committed here.

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

COPY entrypoint.sh /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
