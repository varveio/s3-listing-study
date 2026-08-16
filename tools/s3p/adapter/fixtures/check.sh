#!/usr/bin/env bash
# Reproducible validation of ../normalize.py against staged fixtures.
# Runs AFTER any measurement clock (adapters are never on the clock). No S3 /
# credentials needed — these are synthetic inputs shaped like s3p's real output,
# used because the live listing modes are auth-blocked (see report §8).
#   usage: ./check.sh   (exit 0 = all modes match their .expected.tsv)
set -euo pipefail
cd "$(dirname "$0")"
REPO_ROOT="$(cd -- ../../../.. && pwd)"
N=../normalize.py

# normalize.py imports the packaged DuckDB adapter, so it is RUN BY an
# interpreter chosen here rather than executed on its shebang, which would take
# whatever `python3` the box happens to carry — on a CI runner, one with no
# s3_listing_study at all. Same resolution order as the regression suite:
# `$S3STUDY_PYTHON` if set, else `.venv/` if `uv sync` made one, else `python3`.
PYTHON="${S3STUDY_PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x "$REPO_ROOT/.venv/bin/python3" ]; then
    PYTHON="$REPO_ROOT/.venv/bin/python3"
  else
    PYTHON="python3"
  fi
fi
# A missing dependency stops here, loudly: unresolvable is not the same as a
# mode that normalized wrongly, and five FAIL lines would read as the adapter
# being broken.
if ! PYTHONPATH="$REPO_ROOT" "$PYTHON" -P -c 'import benchmark.runtime.duckdb_adapter, duckdb' 2>/dev/null; then
  printf 'FIXTURE QA CANNOT RUN: %s cannot import the adapter and duckdb.\n' "$PYTHON" >&2
  printf '  Run `uv sync` in the repo root, or point S3STUDY_PYTHON at an interpreter that has them.\n' >&2
  exit 2
fi

fail=0
run() { # <mode> <fixture> <expected>
  local got; got="$(PYTHONPATH="$REPO_ROOT" "$PYTHON" -P "$N" "$1" < "$2")"
  if [ "$got" = "$(cat "$3")" ]; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi
}
run ls-raw    ls-raw.fixture.jsonl  ls-raw.expected.tsv
run ls        ls.fixture.txt        ls.expected.tsv
run ls-long   ls-long.fixture.txt   ls-long.expected.tsv
run summarize summarize.fixture.txt summarize.expected.tsv
run ls        empty.fixture.txt     empty-ls.expected.tsv
exit $fail
