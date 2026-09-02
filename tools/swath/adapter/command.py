#!/usr/bin/env python3
"""Compile Swath listing parameters into exact in-image argv."""

import re

from benchmark.runtime.command_adapter import (
    HEAP_PERCENT,
    Ceiling,
    CommandAdapterError,
    CommandRequest,
    Executable,
    Fixed,
    Mode,
    command_adapter_main,
)

TOOL = "swath"
# Absolute, not the bare "java" of the image entrypoint: the attempt engine
# replaces the environment wholesale with its own allowlist, whose PATH does not
# carry Temurin's /opt/java/openjdk/bin, so a bare name would not resolve.
SWATH = Executable("swath", ("/opt/java/openjdk/bin/java", "-jar", "/opt/swath/swath.jar"))
EXECUTABLES = (SWATH,)
SUPPORTS_UNSIGNED = True
"""--no-sign-request lists anonymously. The signed path drops the flag and has
not been exercised by a committed run."""

CONCURRENCY = Ceiling(64, "source@7b9a5e2")
"""Swath's own width when unsilenced: `S3Config.DEFAULT_MAX_PARALLEL`
(`S3Config.java:81`, bound by `ConnectionOptions.java:79-81` @7b9a5e2).

A ceiling because `--concurrency N` is an AIMD limit: a run starts at
`min(4, N)` permits and the store, not the flag, sets the steady-state level, so
the achieved width is a fact about the run and belongs in evidence.

The 64 is read at the same revision `build/image.json` pins (the v0.3.1
release image, revision 7b9a5e2), and that image's own `list --help` prints
`--concurrency=N  AIMD ceiling for concurrent listing requests (default: 64)`;
the capture is in `docs/running.md`.

What a campaign asks Swath for is plan content -- the historical cap of 8 is a
`concurrency` row field in `benchmark/plans/buckets/*.yaml`, where it is visible
and reviewable, not a number this capsule decides.
"""

TEXT_FIELDS = ("key", "size", "etag", "mtime", "storage_class")
ALIGNED_FIELDS = ("key", "size", "mtime")
"""TableFormatter prints size, last_modified and key only."""

AXES = {"concurrency": CONCURRENCY, "heap_percent": Fixed(HEAP_PERCENT)}
"""Swath is a JVM, so every mode feels the heap share; every mode takes the flag."""

CONFIG_KEYS = frozenset(
    {
        "jvm_max_heap",
        "parquet_part_size",
        "parquet_writers",
        "part_rotation_interval",
        "part_rotation_max_rows",
        "sort_merge_parallelism",
        "text_part_size",
        "text_writers",
        "writeback_size",
    }
)
"""Output controls whose retained values must remain visible in case identity."""

ENV_CONFIG_KEYS = frozenset({"jvm_max_heap"})
"""Declared controls consumed by :func:`build_env`, independent of output mode."""

JVM_MAX_HEAP_RE = re.compile(r"\A[1-9][0-9]*[kKmMgG]\Z")

LISTING = "listing"
"""The logical name every mode here publishes its listing under."""

DATASET_NAME = "listing"
"""The dataset directory built under the engine's sink.

No recognized extension, so ``-o`` infers a directory dataset; the explicit
``--format parquet`` then supplies the format. A ``.parquet`` suffix would infer
FILE kind instead, which the sorted mode rejects as non-resumable.
"""

DATASET = {LISTING: DATASET_NAME}
"""A directory rather than a file, which is what `product_channel="dataset"`
says: verify normalizes its parts through `--dataset`, not through one path."""

TSV = {LISTING: "listing.tsv"}
JSONL = {LISTING: "listing.jsonl"}
TABLE = {LISTING: "listing.txt"}
"""What `--format` renders on stdout. The text formats take no destination, so
the worker lands fd 1 in the declared file; only the Parquet modes have `-o`."""

