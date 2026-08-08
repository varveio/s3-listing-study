"""Receipt handling: metadata, rendering, redaction, and scanning.

The offline half of a smoke run: the redact/scan classifier and the receipt
markdown generator. Docker lifecycle, argv, timeout, and the cleanup trap stay
in ``harness/run-attempt.sh``.
"""
