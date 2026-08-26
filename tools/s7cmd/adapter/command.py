#!/usr/bin/env python3
"""Compile s7cmd listing parameters into exact in-image argv."""

from benchmark.runtime.command_adapter import (
    Ceiling,
    CommandAdapterError,
    CommandRequest,
    Executable,
    Inert,
    Mode,
    command_adapter_main,
)

TOOL = "s7cmd"
S7CMD = Executable("s7cmd", ("/usr/local/bin/s7cmd",))
EXECUTABLES = (S7CMD,)
SUPPORTS_UNSIGNED = True
"""--target-no-sign-request lists anonymously. The signed path drops the flag
and has not been exercised by a committed run."""

CONCURRENCY = Ceiling(64, "help")
"""s7cmd's own width when unsilenced: `--max-parallel-listings [default: 64]`
(`receipts/smoke/_build/help-and-version.txt:328`).

A ceiling because the number only sizes the `Arc<Semaphore>` that bounds prefix
discovery: the achieved width is however many sub-prefixes the delimiter walk
finds down to `--max-parallel-listing-max-depth` (default 2), and a bucket with
no `/` hierarchy discovers none and drains sequentially at width 1. So the
effective width is a fact about the run and belongs in evidence.

What a campaign asks s7cmd for is plan content -- the historical cap of 16 is a
`concurrency` row field in `benchmark/plans/buckets/*.yaml`, where it is visible
and reviewable, not a number this capsule decides. The smoke receipts that ran
at 16 are read against that in `docs/running.md`.
"""

ALL_FIELDS = ("key", "size", "etag", "mtime", "storage_class")
"""What `--tsv --show-storage-class --show-etag` and `--json` both carry."""
ALIGNED_FIELDS = ("key", "size", "mtime")
"""The default formatter prints date, size and key only."""
KEY_FIELDS = ("key",)

PARALLEL = {"concurrency": CONCURRENCY}
"""Recursive modes: `-r` sets no delimiter, which is what arms the parallel path
(`use_parallel = max_parallel_listings > 1 && delimiter.is_none()`)."""
SEQUENTIAL = {"concurrency": Inert()}
"""Without `-r` the engine always sets `delimiter = "/"`, so the same flag reaches
a listing that is one sequential paginated call whatever it says."""

LISTING = "listing"
"""The logical name every mode here publishes its listing under."""

TSV = {LISTING: "listing.tsv"}
JSON = {LISTING: "listing.json"}
TEXT = {LISTING: "listing.txt"}
"""What `--tsv` / `--json` / neither render on stdout. s7cmd takes no output
destination, so the worker lands fd 1 in the declared file, and `-vv` tracing
goes to stderr and leaves the product alone."""

