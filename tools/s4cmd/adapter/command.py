#!/usr/bin/env python3
"""Compile s4cmd listing parameters into exact in-image argv."""

from benchmark.runtime.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
)

TOOL = "s4cmd"
FIXED_COMMAND_PREFIX = ("/usr/local/bin/s4cmd",)
MODES = frozenset({"recursive", "shallow", "show-directory", "du"})
SUPPORTS_UNSIGNED = False
"""No unsigned request path; it signs with the credential in the environment."""
CONFIG_KEYS = frozenset({"concurrency"})
CONCURRENCY_RANGE = (1, 8)
DEFAULT_CONCURRENCY = 4


def _concurrency(request: CommandRequest) -> int:
    """Validate this capsule's own knob; the harness forwards it without reading it."""
    value = request.config.get("concurrency", DEFAULT_CONCURRENCY)
    minimum, maximum = CONCURRENCY_RANGE
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandAdapterError(f"{TOOL} concurrency must be an integer; got: {value!r}")
    if not minimum <= value <= maximum:
        raise CommandAdapterError(
            f"{TOOL} concurrency must be an integer in {minimum}..{maximum}; got: {value}"
        )
    return value


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    threads_arg = str(_concurrency(request))
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
