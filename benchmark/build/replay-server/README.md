# Replay server image

Build input for the server used by replay-backed benchmark cases. It is
separate from the toolbox: the server and its immutable fixture form the replay
backend a plan pins by image digest.

`build.sh <image-tag>` requires a clean `SWATH_REPO`, a sorted-Parquet
`FIXTURE_DIR`, and `FIXTURE_BUCKET`. It assembles a temporary Docker context,
labels the image with the Swath commit and digest over served fixture bytes, and
builds it. Publish only after an explicitly authorized registry operation; put
the resulting `@sha256:` image URI and fixture digest in the replay plan.

The recipe is a build recipe, not a capacity or provider-enforcement test. Run
a dedicated Batch canary to establish those facts before any measurement.
