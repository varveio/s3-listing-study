#!/usr/bin/env python3
"""Compile s3kor listing parameters into exact in-image argv."""

from benchmark.runtime.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
)

TOOL = "s3kor"
FIXED_COMMAND_PREFIX = ("/usr/local/bin/s3kor",)
MODES = frozenset({"list", "list-versions"})
SUPPORTS_UNSIGNED = False
"""No unsigned request path; it signs with the credential in the environment."""


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    uri = f"s3://{request.bucket}" + (f"/{request.prefix}" if request.prefix else "")
    if request.mode == "list":
        return "ls", "--region", request.region, uri
    if request.mode == "list-versions":
        return "ls", "--all-versions", "--region", request.region, uri
    raise CommandAdapterError(f"unknown mode: {request.mode}")


def build_command(request: CommandRequest) -> tuple[str, ...]:
    return *FIXED_COMMAND_PREFIX, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s3kor command adapter"))
