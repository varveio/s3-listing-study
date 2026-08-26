# Replay runner canary fixture

This synthetic fixture exists only to exercise the runner's replay integration.
It contains 2,048 objects across 16 prefixes, enough to force pagination while
remaining far below a tool's meaningful memory limit. It is not a captured
bucket and supports no performance conclusion.

Regenerate `part-00000.parquet` from the repository root with DuckDB 1.5.5:

```sh
uv run python - <<'PY'
from pathlib import Path

import duckdb

sql = Path("benchmark/fixtures/replay-canary/generate.sql").read_text()
duckdb.connect().execute(sql)
PY
```

The plan records the fixture-manifest digest printed by:

```sh
uv run python -m benchmark.replay_fixture benchmark/fixtures/replay-canary
```
