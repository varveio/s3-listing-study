# Go replay qualification wave 3 receipt

Varve maintains this study and builds Swath. This is diagnostic instrument
qualification, not a calibrated benchmark or tool ranking.

One parallel wave of four Spot jobs ran on 2026-11-10 (submitted 21:06 UTC,
all terminal by 21:13 UTC) from the plans in
`benchmark/plans/campaigns/go-replay-qualification-3/`. Every job SUCCEEDED,
every subject exited zero with the fixture's expected row count, and every
result marker binds to its recorded request. No preemptions, retries,
cancellations or follow-up jobs.

- Go replay: `swath-replay@sha256:f8b70f0f1c337271007b6aa8b00bf87f68bdd8d361dbbb7a19401edb5f629f90`,
  Swath main `43ffab2f8c2c11041daba71273451bdf66190fc7` (window-cache fills from
  borrowed batches, PR #443; seekparquet `b1c848a`).
- Worker: wave 2's
  `benchmark-toolbox@sha256:a878e4296bb7c07816eb4437b78f1209c9698e5c3ce891caf53cffe1a7a40e85`,
  harness `1a0f845d7798b84771f85425a6f3dc8dcc6f5e33`. The subject is Swath Java
  0.3.2, never Swath-Go.
- Fixture: the blockchain SeekParquet conversion named in the plans
  (`seekparquet-c4a42eb4e7fb/`), the same as wave 2.

| Group | Attempt | Job UID | Created (UTC) | Terminal (UTC) |
| --- | --- | --- | --- | --- |
| `goq3-blockchain-swath-go` | `swath.e40733239507.s1` | `go-replay-qualify-7fea3191-ed5f-4f4000` | 21:06:49 | 21:11:52 |
| `goq3-blockchain-swath-go` | `swath.e40733239507.s2` | `go-replay-qualify-65bd0884-3ebf-4a7600` | 21:06:49 | 21:12:08 |
| `goq3-blockchain-swath-go-index-off` | `swath.dc15bf8aa437.s1` | `go-replay-qualify-e3ab1883-81c7-43b700` | 21:06:50 | 21:11:45 |
| `goq3-blockchain-ladder-go` | `swath.07cb7eabf980.s1` | `go-replay-qualify-6d2c5892-0dae-47c100` | 21:06:50 | 21:11:58 |

Files: one `goq3-*.json` receipt per group, exported with
`benchmark.receipt --server-log`, so each attempt's `list_index` block carries
its `swath_replay list_index` line exported from Cloud Logging
(`batch_task_logs`, provenance recorded in the block) and the meter cross-check.
`server-lines.txt` holds those four lines verbatim. `metrics.jsonl` adds timing
assessments and server headroom derived from the same bound markers, as in wave
2; CPU p90 is nearest rank over 10-second samples, server RSS is lifetime
VmHWM. `cost-observed.json` applies the wave-1 receipt's us-east1 N4 Spot rate
($1.80688/hour for n4-highcpu-80) to observed provider durations: $0.5076 by
`runDuration`, $0.6150 charging create-to-terminal. These are execution-based
estimates, not an invoice.

Interpretation, per-job tables and the comparison with wave 2 are in the wave
README's *Results* section.
