"""Modules that run on the orchestrating side, never inside a subject image.

The manager submits and monitors work, collects and uploads its results, and
owns everything this repository does with an attempt once it exists —
verification, receipts, capsule validation, normalization and the maintenance
commands. Nothing here may be imported from ``s3_listing_study.worker`` or
``s3_listing_study.common``: ``tests/test_payload_boundary.py`` enforces that
direction, which is what keeps manager-side work from changing the images a
campaign pins.
"""
