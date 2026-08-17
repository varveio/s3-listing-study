#!/usr/bin/env python3
"""Compile pS3 listing parameters into exact in-image argv."""

from benchmark.runtime.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
)

TOOL = "ps3"
FIXED_COMMAND_PREFIX = ("/usr/local/bin/pS3",)
MODES = frozenset({"list", "list-versions", "head"})
SUPPORTS_UNSIGNED = False
"""No unsigned request path; it signs with the credential in the environment."""


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    if request.prefix:
        raise CommandAdapterError(
            f"pS3 has no --prefix flag; mode {request.mode!r} cannot address "
            f"prefix {request.prefix!r}"
        )
    operations = {
        "list": "list-objects-v2",
        "list-versions": "list-object-versions",
        "head": "head-objects",
    }
    try:
        operation = operations[request.mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {request.mode}") from None
    return operation, "--bucket", request.bucket, "--region", request.region


def build_command(request: CommandRequest) -> tuple[str, ...]:
    return *FIXED_COMMAND_PREFIX, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="ps3 command adapter"))
