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

## Follow-up beyond wave 2

One owner-approved job outside the seven-job wave, group
`goq2-followup-blockchain-javaera-go` (`swath.98f424ce32cc.s1`), submitted 18:07 UTC and SUCCEEDED
by 18:12 UTC: blockchain Swath c256 at replay 64/20 with the same Go image,
on wave 1's Java-era fixture (`current-eba31d9/part-*.parquet`,
`f4a839f2…d4b0`), index off. Its receipt, metrics row, server line and cost
are in the same files. With it, observed compute for the eight jobs is $1.0172
create-to-terminal ($0.8446 by `runDuration`).

Public projection (2026-11-11): the receipt files were first committed as
raw exports carrying the study's estate. Every JSON and JSONL file here is now
a projection of that export, checked with the release exporter's
forbidden-pattern list (`benchmark.public_export.FORBIDDEN_PATTERNS`) plus
cloud project, network and secret resource paths, the results bucket and Batch
job UIDs.
Dropped wherever they occur: service accounts, secret resources, project,
network, subnetwork and zone, Batch job names and UIDs (including the worker's
job argument), result-store prefixes and URIs, and the executor
environment apart from boot disk, provisioning and product retention.
Substituted in every string: the results bucket becomes `<result-store>/` or
`<fixture-store>/`, the study's registry path `<study-registry>/` (digests
kept). Everything else is verbatim, including each `result.json` SHA-256.
Where s7cmd ran, the only remaining pattern hit is the s7cmd container's
public subject working directory (`subject_workdir` in
`tools/s7cmd/build/image.json`), which the home-directory pattern matches. Case ids are
carried from the ledger and are not recomputable from the projected case
documents. The raw exports remain in this branch's earlier commits until the
squash merge.
