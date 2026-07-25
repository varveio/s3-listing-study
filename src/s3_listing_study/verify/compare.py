"""Single-receipt comparison: a tool's listing against the reference manifest.

Ports the core diff logic of ``harness/verify-listing.sh`` — full-5-field
drift comparison, re-list-before-FAIL so bucket drift is never charged to a
tool, and refusing a verdict on a truncated verified payload. See
``notes/2026-07-25-cleanup-plan.md`` §4.

Lands in U3 (verifier port).
"""
