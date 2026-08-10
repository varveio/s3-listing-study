# syntax=docker/dockerfile:1
#
# Final assembly is intentionally ordered so worker-only edits never invalidate
# the independently cached tool payload. `base` is the exact common runtime;
# `tool` contains only a `/tool-root` filesystem overlay.
FROM base
USER root
COPY --from=tool /tool-root/ /
COPY src/s3_listing_study/__init__.py /build/payload/s3_listing_study/__init__.py
COPY src/s3_listing_study/common/ /build/payload/s3_listing_study/common/
COPY src/s3_listing_study/worker/ /build/payload/s3_listing_study/worker/
COPY harness/derived-image/zipapp_main.py /build/payload/__main__.py
COPY harness/derived-image/validate_selection.py /opt/s3-listing-study/validate_selection.py
COPY --from=selection image.json /opt/s3-listing-study/selection.json
COPY --from=adapter command.py /opt/s3-listing-study/tool/command.py
COPY --from=adapter normalize.py /opt/s3-listing-study/tool/normalize.py
RUN mkdir -p /aws /data /home/s7cmd /opt/swath
RUN ["/opt/s3-listing-study/python/bin/python3", "-I", "-m", "zipapp", "/build/payload", "-o", "/opt/s3-listing-study/attempt.pyz"]
RUN ["/opt/s3-listing-study/python/bin/python3", "-I", "-B", "/opt/s3-listing-study/validate_selection.py"]

ENTRYPOINT ["/opt/s3-listing-study/python/bin/python3", "-I", "/opt/s3-listing-study/attempt.pyz"]
CMD []
