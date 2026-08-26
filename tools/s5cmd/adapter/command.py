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
LISTING = "listing"
"""The logical name every mode here publishes its listing under."""

TEXT = {LISTING: "listing.txt"}
JSON = {LISTING: "listing.json"}
"""s5cmd prints its listing and has no flag that writes one to a path, so the
worker lands fd 1 in the declared file; `--json` chooses which of these it is."""

MODES = {
    "recursive": Mode(
        product="text",
        fields=FULL_FIELDS,
        executable=S5CMD.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "delimiter": Mode(
        product="text",
        fields=FULL_FIELDS,
        executable=S5CMD.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "rootkeys": Mode(
        product="text",
        fields=FULL_FIELDS,
        executable=S5CMD.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "json": Mode(
        product="text",
        fields=FULL_FIELDS,
        executable=S5CMD.name,
        artifacts=JSON,
        product_artifact=LISTING,
    ),
    "listv1": Mode(
        product="text",
        fields=FULL_FIELDS,
        executable=S5CMD.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    # ListObjectVersions on this study's unversioned buckets collapses to
    # current objects, so its throughput is not comparable to the other modes'.
    "allversions": Mode(
        product="text",
        fields=FULL_FIELDS,
        purpose_ceiling="diagnostic",
        executable=S5CMD.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "fullpath": Mode(
        product="text",
        fields=KEY_ONLY,
        executable=S5CMD.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
}


def _auth_flags(request: CommandRequest) -> tuple[str, ...]:
    # Authenticated runs sign requests with the credential the engine put in
    # the child's environment; anonymous runs pin no-sign-request so a subject
    # can never fall back to an ambient credential it should not have.
    return ("--no-sign-request",) if not request.signed else ()


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    if request.endpoint_url and request.mode in {"listv1", "allversions"}:
        raise CommandAdapterError(
            f"{TOOL} mode {request.mode!r} cannot run against the replay endpoint because "
            "it does not issue ListObjectsV2"
        )
    target = f"s3://{request.bucket}/{request.prefix}"
    recursive = target + "*"
    auth = _auth_flags(request)
    endpoint = ("--endpoint-url", request.endpoint_url) if request.endpoint_url else ()
    commands = {
        "recursive": (*endpoint, *auth, "ls", "-e", "-s", recursive),
        "delimiter": (*endpoint, *auth, "ls", "-e", "-s", target),
        "rootkeys": (*endpoint, *auth, "ls", "-e", "-s", target),
        "json": ("--json", *endpoint, *auth, "ls", recursive),
        "listv1": (*endpoint, *auth, "--use-list-objects-v1", "ls", "-e", "-s", recursive),
        "allversions": (*endpoint, *auth, "ls", "--all-versions", "-e", "-s", recursive),
        "fullpath": (*endpoint, *auth, "ls", "--show-fullpath", recursive),
    }
    try:
        return commands[request.mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {request.mode}") from None


def build_command(request: CommandRequest) -> tuple[str, ...]:
    return *S5CMD.argv, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s5cmd command adapter"))
