#!/usr/bin/env python3
"""Compile AWS CLI listing parameters into exact in-image argv."""

from __future__ import annotations

from benchmark.runtime.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
)

TOOL = "aws-cli"
FIXED_COMMAND_PREFIX = ("/usr/local/bin/aws",)
MODES = frozenset(
    {
        "s3api-v2-text",
        "s3api-v2-json",
        "s3api-v2-yamlstream",
        "s3api-v1-text",
        "s3api-versions-text",
        "s3api-v2-delimiter",
        "s3api-v2-remainder",
        "s3-ls-recursive",
        "s3-ls-delimiter",
    }
)
SUPPORTS_UNSIGNED = True
"""--no-sign-request lists anonymously; otherwise the credential in the
environment signs."""
Q_CONTENTS = "Contents[].[Key,Size,ETag,LastModified,StorageClass]"
Q_VERSIONS = "Versions[].[Key,Size,ETag,LastModified,StorageClass]"


def _auth_flags(request: CommandRequest) -> tuple[str, ...]:
    # Authenticated runs sign requests with the credential the engine put in
    # the child's environment; anonymous runs pin no-sign-request so a subject
    # can never fall back to an ambient credential it should not have.
    return ("--no-sign-request",) if not request.signed else ()


def _s3api(request: CommandRequest, operation: str) -> list[str]:
    argv = [
        "s3api",
        operation,
        "--bucket",
        request.bucket,
        "--region",
        request.region,
        *_auth_flags(request),
    ]
    if request.prefix:
        argv.extend(("--prefix", request.prefix))
    return argv


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    mode = request.mode
    if mode == "s3api-v2-remainder":
        return (
            "s3api",
            "list-objects-v2",
            "--bucket",
            request.bucket,
            "--region",
            request.region,
            *_auth_flags(request),
            "--delimiter",
            "/",
            "--query",
            Q_CONTENTS,
            "--output",
            "text",
        )
    if mode in {"s3-ls-recursive", "s3-ls-delimiter"}:
        target = f"s3://{request.bucket}/{request.prefix}"
        argv = ["s3", "ls", target]
        if mode == "s3-ls-recursive":
            argv.append("--recursive")
        argv.extend(("--region", request.region, *_auth_flags(request)))
        return tuple(argv)

    operations = {
        "s3api-v2-text": ("list-objects-v2", Q_CONTENTS, "text"),
        "s3api-v2-json": ("list-objects-v2", None, "json"),
        "s3api-v2-yamlstream": ("list-objects-v2", None, "yaml-stream"),
        "s3api-v1-text": ("list-objects", Q_CONTENTS, "text"),
        "s3api-versions-text": ("list-object-versions", Q_VERSIONS, "text"),
        "s3api-v2-delimiter": ("list-objects-v2", None, "json"),
    }
    try:
        operation, query, output = operations[mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {mode}") from None
    if mode == "s3api-v2-delimiter":
        argv = [
            "s3api",
            "list-objects-v2",
            "--bucket",
            request.bucket,
            "--region",
            request.region,
            *_auth_flags(request),
            "--delimiter",
            "/",
        ]
        if request.prefix:
            argv.extend(("--prefix", request.prefix))
    else:
        argv = _s3api(request, operation)
    if query is not None:
        argv.extend(("--query", query))
    argv.extend(("--output", output))
    return tuple(argv)


def build_command(request: CommandRequest) -> tuple[str, ...]:
    return *FIXED_COMMAND_PREFIX, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="aws-cli command adapter"))
