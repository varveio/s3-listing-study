#!/usr/bin/env python3
"""Compile MinIO Client listing parameters into exact in-image argv."""

from s3_listing_study.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
    validate_concurrency,
)

TOOL = "minio-mc"
FIXED_COMMAND_PREFIX = ("/usr/bin/mc",)
MODES = frozenset(
    {"recursive", "recursive-json", "shallow", "shallow-json", "versions-json", "find", "find-json"}
)
FUNCTIONAL_ENV = {"MC_HOST_s3": "https://s3.amazonaws.com"}
"""Defines the ad-hoc alias `s3` mc's argv below targets.

mc has no ``--no-sign-request`` flag; it resolves credentials from a named
alias, and this one carries none, so minio-go issues unsigned requests
(anonymous). Endpoint configuration mc structurally requires — not a
credential, not a traffic redirect. See ``docs/running.md`` § Anonymous
wiring. Whether an authenticated attempt can reuse this same keyless alias
(minio-go falling back to ambient ``AWS_ACCESS_KEY_ID``/
``AWS_SECRET_ACCESS_KEY``) is unverified — confirm before trusting an
authenticated mc receipt; this mechanism has no way to embed a credential
into a per-request alias URL, since ``FUNCTIONAL_ENV`` is a static,
request-independent declaration.
"""


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
