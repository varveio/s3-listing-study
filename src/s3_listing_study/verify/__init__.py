"""The verdict engine.

Replaces ``harness/verify-listing.sh`` (998 lines): PASS / FAIL / DRIFT /
ERROR verdicts over a tool's receipt against the reference manifest,
including the full-5-field drift comparison and the union re-list branches.
"""
