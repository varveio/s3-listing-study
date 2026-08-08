# AWS CLI derived attempt image

[`Dockerfile`](Dockerfile) builds a stdlib-only zipapp from the shared
`src/s3_listing_study/attempt/` source and the shared credential-shape scanner,
then copies that single payload into a fresh stage of the pinned ARM64 AWS CLI
2.36.1 subject image.

Live inspection of that subject established the compatibility facts used by
this first recipe: `/usr/bin/python3` exists, the effective user is the image
default (unset, therefore root), the working directory is `/aws`, and the image
adds no environment beyond `PATH`. The final stage starts again from the exact
subject digest, declares the same workdir, and does not declare `USER` or `ENV`.

Build from the repository root:

```sh
docker build -f harness/images/aws-cli/Dockerfile .
```

At runtime, set `S3_STUDY_ATTEMPT_OUT=/output` and mount a unique empty attempt
directory at `/output`. The exec-form entrypoint is equivalent to:

```text
/usr/bin/python3 -I /opt/s3-listing-study/attempt.pyz \
  --tool aws-cli --command-prefix /usr/local/bin/aws \
  --mode s3api-v2-text --bucket BUCKET --region REGION \
  --prefix '' --scope full \
  <remaining runner metadata options> -- <adapter argv...>
```

The scheduler obtains `<adapter argv...>` from
`tools/aws-cli/adapter/run.sh`. Metadata options such as the attempt ID, tool
version, actual derived-image digest, mode, bucket, region, prefix, and verifier
scope are placed before `--`; the exact tool argv follows it. Neither the image
nor the runner evaluates a shell command.

After the measured subject exits, the runner applies the shared
credential-shape scanner to the complete raw stdout and stderr as opaque bytes.
A flag or scanner error exits 2 and publishes no attempt artifacts. Clean runs
record their scan status, exact child environment, and tool-neutral target
metadata in `result.json` before publishing the usual three-artifact set.

This image uses the subject's compatible system Python, but the runner source is
the same shared implementation. Subjects without a compatible interpreter
still need a common self-contained payload solution; that packaging problem is
not permission to fork the runner.

Before evidentiary use, compare the original and derived images for effective
user, workdir, environment, architecture, tool version, and exact subject argv.
Building or running Docker remains outside this implementation slice.
