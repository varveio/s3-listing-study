"""Command-line interface for the single attempt engine."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from .engine import AttemptError, AttemptOptions, run_attempt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m s3_listing_study.attempt")
    parser.add_argument("--output", default=os.environ.get("S3_STUDY_ATTEMPT_OUT"))
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--term-grace", type=float, default=5.0)
    parser.add_argument("--attempt-id", default="")
    parser.add_argument("--tool", default="unknown")
    parser.add_argument("--tool-version")
    parser.add_argument("--subject-image")
    parser.add_argument("--derived-image")
    parser.add_argument("--harness-revision")
    parser.add_argument("--auth", choices=("anonymous",), default="anonymous")
    parser.add_argument("--mode")
    parser.add_argument("--bucket")
    parser.add_argument("--region")
    parser.add_argument("--prefix")
    parser.add_argument("--scope")
    parser.add_argument(
        "--command-prefix",
        action="append",
        default=[],
        help="fixed argv element baked into a derived image; repeat for multiple elements",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output is None:
        print("attempt-runner: --output or S3_STUDY_ATTEMPT_OUT is required", file=sys.stderr)
        return 2
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    try:
        _result, runner_exit = run_attempt(
            AttemptOptions(
                output=Path(args.output),
                argv=(*args.command_prefix, *command),
                timeout_s=args.timeout,
                term_grace_s=args.term_grace,
                attempt_id=args.attempt_id,
                tool=args.tool,
                tool_version=args.tool_version,
                subject_image=args.subject_image,
                derived_image=args.derived_image,
                harness_revision=args.harness_revision,
                auth=args.auth,
                mode=args.mode,
                bucket=args.bucket,
                region=args.region,
                prefix=args.prefix,
                scope=args.scope,
            )
        )
    except (AttemptError, OSError) as exc:
        print(f"attempt-runner: {exc}", file=sys.stderr)
        return 2
    return runner_exit
