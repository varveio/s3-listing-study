"""Command-line entry point for verification, receipts, and repository checks."""

import argparse
import sys
from collections.abc import Sequence

from s3_listing_study import __version__
from s3_listing_study.links import main as links_main
from s3_listing_study.receipt.cli import main as receipt_main
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
    # `receipt` likewise: its `finish` subcommand carries one flag per run fact
    # the wrapper measured, and re-declaring that surface here would be a second
    # place for a field to go missing.
    if args and args[0] == "receipt":
        return receipt_main(args[1:])
    if args and args[0] == "check-links":
        return links_main(args[1:])
    if args and args[0] == "check-source-anchors":
        from s3_listing_study.source_anchors import main as source_anchors_main

        return source_anchors_main(args[1:])
    # `validate-capsule` is imported here rather than at module scope: it is the
    # one subcommand that needs `jsonschema`, and a missing install must not take
    # the other subcommands down with it.
    if args and args[0] == "validate-capsule":
        from s3_listing_study.capsule import main as capsule_main

        return capsule_main(args[1:])

    parser = argparse.ArgumentParser(prog="s3-listing-study")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=[
            "verify",
            "receipt",
            "validate-capsule",
            "check-links",
            "check-source-anchors",
        ],
        help="subcommand to run",
    )
    parser.parse_args(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
