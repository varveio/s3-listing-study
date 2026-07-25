# normalize.py adapter fixtures

These validate `tools/s3p/adapter/normalize.py` **without** a live listing — s3p's
listing modes are auth-blocked in this campaign (no anonymous access, `CREDS=none`;
see `research/report.md` §8), so no real tool output could be captured. The
inputs are synthetic but shaped exactly like s3p's real output for each mode
(`ls-raw` = a `listObjectsV2` Contents element per line; `ls` = one key per line;
`ls-long` = the human date/size/key line; `summarize` = an aggregate report).

`check.sh` runs each fixture through `normalize.py` and diffs against the
committed `*.expected.tsv`. Regenerate expected files by piping each fixture
through `normalize.py <mode>`.

Coverage of note:
- `ls-raw.fixture.jsonl` includes `weird/back\slash-key` — a backslash is inside
  s3p's 95-char supported alphabet, and it must survive normalization as a single
  backslash (raw key bytes, no re-encoding). The shell adapter used raw `jq -j`
  and not `@tsv` for this reason; the ported adapter carries the key as bytes.
- `empty.fixture.txt` is empty: normalizing it must yield empty output at exit 0,
  not a `pipefail` failure.
