# Go replay qualification wave 4 receipt

Varve maintains this study and builds Swath. This is diagnostic instrument
qualification, not a calibrated benchmark or tool ranking.

Eleven Spot jobs ran on 2026-11-11 from the plans in
`benchmark/plans/campaigns/go-replay-qualification-4/`, in two stages: stage A
(the three same-image controls and the blockchain 16/5 pair) submitted at
00:18 UTC, stage B (the six remaining rungs) at 00:34 UTC after the stage gate
held (stage-A compute $0.3470 against the $0.60 gate). All terminal by
00:47 UTC. Every job SUCCEEDED, every subject exited zero with the fixture's
expected row count, and every result marker binds to its recorded request. No
preemptions, resubmissions, cancellations or follow-up jobs; neither the
20-minute stop nor the aggregate spend stop fired.

- Go replay: `swath-replay@sha256:f8b70f0f1c337271007b6aa8b00bf87f68bdd8d361dbbb7a19401edb5f629f90`,
  Swath main `43ffab2f8c2c11041daba71273451bdf66190fc7` (wave 3's image).
- Worker: wave 2's
  `benchmark-toolbox@sha256:a878e4296bb7c07816eb4437b78f1209c9698e5c3ce891caf53cffe1a7a40e85`,
  harness `1a0f845d7798b84771f85425a6f3dc8dcc6f5e33`. The subject is Swath Java
  0.3.2 for the Swath cells, never Swath-Go.
- Fixtures: the SeekParquet conversions named in the plans, the same as waves 2
  and 3.

| Stage | Group | Attempt | Job UID | Machine | Created (UTC) | Terminal (UTC) |
| --- | --- | --- | --- | --- | --- | --- |
| A | `goq4-nara-swath-go-20-20` | `swath.ca43fae5e517.s1` | `go-replay-qualify-22dede5b-be92-49f800` | n4-highcpu-32 | 00:18:05 | 00:20:27 |
| A | `goq4-nara-s7cmd-go-20-20` | `s7cmd.f93baa7f7142.s1` | `go-replay-qualify-622fe59d-9ebf-490500` | n4-highcpu-32 | 00:18:05 | 00:30:15 |
| A | `goq4-fourcast-s5cmd-go-8-8` | `s5cmd.55ee5e4c62b8.s1` | `go-replay-qualify-05b74fd1-1120-4ad800` | n4-highcpu-16 | 00:18:06 | 00:27:22 |
| A | `goq4-blockchain-swath-go-16-5` | `swath.b9c1ed5ae8a2.s1` | `go-replay-qualify-31269ec6-3c4b-4c5200` | n4-highcpu-32 | 00:18:06 | 00:23:06 |
| A | `goq4-blockchain-swath-go-16-5` | `swath.b9c1ed5ae8a2.s2` | `go-replay-qualify-c93987d2-5eb8-42de00` | n4-highcpu-32 | 00:18:07 | 00:22:45 |
| B | `goq4-blockchain-swath-go-8-4` | `swath.c1505ec66d66.s1` | `go-replay-qualify-6b9f8bd0-f45b-484c00` | n4-highcpu-32 | 00:34:01 | 00:39:05 |
| B | `goq4-nara-swath-go-16-5` | `swath.73401a67238f.s1` | `go-replay-qualify-ea2640aa-6dd6-40eb00` | n4-highcpu-32 | 00:34:01 | 00:37:03 |
| B | `goq4-nara-swath-go-8-4` | `swath.bc04b205a545.s1` | `go-replay-qualify-6d101364-59bc-4d0b00` | n4-highcpu-32 | 00:34:02 | 00:36:31 |
| B | `goq4-nara-s7cmd-go-16-5` | `s7cmd.3f553acb0aca.s1` | `go-replay-qualify-212d1c95-572a-4bf500` | n4-highcpu-32 | 00:34:02 | 00:46:24 |
| B | `goq4-nara-s7cmd-go-8-4` | `s7cmd.5c9e1f4d4685.s1` | `go-replay-qualify-055db792-0183-402300` | n4-highcpu-32 | 00:34:03 | 00:46:34 |
| B | `goq4-fourcast-s5cmd-go-8-4` | `s5cmd.37412f5e21c0.s1` | `go-replay-qualify-6796a69b-7cb1-4bd700` | n4-highcpu-16 | 00:34:03 | 00:43:33 |

Files: one `goq4-*.json` receipt per group, exported with
`benchmark.receipt --server-log`, so each attempt's `list_index` block carries
its `swath_replay list_index` line exported from Cloud Logging
(`batch_task_logs`, provenance recorded in the block) and the meter cross-check;
all eleven report `delivered: true` with no disagreement. `server-lines.txt`
holds those eleven lines verbatim. `metrics.jsonl` adds timing assessments and
server headroom derived from the same bound markers, as in waves 2 and 3; CPU
p90 is nearest rank over 10-second samples, server RSS is lifetime VmHWM.
`cost-observed.json` applies the wave-1 receipt's us-east1 N4 Spot rates
($0.722752/hour for n4-highcpu-32, $0.361376/hour for n4-highcpu-16) to
observed provider durations: $0.7243 by `runDuration`, $0.8313 charging
create-to-terminal, against the $0.87 pre-submit estimate and the $3 cap. These
are execution-based estimates, not an invoice.

Interpretation, per-job tables and the comparison with waves 2 and 3 are in the
wave README's *Results* section.
