# Replay server image

Build input for the server used by replay-backed benchmark cases. It is
separate from the toolbox. It contains server code only; fixture bytes are
downloaded from the plan's versioned GCS URI on the Batch worker.

`build.sh <image-tag>` requires a clean `SWATH_REPO`. It assembles a temporary
Docker context and labels the image with the Swath commit. Publish only after an
explicitly authorized registry operation; put the resulting `@sha256:` image
URI in the replay plan. Never add a fixture directory to this build context.

The recipe is a build recipe, not a capacity or provider-enforcement test. Run
a dedicated Batch canary to establish those facts before any measurement.
