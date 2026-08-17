#!/usr/bin/env python3
"""Compile s5cmd listing parameters into exact in-image argv."""

from benchmark.runtime.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    Executable,
    Mode,
    command_adapter_main,
)

TOOL = "s5cmd"
S5CMD = Executable(TOOL, ("/s5cmd",))
EXECUTABLES = (S5CMD,)
SUPPORTS_UNSIGNED = True
"""--no-sign-request lists anonymously; otherwise the credential in the
environment signs."""

FULL_FIELDS = ("key", "size", "etag", "mtime", "storage_class")
KEY_ONLY = ("key",)
"""`--show-fullpath` prints one absolute URL per line and nothing else
(`normalize.py`'s `fullpath` query selects `NULL` for the other four columns)."""

# Every mode here is a single `ls` invocation. `--numworkers` (default 256)
# sizes the `run`/transfer worker pool and is never consumed by the LIST chain
# (`command/app.go:18`, `command/run.go:76`) -- exactly aws-cli's `s3api`/`s3 ls`
# case, so absence is the declaration for the same reason.
MODES = {
    "recursive": Mode(product="text", fields=FULL_FIELDS, executable=S5CMD.name),
    "delimiter": Mode(product="text", fields=FULL_FIELDS, executable=S5CMD.name),
    "rootkeys": Mode(product="text", fields=FULL_FIELDS, executable=S5CMD.name),
    "json": Mode(product="text", fields=FULL_FIELDS, executable=S5CMD.name),
    "listv1": Mode(product="text", fields=FULL_FIELDS, executable=S5CMD.name),
    # ListObjectVersions on this study's unversioned buckets collapses to
    # current objects, so its throughput is not comparable to the other modes'.
    "allversions": Mode(
        product="text",
        fields=FULL_FIELDS,
        purpose_ceiling="diagnostic",
        executable=S5CMD.name,
    ),
    "fullpath": Mode(product="text", fields=KEY_ONLY, executable=S5CMD.name),
}


def _auth_flags(request: CommandRequest) -> tuple[str, ...]:
    # Authenticated runs sign requests with the credential the engine put in
    # the child's environment; anonymous runs pin no-sign-request so a subject
    # can never fall back to an ambient credential it should not have.
    return ("--no-sign-request",) if not request.signed else ()


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
    return *S5CMD.argv, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s5cmd command adapter"))
