"""Repository-study validation commands; measurement operations live in ``benchmark``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from s3_listing_study import __version__
from s3_listing_study.repo.capsule import main as capsule_main
from s3_listing_study.repo.links import main as links_main
from s3_listing_study.repo.source_anchors import main as source_anchors_main

Handler = Callable[[Sequence[str] | None], int]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="s3-listing-study", allow_abbrev=False)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    for name, help_text, handler in (
        ("validate-capsule", "validate one tool study capsule", capsule_main),
        ("check-links", "check repository-local Markdown links", links_main),
        ("check-source-anchors", "validate public-source anchors", source_anchors_main),
    ):
        child = commands.add_parser(name, help=help_text, add_help=False, allow_abbrev=False)
        child.set_defaults(handler=handler)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args, rest = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    handler: Handler | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(rest)


if __name__ == "__main__":
    raise SystemExit(main())
