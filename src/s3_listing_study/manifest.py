"""Build and read the reference manifest.

Will hold the Python replacement for the manifest half of the smoke/verify
pipeline: building a canonical reference manifest from a live listing and
reading a committed ``*.tsv.gz`` manifest back for comparison. See
``notes/2026-07-25-cleanup-plan.md`` §1 and §6 (A1 — live validation,
``build-manifest.py``) for the acceptance requirements (sha-verified against
the committed registry binding).

Lands in A1 (live validation) with an offline half landing earlier in the
U-track.
"""
