"""``python -m s3_listing_study.receipt`` — the wrapper's offline half."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
