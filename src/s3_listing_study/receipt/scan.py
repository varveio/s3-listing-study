"""Secret scanning: classify a redacted stream as clean, flagged, or broken.

Ports the scan half of ``harness/scan-lib.sh`` and ``harness/scan-tree.sh``.
Preserves the three-way outcome that must never be conflated: clean /
flagged / scanner-broke (see ``notes/2026-07-25-cleanup-plan.md`` §4).
Validated against ``harness/tests/scan-fixtures/``.

Lands in U4 (smoke-run split, offline halves).
"""
