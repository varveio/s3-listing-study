# syntax=docker/dockerfile:1
#
# The `python` named context is the study's pinned CPython, fetched and verified
# on the host by src/s3_listing_study/python_runtime.py. It is bound here rather
# than taken from the subject because only one of the eleven subject images has
# an interpreter at all, and because one pinned interpreter across every subject
# is one fewer uncontrolled difference in the comparison.
FROM subject AS attempt-builder

# Subject images run as their own unprivileged user (Swath's is 10001:10001).
# The builder stage writes the payload and the zipapp, so it needs root; this
# stage is discarded and never affects what the final image runs as.
USER root
WORKDIR /build
COPY --from=python . /opt/s3-listing-study/python/
# Only the two packages an attempt can reach. s3_listing_study/host/ is
# deliberately absent: it never executes here, and leaving it out is what keeps a
# subject image's digest still while host-side code changes — a campaign pins
# these digests, so orchestrator work must not move them.
COPY src/s3_listing_study/__init__.py /build/payload/s3_listing_study/__init__.py
COPY src/s3_listing_study/common/ /build/payload/s3_listing_study/common/
COPY src/s3_listing_study/attempt/ /build/payload/s3_listing_study/attempt/
COPY harness/derived-image/zipapp_main.py /build/payload/__main__.py
COPY harness/derived-image/validate_selection.py /build/validate_selection.py
COPY --from=selection image.json /build/image.json
COPY --from=adapter command.py /build/tool/command.py
COPY --from=adapter normalize.py /build/tool/normalize.py
RUN ["/opt/s3-listing-study/python/bin/python3", "-I", "/build/validate_selection.py"]
RUN ["/opt/s3-listing-study/python/bin/python3", "-I", "-m", "zipapp", "/build/payload", "-o", "/attempt.pyz"]

FROM subject

COPY --from=python . /opt/s3-listing-study/python/
COPY --from=attempt-builder /attempt.pyz /opt/s3-listing-study/attempt.pyz
COPY --from=attempt-builder /build/tool/ /opt/s3-listing-study/tool/
COPY --from=attempt-builder /build/image.json /opt/s3-listing-study/selection.json
ENTRYPOINT ["/opt/s3-listing-study/python/bin/python3", "-I", "/opt/s3-listing-study/attempt.pyz"]
CMD []
