"""Build and read the reference manifest.

Will hold the Python replacement for the manifest half of the smoke/verify
pipeline: building a canonical reference manifest from a live listing and
reading a committed ``*.tsv.gz`` manifest back for comparison. Acceptance
requires the manifest to be sha-verified against the committed registry
binding, and the builder to be validated against a live listing.

Lands in A1 (live validation) with an offline half landing earlier in the
U-track.
"""
