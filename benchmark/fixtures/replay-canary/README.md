# Replay runner canary fixture

This synthetic fixture exists only to exercise the runner's replay integration.
It contains 2,048 objects across 16 prefixes, enough to force pagination while
remaining far below a tool's meaningful memory limit. It is not a captured
bucket and supports no performance conclusion.

The repository stores the SELECT, not its generated Parquet product. Generate a
temporary fixture from the repository root with the lockfile's DuckDB 1.5.5:

```sh
fixture_dir=$(mktemp -d)
uv run --frozen python -m benchmark.replay_fixture "$fixture_dir" \
  --generate-query benchmark/fixtures/replay-canary/generate.sql \
  --expect 6e1c2d47a92bbd1062469fb323f95b1d0f127b4e601b93f0d94576ab16d7c8b4 \
  --show-manifest
```

Upload the generated part once under its content-addressed fixture prefix. Use a
create-only precondition so an existing object cannot be replaced:

```sh
gcloud storage cp --if-generation-match=0 \
  "$fixture_dir/part-00000.parquet" \
  gs://RESULTS_BUCKET/fixtures/runner-replay-canary/6e1c2d47a92bbd1062469fb323f95b1d0f127b4e601b93f0d94576ab16d7c8b4/part-00000.parquet
```

The replay canary plan stages that object before starting the ordinary pinned
replay-server image. Each job verifies the one-part manifest against the plan's
fixture digest before the server starts; generation and upload are not repeated
per case.
