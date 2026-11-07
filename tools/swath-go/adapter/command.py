#!/usr/bin/env python3
"""Compile Swath (Go port) listing parameters into exact in-image argv.

The subject is the Go port of Swath at the pinned source revision in
``build/image.json``: one static binary that lists an S3 bucket into a globally
key-sorted Parquet dataset (format 2: ``data/part-NNNNN.parquet`` plus
``manifest.json``, the commit) or into one text file whose rows are the S3
API's object. Every flag rendered here is read from the public flag set at that
revision (``go/docs/cli.md``, generated from the flag set and test-gated
upstream), not from the Java capsule in ``tools/swath/``: the two CLIs share a
name and a few aliases, and the adapters share their shape and nothing else.
"""

import re

from benchmark.runtime.command_adapter import (
    Ceiling,
    CommandAdapterError,
    CommandRequest,
    Executable,
    Mode,
    command_adapter_main,
)

TOOL = "swath-go"
SWATH_GO = Executable("swath-go", ("/opt/swath-go/swath",))
EXECUTABLES = (SWATH_GO,)
SUPPORTS_UNSIGNED = True
"""``--anonymous`` sends unsigned requests whatever the environment holds. The
signed path drops the flag and lets the AWS SDK default chain find the
credential; neither path has been exercised by a committed run."""

PINNED_REVISION = "7b05605"
"""The source revision every ``source@`` provenance below is read at. It is the
head of upstream pull request varveio/swath#255 and moves to the merge commit
when that pull request merges; ``build/image.json`` pins the same one."""

CONCURRENCY = Ceiling(64, f"source@{PINNED_REVISION}")
"""The port's own width when unsilenced: ``listing.DefaultWorkers = 64``
(``go/internal/listing/listing.go:215-218``, applied when ``--workers`` is 0 at
``:248-250`` @7b05605), "whatever the CPU count". The flag has no upper bound:
``resolve`` refuses only a negative value (``listing.go:235``).

A ceiling, as the Java capsule's AIMD 64 is, because ``--workers N`` sets both
the listing goroutines and the maximum of the request limiter
(``go/internal/source/objhttp/limiter.go:31-36,74-82``): with the limiter on
(the default, ``go/internal/source/keyrange/flags.go:52-54``) the limit starts
at N, falls by x0.7 on each throttle answer and grows back one permit per
second after 10 s without one (``limiter.go:13-29``), so the achieved width is
a fact about the run and belongs in evidence. Unlike the Java engine the limit
starts at N rather than ``min(4, N)``; ``--limiter-slow-start`` is the opt-in
Java ramp and is not rendered here. ``limiter: false`` keeps a fixed N requests
in flight, which N still bounds.

What a campaign asks for is plan content, never a number this capsule decides.
"""

FIELDS = ("key", "size", "etag", "mtime", "storage_class")
"""All five, in every mode: the format-2 S3 schema ``swath/s3-objects/v1`` carries
them as columns (``go/docs/internals/parquet-output.md:162-166``), and the TSV
file carries them as its ``Key``, ``Size``, ``ETag``, ``LastModified`` and
``StorageClass`` columns (``go/internal/textout/sink.go:55-66``)."""

AXES = {"concurrency": CONCURRENCY}
"""No ``heap_percent``: a Go binary has no managed heap the harness's share is
rendered into, and its row budget is the separate ``--row-budget`` flag."""

LISTING = "listing"
"""The logical name every mode publishes its listing under."""

DATASET_NAME = "listing"
"""The dataset directory under the engine's sink: ``-o <sink>/listing``. A name
without a text extension is the Parquet dataset (``go/cmd/swath/output.go:15-58``)."""

TSV_NAME = "listing.tsv"
TSV_ZSTD_NAME = "listing.tsv.zst"
"""The one text file a TSV mode writes: ``-o`` decides the format and the
compression by its name (``output.go:19-31``). The port has no directory of
text parts; the Java capsule's ``recursive-tsv-zstd`` wrote one, so the two
capsules' products of that mode differ in shape."""

