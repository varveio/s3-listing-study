"""Strict logical-request CLI for the single attempt engine."""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from s3_listing_study.argparse_utils import UniqueStoreAction
from s3_listing_study.build_selection import BuildSelectionError
from s3_listing_study.command_adapter import CommandAdapterError, CommandRequest

from .driver import resolve_invocation
from .engine import AttemptError, AttemptOptions, run_attempt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="/opt/s3-listing-study/attempt.pyz",
        description="Resolve and run one image-owned logical listing request.",
        allow_abbrev=False,
    )
    parser.add_argument("--request-schema", action=UniqueStoreAction, choices=("1",), default="1")
    parser.add_argument(
        "--output", action=UniqueStoreAction, default=os.environ.get("S3_STUDY_ATTEMPT_OUT")
    )
    parser.add_argument("--timeout", action=UniqueStoreAction, default="300.0")
    parser.add_argument("--term-grace", action=UniqueStoreAction, default="5.0")
    parser.add_argument("--attempt-id", action=UniqueStoreAction, default="")
    parser.add_argument("--tool", action=UniqueStoreAction, required=True)
    parser.add_argument("--tool-version", action=UniqueStoreAction)
    parser.add_argument("--derived-image", action=UniqueStoreAction, required=True)
    parser.add_argument("--harness-revision", action=UniqueStoreAction)
    parser.add_argument("--operation", action=UniqueStoreAction, choices=("list",), required=True)
    parser.add_argument(
        "--auth", action=UniqueStoreAction, choices=("anonymous",), default="anonymous"
    )
    parser.add_argument("--mode", action=UniqueStoreAction, required=True)
    parser.add_argument("--bucket", action=UniqueStoreAction, required=True)
    parser.add_argument("--region", action=UniqueStoreAction, required=True)
    parser.add_argument("--prefix", action=UniqueStoreAction, default="")
    parser.add_argument("--scope", action=UniqueStoreAction)
    parser.add_argument("--concurrency", action=UniqueStoreAction)
    return parser


def _parse_numbers(timeout_raw: str, term_grace_raw: str) -> tuple[float, float]:
    try:
        timeout = float(timeout_raw)
        term_grace = float(term_grace_raw)
    except ValueError:
        raise CommandAdapterError("--timeout and --term-grace must be numbers") from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise CommandAdapterError("--timeout must be a finite number greater than zero")
    if not math.isfinite(term_grace) or term_grace < 0:
        raise CommandAdapterError("--term-grace must be a finite nonnegative number")
    return timeout, term_grace


def _parse_concurrency(raw: str | None) -> int | None:
    if raw is None:
        return None
    if re.fullmatch(r"[0-9]+", raw) is None:
        raise CommandAdapterError("--concurrency must be an ASCII integer")
    return int(raw)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output is None:
        print("attempt-runner: --output or S3_STUDY_ATTEMPT_OUT is required", file=sys.stderr)
        return 2
    try:
        timeout, term_grace = _parse_numbers(args.timeout, args.term_grace)
        concurrency = _parse_concurrency(args.concurrency)
        request = CommandRequest(
            mode=args.mode,
            bucket=args.bucket,
            region=args.region,
            prefix=args.prefix,
            tool=args.tool,
            operation=args.operation,
            auth=args.auth,
            concurrency=concurrency,
        )
        invocation = resolve_invocation(request)
        _result, runner_exit = run_attempt(
            AttemptOptions(
                output=Path(args.output),
                argv=invocation.argv,
                timeout_s=timeout,
                adapter_bundle_sha256=invocation.adapter_bundle_sha256,
                term_grace_s=term_grace,
                attempt_id=args.attempt_id,
                tool=args.tool,
                tool_version=args.tool_version,
                subject_image=invocation.subject_image_digest,
                derived_image=args.derived_image,
                harness_revision=args.harness_revision,
                operation=args.operation,
                auth=args.auth,
                mode=args.mode,
                bucket=args.bucket,
                region=args.region,
                prefix=args.prefix,
                scope=args.scope,
                concurrency=concurrency,
            )
        )
    except (AttemptError, BuildSelectionError, CommandAdapterError, OSError) as exc:
        print(f"attempt-runner: {exc}", file=sys.stderr)
        return 2
    return runner_exit
