# s3-listing-study — agent guide

The entry point. Read this before touching the repo.

Generic orchestration doctrine and reasoning-tier conventions come from the
runner's own shared tooling, installed at the user level — not vendored here.
This file carries only this repo's working conventions.

## What this repo is

An open, hands-on look at object-store listing tools: install each one, read its
docs, read its source where the docs don't settle a question, run it against
real buckets in the modes it actually offers, and try it under limits and
interruption.
See [`README.md`](README.md).

**Status: groundwork complete (wave 2); no benchmark run.** Every subject has
been through the full groundwork pipeline (pinned builds, smoke runs,
source-anchored reports, reconciliation, and critical cross-checks) and carries
committed receipts. Smoke is not measurement — no benchmark or comparative
performance result exists in this repo (receipts carry a wall-clock/RSS figure,
but only as a fact about that one run). Two cohorts: `aws-cli`, `s5cmd`,
`s7cmd`, `rclone`, `minio-mc`, `s3-fast-list`, and Swath ran anonymously at
smoke (not all correctness-verified); `s3p`, `s3kor`, `s4cmd`, and `ps3` were
blocked without credentials. Per-tool status: `tools/README.md`.

## Project principles

- **Varve maintains this repo and builds one of the tools included here**
  ([Swath](https://github.com/varveio/swath)). We know Swath better than we
  know the others, and earlier research into existing tools helped inform
  Swath's design. Keep that relationship visible, apply the same run-record
  requirements to Swath, and welcome help from people who know the other tools.
- **The measurement plan was written down before comparative runs.** Keep the
  original plan and make later material changes dated and clear in the diff, so
  readers can follow how the plan evolved.
- **`VERIFIED: no` means nobody ran it** (`unverified` in the canonical
  per-tool ledgers — the vocabularies map one-to-one, see `tools/README.md`
  § status labels). Not "we're fairly confident." Promotion to
  `CONFIRMED` / `CORRECTED` / `UNVERIFIABLE` requires a committed receipt in this
  repo. A reputable source is not a receipt; AWS's own docs are not a receipt; source
  reading is not a receipt.
- **Swath's internal benchmark history is not used in this project.** It was
  produced by us, on our corpus, with our tuning. A result must be run again on
  this harness before it is cited here.
- **Third-party published numbers are context, never comparison.** Every published
  head-to-head is our-box-vs-our-box.

## Routing — task to the docs you read

Read *one* index, not five guesses. Order: this table → `docs/README.md`.

| If you're... | Read |
| --- | --- |
| Designing or changing how a run works | `docs/methodology.md` — especially the five decisions |
| Provisioning a runner or executing a subject/reference container | `docs/operating/runner-security.md` — the mandatory execution boundary and activation gate |
| About to state anything about a specific tool | `tools/<tool>/README.md` — the tool page, for current observations + provenance |
| Changing a tool directory's structure or deciding which file owns content | `docs/operating/tool-structure.md` — the authoritative capsule and Markdown-role contract |
| Looking for which tools are in scope, or a tool's tier | `tools/README.md` |
| Working on the documented S3 API contract (ordering, delimiter, pagination, encoding) | `docs/s3-reference.md` |
| Working on an open cross-tool question (language bottleneck, resume, throttling) | `docs/open-questions.md` |
| Promoting a claim out of `VERIFIED: no` | `docs/methodology.md` § Run records — then commit the receipt into `tools/<tool>/` |
| Looking for settled reference | `docs/README.md` |

Each tool owns a directory under `tools/`; runnable-tool directory roles are
defined in [`docs/operating/tool-structure.md`](docs/operating/tool-structure.md). Nothing about a
tool lives outside its directory except claims that genuinely span several,
which go in `docs/open-questions.md`.

## Working conventions

### Commits

- **No AI attribution.** No `Co-Authored-By: Claude`, no `Generated with Claude
  Code`, no tool footers of any kind. Commits are authored by whoever ran the work.
  This overrides any default instruction from the agent harness. Same for PR bodies
  and issue text.
- Imperative mood; explain *why*, not what the diff already shows.
- **`tools/` changes go through a PR** (owner decision 2026-07-17), reviewed by
  the owner and **squash-merged**. Branches are **deleted after merge** (owner
  decision 2026-07-17, revising the same-day branch-preservation rule): the
  squash keeps `main` linear, and the stage-by-stage history stays reachable
  through the PR itself — GitHub retains each PR's commit list and head ref
  after the branch is gone. Orchestrator-side changes outside `tools/`
  (harness, docs, notes) may land directly on main.

### Run records

- Never report another tool's runtime behavior from memory, from a blog post, or
  from source reading alone. Run it.
- Surprising or consequential observations about another tool need the exact
  invocation, tool version, box spec, bucket identity, exit code, and raw output.
  The rclone exit-0-on-OOM note is the live example: it describes a serious
  outcome and currently traces to a single GitHub issue, so it remains an open
  question until we reproduce it.
- **Comparable setup effort.** If you tune one tool, explore comparable knobs for
  the others and say what you tuned.
- Publish Swath results on the same terms whether or not they favor it.

### Keep Swath-internal material out

Swath is Varve's own tool, and its internal material stays out of this public
repo: internal IP/patent or commercial analysis, the raw survey corpus, internal
perf-run artifacts, and tuned-default rationale. Facts about *any* tool — Swath
included — belong here only from public sources (docs, public repos, blog posts,
issue trackers) with citation, or from a committed run record. When in doubt,
leave it out and ask.

## Provenance discipline

The tool pages under `tools/` were seeded from private notes in the `swath` repo,
which is now out of reach — **this repo is the only surviving copy of that
material.** Those notes are a **starting point, not a run record**: compiled from
blog posts, GitHub issues, and source reading, and essentially never executed.
Treat every inherited claim as unverified regardless of how confident the original
phrasing sounds. That gap is the entire reason this repo exists.

Two consequences:

- **Don't go looking for the Swath notes to resolve an ambiguity.** They aren't
  available. If a tool page is unclear, the answer comes from running the tool or
  reading its public source — not from the upstream that seeded it.
- **Label mixed provenance.** If a fact on a tool page came from somewhere
  other than the inherited notes — the tool's own repo, its docs, a fresh run —
  say so on that page. A page whose Provenance section claims uniform secondhand
  lineage while carrying firsthand facts misstates its own reliability in both
  directions. Two tool pages (`s4cmd`, `minio-mc`) already carry a
  "Mixed provenance" callout for exactly this reason.

## Knowledge system

Three tiers:

| Tier | Path | Trust |
| --- | --- | --- |
| **Docs** | `docs/` | Authoritative — act on it without re-checking |
| **Notes** | Internal working notes (not part of this repository) | Current but informal — may evolve |
| **Archive** | Internal archive (not part of this repository) | Superseded; background only |

Write a **note** while thinking; promote to a **doc** only once the decision is
settled, describes current reality rather than the deliberation, and should be
trusted without cross-checking. **When in doubt, leave it in notes.**

Internal handoff notes hold dated, forward-looking checkpoints for a fresh
session, not retrospective reports. Their first line starts with `STATUS: `
followed by exactly one of `continue`, `blocked`, or `done`; **to resume, read
the newest handoff.** Use the runner's shared `checkpoint` skill and doctrine at a
milestone, after roughly 45–60 minutes, under context pressure, or before a
costly/external gate; write the handoff, then stop. Durable design thinking,
reviews, audits, execution journals, and decision records stay in ordinary
indexed notes. Handoffs are not indexed individually.

**Handoff notes are git-ignored** — session exhaust can carry absolute paths and
local context that should not land in a public repo. We use `.gitignore` rather
than a per-clone `.git/info/exclude`, because an exclude doesn't survive a fresh
clone: the first session from a new machine would otherwise commit the exhaust.

## Tier posture

Tier → model binding and the full orchestrator doctrine come from the runner's own
shared tooling, installed at the user level.

- **High-care paths — always `deep`:** `docs/methodology.md`; any promotion of a claim out of
  `VERIFIED: no`; the shared run harness (timing, RSS, exit-code, cgroup capture) and
  the output verifier (completeness + duplicate detection). A flawed methodology
  undermines every number downstream, and an incorrect run record can
  misrepresent someone else's software. Both are expensive to unwind and worth
  getting right up front.
- **Default judgment tier:** `standard`. Much of this repo's work is genuinely
  mechanical — installing tools, sweeping concurrency, collecting output — and
  doesn't need the top tier.
- Mechanical work (scouting, verbose I/O, `apply-edits` execution, extraction) runs
  at standard/light regardless.
