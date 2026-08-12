# syntax=docker/dockerfile:1
# The worker layer consumes an already-built immutable tool image by digest.
ARG TOOL_IMAGE
FROM ${TOOL_IMAGE} AS assembler
USER root
COPY src/s3_listing_study/__init__.py /payload/s3_listing_study/__init__.py
COPY src/s3_listing_study/common/ /payload/s3_listing_study/common/
COPY src/s3_listing_study/worker/ /payload/s3_listing_study/worker/
COPY src/twinstamp/ /payload/twinstamp/
COPY harness/derived-image/zipapp_main.py /payload/__main__.py
RUN find /payload -exec touch -h -d '1980-01-01 00:00:00 UTC' {} + \
    && rm -rf /opt/s3-listing-study-worker-build \
    && mkdir /opt/s3-listing-study-worker-build \
    && /usr/bin/python3 -I -m zipapp /payload \
      -o /opt/s3-listing-study-worker-build/attempt.pyz \
    && touch -d '1980-01-01 00:00:00 UTC' \
      /opt/s3-listing-study-worker-build/attempt.pyz

ARG TOOL_IMAGE
FROM ${TOOL_IMAGE}
USER root
ARG TOOL_IMAGE_DIGEST
ARG TOOL_IMAGE_URI
ARG SELECTION_SHA256
ARG WORKER_SOURCE_SHA256
LABEL io.varve.s3-listing-study.worker-source-sha256="${WORKER_SOURCE_SHA256}"
COPY --from=assembler /opt/s3-listing-study-worker-build/attempt.pyz /opt/s3-listing-study/attempt.pyz
COPY harness/derived-image/validate_selection.py /opt/s3-listing-study/validate_selection.py
COPY --from=selection image.json /opt/s3-listing-study/selection.json
COPY --from=adapter command.py /opt/s3-listing-study/tool/command.py
COPY --from=adapter normalize.py /opt/s3-listing-study/tool/normalize.py
RUN /usr/bin/python3 -I -B -c \
      'import json,sys; print(json.dumps({"schema_version":2,"selection_sha256":sys.argv[1],"worker_source_sha256":sys.argv[2],"tool_image":{"digest":sys.argv[3],"uri":sys.argv[4]}},sort_keys=True,separators=(",",":")))' \
      "${SELECTION_SHA256}" "${WORKER_SOURCE_SHA256}" "${TOOL_IMAGE_DIGEST}" "${TOOL_IMAGE_URI}" \
      > /opt/s3-listing-study/image-provenance.json \
    && chmod 0444 /opt/s3-listing-study/attempt.pyz \
        /opt/s3-listing-study/image-provenance.json \
        /opt/s3-listing-study/selection.json \
        /opt/s3-listing-study/tool/*.py \
    && chown -R root:root /opt/s3-listing-study \
    && /usr/bin/python3 -I -B /opt/s3-listing-study/validate_selection.py

USER 10001:10001
ENTRYPOINT ["/usr/bin/python3", "-I", "/opt/s3-listing-study/attempt.pyz"]
CMD []
