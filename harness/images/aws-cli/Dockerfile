FROM amazon/aws-cli@sha256:406ca32d31e640a56e8d52921b40528cc64bfa59ec9cb4ee1456db6746cb7292 AS attempt-builder

WORKDIR /build
COPY src/s3_listing_study/__init__.py /build/s3_listing_study/__init__.py
COPY src/s3_listing_study/secret_scan.py /build/s3_listing_study/secret_scan.py
COPY src/s3_listing_study/attempt/ /build/s3_listing_study/attempt/
COPY harness/images/aws-cli/zipapp_main.py /build/__main__.py
RUN ["/usr/bin/python3", "-I", "-m", "zipapp", "/build", "-o", "/attempt.pyz"]

FROM amazon/aws-cli@sha256:406ca32d31e640a56e8d52921b40528cc64bfa59ec9cb4ee1456db6746cb7292

COPY --from=attempt-builder /attempt.pyz /opt/s3-listing-study/attempt.pyz
WORKDIR /aws
ENTRYPOINT ["/usr/bin/python3", "-I", "/opt/s3-listing-study/attempt.pyz", "--tool", "aws-cli", "--command-prefix", "/usr/local/bin/aws"]
CMD []
