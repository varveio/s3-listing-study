#!/usr/bin/env python3
"""Compile s4cmd listing parameters into exact in-image argv."""

from s3_listing_study.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
    validate_concurrency,
)

TOOL = "s4cmd"
FIXED_COMMAND_PREFIX = ("s4cmd",)
MODES = frozenset({"recursive", "shallow", "show-directory", "du"})
CONCURRENCY_RANGE = (1, 8)
DEFAULT_CONCURRENCY = 4


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    threads = validate_concurrency(
        request,
        tool=TOOL,
        supported_range=CONCURRENCY_RANGE,
    )
    threads_arg = str(DEFAULT_CONCURRENCY if threads is None else threads)
    url = f"s3://{request.bucket}/{request.prefix}"
    commands = {
        "recursive": ("ls", "-r", "-c", threads_arg, url),
        "shallow": ("ls", "-c", threads_arg, url),
        "show-directory": ("ls", "-d", "-c", threads_arg, url),
        "du": ("du", "-r", "-c", threads_arg, url),
    }
    try:
        return commands[request.mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {request.mode}") from None


def build_command(request: CommandRequest) -> tuple[str, ...]:
    return *FIXED_COMMAND_PREFIX, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s4cmd command adapter"))
