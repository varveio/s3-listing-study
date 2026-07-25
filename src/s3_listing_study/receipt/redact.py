"""Redaction: strip known-sensitive fields before a payload is scanned.

Ports the redaction half of ``harness/scan-lib.sh``. The order is fixed and
load-bearing: redact, then scan, then truncate, then hash — applied to the
full stream, quarantining on a scanner flag rather than dropping.

Lands in U4 (smoke-run split, offline halves).
"""
