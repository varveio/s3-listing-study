#!/usr/bin/env python3
"""Compile MinIO Client listing parameters into exact in-image argv."""

from s3_listing_study.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
    validate_concurrency,
)

TOOL = "minio-mc"
FIXED_COMMAND_PREFIX = ("mc",)
MODES = frozenset(
    {"recursive", "recursive-json", "shallow", "shallow-json", "versions-json", "find", "find-json"}
)


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    prefix = request.prefix[1:] if request.prefix.startswith("/") else request.prefix
    target = f"s3/{request.bucket}/{prefix}"
    commands = {
        "recursive": ("ls", "--recursive", target),
        "recursive-json": ("--json", "ls", "--recursive", target),
        "shallow": ("ls", target),
        "shallow-json": ("--json", "ls", target),
        "versions-json": ("--json", "ls", "--versions", "--recursive", target),
        "find": ("find", target),
        "find-json": ("--json", "find", target),
    }
    try:
        return commands[request.mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {request.mode}") from None


def build_command(request: CommandRequest) -> tuple[str, ...]:
    validate_concurrency(request, tool=TOOL)
    return *FIXED_COMMAND_PREFIX, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="minio-mc command adapter"))
