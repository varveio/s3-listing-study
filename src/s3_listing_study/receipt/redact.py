"""Redaction: strip known-sensitive fields before a payload is scanned.

Ports the redaction half of ``harness/scan-lib.sh``. Order matters and is
fixed by ``notes/2026-07-25-cleanup-plan.md`` §4: redact, then scan, then
truncate, then hash — applied to the full stream, quarantining on a scanner
flag rather than dropping.

Lands in U4 (smoke-run split, offline halves).
"""
