# Onboarding a new tool

How a new subject joins the study, from scope decision to a reviewed capsule,
and how an existing subject is re-derived when its upstream releases a new
version. This page owns only the sequence and the seams; each step's substance
lives in the document that owns it, linked in place. Nothing here duplicates
protocol text.

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
   lifecycle table are all owned by [`tool-structure.md`](tool-structure.md);
   the *procedure* for producing one — build order, agent topology, and the
   verification loop — is owned by
   [`capsule-authoring.md`](capsule-authoring.md).
   For a born-canonical tool: `research/` starts with `report.md` (and the
   independent-review record when one exists) only; `data/claims.json` records
   the study's own findings with the same status vocabulary and evidence
   shapes, with no conservation apparatus; receipts are committed as produced.
   The migration playbook does **not** apply — it converts legacy directories,
   which a new tool never has.
4. **Validate and review.** Run
   `uv run s3-listing-study validate-capsule --tool <tool>`. Pass no
   `--migration-base`: there is no legacy base to regress against, and the
   validator now rejects the flag on a capsule with no migration stratum.
   Independent review (a different-model reviewer plus the standard one) and
   the owner-reviewed `tools/` PR apply exactly as for every `tools/` change
   ([`../AGENTS.md`](../AGENTS.md) § Working conventions).

## The migration stratum is optional (machinery, 2026-08-02)

The schemas and validator were built during the migration wave and assumed a
migrated capsule, so a born-canonical capsule could not land. That is closed;
step 4 needs no exception:

- `schemas/claims.schema.json` — root `legacy_ledger` and per-claim
  `legacy_origins` are optional, but not independently so: a conditional binds
  them, so a document with a ledger carries `legacy_origins` on *every* claim
  and one without a ledger carries it on none. Present, they validate exactly
  as before.
- `src/s3_listing_study/capsule.py` — `research/tool-page.md` and
  `research/claims-migration.md` moved out of `REQUIRED_FILES` into
  `MIGRATION_FILES`, required only for a capsule with a migration stratum. The README checks follow the same condition: navigation
  names the two files, and Provenance names `Mixed provenance`,
  `research/reconciliation.md` and `research/tool-page.md`, only for a capsule
  that has an inherited layer to describe. `not a run record` stays required
  of every capsule — distinguishing what was read from what was executed is
  not a migration-era concern. `check_claim_schema_contract` carries fixtures
  for the born-canonical shape.

The rule is now **all or nothing**, which is stricter than what came before: a
capsule has a migration stratum exactly when the validator's `MIGRATED_TOOLS`
roster says so, and its ledger, its two research files and its claims' origins
must all agree with that. A ledger without the two files, the two files without
a ledger, claims carrying `legacy_origins` with no ledger, a ledger with any
claim missing them, and a ledger disagreeing with the roster in either
direction are each an error. So the gap cannot be worked around by fabricating
an empty ledger or a placeholder frozen page — that would turn the stratum from
evidence into ritual — and a migrated capsule cannot shed its evidence by
dropping the ledger, because identity is declared in the roster rather than
inferred from content the same commit could delete. Retiring a stratum stays
possible under the subject-retirement rule in
[`tool-structure.md`](tool-structure.md); it now has to remove the slug from
the roster too, which is the point — the deletion appears in the diff an owner
reviews instead of taking effect by inference. The migrated capsules still
validate unchanged.

## Re-deriving an existing subject at a new upstream version

A capsule describes one version, and upstreams release. When the subject moves
— first done for `swath`, whose v0.1.0-era capsule was retargeted to v0.2.0 —
the trigger is a **subject change, not doc staleness**. Editing the existing
pages in place silently inherits whatever the old pages got wrong, which is the
exact failure this study exists to correct, so the derivation is redone rather
than patched. Only the seams that differ from the sequence above:

1. **Freeze the subject before dispatching anyone.** Check the new tag out in
   a detached worktree and point every reader at that path. Upstream `main`
   moved twice mid-run on the swath re-derivation and shifted one reader's
   line numbers underneath it.
2. **Re-derive blind.** The researcher must not read the existing capsule
   pages first, for the same reason original groundwork is blind — the method
   in [`tool-research-brief.md`](tool-research-brief.md) still governs, and its
   file layout is still historical. The new derivation is its own record under
   `research/`, never an edit of the old one: a version-named subdirectory
   while both eras coexist, flat only once the superseded era has been
   deliberately retired. Either way `research/README.md` must say which
   subject each file describes — a research directory holding two versions of
   one tool is unreadable without that router (see
   [`../../tools/swath/research/README.md`](../../tools/swath/research/README.md)).
3. **Check the adapter against the new version's real CLI.** Swath's legacy
   command wrapper still emitted `--max-parallel-listings`, `--seed` and
   `--force-sort`, none of which exist at v0.2.0 — every mode it drove would
   have failed at exit 2, silently rotted by a version bump. Diffing the
   adapter's flags against the new `--help` is cheap and should be routine.
4. **Preserve any claim ID that conserves a legacy origin.** The conservation
   map is frozen; renumbering those claims stops it resolving. Behaviour that
   reversed is recorded as `contradicted` under its existing ID, not deleted.
5. **A version bump is not by itself a born-canonical event.** Re-deriving
   does not retire the migration stratum. Retiring it is a separate decision
   about the legacy layer itself, argued on its own grounds and recorded on
   the capsule's Provenance section — swath's was retired because the subject
   beneath it was never released and its records were seeded from design
   documentation rather than from runs, not because the version moved.

Validation and review are unchanged from step 4 above.

## What this page deliberately does not contain

Directory trees, README outlines, claim-field tables, receipt rules, review
checklists, or harness invocations — those all have owning documents linked
above, and repeating them here would create the drift this page exists to
avoid.
