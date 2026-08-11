"""Command-line entry point for verification, receipts, and repository checks."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from s3_listing_study import __version__
from s3_listing_study.repo.links import main as links_main

Handler = Callable[[Sequence[str] | None], int]


def _verify_main(argv: Sequence[str] | None) -> int:
    from s3_listing_study.manager.verify import main

    return main(argv)


def _receipt_main(argv: Sequence[str] | None) -> int:
    from s3_listing_study.manager.receipt.cli import main

    return main(argv)


def _capsule_main(argv: Sequence[str] | None) -> int:
    from s3_listing_study.repo.capsule import main

    return main(argv)


def _source_anchors_main(argv: Sequence[str] | None) -> int:
    from s3_listing_study.repo.source_anchors import main

    return main(argv)


def _build_derived_image_main(argv: Sequence[str] | None) -> int:
    from s3_listing_study.common.build_selection import build_derived_image_main

    return build_derived_image_main(argv)


def _resolve_plan_main(argv: Sequence[str] | None) -> int:
    from s3_listing_study.manager.bench.cli import resolve_plan_main

    return resolve_plan_main(argv)


def _submit_campaign_main(argv: Sequence[str] | None) -> int:
    from s3_listing_study.manager.campaign.cli import submit_campaign_main

    return submit_campaign_main(argv)


def _watch_campaign_main(argv: Sequence[str] | None) -> int:
    from s3_listing_study.manager.campaign.watch import watch_campaign_main

    return watch_campaign_main(argv)


def _publish_derived_image_main(argv: Sequence[str] | None) -> int:
    from s3_listing_study.manager.campaign.images import publish_derived_image_main

    return publish_derived_image_main(argv)


def _build_shared_image_main(argv: Sequence[str] | None) -> int:
    from s3_listing_study.common.build_selection import build_shared_image_main

    return build_shared_image_main(argv)


def _upload_attempt_main(argv: Sequence[str] | None) -> int:
    from s3_listing_study.manager.upload import upload_attempt_main

    return upload_attempt_main(list(argv) if argv is not None else None)


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    help: str
    handler: Handler


COMMANDS = (
    Command("verify", "audit tool output against a recorded manifest", _verify_main),
    Command("receipt", "inspect retained historical receipt material", _receipt_main),
    Command("validate-capsule", "validate one tool capsule", _capsule_main),
    Command(
        "build-shared-image",
        "build the one common worker/runtime base",
        _build_shared_image_main,
    ),
    Command(
        "build-derived-image",
        "build one slug-selected shared derived image",
        _build_derived_image_main,
    ),
    Command(
        "resolve-plan",
        "expand a bucket's benchmark plan and show every case it generates",
        _resolve_plan_main,
    ),
    Command(
        "submit-campaign",
        "freeze and submit one resolved campaign to GCP Batch",
        _submit_campaign_main,
    ),
    Command(
        "watch-campaign",
        "watch GCP Batch and update one campaign's ledger states",
        _watch_campaign_main,
    ),
    Command(
        "publish-derived-image",
        "build, push, and record one registered derived image",
        _publish_derived_image_main,
    ),
    Command(
        "upload-attempt",
        "upload a finalized attempt directory to a GCS destination prefix",
        _upload_attempt_main,
    ),
    Command("check-links", "check repository-local Markdown links", links_main),
    Command("check-source-anchors", "validate source anchors", _source_anchors_main),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="s3-listing-study", allow_abbrev=False)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    for command in COMMANDS:
        child = subparsers.add_parser(
            command.name, help=command.help, add_help=False, allow_abbrev=False
        )
        child.set_defaults(handler=command.handler)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command tree and dispatch through its registered handler."""
    parser = build_parser()
    args, arguments = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    handler: Handler | None = getattr(args, "handler", None)
    if handler is None:
        if arguments:
            parser.error(f"unrecognized arguments: {' '.join(arguments)}")
        parser.print_help()
        return 0
    return handler(arguments)


if __name__ == "__main__":
    sys.exit(main())
