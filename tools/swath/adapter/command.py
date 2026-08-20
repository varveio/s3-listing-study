#!/usr/bin/env python3
"""Compile Swath listing parameters into exact in-image argv."""

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

CONCURRENCY = Ceiling(64, "source@cef8ec2")
"""Swath's own width when unsilenced: `S3Config.DEFAULT_MAX_PARALLEL`
(`ConnectionOptions.java:78-80`).

A ceiling because `--concurrency N` is an AIMD limit: a run starts at
`min(4, N)` permits and the store, not the flag, sets the steady-state level, so
the achieved width is a fact about the run and belongs in evidence.

**Receipt owed.** `cef8ec2` is v0.2.0 source while `build/image.json` pins the
0.2.4 jar, so the 64 stands on a reading of the wrong revision until a
`list --help` on 0.2.4 confirms it. Also in `docs/running.md`.

What a campaign asks Swath for is plan content -- the historical cap of 8 is a
`concurrency` row field in `benchmark/plans/buckets/*.yaml`, where it is visible
and reviewable, not a number this capsule decides.
"""

TEXT_FIELDS = ("key", "size", "etag", "mtime", "storage_class")
ALIGNED_FIELDS = ("key", "size", "mtime")
"""TableFormatter prints size, last_modified and key only."""

AXES = {"concurrency": CONCURRENCY, "heap_percent": Fixed(HEAP_PERCENT)}
"""Swath is a JVM, so every mode feels the heap share; every mode takes the flag."""

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
    return {"JAVA_TOOL_OPTIONS": f"-XX:MaxRAMPercentage={request.heap_percent}"}


def _concurrency(request: CommandRequest) -> str:
    """Render the asked-for ceiling; declared in :data:`MODES`, never pinned here."""
    value = request.config.get("concurrency", CONCURRENCY.value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CommandAdapterError(f"{TOOL} concurrency must be a positive integer; got: {value!r}")
    return str(value)


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
    return *SWATH.argv, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="swath command adapter"))
