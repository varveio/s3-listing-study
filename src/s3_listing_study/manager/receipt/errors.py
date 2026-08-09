"""The one failure mode this package has: the receipt must not be written.

Every refusal here is a *harness* error, never a tool result — a flagged scan, a
control byte in a field, a payload that changed under its own hash. The wrapper
exits 2 on all of them, because recording the harness's own error as a tool
result would misreport someone else's work.
"""

from __future__ import annotations


class ReceiptError(Exception):
    """Refuse to produce or stage a receipt."""
