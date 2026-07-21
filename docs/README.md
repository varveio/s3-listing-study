# Documentation

Start here. Per-tool content lives in each tool's own directory — see
[`../tools/README.md`](../tools/README.md) for the roster. Working thinking is
kept in internal notes that are not published in this repository.

## Understanding the study

For anyone evaluating what this study is, how credible it is, and what it found.

- [`methodology.md`](methodology.md) — how the study is run: the five decisions
  that shape everything, the Phase 1 / Phase 2 split, and the run-record
  requirements. The measurement plan, written down before comparative runs.
- [`s3-reference.md`](s3-reference.md) — the documented `ListObjectsV2` contract
  the tools drive: lexicographical ordering, `prefix`/`delimiter`/`CommonPrefixes`,
  `StartAfter`, pagination, key encoding, and the Express One Zone differences.
  `supported` by AWS docs, verified against the API reference.
- [`open-questions.md`](open-questions.md) — the study's open cross-tool
  questions and inherited leads (latency vs throttling, client language,
  crash-resume, S3-compatible differences). All `unverified`. **Read before
  interpreting any result** — several decide whether a number means what it
  appears to mean.
- [`smoke-bucket.md`](smoke-bucket.md) — the registry of buckets, regions,
  manifest snapshots, and measured keyspace shapes the study tests against; the
  binding source every receipt resolves its bucket and manifest from.
- [`../tools/README.md`](../tools/README.md) — the roster: every tool in scope,
  one directory each, and where each subject stands.

## Results

No measurement results yet — the benchmark phase has not started, and no
comparative performance result exists in this repository. (Smoke receipts carry
per-run wall-clock and RSS figures, but never as a comparison.)

Groundwork findings *do* exist for every subject and live on those tools' own
pages, with receipts under `tools/<tool>/receipts/`. See
[`../tools/README.md`](../tools/README.md) for the cohort split and per-tool
status. Cross-tool findings land in
[`open-questions.md`](open-questions.md).

## Operating & extending the study

For provisioning a runner, reproducing a run, or adding a tool — the machinery,
not the findings. See [`operating/`](operating/README.md).
