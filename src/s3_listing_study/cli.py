"""Command-line entry point: ``s3-listing-study``.

Will grow subcommands ``verify | smoke | build-manifest | scan-tree`` as the
corresponding units land (see ``notes/2026-07-25-cleanup-plan.md`` §5). For
now this only exposes ``--version`` so the package installs to a working
console script.
"""

import argparse
import sys
from collections.abc import Sequence

from s3_listing_study import __version__


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch to a subcommand.

    Returns the process exit code.
    """
    parser = argparse.ArgumentParser(prog="s3-listing-study")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
