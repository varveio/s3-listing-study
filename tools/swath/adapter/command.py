#!/usr/bin/env python3
"""Compile Swath listing parameters into exact in-image argv."""

from s3_listing_study.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
    validate_concurrency,
)

TOOL = "swath"
FIXED_COMMAND_PREFIX = ("java", "-jar", "/opt/swath/swath.jar")
MODES = frozenset(
    {
        "recursive-tsv",
        "recursive-jsonl",
        "recursive-table",
        "seed-none",
        "parquet-probe",
        "sort-probe",
    }
)


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    uri = f"s3://{request.bucket}" + (f"/{request.prefix}" if request.prefix else "")
    common = (
        "-v",
        "list",
        uri,
        "--region",
        request.region,
        "--no-sign-request",
        "--checkpoint",
        "none",
        "--concurrency",
        "8",
    )
    commands = {
        "recursive-tsv": (*common, "--format", "tsv"),
        "recursive-jsonl": (*common, "--format", "jsonl"),
        "recursive-table": (*common, "--format", "table"),
        "seed-none": (*common, "--format", "tsv", "--tune", "seed.mode=none"),
        "parquet-probe": (*common, "--format", "parquet", "-o", "/tmp/swout"),
        "sort-probe": (
            "-v",
            "list",
            uri,
            "--region",
            request.region,
            "--no-sign-request",
            "--checkpoint",
            "auto",
            "--concurrency",
            "8",
            "--format",
            "parquet",
            "--sort",
            "--tune",
            "sort.ignore-disk-check=on",
            "-o",
            "/tmp/swout",
        ),
    }
    try:
        return commands[request.mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {request.mode}") from None


def build_command(request: CommandRequest) -> tuple[str, ...]:
    validate_concurrency(request, tool=TOOL)
    return *FIXED_COMMAND_PREFIX, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="swath command adapter"))
