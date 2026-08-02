# Research — how this capsule's understanding was derived

This directory is history, not current state. Current facts live in
[`../data/claims.json`](../data/claims.json), explained by
[`../docs/`](../docs/) and summarised in [`../README.md`](../README.md).

Everything here derives **one** subject: swath **v0.2.0** (`cef8ec2`). This
capsule carries **no migration stratum** — no frozen pre-restructure page, no
conservation map, and no `legacy_origins` on any claim — so nothing here is
path-pinned by the claims schema.

| File | What it is |
| --- | --- |
| [`report.md`](report.md) | The consolidated source-first report. `data/claims.json` was written from this. |
| [`reader-A-engine.md`](reader-A-engine.md) | Seeding, stealing, range scanning, concurrency, the v0.1.0→v0.2.0 engine delta, and the disjointness invariants. |
| [`reader-B-store.md`](reader-B-store.md) | Request shapes, retries, 503/AIMD handling, key fidelity through `encoding-type=url`, protocol-violation defences, request observability. |
| [`reader-C-cli.md`](reader-C-cli.md) | CLI surface, the mode-versus-tunable inventory, anonymous access, resume semantics, exit codes, documentation drift. |
| [`reader-D-output.md`](reader-D-output.md) | Output formats, the normalizer contract per format, the memory model, `--report` and `cost.api_calls`. |
| [`reader-E-build.md`](reader-E-build.md) | Build and packaging, the published image and its provenance, the architecture matrix, upstream health. |
| [`ERRATA.md`](ERRATA.md) | Defects found in the above **after** they were frozen. Read it beside `report.md`. |
| [`codex-review.md`](codex-review.md) | The independent cross-model review and the resolution of each finding. |

## How it was derived

Five readers with disjoint file sets worked a frozen worktree at the `v0.2.0`
tag, with no access to any existing capsule prose — source-first by discipline,
because reading finished prose first anchors a researcher into confirming rather
than discovering. Their reports were consolidated into `report.md`, which
was then reviewed by a different model family; all eight of that review's
findings were re-verified against source and accepted.

The reader files are kept rather than folded into `report.md` because they carry
the full evidence at a depth the consolidated report compresses, and because
`ERRATA.md` records that anchor accuracy varies between them.

## Reading order

Auditing a current claim: start at [`../data/claims.json`](../data/claims.json),
follow its evidence, and come here only for how the wording was reached. Auditing
the derivation itself: read [`report.md`](report.md) with [`ERRATA.md`](ERRATA.md)
open beside it — the errata records source anchors whose line numbers are wrong
in the report and correct in the ledger.
