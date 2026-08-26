# Replay canary image

This image adds only the small synthetic runner fixture to the pinned replay
server. Keeping it separate from both the toolbox and normal replay-server
image prevents a runner qualification from depending on private fixture
storage or worker IAM.

Build from the repository root:

```sh
docker build -f benchmark/build/replay-canary/Dockerfile \
  -t replay-canary:local .
```

Publish only after an explicitly authorized registry operation, then put the
immutable image digest in the replay canary plan. The plan's
`fixture_sha256` independently binds the Parquet bytes inside the image.
