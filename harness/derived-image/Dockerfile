# syntax=docker/dockerfile:1
FROM subject AS attempt-builder

WORKDIR /build
COPY src/s3_listing_study/__init__.py /build/payload/s3_listing_study/__init__.py
COPY src/s3_listing_study/argparse_utils.py /build/payload/s3_listing_study/argparse_utils.py
COPY src/s3_listing_study/build_selection.py /build/payload/s3_listing_study/build_selection.py
COPY src/s3_listing_study/command_adapter.py /build/payload/s3_listing_study/command_adapter.py
COPY src/s3_listing_study/secret_scan.py /build/payload/s3_listing_study/secret_scan.py
COPY src/s3_listing_study/attempt/ /build/payload/s3_listing_study/attempt/
COPY harness/derived-image/zipapp_main.py /build/payload/__main__.py
COPY harness/derived-image/validate_selection.py /build/validate_selection.py
COPY --from=selection image.json /build/image.json
COPY --from=adapter command.py /build/tool/command.py
COPY --from=adapter normalize.py /build/tool/normalize.py
RUN ["/usr/bin/python3", "-I", "/build/validate_selection.py"]
RUN ["/usr/bin/python3", "-I", "-m", "zipapp", "/build/payload", "-o", "/attempt.pyz"]

FROM subject

COPY --from=attempt-builder /attempt.pyz /opt/s3-listing-study/attempt.pyz
COPY --from=attempt-builder /build/tool/ /opt/s3-listing-study/tool/
COPY --from=attempt-builder /build/image.json /opt/s3-listing-study/selection.json
ENTRYPOINT ["/usr/bin/python3", "-I", "/opt/s3-listing-study/attempt.pyz"]
CMD []