MODES = {
    "recursive-tsv": Mode(
        product="text",
        fields=TEXT_FIELDS,
        axes=AXES,
        executable=SWATH.name,
        artifacts=TSV,
        product_artifact=LISTING,
    ),
    "recursive-jsonl": Mode(
        product="text",
        fields=TEXT_FIELDS,
        axes=AXES,
        executable=SWATH.name,
        artifacts=JSONL,
        product_artifact=LISTING,
    ),
    "recursive-tsv-dataset": Mode(
        product="text",
        fields=TEXT_FIELDS,
        axes=AXES,
        purpose_ceiling="diagnostic",
        executable=SWATH.name,
        artifacts=DATASET,
        product_artifact=LISTING,
        product_channel="dataset",
    ),
    "recursive-tsv-zstd": Mode(
        product="text",
        fields=TEXT_FIELDS,
        axes=AXES,
        purpose_ceiling="diagnostic",
        executable=SWATH.name,
        artifacts=DATASET,
        product_artifact=LISTING,
        product_channel="dataset",
    ),
    # Aligned text discards etag and storage_class, so it cannot be verified on
    # the same fields as the modes it would be ranked against.
    "recursive-table": Mode(
        product="text",
        fields=ALIGNED_FIELDS,
        axes=AXES,
        purpose_ceiling="diagnostic",
        executable=SWATH.name,
        artifacts=TABLE,
        product_artifact=LISTING,
    ),
    "seed-none": Mode(
        product="text",
        fields=TEXT_FIELDS,
        axes=AXES,
        executable=SWATH.name,
        artifacts=TSV,
        product_artifact=LISTING,
    ),
    "recursive-parquet": Mode(
        product="parquet",
        fields=TEXT_FIELDS,
        axes=AXES,
        executable=SWATH.name,
        artifacts=DATASET,
        product_artifact=LISTING,
        product_channel="dataset",
    ),
    "recursive-parquet-sorted": Mode(
        product="parquet-sorted",
        fields=TEXT_FIELDS,
        axes=AXES,
        executable=SWATH.name,
        artifacts=DATASET,
        product_artifact=LISTING,
        product_channel="dataset",
    ),
}

SINK_MODES = frozenset(
    mode for mode, manifest in MODES.items() if manifest.product_channel == "dataset"
)
"""Modes whose listing lands in the engine's sink directory, not on stdout.

Read off the manifests rather than listed again: which channel a mode's product
travels on is declared once, and argv is what has to agree with it.

Swath refuses Parquet on stdout at every version this study has tested: the
sink is opened through ``OutputOptions.openParquetDir``, which rejects a stdout
destination outright ("Parquet output requires -o <dir>"). Sorted output is
narrower still — ``ListCommand.validateSortFlags`` requires ``--format parquet``
AND a directory-dataset destination, refusing both stdout and a single file.
"""


def build_env(request: CommandRequest) -> dict[str, str]:
    """Render the harness's heap share into what this runtime reads.

    The share is the harness's methodology decision and arrives on the request;
    that Swath is a JVM told through ``JAVA_TOOL_OPTIONS`` is this capsule's
    knowledge and lives nowhere else.
    """
    options = f"-XX:MaxRAMPercentage={request.heap_percent}"
    maximum = request.config.get("jvm_max_heap")
    if maximum is not None:
        if not isinstance(maximum, str) or JVM_MAX_HEAP_RE.fullmatch(maximum) is None:
            raise CommandAdapterError(
                f"{TOOL} jvm_max_heap must be a positive JVM size such as '4g'; got: {maximum!r}"
            )
        options = f"{options} -Xmx{maximum}"
    return {"JAVA_TOOL_OPTIONS": options}


