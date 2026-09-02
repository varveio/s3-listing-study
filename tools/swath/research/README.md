# Research — how this capsule's understanding was derived

This directory is history, not current state. Current facts live in
[`../data/claims.json`](../data/claims.json), explained by
[`../docs/`](../docs/) and summarised in [`../README.md`](../README.md).

Two subjects are recorded here, and the router below says which files describe
which. The current subject is swath **v0.3.1** (`7b9a5e2`); the flat files
derive the previous subject, **v0.2.0** (`cef8ec2`), whose blind derivation
remains the foundation the v0.3.1 update re-tested. This capsule carries **no
migration stratum** — no frozen pre-restructure page, no conservation map, and
no `legacy_origins` on any claim — so nothing here is path-pinned by the claims
schema.

| File | Subject | What it is |
| --- | --- | --- |
| [`v0.3.1/report.md`](v0.3.1/report.md) | v0.3.1 | The delta re-derivation: method and its stated deviation, image identity, installed-help diff, adapter round-trip, and the per-claim verdict table. `data/claims.json` was re-anchored and revised from this. |
| [`v0.3.1/reader-A.md`](v0.3.1/reader-A.md) … [`reader-D.md`](v0.3.1/reader-D.md) | v0.3.1 | Per-area companions: what changed between the tags in the engine, the S3 store, the CLI and resume surface, and the output paths, with anchors at `7b9a5e2`. |
| [`v0.3.1/codex-review.md`](v0.3.1/codex-review.md) | v0.3.1 | The independent cross-model review of the update and the resolution of each finding. |
| [`report.md`](report.md) | v0.2.0 | The consolidated source-first report. The v0.2.0 `data/claims.json` was written from this. |
| [`reader-A-engine.md`](reader-A-engine.md) | v0.2.0 | Seeding, stealing, range scanning, concurrency, the v0.1.0→v0.2.0 engine delta, and the disjointness invariants. |
| [`reader-B-store.md`](reader-B-store.md) | v0.2.0 | Request shapes, retries, 503/AIMD handling, key fidelity through `encoding-type=url`, protocol-violation defences, request observability. |
| [`reader-C-cli.md`](reader-C-cli.md) | v0.2.0 | CLI surface, the mode-versus-tunable inventory, anonymous access, resume semantics, exit codes, documentation drift. |
| [`reader-D-output.md`](reader-D-output.md) | v0.2.0 | Output formats, the normalizer contract per format, the memory model, `--report` and `cost.api_calls`. |
| [`reader-E-build.md`](reader-E-build.md) | v0.2.0 | Build and packaging, the published image and its provenance, the architecture matrix, upstream health. |
| [`ERRATA.md`](ERRATA.md) | v0.2.0 | Defects found in the above **after** they were frozen. Read it beside `report.md`. |
| [`codex-review.md`](codex-review.md) | v0.2.0 | The independent cross-model review and the resolution of each finding. |

## How it was derived

**v0.2.0.** Five readers with disjoint file sets worked a frozen worktree at the
`v0.2.0` tag, with no access to any existing capsule prose — source-first by
discipline, because reading finished prose first anchors a researcher into
confirming rather than discovering. Their reports were consolidated into
`report.md`, which was then reviewed by a different model family; all eight of
that review's findings were re-verified against source and accepted.

**v0.3.1.** The owner asked for the minimal change that makes the capsule
describe 0.3.1, so the update was diff-driven rather than blind — a stated
deviation from [`tool-onboarding.md`](../../../docs/operating/tool-onboarding.md)
§ Re-deriving, recorded with its reasoning in `v0.3.1/report.md`. Four readers
over the same disjoint areas took every v0.2.0 claim to the frozen `v0.3.1`
tree and reported, per claim, whether it held, moved, changed or reversed,
with the lines they actually read; the integrator read the build and upstream
area, applied the verdicts to the ledger, and the anchor gate re-checked every
anchor at `7b9a5e2`. Behaviours new since v0.2.0 entered the ledger only from
the readers' change sweeps of their own areas.

The v0.2.0 reader files are kept rather than folded into `report.md` because
they carry the full evidence at a depth the consolidated report compresses, and
because `ERRATA.md` records that anchor accuracy varies between them; the
v0.3.1 readers found the stealing and seeding core they describe byte-identical
between the tags, and recorded which retry, gauge, scanner and command files
were not.

## Reading order

Auditing a current claim: start at [`../data/claims.json`](../data/claims.json),
follow its evidence, and come here only for how the wording was reached — the
v0.3.1 verdict table first, then the v0.2.0 report for the original reasoning.
Auditing the v0.2.0 derivation itself: read [`report.md`](report.md) with
[`ERRATA.md`](ERRATA.md) open beside it — the errata records source anchors
whose line numbers are wrong in the report and correct in the ledger.
