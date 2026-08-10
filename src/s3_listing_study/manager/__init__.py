"""Orchestration-side modules that never ship inside a final per-tool image.

The manager submits and monitors work and owns verification, receipts, capsule
validation, and maintenance commands. It may import the deliberately shared
``s3_listing_study.common`` layer. The shipped ``worker`` and ``common`` layers
must never import this package. A host-side manager upload wrapper deliberately
reuses the worker-owned create-only uploader, but that reverse dependency does
not affect the image boundary; ``tests/test_payload_boundary.py`` enforces the
image-critical direction.
"""
