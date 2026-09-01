# Results

One directory per release under [`results/`](results/). A release is immutable
and generated; the report beside it is written by hand and cites the attempt id
behind every claim.

## Current release

**[`results/2026-09-scale-diagnostics/`](results/2026-09-scale-diagnostics/)** —
*Scale-study replay diagnostics*, 284 settled attempts, data as of 2026-09-01.

> **Diagnostic release. Nothing in it is a measurement-grade comparison.** No
> attempt carries `purpose = measurement`; the replay instrument has a known
> defect that penalises one subject in one direction; the live-S3 rows are
> single uncontrolled observations of one tool.

- **[`REPORT.md`](results/2026-09-scale-diagnostics/REPORT.md)** — start here.
  What ran, where each subject stopped and why, the instrument and its defect,
  the per-tool dispositions, and what the study does not establish.
- [`summary.csv`](results/2026-09-scale-diagnostics/summary.csv) — every
  attempt, flat, for a spreadsheet.
- [`attempts.jsonl`](results/2026-09-scale-diagnostics/attempts.jsonl) — the
  canonical dataset, one object per attempt, with the verbatim command, the
  image digests and the evidence digests.
- [`manifest.json`](results/2026-09-scale-diagnostics/manifest.json) — the
  machine-readable claim ceiling, the counts and the disclosures.
- [`charts/`](results/2026-09-scale-diagnostics/charts/) — four figures, each
  with the exact rows it drew beside it as CSV.

Eleven tools were screened from a 4.08-million-object fixture upward. Ten
completed the first rung; four reached 143 million. Separately, one tool listed
a 1.07-billion-object public bucket on live S3 from a single 32-vCPU VM, with an
exact count — n=1, one tool, and bounded in the report.

## What a release is

[`results/README.md`](results/README.md) is the contract: immutable directories,
an allowlisted projection of a private ledger, generated derived data, a
machine-readable claim ceiling, and a correction policy that publishes a new
release rather than editing an old one.

Anyone with a clone can check a release against itself, with no private access:

```
uv run python -m benchmark.public_validate \
    --release-dir results/2026-09-scale-diagnostics
```

## What is not here

- No ranking of listing tools, and no overall ordering of them.
- No calibrated benchmark. The current release says so in
  `manifest.json.claim_ceiling`, and the report says why.
- No content verification: counts are checked against fixture object counts,
  not key by key against a reference manifest.
- No raw listing products or key material. Fixtures are published as digests
  and shape metrics only.

Per-tool pages, mechanism reports and groundwork receipts live under
[`tools/`](tools/README.md); the measurement plan is
[`docs/methodology.md`](docs/methodology.md); the harness and the release
pipeline are documented in [`benchmark/`](benchmark/README.md).
