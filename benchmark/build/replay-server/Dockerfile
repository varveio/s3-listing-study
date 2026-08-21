# Replay-server image for replay-backed benchmark cases.
#
# The server distribution is assembled from an explicit Swath checkout. Fixture
# data is never part of this build context or image; Batch stages it from the
# plan's GCS URI onto the worker before starting this container.
#
# `build.sh` assembles the external build context. Neither the fixture nor the
# server distribution is committed here.

FROM eclipse-temurin:25-jre-noble@sha256:d062925a8ef2d71547b3ef558ee10037b60cf8a325d6b4575c6b0c4e9acabd08

ARG SWATH_COMMIT
LABEL io.varve.swath.commit="${SWATH_COMMIT}"

# A fixture is served by seeking into it, and a timestamp is rendered per row:
# the box's zone must not be a variable the results carry.
ENV TZ=UTC

COPY dist/ /opt/swath-replay-server/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
