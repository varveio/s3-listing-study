# Go replay qualification wave 2 receipt

Varve maintains this study and builds Swath. This is diagnostic instrument
qualification, not a calibrated benchmark or tool ranking.

One parallel wave of seven Spot jobs ran on 2026-11-10 (submitted 17:48 UTC,
all terminal by 18:03 UTC) from the plans in
`benchmark/plans/campaigns/go-replay-qualification-2/`. Every job SUCCEEDED,
every subject exited zero with the fixture's expected row count, and every
result marker binds to its recorded request. No preemptions, retries,
cancellations or follow-up jobs.

- Go replay: `swath-replay@sha256:3b3584460761de559c07e73b79c4aa7bffd77318108fdbf12ae36c41a98933b5`,
  Swath main `545a10cbdada373419e105202dc8cd9be57f4fa3` (list index, PR #425).
- Worker: `benchmark-toolbox@sha256:a878e4296bb7c07816eb4437b78f1209c9698e5c3ce891caf53cffe1a7a40e85`,
  harness `1a0f845d7798b84771f85425a6f3dc8dcc6f5e33`. The subject is Swath Java
  0.3.2 for the Swath cells, never Swath-Go.
- Fixtures: the SeekParquet conversions named in the plans; each plan records
  the wave-1 fixture digest it was converted from.

Files: one `goq2-*.json` receipt per group, exported with
`benchmark.receipt --server-log`, so each Go attempt's `list_index` block carries
its `swath_replay list_index` line exported from Cloud Logging
(`batch_task_logs`, provenance recorded in the block) and the meter cross-check.
`server-lines.txt` holds those seven lines verbatim. `metrics.jsonl` adds
timing assessments and server headroom derived from the same bound markers;
CPU p90 is nearest rank over 10-second samples, server RSS is lifetime VmHWM.
`cost-observed.json` applies the wave-1 receipt's us-east1 N4 Spot rates to
observed provider durations: $0.7263 by `runDuration`, $0.8757 charging
create-to-terminal. These are execution-based estimates, not an invoice.

Interpretation, per-job tables and the cross-wave comparison are in the wave
README's *Results* section.
