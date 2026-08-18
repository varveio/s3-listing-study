#!/usr/bin/env python3
"""Compile s3kor listing parameters into exact in-image argv."""

from benchmark.runtime.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    Executable,
    Mode,
    command_adapter_main,
)

TOOL = "s3kor"
S3KOR = Executable(TOOL, ("/usr/local/bin/s3kor",))
EXECUTABLES = (S3KOR,)
SUPPORTS_UNSIGNED = False
"""No unsigned request path; it signs with the credential in the environment."""

FIELDS = ("key",)
"""Neither mode exposes size, etag, mtime or storage_class; `normalize.py`
emits every one of them as NULL for both modes."""

# No concurrency axis: `ls` drives the SDK's `ListObjectsV2Pages`/
# `ListObjectVersionsPages` auto-paginator, a single serial continuation
# chain -- concurrency is structurally 1, not a settable knob. `-c/--concurrent`
# exists only on `cp`/`sync`, not `ls` (`s3kor.go:48,76` vs `39-41`). Absence
# is the declaration.
LISTING = "listing"
"""The logical name every mode here publishes its listing under."""

TEXT = {LISTING: "listing.txt"}
"""s3kor prints its listing and takes no output destination, so the worker lands
fd 1 in the declared file."""

MODES = {
    "list": Mode(
        product="text",
        fields=FIELDS,
        executable=S3KOR.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "list-versions": Mode(
        product="text",
        fields=FIELDS,
        executable=S3KOR.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
}


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    uri = f"s3://{request.bucket}" + (f"/{request.prefix}" if request.prefix else "")
    if request.mode == "list":
        return "ls", "--region", request.region, uri
    if request.mode == "list-versions":
        return "ls", "--all-versions", "--region", request.region, uri
    raise CommandAdapterError(f"unknown mode: {request.mode}")


def build_command(request: CommandRequest) -> tuple[str, ...]:
    return *S3KOR.argv, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s3kor command adapter"))
