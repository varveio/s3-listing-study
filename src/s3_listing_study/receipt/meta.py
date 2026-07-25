"""``run.meta`` reading and writing.

Ports the ``meta_field`` emission path and readers of ``harness/smoke-run.sh``
(the line-oriented, control-byte-rejecting ``run.meta`` format that the
verifier treats as the single source of truth it refuses to second-guess).
See ``notes/2026-07-25-cleanup-plan.md`` §4.

Lands in U4 (smoke-run split, offline halves).
"""
