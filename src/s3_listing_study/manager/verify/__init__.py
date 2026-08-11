"""The verdict engine.

PASS / FAIL / DRIFT / ERROR verdicts over a tool's receipt against the
reference manifest, including the full-5-field drift comparison and the union
re-list branches.

The set math is DuckDB (:mod:`s3_listing_study.manager.verify.compare`); the record
framing, the mtime shape gate and ``canon_mtime`` come from
:mod:`s3_listing_study.manager.contract` rather than being re-derived here.
"""

from .cli import main

__all__ = ["main"]
