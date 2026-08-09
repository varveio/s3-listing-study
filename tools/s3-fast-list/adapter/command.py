#!/usr/bin/env python3
"""Compile s3-fast-list parameters into exact standalone argv."""

from s3_listing_study.common.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
    validate_concurrency,
)

TOOL = "s3-fast-list"
FIXED_COMMAND_PREFIX = ("/usr/bin/s3-fast-list",)
MODES = frozenset({"list"})


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    if request.mode != "list":
        raise CommandAdapterError(f"unknown mode: {request.mode}")
    argv = [
        "--no-sign-request",
        "--output-parquet-file",
        "/dev/stdout",
        "--output-ks-file",
        "/dev/null",
    ]
    if request.prefix:
        argv.extend(("--prefix", request.prefix))
    argv.extend(("list", "--region", request.region, "--bucket", request.bucket))
    return tuple(argv)


def build_command(request: CommandRequest) -> tuple[str, ...]:
    validate_concurrency(request, tool=TOOL)
    return *FIXED_COMMAND_PREFIX, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s3-fast-list command adapter"))
