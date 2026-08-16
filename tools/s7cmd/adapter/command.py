#!/usr/bin/env python3
"""Compile s7cmd listing parameters into exact in-image argv."""

from benchmark.runtime.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
    validate_concurrency,
)

TOOL = "s7cmd"
FIXED_COMMAND_PREFIX = ("/usr/local/bin/s7cmd",)
MODES = frozenset(
    {
        "recursive-tsv",
        "recursive-tsv-nosort",
        "recursive-aligned",
        "recursive-json",
        "recursive-one",
        "all-versions",
        "max-depth",
        "shallow-tsv",
        "bucket-list",
    }
)


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    target = f"s3://{request.bucket}/{request.prefix}"
    obs = ("-vv", "--disable-color-tracing")
    parallel = ("--max-parallel-listings", "16")
    anonymous = ("--target-no-sign-request", "--target-region", request.region)
    commands = {
        "recursive-tsv": (
            "ls",
            "-r",
            *obs,
            "--tsv",
            "--show-storage-class",
            "--show-etag",
            *parallel,
            *anonymous,
            target,
        ),
        "recursive-tsv-nosort": (
            "ls",
            "-r",
            *obs,
            "--no-sort",
            "--tsv",
            "--show-storage-class",
            "--show-etag",
            *parallel,
            *anonymous,
            target,
        ),
        "recursive-aligned": ("ls", "-r", *obs, *parallel, *anonymous, target),
        "recursive-json": ("ls", "-r", *obs, "--json", *parallel, *anonymous, target),
        "recursive-one": ("ls", "-r", *obs, "-1", *parallel, *anonymous, target),
        "all-versions": (
            "ls",
            "-r",
            *obs,
            "--all-versions",
            "--tsv",
            "--show-storage-class",
            "--show-etag",
            *parallel,
            *anonymous,
            target,
        ),
        "max-depth": (
            "ls",
            "-r",
            *obs,
            "--max-depth",
            "1",
            "--tsv",
            "--show-storage-class",
            "--show-etag",
            *parallel,
            *anonymous,
            target,
        ),
        "shallow-tsv": (
            "ls",
            *obs,
            "--tsv",
            "--show-storage-class",
            "--show-etag",
            *anonymous,
            target,
        ),
        "bucket-list": ("ls", *obs, *anonymous),
    }
    try:
        return commands[request.mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {request.mode}") from None


def build_command(request: CommandRequest) -> tuple[str, ...]:
    validate_concurrency(request, tool=TOOL)
    return *FIXED_COMMAND_PREFIX, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s7cmd command adapter"))
