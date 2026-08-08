#!/usr/bin/env python3
"""Compile s3kor listing parameters into exact in-image argv."""

from s3_listing_study.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
    validate_concurrency,
)

TOOL = "s3kor"
FIXED_COMMAND_PREFIX = ("/usr/local/bin/s3kor",)
MODES = frozenset({"list", "list-versions"})


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    uri = f"s3://{request.bucket}" + (f"/{request.prefix}" if request.prefix else "")
    if request.mode == "list":
        return "ls", "--region", request.region, uri
    if request.mode == "list-versions":
        return "ls", "--all-versions", "--region", request.region, uri
    raise CommandAdapterError(f"unknown mode: {request.mode}")


def build_command(request: CommandRequest) -> tuple[str, ...]:
    validate_concurrency(request, tool=TOOL)
    return *FIXED_COMMAND_PREFIX, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s3kor command adapter"))
