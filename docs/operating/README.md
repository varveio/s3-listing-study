# Operating and extending the study

The repository has two strict operational boundaries:

- Tool capsules under `tools/` own research, current claims, build facts,
  adapter declarations, and immutable groundwork receipts.
- [`../../benchmark/`](../../benchmark/) owns all comparative plans, toolbox
  construction and smoke checks, cloud canaries, Batch jobs, metric/output
  capture, verification, and reports.

Capsule contributors should read [`tool-structure.md`](tool-structure.md),
[`tool-onboarding.md`](tool-onboarding.md), and
[`capsule-authoring.md`](capsule-authoring.md). Operators should start at the
benchmark README, then work from
[`../../benchmark/docs/running.md`](../../benchmark/docs/running.md) — the
campaign runbook, whose procedure stays `VERIFIED: no` until a real campaign
exercises it. [`artifact-availability.md`](artifact-availability.md) records
which historical capsule artifacts remain retrievable; those old receipts are
evidence and are never rewritten to match the current harness.
