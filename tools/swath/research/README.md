# Research — how this capsule's understanding was derived

This directory is history, not current state. Current facts live in
[`../data/claims.json`](../data/claims.json), explained by
[`../docs/`](../docs/) and summarised in [`../README.md`](../README.md).
Everything here describes one subject, swath **v0.3.1** (`7b9a5e2`).

| File | What it is |
| --- | --- |
| [`report.md`](report.md) | How the ledger was derived: method, image identity, the installed-help diff against 0.2.0, the adapter round-trip, and the per-claim verdict table. |
| [`reader-A.md`](reader-A.md) | Engine: work stealing, seeding, AIMD, retries, watchdog, engine toggles. |
| [`reader-B.md`](reader-B.md) | S3 store: request shape, streamed response parsing and key decoding, retries, region, User-Agent. |
| [`reader-C.md`](reader-C.md) | CLI, modes, exit codes, checkpoints and resume, documentation drift. |
| [`reader-D.md`](reader-D.md) | Output formats, timestamps, Parquet key typing, datasets and publication. |
| [`codex-review.md`](codex-review.md) | The independent cross-model review of the ledger and pages, and the resolution of each finding. |

## How it was derived

The ledger descends from a blind, source-first derivation of swath v0.2.0
(`cef8ec2`): five readers over disjoint file sets on a frozen worktree with no
access to any existing capsule prose, an integrator, and a cross-model review.
When upstream released 0.3.1, four readers over the same disjoint areas took
every claim to the frozen `7b9a5e2` tree and reported, per claim, whether it
held, moved, changed or reversed, with the lines they actually read; the
integrator read the build and upstream area, applied the verdicts, added the
behaviours the readers' change sweeps found, and the anchor gate re-checked
every anchor at `7b9a5e2`. That update was diff-driven rather than blind — a
deviation from [`tool-onboarding.md`](../../../docs/operating/tool-onboarding.md)
§ Re-deriving, made at the owner's request and argued in `report.md`. A second
cross-model review then read the result and its blockers were resolved.

## Retired: the v0.2.0 layer

The v0.2.0 derivation record — five reader reports, the consolidated report,
its errata, the first cross-model review, the two instrumented v0.2.0 run
observations, and three 2026-08-10 diagnostic attempt receipts on 0.2.2 and
0.2.4 — was retired on 2026-09-02 when the capsule became 0.3.1-only, by owner
decision and before the study was published. The criterion was that the
subject was superseded, not anything its evidence showed: every conclusion of
that layer that still holds at 0.3.1 is conserved in the ledger through the
per-claim re-test, and the seven ledger claims that stated v0.2.0 run figures
were dropped rather than carried. The files remain reachable at commit
`8733e22` of this repository.
