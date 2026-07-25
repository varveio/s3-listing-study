"""``receipt.md`` generation from a completed run's ``run.meta``.

Ports the receipt markdown generator tail of ``harness/smoke-run.sh``.
Validated by golden regeneration from committed ``run.meta`` files (see
``notes/2026-07-25-cleanup-plan.md`` §5, U4) — if ``run.meta`` turns out not
to carry everything needed, this degrades to fixture tests.

Lands in U4 (smoke-run split, offline halves).
"""
