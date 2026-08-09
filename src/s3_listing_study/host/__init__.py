"""Modules that run on a host and never inside a subject image.

Capsule validation, verification, receipts, normalization, collection, upload
and the repository's own maintenance commands. Nothing here may be imported
from ``s3_listing_study.attempt`` or ``s3_listing_study.common``:
``tests/test_payload_boundary.py`` enforces that direction, which is what keeps
orchestrator work from changing the images a campaign pins.
"""
