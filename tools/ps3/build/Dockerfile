# ps3 (pS3) — smoke-test container for the s3-listing-study.
#
# Upstream (https://github.com/jboothomas/ps3) ships NEITHER a published image
# NOR a Dockerfile, so per the brief we write one. It also ships no go.mod and
# its source at the pinned commit does NOT compile (see research/report.md
# § Container and receipts/smoke/_build/), so we cannot build from source.
# What upstream *does* publish is a committed, prebuilt binary `pS3.0-1-16`
# (amd64 only). That binary is the only artifact the project actually ships,
# so it is what we run — fetched by content hash from the pinned commit.
#
# amd64 only: the prebuilt binary is ELF x86-64. On an arm64 runner this image
# runs under qemu emulation (smoke only) — see the receipts' arch/emulated.

FROM debian@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818

# CA roots for HTTPS to S3; tzdata so TZ=UTC resolves. Pinned base digest above.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV TZ=UTC

# The tool's shipped prebuilt binary, pinned by commit path AND content hash.
# sha256 3bc7bbbb0d45d6f96b0130f98859ea7cfe693b13a8438461240423511322a9c2
ADD --checksum=sha256:3bc7bbbb0d45d6f96b0130f98859ea7cfe693b13a8438461240423511322a9c2 \
    https://raw.githubusercontent.com/jboothomas/ps3/9428492291ef3aa824dba0b495583279c3d33760/pS3.0-1-16 \
    /usr/local/bin/pS3
RUN chmod +x /usr/local/bin/pS3

# Entrypoint IS the tool binary: run.sh argv starts at the subcommand.
ENTRYPOINT ["/usr/local/bin/pS3"]
