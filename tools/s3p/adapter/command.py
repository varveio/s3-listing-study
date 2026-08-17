#!/usr/bin/env python3
"""Compile s3p listing parameters into exact in-image argv."""

from benchmark.runtime.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
)

TOOL = "s3p"
FIXED_COMMAND_PREFIX = ("/usr/local/bin/s3p",)
MODES = frozenset({"ls", "ls-long", "ls-raw", "summarize"})
SUPPORTS_UNSIGNED = False
"""No unsigned request path; it signs with the credential in the environment."""


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
    return *FIXED_COMMAND_PREFIX, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s3p command adapter"))
