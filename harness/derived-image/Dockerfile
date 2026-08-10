# syntax=docker/dockerfile:1
# The worker layer consumes an already-built immutable tool image by digest.
ARG TOOL_IMAGE
FROM ${TOOL_IMAGE} AS assembler
USER root
COPY src/s3_listing_study/__init__.py /payload/s3_listing_study/__init__.py
COPY src/s3_listing_study/common/ /payload/s3_listing_study/common/
COPY src/s3_listing_study/worker/ /payload/s3_listing_study/worker/
COPY harness/derived-image/zipapp_main.py /payload/__main__.py
RUN mkdir /out \
    && /usr/bin/python3 -I -m zipapp /payload -o /out/attempt.pyz

ARG TOOL_IMAGE
FROM ${TOOL_IMAGE}
USER root
ARG TOOL_IMAGE_DIGEST
ARG TOOL_IMAGE_URI
ARG SELECTION_SHA256
COPY --from=assembler /out/attempt.pyz /opt/s3-listing-study/attempt.pyz
COPY harness/derived-image/validate_selection.py /opt/s3-listing-study/validate_selection.py
COPY --from=selection image.json /opt/s3-listing-study/selection.json
COPY --from=adapter command.py /opt/s3-listing-study/tool/command.py
COPY --from=adapter normalize.py /opt/s3-listing-study/tool/normalize.py
RUN /usr/bin/python3 -I -B -c \
      'import json,sys; print(json.dumps({"schema_version":1,"selection_sha256":sys.argv[1],"tool_image":{"digest":sys.argv[2],"uri":sys.argv[3]}},sort_keys=True,separators=(",",":")))' \
      "${SELECTION_SHA256}" "${TOOL_IMAGE_DIGEST}" "${TOOL_IMAGE_URI}" \
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
