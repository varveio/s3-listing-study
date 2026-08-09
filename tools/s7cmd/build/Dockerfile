# s7cmd — built from source at the pinned tag v1.5.0.
#
# This IS upstream's own Dockerfile (nidor1998/s7cmd @ d589df7ce691edbede05fc9a691ab1787cdb6b9e),
# the artifact that most closely matches what the project intends users to
# run — see ../docs/running.md § Build for the full provenance and the
# earlier local-only build this replaces (arm64, never registered under the
# current schema). The only deviation is digest-pinning both bases; upstream
# floats `rust:1-trixie` / `debian:trixie-slim`, this study never floats a
# base. Runtime base is Debian (glibc) — NOT Alpine — despite
# harness/derived-image/README.md's libc note, which is stale on this point.
#
# Pinned commit: d589df7ce691edbede05fc9a691ab1787cdb6b9e (tag v1.5.0)
FROM rust@sha256:3382bd20aa942806c533e9a73cd000474fb3ef173f71e684cc9b942675781769 AS builder
WORKDIR /s7cmd
COPY . ./
RUN git config --global --add safe.directory /s7cmd \
&& cargo build --release

FROM debian@sha256:3a39a0592364683e6bab97937b72cad5a8fa6dcbbee90edb3bb48c7f8e94f258
RUN apt-get update \
&& apt-get install --no-install-recommends -y ca-certificates \
&& apt-get clean \
&& rm -rf /var/lib/apt/lists/*

COPY --from=builder /s7cmd/target/release/s7cmd /usr/local/bin/s7cmd

RUN useradd -m -s /bin/bash s7cmd
USER s7cmd
WORKDIR /home/s7cmd/
ENTRYPOINT ["/usr/local/bin/s7cmd"]