REPORT_NAME = "swath-go-report.json"
"""Where ``--report`` writes the run report, beside the product in the sink.

Upstream refuses a report path on managed output (``go/docs/cli.md:16-24``), so
it is not under ``listing/``. It is a different document from the Java
capsule's ``_swath_summary.json`` and deliberately does not borrow that name;
the harness keeps it on every attempt because ``RUN_REPORT_NAMES`` lists it."""

LIST_MODE = "recursive-parquet-sorted"
RESUME_MODE = "resume-parquet-sorted"
TSV_MODE = "recursive-tsv"
TSV_ZSTD_MODE = "recursive-tsv-zstd"

MODES = {
    LIST_MODE: Mode(
        product="parquet-sorted",
        fields=FIELDS,
        axes=AXES,
        executable=SWATH_GO.name,
        artifacts={LISTING: DATASET_NAME},
        product_artifact=LISTING,
        product_channel="dataset",
    ),
    # `swath resume DIR` continues an interrupted run in the same sink with the
    # command the journal recorded. It publishes exactly what the list mode
    # publishes, on the same channel, and needs no step of its own, which is the
    # shape a killed-and-resumed attempt requires of its second step. Naming it as
    # the list mode's resume is a field the harness's Mode does not carry yet;
    # until it does, this mode exists so that wiring is one line. Run on its own
    # it has nothing to resume (exit 2 on an empty sink), hence the ceiling.
    RESUME_MODE: Mode(
        product="parquet-sorted",
        fields=FIELDS,
        axes=AXES,
        purpose_ceiling="diagnostic",
        executable=SWATH_GO.name,
        artifacts={LISTING: DATASET_NAME},
        product_artifact=LISTING,
        product_channel="dataset",
    ),
    # One uncompressed TSV file, written by the port itself (`-o`), so the file
    # channel; the worker gzips it for upload as it does every text product.
    TSV_MODE: Mode(
        product="text",
        fields=FIELDS,
        axes=AXES,
        executable=SWATH_GO.name,
        artifacts={LISTING: TSV_NAME},
        product_artifact=LISTING,
        product_channel="file",
    ),
    # The same rows, zstd-compressed by the port: uploaded as written, since
    # gzipping a zstd stream spends CPU to make it slightly bigger.
    TSV_ZSTD_MODE: Mode(
        product="text",
        fields=FIELDS,
        axes=AXES,
        executable=SWATH_GO.name,
        artifacts={LISTING: TSV_ZSTD_NAME},
        product_artifact=LISTING,
        product_channel="file",
        product_compress=False,
    ),
}

TEXT_MODES = {TSV_MODE: TSV_NAME, TSV_ZSTD_MODE: TSV_ZSTD_NAME}

CONFIG_KEYS = frozenset(
    {
        "limiter",
        "max_duration",
        "part_interval",
        "part_size",
        "row_budget",
        "seed",
        "spill_max",
        "structure",
    }
)
"""Tool-specific knobs a plan may state under ``config:``; anything else is refused.

``max_duration`` matters operationally: upstream says a hosted runner needs a
finite ``--max-duration`` on every attempt and its own deadline above it plus
20 s (``go/docs/cli.md:555-564``). The adapter cannot see the harness's
``timeout_s``, so a plan states the value below ``timeout_s - 20 s`` and that
relation is the plan's to keep."""

PARQUET_ONLY_KEYS = frozenset({"part_interval", "part_size"})
"""Part controls: a text run writes one file and has no parts, so a TSV mode
refuses them rather than rendering a flag with no effect."""

DURATION_RE = re.compile(r"\A(?:0|(?:[0-9]+(?:\.[0-9]+)?(?:ns|us|µs|ms|s|m|h))+)\Z")
"""Go's ``time.ParseDuration`` grammar, without a sign: the duration flags refuse
a negative value and 0 means off (``go/docs/cli.md:348-350``)."""

