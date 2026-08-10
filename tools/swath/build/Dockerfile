# syntax=docker/dockerfile:1
# The official release jar and the same digest-pinned Temurin 25 JRE used by
# upstream, copied onto the common study base. The JRE stage is discarded.
FROM eclipse-temurin:25-jre-noble@sha256:2f1da100788559b397bcf48c736169ea5b070bde84e55f203bbee8e83d87a175 AS jre

FROM scratch
COPY --from=jre /opt/java/openjdk/ /tool-root/opt/java/openjdk/
ADD --checksum=sha256:4bd2204d17db657436046d3bf4cb1aedc44fdcd645f35c4b6fed4108ca251a02 \
    https://github.com/varveio/swath/releases/download/v0.2.2/swath-0.2.2.jar \
    /tool-root/opt/swath/swath.jar
