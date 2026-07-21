# Onboarding a new tool

How a new subject joins the study, from scope decision to a reviewed capsule.
This page owns only the sequence and the seams; each step's substance lives in
the document that owns it, linked in place. Nothing here duplicates protocol
text.

A new tool is **born canonical**: it is built directly in the function-grouped
capsule shape. It never passes through the legacy consolidated layout, and it
has no migration stratum — no frozen pre-restructure page, no conservation
map, no legacy origins.

## Sequence

1. **Scope (owner decision).** The owner decides the tool is in scope and its
   cohort, and adds its row to [`../tools/README.md`](../tools/README.md). Study
   scope and tiers are catalog concerns; nothing else starts before this.
2. **Groundwork research.** Follow the *method* in
   [`tool-research-brief.md`](tool-research-brief.md) — pinned subject, smoke
   runs under the shared harness, source-anchored report, critical
   cross-check. That document is frozen as the committed protocol record, so
   its *file layout* references are historical; every output lands in the
   capsule layout of step 3 instead. Runner provisioning and execution follow
   [`runner-security.md`](runner-security.md) unchanged.
3. **Build the capsule directly.** The target shape, every directory's
   purpose, the Markdown content contracts, the canonical-data rules, and the
   lifecycle table are all owned by [`tool-structure.md`](tool-structure.md).
   For a born-canonical tool: `research/` starts with `report.md` (and the
   independent-review record when one exists) only; `data/claims.json` records
   the study's own findings with the same status vocabulary and evidence
   shapes, with no conservation apparatus; receipts are committed as produced.
   The migration playbook does **not** apply — it converts legacy directories,
   which a new tool never has.
4. **Validate and review.** Run
   `python3 scripts/validate-tool-capsule.py --tool <tool> --base <ref>`,
   subject to the machinery gap below. Independent review (a different-model
   reviewer plus the standard one) and the owner-reviewed `tools/` PR apply
   exactly as for every `tools/` change ([`../AGENTS.md`](../AGENTS.md)
   § Working conventions).

## Known machinery gap — resolve at first use

The schemas and validator were built during the migration wave and currently
assume a migrated capsule. Before the first born-canonical tool lands, one
owner-approved machinery change is needed:

- `schemas/claims.schema.json` requires `legacy_ledger` (source, migration
  map, expected origins) at the root; a born-canonical ledger has none.
- `scripts/validate-tool-capsule.py` requires `research/tool-page.md` and
  `research/claims-migration.md` and runs conservation and frozen-page checks
  against the base ref; a born-canonical tool has neither file and no legacy
  base.

The intended change is small and additive: make the migration stratum
optional — present and fully checked when a legacy page exists, absent for
born-canonical capsules — without weakening any check that guards the eleven
migrated tools. Do not work around the gap by fabricating an empty legacy
ledger or a placeholder frozen page; that would turn the migration stratum
from evidence into ritual.

## What this page deliberately does not contain

Directory trees, README outlines, claim-field tables, receipt rules, review
checklists, or harness invocations — those all have owning documents linked
above, and repeating them here would create the drift this page exists to
avoid.
