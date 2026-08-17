#!/usr/bin/env python3
"""Compile rclone listing parameters into exact in-image argv."""

from benchmark.runtime.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
)

TOOL = "rclone"
FIXED_COMMAND_PREFIX = ("/usr/local/bin/rclone",)
MODES = frozenset(
    {
        "recursive-fastlist",
        "recursive-hierarchical",
        "recursive-walk",
        "delimiter-shallow",
        "listv1",
        "lsf",
        "debug",
        "walk-debug",
    }
)
SUPPORTS_UNSIGNED = True
"""The s3 backend falls back to anonymous credentials when none are configured;
env_auth=true is what makes it read the credential the engine populated."""


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    backend = f"s3,provider=AWS,region={request.region}"
    if request.mode == "listv1":
        backend += ",list_version=1"
    if request.signed:
        # rclone's s3 backend falls back to anonymous credentials when none
        # are configured (backend/s3/s3.go:1508-1511); env_auth=true is what
        # tells it to actually read AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY
        # from the process environment the engine already populated.
        backend += ",env_auth=true"
    remote = f":{backend}:{request.bucket}"
    if request.prefix:
        remote += f"/{request.prefix}"
    standard = ("--files-only", "--use-server-modtime", "--no-mimetype")
    commands = {
        "recursive-fastlist": ("lsjson", "--fast-list", *standard, "-R", remote),
        "recursive-hierarchical": ("lsjson", *standard, "--checkers", "4", "-R", remote),
        "recursive-walk": (
            "lsjson",
            *standard,
            "--disable",
            "ListR",
            "--checkers",
            "4",
            "-R",
            remote,
        ),
        "delimiter-shallow": ("lsjson", "--use-server-modtime", "--no-mimetype", remote),
        "listv1": ("lsjson", "--fast-list", *standard, "-R", remote),
        "lsf": (
            "lsf",
            "--fast-list",
            "--files-only",
            "--format",
            "ps",
            "--separator",
            ";",
            "-R",
            remote,
        ),
        "debug": ("lsjson", "--fast-list", *standard, "-R", "-vv", "--dump", "headers", remote),
        "walk-debug": (
            "lsjson",
            *standard,
            "--disable",
            "ListR",
            "--checkers",
            "4",
            "-R",
            "-vv",
            "--dump",
            "headers",
            remote,
        ),
    }
    try:
        return commands[request.mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {request.mode}") from None


def build_command(request: CommandRequest) -> tuple[str, ...]:
    return *FIXED_COMMAND_PREFIX, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="rclone command adapter"))
