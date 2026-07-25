"""``receipt.md`` generation from a completed run's ``run.meta``.

Ports the receipt markdown generator tail of ``harness/smoke-run.sh``.
Validated by golden regeneration from committed ``run.meta`` files — if
``run.meta`` turns out not to carry everything needed, this degrades to
fixture tests.

Lands in U4 (smoke-run split, offline halves).
"""