MODES = {
    # Sorted: the Aggregator buffers every entry into a Vec and emits nothing
    # until the last page, so output is key-ordered and time-to-first-byte is
    # end-of-listing. `product` has no vocabulary for sorted text -- only
    # parquet-sorted -- so the fact lives here.
    "recursive-tsv": Mode(
        product="text",
        fields=ALL_FIELDS,
        axes=PARALLEL,
        artifacts=TSV,
        product_artifact=LISTING,
    ),
    # The one mode that reaches `--no-sort`: the Aggregator streams, so records
    # arrive in listing order (lexicographic within one op, interleaved across
    # parallel ops) and RSS stays near-constant instead of growing with the Vec.
    "recursive-tsv-nosort": Mode(
        product="text",
        fields=ALL_FIELDS,
        axes=PARALLEL,
        artifacts=TSV,
        product_artifact=LISTING,
    ),
    "recursive-aligned": Mode(
        product="text",
        fields=ALIGNED_FIELDS,
        axes=PARALLEL,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "recursive-json": Mode(
        product="text",
        fields=ALL_FIELDS,
        axes=PARALLEL,
        artifacts=JSON,
        product_artifact=LISTING,
    ),
    "recursive-one": Mode(
        product="text",
        fields=KEY_FIELDS,
        axes=PARALLEL,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "all-versions": Mode(
        product="text",
        fields=ALL_FIELDS,
        axes=PARALLEL,
        artifacts=TSV,
        product_artifact=LISTING,
    ),
    # `--max-depth 1` synthesizes a CommonPrefix at the boundary instead of
    # recursing, so nothing is ever spawned and the walk is one API call. The
    # knob is not inert -- the parallel path is armed -- its width is bounded by
    # a keyspace this mode refuses to descend into.
    "max-depth": Mode(
        product="text",
        fields=ALL_FIELDS,
        axes=PARALLEL,
        artifacts=TSV,
        product_artifact=LISTING,
    ),
    "shallow-tsv": Mode(
        product="text",
        fields=ALL_FIELDS,
        axes=SEQUENTIAL,
        artifacts=TSV,
        product_artifact=LISTING,
    ),
    # `ls` with no target is ListBuckets, not a listing: `normalize.py` has no
    # query for it, and anonymously S3 answers 307 and the tool exits 1.
    "bucket-list": Mode(
        product="text",
        fields=KEY_FIELDS,
        axes=SEQUENTIAL,
        purpose_ceiling="diagnostic",
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
}

SEQUENTIAL_MODES = frozenset({"shallow-tsv", "bucket-list"})
"""Modes that take no `--max-parallel-listings`, because it governs nothing they
do; the flag is accepted and declared :data:`SEQUENTIAL` rather than rendered."""


def _concurrency(request: CommandRequest) -> str:
    """Render the asked-for ceiling; declared in :data:`MODES`, never pinned here."""
    value = request.config.get("concurrency", CONCURRENCY.value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CommandAdapterError(f"{TOOL} concurrency must be a positive integer; got: {value!r}")
    return str(value)


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    if request.endpoint_url and request.mode in {"all-versions", "bucket-list"}:
        raise CommandAdapterError(
            f"{TOOL} mode {request.mode!r} cannot run against the replay endpoint because "
            "it does not issue ListObjectsV2"
        )
    target = f"s3://{request.bucket}/{request.prefix}"
    obs = ("-vv", "--disable-color-tracing")
    parallel = (
        ()
        if request.mode in SEQUENTIAL_MODES
        else ("--max-parallel-listings", _concurrency(request))
    )
    stratum = () if request.signed else ("--target-no-sign-request",)
    # The SDK's own connect-timeout default proved too tight for this study's
    # boxes: a real dispatch-level connect timeout at ~3.1s killed the whole
    # listing outright (no retry classification covers it) on both a 2-vCPU/
    # 64-way ghcn run at the very start of its work and a 2-vCPU/64-way sorel
    # run 25 minutes into an otherwise-clean 10M-object listing -- a socket
    # queued behind sixty-three others on two cores occasionally needs longer
    # than 3.1s to complete a handshake, and the tool has no fallback for that
    # one slow connection. Fixed rather than plan-tunable: it is headroom for
    # this harness's own contention, not a methodology axis, so every mode
    # gets it regardless of what a plan asks for.
    resilient = ("--connect-timeout-milliseconds", "15000")
    endpoint = (
        ("--target-endpoint-url", request.endpoint_url, "--target-force-path-style")
        if request.endpoint_url
        else ()
    )
    anonymous = (*stratum, "--target-region", request.region, *endpoint, *resilient)
    tsv = ("--tsv", "--show-storage-class", "--show-etag")
    commands = {
        "recursive-tsv": ("ls", "-r", *obs, *tsv, *parallel, *anonymous, target),
        "recursive-tsv-nosort": (
            "ls",
            "-r",
            *obs,
            "--no-sort",
            *tsv,
            *parallel,
            *anonymous,
            target,
        ),
        "recursive-aligned": ("ls", "-r", *obs, *parallel, *anonymous, target),
        "recursive-json": ("ls", "-r", *obs, "--json", *parallel, *anonymous, target),
        "recursive-one": ("ls", "-r", *obs, "-1", *parallel, *anonymous, target),
        "all-versions": ("ls", "-r", *obs, "--all-versions", *tsv, *parallel, *anonymous, target),
        "max-depth": (
            "ls",
            "-r",
            *obs,
            "--max-depth",
            "1",
            *tsv,
            *parallel,
            *anonymous,
            target,
        ),
        "shallow-tsv": ("ls", *obs, *tsv, *anonymous, target),
        "bucket-list": ("ls", *obs, *anonymous),
    }
    try:
        return commands[request.mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {request.mode}") from None


def build_command(request: CommandRequest) -> tuple[str, ...]:
    return *S7CMD.argv, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s7cmd command adapter"))
