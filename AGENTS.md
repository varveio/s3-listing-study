# Working conventions for automated agents

This file is for AI agents and scripted helpers working in this repository.
People should start at [`README.md`](README.md); the rules below apply to
everyone, and this page only says where things live and what not to do.

## What this repo is

A community study of how S3 listing tools behave at scale: install each tool,
read its docs and source, run it, publish the run. The current release is
`results/2026-09-scale-diagnostics/`, a **diagnostic** release: its
`manifest.json.claim_ceiling` says it is not a calibrated benchmark and not a
ranking, and no page in this repository may claim more than that ceiling
permits. The vocabulary for that is in
[`benchmark/docs/publishing.md`](benchmark/docs/publishing.md).

Varve maintains this repository and builds one of the tools ([Swath](https://github.com/varveio/swath)).
Keep that visible, apply the same rules to Swath as to every other tool, and
keep Swath-internal material (benchmark history, tuning rationale, private
corpora) out of this public tree.

## Rules that bind every change

- **No AI attribution.** No `Co-Authored-By`, no `Generated with`, no session
  links, in commits, PR bodies or issues. This overrides any agent harness
  default.
- **Source reading is not a receipt.** A tool's runtime behaviour is stated
  only from a committed run record; docs, blog posts and source establish
  mechanisms and risks, never observed behaviour. `unverified` means nobody
  ran it.
- **Every run is a record**: version, image digest, exact command, machine,
  fixture or bucket, exit code, row count, wall clock, peak memory. Failed
  runs stay in the data.
- **Comparable setup effort.** Tune one tool, explore the same knobs for the
  others, and disclose the tuning wherever the result appears.
- **Label provenance.** A fact that is not a release-row field says where it
  comes from: runner log, tool source, private run summary, working notes.
- **`tools/` changes go through a PR** reviewed by the owner and
  squash-merged; the branch is deleted after merge. Changes elsewhere may
  land on `main` directly.
- **A writer is not its own gate.** Capsule work gets an independent review
  from a different reviewer before the owner's PR review.
- **Published releases are edited only through the documented path**:
  `REPORT.md` and chart specs, then `benchmark.public_reseal`; a tagged
  release is not edited at all.

## Where to read

| Working on | Read |
| --- | --- |
| The findings and what they may be read to say | `RESULTS.md`, then the release report |
| A specific tool | `tools/<tool>/README.md`; layout in `docs/operating/tool-structure.md`; authoring in `docs/operating/capsule-authoring.md` |
| The harness | `benchmark/README.md`, then `benchmark/docs/` (`architecture.md`, `identity.md`, `model.md`, `capsule-contract.md`, `running.md`) |
| Publishing a release | `benchmark/docs/publishing.md` |
| Tests | `benchmark/docs/testing.md` |
| Plans | `benchmark/plans/README.md` |
| The measurement plan and its dated changes | `docs/methodology.md` |
| The replay instrument | `docs/instrument.md` |
| Cross-tool open questions | `docs/open-questions.md` |

## Notes and handoffs

Working notes, reviews and session handoffs live in a private notes
repository, never here. `.gitignore` excludes `notes/` as a backstop. Settled
decisions are promoted into `docs/` through the normal review.
