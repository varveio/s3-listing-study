#!/usr/bin/env python3
"""Compile s3p listing parameters into exact in-image argv."""

from s3_listing_study.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
    validate_concurrency,
)

TOOL = "s3p"
FIXED_COMMAND_PREFIX = ("s3p",)
MODES = frozenset({"ls", "ls-long", "ls-raw", "summarize"})


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    heads = {
        "ls": ("ls",),
        "ls-long": ("ls", "--long"),
        "ls-raw": ("ls", "--raw"),
        "summarize": ("summarize",),
    }
    try:
        head = heads[request.mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {request.mode}") from None
    common = ["--bucket", request.bucket, "--region", request.region, "--list-concurrency", "8"]
    if request.prefix:
        common.extend(("--prefix", request.prefix))
    return *head, *common


def build_command(request: CommandRequest) -> tuple[str, ...]:
    validate_concurrency(request, tool=TOOL)
    return *FIXED_COMMAND_PREFIX, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s3p command adapter"))
