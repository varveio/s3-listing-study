"""Receipt handling: metadata, rendering, redaction, and scanning.

Replaces the offline halves of ``harness/smoke-run.sh``: the redact/scan
classifier and the receipt markdown generator. Docker lifecycle, argv,
timeout, and the cleanup trap stay bash.
"""
