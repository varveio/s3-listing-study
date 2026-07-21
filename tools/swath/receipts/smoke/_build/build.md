# Build provenance — swath image

The smoke image was built by the research agent from the pinned upstream checkout;
this receipt records the source->image binding the run.meta files cannot (they
carry only the image digest + `--version` string, not a source SHA).

- Source checkout: `<sources>/swath` at git HEAD
  **`f1009db599861a7e905a539778d915f1bb5426eb`** (default-branch HEAD; no releases/tags).
- Build command: `docker build -t swath:groundwork .` (upstream `Dockerfile` verbatim,
  multi-stage `:swath-cli:shadowJar`), in that checkout.
- Built image manifest digest (Docker 29 containerd store; image ID == manifest digest):
  **`sha256:1dc6d1e60d4f9aabffcde8b789e49688938cbabcf93b3e35a1c53fc73ea8f9d1`**.
- Pushed to a throwaway local registry for the wrapper's digest-pin requirement:
  `localhost:5000/swath@sha256:1dc6d1e60d4f9aabffcde8b789e49688938cbabcf93b3e35a1c53fc73ea8f9d1`.
- Reported `--version`: `swath 0.1.0-SNAPSHOT`. Host/build arch: arm64, native.

CAVEAT (codex review F1): this source->image link is an **agent-asserted build
fact**, not a cryptographic binding embedded in each run receipt. The image carries
no OCI `revision`/source-SHA label, so a receipt read in isolation proves only
"this digest, version 0.1.0-SNAPSHOT", not "built from f1009db". A future build
should stamp the source SHA into an image label so the binding is receipt-checkable.
