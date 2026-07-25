"""Verdict report rendering: ``verify.md``.

Ports the report-writing tail of ``harness/verify-listing.sh``. Byte-identical
``verify.md`` output is the acceptance bar for the port (see
``notes/2026-07-25-cleanup-plan.md`` §4); any report-format improvement is a
separate change made only after equivalence is proven.

Lands in U3 (verifier port).
"""
