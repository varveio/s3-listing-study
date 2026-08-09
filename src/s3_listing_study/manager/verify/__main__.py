"""``python -m s3_listing_study.manager.verify`` — the verifier as a process."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
