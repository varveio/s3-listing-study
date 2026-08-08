#!/usr/bin/env python3
"""Compile s5cmd listing parameters into exact in-image argv."""

from s3_listing_study.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
    validate_concurrency,
)

TOOL = "s5cmd"
FIXED_COMMAND_PREFIX = ("/s5cmd",)
MODES = frozenset(
    {"recursive", "delimiter", "rootkeys", "json", "listv1", "allversions", "fullpath"}
)


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    target = f"s3://{request.bucket}/{request.prefix}"
    recursive = target + "*"
    commands = {
        "recursive": ("--no-sign-request", "ls", "-e", "-s", recursive),
        "delimiter": ("--no-sign-request", "ls", "-e", "-s", target),
        "rootkeys": ("--no-sign-request", "ls", "-e", "-s", target),
        "json": ("--json", "--no-sign-request", "ls", recursive),
        "listv1": ("--no-sign-request", "--use-list-objects-v1", "ls", "-e", "-s", recursive),
        "allversions": ("--no-sign-request", "ls", "--all-versions", "-e", "-s", recursive),
        "fullpath": ("--no-sign-request", "ls", "--show-fullpath", recursive),
    }
    try:
        return commands[request.mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {request.mode}") from None


def build_command(request: CommandRequest) -> tuple[str, ...]:
    validate_concurrency(request, tool=TOOL)
    return *FIXED_COMMAND_PREFIX, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s5cmd command adapter"))