SIZE_RE = re.compile(r"\A[0-9]+(?:\.[0-9]+)?(?:b|k|kb|kib|m|mb|mib|g|gb|gib|t|tb|tib)?\Z", re.I)
"""``filter.ParseSize`` (``go/internal/filter/filter.go:224-253`` @7b05605), the
port's one size syntax: a decimal number and an optional binary unit in any
case, coming to a whole number of bytes, which only the binary can check."""

POSITIVE_SIZE_KEYS = frozenset({"part_size", "row_budget"})
"""Sizes whose flag refuses 0 (``(*size).Set``, ``go/cmd/swath/main.go:872-882``);
``--spill-max`` is a ``zsize`` and takes 0 for its derived default."""

STRUCTURE_VALUES = ("auto", "on", "off")
"""``--structure``'s values (``keyrange/flags.go:109-121``). It is a diagnostic
flag, registered only on ``swath diag list`` (``flags.go:58-59``), so a case
stating it runs ``diag list``; without it the plain ``list`` runs at the
default, ``auto``."""


def _concurrency(request: CommandRequest) -> str:
    value = request.config.get("concurrency", CONCURRENCY.value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CommandAdapterError(f"{TOOL} concurrency must be a positive integer; got: {value!r}")
    return str(value)


def _bool_option(request: CommandRequest, key: str, flag: str) -> tuple[str, ...]:
    """An explicit ``--flag=true|false``; absent renders nothing (the tool's default)."""
    value = request.config.get(key)
    if value is None:
        return ()
    if not isinstance(value, bool):
        raise CommandAdapterError(f"{TOOL} {key} must be true or false; got: {value!r}")
    return (f"{flag}={'true' if value else 'false'}",)


def _duration_option(request: CommandRequest, key: str, flag: str) -> tuple[str, ...]:
    value = request.config.get(key)
    if value is None:
        return ()
    if not isinstance(value, str) or DURATION_RE.fullmatch(value) is None:
        raise CommandAdapterError(f"{TOOL} {key} must be a Go duration such as '2h'; got: {value!r}")
    return flag, value


def _size_option(request: CommandRequest, key: str, flag: str) -> tuple[str, ...]:
    value = request.config.get(key)
    if value is None:
        return ()
    if not isinstance(value, str) or SIZE_RE.fullmatch(value) is None:
        raise CommandAdapterError(f"{TOOL} {key} must be a size such as '64MiB'; got: {value!r}")
    if key in POSITIVE_SIZE_KEYS and not re.search(r"[1-9]", value):
        raise CommandAdapterError(f"{TOOL} {key} must be positive; got: {value!r}")
    return flag, value


def _structure(request: CommandRequest) -> tuple[str, ...]:
    value = request.config.get("structure")
    if value is None:
        return ()
    if value not in STRUCTURE_VALUES:
        raise CommandAdapterError(
            f"{TOOL} structure must be one of {', '.join(STRUCTURE_VALUES)}; got: {value!r}"
        )
    # Written with '=': the bare flag is a boolean, so a separate word would be
    # read as the next argument (`flags.go:59`).
    return (f"--structure={value}",)


def _sink(request: CommandRequest) -> str:
    if not request.sink_dir:
        raise CommandAdapterError(
            f"mode {request.mode!r} writes its product into the sink and requires a sink directory"
        )
    return request.sink_dir.rstrip("/")


def _list_tail(request: CommandRequest, output: str, report: str) -> tuple[str, ...]:
    if request.mode in TEXT_MODES:
        unused = sorted(PARQUET_ONLY_KEYS & set(request.config))
        if unused:
            raise CommandAdapterError(
                f"{TOOL} mode {request.mode!r} writes one text file and does not use: "
                + ", ".join(unused)
            )
    structure = _structure(request)
    uri = f"s3://{request.bucket}" + (f"/{request.prefix}" if request.prefix else "")
    return (
        *(("diag", "list") if structure else ("list",)),
        uri,
        "-o",
        output,
        "--report",
        report,
        "--region",
        request.region,
        *((), ("--anonymous",))[not request.signed],
        "--workers",
        _concurrency(request),
        # Path-style by default with a custom endpoint, as the Java capsule's
        # --endpoint-url gets (`go/internal/source/s3/register.go:73`).
        *(("--endpoint", request.endpoint_url) if request.endpoint_url else ()),
        *_bool_option(request, "limiter", "--limiter"),
        *_bool_option(request, "seed", "--seed"),
        *structure,
        *_size_option(request, "row_budget", "--row-budget"),
        *_size_option(request, "spill_max", "--spill-max"),
        *_duration_option(request, "max_duration", "--max-duration"),
        *_size_option(request, "part_size", "--part-size"),
        *_duration_option(request, "part_interval", "--part-interval"),
    )


RESUME_FROZEN_KEYS = frozenset({"seed", "structure", "part_size"})
"""Config a resume may not restate: the seed and structure policy and the part
size are part of the recorded command and its resume identity, and ``swath
resume`` accepts only the runtime and operational flags (``go/docs/cli.md:29-41``,
``go/cmd/swath/main.go:319-328`` @7b05605), so a case stating one on the resume
mode is refused rather than silently run at the journal's value."""


def _resume_tail(request: CommandRequest, dataset: str, report: str) -> tuple[str, ...]:
    """``swath resume DIR``: the journal replays the recorded command, with overrides.

    ``swath resume`` takes the runtime flags (``--workers``, ``--row-budget``,
    ``--spill-max``, ``--part-interval``, ``--report``, ``--max-duration`` and
    others) and the operational ones (``--anonymous``, ``--limiter`` and others)
    again, the last value winning; anything else is a usage error. So every
    stated key of that kind is rendered, and the signing choice always is
    (``--anonymous`` or ``--anonymous=false``, which undoes a recorded one), so
    the resumed step runs at what the case says rather than at whatever the
    journal recorded. A frozen key is refused (:data:`RESUME_FROZEN_KEYS`). The
    source, endpoint and region are frozen in the journal's identity and cannot
    be restated; the port's identity check refuses a resume that would resolve
    another route. The report goes to the same path the list step used; the
    killed step's report must therefore be copied out before the resume starts,
    since a resume that dies before writing its own would leave the earlier one
    there.
    """
    frozen = sorted(RESUME_FROZEN_KEYS & set(request.config))
    if frozen:
        raise CommandAdapterError(
            f"{TOOL} mode {request.mode!r} cannot restate {', '.join(frozen)}: "
            "it is frozen in the journal's recorded command"
        )
    return (
        "resume",
        dataset,
        "--report",
        report,
        "--workers",
        _concurrency(request),
        "--anonymous=false" if request.signed else "--anonymous",
        *_bool_option(request, "limiter", "--limiter"),
        *_size_option(request, "row_budget", "--row-budget"),
        *_size_option(request, "spill_max", "--spill-max"),
        *_duration_option(request, "max_duration", "--max-duration"),
        *_duration_option(request, "part_interval", "--part-interval"),
    )


def build_command(request: CommandRequest) -> tuple[str, ...]:
    if request.mode not in MODES:
        raise CommandAdapterError(f"unknown mode: {request.mode}")
    sink = _sink(request)
    report = f"{sink}/{REPORT_NAME}"
    if request.mode == RESUME_MODE:
        return *SWATH_GO.argv, *_resume_tail(request, f"{sink}/{DATASET_NAME}", report)
    output = f"{sink}/{TEXT_MODES.get(request.mode, DATASET_NAME)}"
    return *SWATH_GO.argv, *_list_tail(request, output, report)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="swath-go command adapter"))
