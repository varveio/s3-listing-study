#!/usr/bin/env python3
"""Compile s5cmd listing parameters into exact in-image argv."""

from s3_listing_study.common.command_adapter import (
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


def _auth_flags(request: CommandRequest) -> tuple[str, ...]:
    # Authenticated runs sign requests with the credential the engine put in
    # the child's environment; anonymous runs pin no-sign-request so a subject
    # can never fall back to an ambient credential it should not have.
    return ("--no-sign-request",) if request.auth == "anonymous" else ()


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    target = f"s3://{request.bucket}/{request.prefix}"
    recursive = target + "*"
    auth = _auth_flags(request)
    commands = {
        "recursive": (*auth, "ls", "-e", "-s", recursive),
        "delimiter": (*auth, "ls", "-e", "-s", target),
        "rootkeys": (*auth, "ls", "-e", "-s", target),
        "json": ("--json", *auth, "ls", recursive),
        "listv1": (*auth, "--use-list-objects-v1", "ls", "-e", "-s", recursive),
        "allversions": (*auth, "ls", "--all-versions", "-e", "-s", recursive),
        "fullpath": (*auth, "ls", "--show-fullpath", recursive),
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
