# Shared derived attempt image

[`Dockerfile`](Dockerfile) is the single derived-image recipe. A registered tool
slug selects capsule metadata that binds the subject digest, runtime,
executable, command driver, normalizer, and canonical adapter-bundle digest.
The build refuses mismatches and bundles only the selected adapter. The
scheduler supplies a typed logical request; the bundled driver resolves
complete subject argv inside the image.

The current recipe requires the subject image to provide compatible
`/usr/bin/python3` in both build and runtime stages. That is a centralized
runtime constraint, not permission to fork the recipe. A subject without that
path remains unsupported until the shared payload becomes self-contained.

Tool capsules record their declarative build inputs in `build/image.json`.
Build from the repository root through the slug-only command:

```sh
s3-listing-study build-derived-image --tool aws-cli --tag TAG
```

The command validates the slug and registered capsule paths, then binds the
generic Dockerfile's `subject`, `adapter`, and `selection` BuildKit named
contexts. Subject image, runtime, workdir, executable, and component paths are
never independent build arguments. The final `FROM subject` keeps the subject
image's workdir and includes the validated `selection.json` attestation.

`adapter_bundle_sha256` is the sole adapter identity recorded in new attempt
results. It hashes a canonical manifest: the ASCII header
`s3-listing-study-adapter-bundle-v1` plus NUL; then, in `command.py`,
`normalize.py` order, the UTF-8 filename plus NUL, an unsigned eight-byte
big-endian byte length, and the exact file bytes. Selection computes this hash
without importing the normalizer.

At runtime, mount a unique empty attempt directory at `/output`, set
`S3_STUDY_ATTEMPT_OUT=/output`, and pass the logical request directly:

```text
/usr/bin/python3 -I /opt/s3-listing-study/attempt.pyz \
  --tool aws-cli --operation list --mode s3api-v2-text \
  --bucket BUCKET --region REGION --prefix '' --scope full
```
