"""Tier 3's entry point into the ported verifier: the seam, without a flag.

A ``--skip-preflight`` argument on the shipped CLI would let anyone run the
reference re-list against a real bucket with the mandatory runner-readiness gate
(``docs/operating/runner-security.md``) turned off, from the installed console
script, with full production argv. So the seam must be unreachable from a
production invocation.

So the boundary is a keyword argument to :func:`s3_listing_study.manager.verify.cli.main`
instead, and this module — which is not installed, not on
``[project.scripts]``, and reachable only as ``python -m
tests.tier3.verifier_entry`` from a source checkout — is the only caller that
passes :class:`~s3_listing_study.manager.verify.security.PreflightSkipped`. Everything
else is the shipped path: the same argv parse, the same dispatch, the same
docker argv construction, the same exit codes.
"""

from __future__ import annotations

import sys

from s3_listing_study.manager.verify.cli import main
from s3_listing_study.manager.verify.security import PreflightSkipped

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], PreflightSkipped()))
