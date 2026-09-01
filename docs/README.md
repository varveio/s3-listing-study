# Documentation

Start here. Per-tool content lives in each tool's own directory — see
[`../tools/README.md`](../tools/README.md) for the roster. Working thinking is
kept in internal notes that are not published in this repository.

## Understanding the study

For anyone evaluating what this study is, how credible it is, and what it found.

- [`methodology.md`](methodology.md) — how the study is run: the five decisions
  that shape everything, the replay-screening → real-S3-validation funnel, and
  the run-record requirements. The measurement plan, written down before
  comparative runs and carrying dated material changes; its status header says
  which sections are the preregistration and which describe what has since run.
- [`../results/2026-09-scale-diagnostics/REPORT.md`](../results/2026-09-scale-diagnostics/REPORT.md)
  — the current release's report: the funnel, the instrument and its defect,
  the per-tool dispositions, and the limits on every claim.
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

Published results live outside `docs/`, in the release directories under
[`../results/`](../results/README.md). [`../RESULTS.md`](../RESULTS.md) is the
index.

The current release is `2026-09-scale-diagnostics`, and its written companion —
what ran, where each subject stopped and why, the instrument and its known
defect, and what the study does not establish — is
[`../results/2026-09-scale-diagnostics/REPORT.md`](../results/2026-09-scale-diagnostics/REPORT.md).
It is a **diagnostic** release: no attempt in it carries
`purpose = measurement`, so nothing in it is a calibrated benchmark or a
ranking. The machine-readable ceiling is in that release's `manifest.json`.

Groundwork findings also exist for every subject and live on those tools' own
pages, with receipts under `tools/<tool>/receipts/`. Smoke receipts carry
per-run wall-clock and RSS figures, but never as a comparison. See
[`../tools/README.md`](../tools/README.md) for the cohort split, the per-tool
status and the current release outcome. Cross-tool questions land in
[`open-questions.md`](open-questions.md).

## Operating & extending the study

For provisioning a runner, reproducing a run, or adding a tool — the machinery,
not the findings. See [`operating/`](operating/README.md).

Benchmark operators should start with [`../benchmark/README.md`](../benchmark/README.md).
