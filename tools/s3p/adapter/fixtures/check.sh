#!/usr/bin/env bash
# Reproducible validation of ../normalize.sh against staged fixtures.
# Runs AFTER any measurement clock (adapters are never on the clock). No S3 /
# credentials needed — these are synthetic inputs shaped like s3p's real output,
# used because the live listing modes are auth-blocked (see report §8).
#   usage: ./check.sh   (exit 0 = all modes match their .expected.tsv)
set -euo pipefail
cd "$(dirname "$0")"
N=../normalize.sh
fail=0
run() { # <mode> <fixture> <expected>
  local got; got="$("$N" "$1" < "$2")"
  if [ "$got" = "$(cat "$3")" ]; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi
}
run ls-raw    ls-raw.fixture.jsonl  ls-raw.expected.tsv
run ls        ls.fixture.txt        ls.expected.tsv
run ls-long   ls-long.fixture.txt   ls-long.expected.tsv
run summarize summarize.fixture.txt summarize.expected.tsv
run ls        empty.fixture.txt     empty-ls.expected.tsv
exit $fail
