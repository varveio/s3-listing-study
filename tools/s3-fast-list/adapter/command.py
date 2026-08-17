#!/usr/bin/env python3
"""Compile s3-fast-list parameters into exact standalone argv."""

from benchmark.runtime.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
)

TOOL = "s3-fast-list"
FIXED_COMMAND_PREFIX = ("/usr/bin/s3-fast-list",)
MODES = frozenset({"list"})
SUPPORTS_UNSIGNED = True
"""--no-sign-request lists anonymously. The signed path drops the flag and has
not been exercised by a committed run."""


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    if request.mode != "list":
        raise CommandAdapterError(f"unknown mode: {request.mode}")
    argv = [
        *((), ("--no-sign-request",))[not request.signed],
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
    return *FIXED_COMMAND_PREFIX, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s3-fast-list command adapter"))
