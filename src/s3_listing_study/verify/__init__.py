"""The verdict engine.

Replaces ``harness/verify-listing.sh`` (998 lines): PASS / FAIL / DRIFT /
ERROR verdicts over a tool's receipt against the reference manifest,
including the full-5-field drift comparison and the union re-list branches.
See ``notes/2026-07-25-cleanup-plan.md`` §4 (what is preserved) and §5 (U3).
"""
