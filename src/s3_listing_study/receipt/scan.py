"""Compatibility imports for the historical receipt secret scanner.

The implementation is shared with the attempt engine in
``s3_listing_study.secret_scan``. Existing receipt and audit callers retain
this module path.
"""

from s3_listing_study.secret_scan import (
    LINE_SIZE_LIMIT,
    SCAN_SECRET_RE,
    LineTooLongError,
    Outcome,
    TreeScanError,
    bounded_lines,
    scan_binary_file,
    scan_bytes,
    scan_file,
    scan_tree,
)

__all__ = [
    "LINE_SIZE_LIMIT",
    "SCAN_SECRET_RE",
    "LineTooLongError",
    "Outcome",
    "TreeScanError",
    "bounded_lines",
    "scan_binary_file",
    "scan_bytes",
    "scan_file",
    "scan_tree",
]