def _concurrency(request: CommandRequest) -> str:
    """Render the asked-for ceiling; declared in :data:`MODES`, never pinned here."""
    value = request.config.get("concurrency", CONCURRENCY.value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CommandAdapterError(f"{TOOL} concurrency must be a positive integer; got: {value!r}")
    return str(value)


def _parquet_writers(request: CommandRequest) -> tuple[str, ...]:
    value = request.config.get("parquet_writers")
    if value is None:
        return ()
    if isinstance(value, bool) or not isinstance(value, int) or not 2 <= value <= 64:
        raise CommandAdapterError(
            f"{TOOL} parquet_writers must be an integer from 2 through 64; got: {value!r}"
        )
    return "--tune", f"parquet.writers={value}"


def _text_writers(request: CommandRequest) -> str:
    value = request.config.get("text_writers", 3)
    if isinstance(value, bool) or not isinstance(value, int) or not 2 <= value <= 64:
        raise CommandAdapterError(
            f"{TOOL} text_writers must be an integer from 2 through 64; got: {value!r}"
        )
    return str(value)


def _mode_config(request: CommandRequest, allowed: frozenset[str]) -> None:
    """Refuse a declared Swath knob neither the mode argv nor environment consumes."""
    unused = sorted((set(request.config) & CONFIG_KEYS) - allowed - ENV_CONFIG_KEYS)
    if unused:
        raise CommandAdapterError(
            f"{TOOL} mode {request.mode!r} does not use config key(s): {', '.join(unused)}"
        )


def _size_option(request: CommandRequest, key: str, option: str) -> tuple[str, ...]:
    value = request.config.get(key)
    if value is None:
        return ()
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise CommandAdapterError(f"{TOOL} {key} must be one non-empty size token; got: {value!r}")
    return option, value


def _rotation_options(request: CommandRequest) -> tuple[str, ...]:
    interval = request.config.get("part_rotation_interval")
    rows = request.config.get("part_rotation_max_rows")
    rendered: list[str] = []
    if interval is not None:
        if (
            not isinstance(interval, str)
            or not interval
            or any(character.isspace() for character in interval)
        ):
            raise CommandAdapterError(
                f"{TOOL} part_rotation_interval must be one non-empty duration token; "
                f"got: {interval!r}"
            )
        rendered.extend(("--part-rotation-interval", interval))
    if rows is not None:
        if isinstance(rows, bool) or not isinstance(rows, int) or rows < 0:
            raise CommandAdapterError(
                f"{TOOL} part_rotation_max_rows must be a non-negative integer; got: {rows!r}"
            )
        rendered.extend(("--part-rotation-max-rows", str(rows)))
    return tuple(rendered)


def _sort_merge_parallelism(request: CommandRequest) -> tuple[str, ...]:
    value = request.config.get("sort_merge_parallelism")
    if value is None:
        return ()
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 16:
        raise CommandAdapterError(
            f"{TOOL} sort_merge_parallelism must be an integer from 1 through 16; got: {value!r}"
        )
    return "--tune", f"sort.merge-parallelism={value}"


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
        *((), ("--no-sign-request",))[not request.signed],
        "--concurrency",
        _concurrency(request),
        *(("--endpoint-url", request.endpoint_url) if request.endpoint_url else ()),
    )
    # Every mode but the sorted one is a single ephemeral shot, so it keeps no
    # durable state. Sorting is the exception: it tracks sort segments across the
    # run and refuses outright under --checkpoint none.
    common = (*head, "--checkpoint", "none")
    if request.mode in SINK_MODES:
        if not request.sink_dir:
            raise CommandAdapterError(
                f"mode {request.mode!r} writes a directory dataset and requires a sink directory"
            )
        dataset = f"{request.sink_dir.rstrip('/')}/{DATASET_NAME}"
        if request.mode in {"recursive-tsv-dataset", "recursive-tsv-zstd"}:
            _mode_config(
                request,
                frozenset(
                    {
                        "part_rotation_interval",
                        "part_rotation_max_rows",
                        "text_part_size",
                        "text_writers",
                        "writeback_size",
                    }
                ),
            )
            return (
                *common,
                "--format",
                "tsv",
                "--output-type",
                "dir",
                "-o",
                dataset,
                "--text-writers",
                _text_writers(request),
                "--compression",
                "zstd" if request.mode == "recursive-tsv-zstd" else "none",
                *_size_option(request, "text_part_size", "--text-part-size"),
                *_size_option(request, "writeback_size", "--writeback-size"),
                *_rotation_options(request),
            )
        if request.mode == "recursive-parquet":
            _mode_config(
                request,
                frozenset(
                    {
                        "parquet_part_size",
                        "parquet_writers",
                        "part_rotation_interval",
                        "part_rotation_max_rows",
                        "writeback_size",
                    }
                ),
            )
            return (
                *common,
                "--format",
                "parquet",
                "-o",
                dataset,
                *_parquet_writers(request),
                *_size_option(request, "parquet_part_size", "--parquet-part-size"),
                *_size_option(request, "writeback_size", "--writeback-size"),
                *_rotation_options(request),
            )
        # sort.ignore-disk-check keeps the run from refusing on a container
        # filesystem whose free space it cannot size.
        _mode_config(
            request,
            frozenset({"parquet_part_size", "sort_merge_parallelism", "writeback_size"}),
        )
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
            *_sort_merge_parallelism(request),
            *_size_option(request, "parquet_part_size", "--parquet-part-size"),
            *_size_option(request, "writeback_size", "--writeback-size"),
        )

    _mode_config(request, frozenset())
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
    return *SWATH.argv, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="swath command adapter"))
