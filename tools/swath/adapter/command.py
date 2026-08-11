#!/usr/bin/env python3
"""Compile Swath listing parameters into exact in-image argv."""

from s3_listing_study.common.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    command_adapter_main,
    validate_concurrency,
)

TOOL = "swath"
# Absolute, not the bare "java" of the image entrypoint: the attempt engine
# replaces the environment wholesale with its own allowlist, whose PATH does not
# carry Temurin's /opt/java/openjdk/bin, so a bare name would not resolve.
FIXED_COMMAND_PREFIX = ("/opt/java/openjdk/bin/java", "-jar", "/opt/swath/swath.jar")
MODES = frozenset(
    {
        "recursive-tsv",
        "recursive-jsonl",
        "recursive-table",
        "seed-none",
        "recursive-parquet",
        "recursive-parquet-sorted",
    }
)

SINK_MODES = frozenset({"recursive-parquet", "recursive-parquet-sorted"})
"""Modes whose listing lands in the engine's sink directory, not on stdout.

Swath refuses Parquet on stdout at every version this study has tested: the
sink is opened through ``OutputOptions.openParquetDir``, which rejects a stdout
destination outright ("Parquet output requires -o <dir>"). Sorted output is
narrower still — ``ListCommand.validateSortFlags`` requires ``--format parquet``
AND a directory-dataset destination, refusing both stdout and a single file.
"""

DATASET_NAME = "listing"
"""The dataset directory built under the engine's sink.

No recognized extension, so ``-o`` infers a directory dataset; the explicit
``--format parquet`` then supplies the format. A ``.parquet`` suffix would infer
FILE kind instead, which the sorted mode rejects as non-resumable.
"""


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    uri = f"s3://{request.bucket}" + (f"/{request.prefix}" if request.prefix else "")
    # --color replaces v0.2.0's --disable-color-tracing, which no longer exists:
    # driving 0.2.2 with the old flag exits 2 before the first request.
    head = (
        "-v",
        "--color",
        "never",
        "list",
        uri,
        "--region",
        request.region,
        "--no-sign-request",
        "--concurrency",
        "8",
    )
    # Every mode but the sorted one is a single ephemeral shot, so it keeps no
    # durable state. Sorting is the exception: it tracks sort segments across the
    # run and refuses outright under --checkpoint none.
    common = (*head, "--checkpoint", "none")
    if request.mode in SINK_MODES:
        if not request.sink_dir:
            raise CommandAdapterError(
                f"mode {request.mode!r} writes a Parquet dataset and requires a sink directory"
            )
        dataset = f"{request.sink_dir.rstrip('/')}/{DATASET_NAME}"
        if request.mode == "recursive-parquet":
            return (*common, "--format", "parquet", "-o", dataset)
        # sort.ignore-disk-check keeps the run from refusing on a container
        # filesystem whose free space it cannot size.
        return (
            *head,
            "--checkpoint",
            "auto",
            "--format",
            "parquet",
            "-o",
            dataset,
            "--sort",
            "--tune",
            "sort.ignore-disk-check=on",
        )

    commands = {
        "recursive-tsv": (*common, "--format", "tsv"),
        "recursive-jsonl": (*common, "--format", "jsonl"),
        "recursive-table": (*common, "--format", "table"),
        "seed-none": (*common, "--format", "tsv", "--tune", "seed.mode=none"),
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
