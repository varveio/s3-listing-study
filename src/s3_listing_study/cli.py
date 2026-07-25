"""Command-line entry point: ``s3-listing-study``.

Will grow subcommands ``smoke | build-manifest | scan-tree`` as the
corresponding units land; ``verify`` is here.
"""

import argparse
import sys
from collections.abc import Sequence

from s3_listing_study import __version__
from s3_listing_study.verify import main as verify_main


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch to a subcommand.

    Returns the process exit code.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    # `verify` owns its whole argument surface: it reproduces the shell
    # verifier's argv exactly, including repeated --input/--receipt, so it is
    # handed the tail untouched rather than re-declared here.
    if args and args[0] == "verify":
        return verify_main(args[1:])

    parser = argparse.ArgumentParser(prog="s3-listing-study")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument("command", nargs="?", choices=["verify"], help="subcommand to run")
    parser.parse_args(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
