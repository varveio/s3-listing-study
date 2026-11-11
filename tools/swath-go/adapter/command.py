#!/usr/bin/env python3
"""Compile Swath (Go port) listing parameters into exact in-image argv.

The subject is the Go port of Swath at the pinned source revision in
``build/image.json``: one static binary that lists an S3 bucket into a globally
key-sorted Parquet dataset (a swath dataset in the SeekParquet format:
``data/part-NNNNN.parquet`` plus ``manifest.json``, the commit) or into one TSV
file of the dataset's columns. Every flag rendered here is read from the public
flag set at that revision (``go/cmd/swath/flags.go`` and the provider flag
groups, with the help pages ``go/docs/commands/swath-list.md`` and
``swath-resume.md`` generated from them), not from the Java capsule in
``tools/swath/``: the two CLIs share a name and little else, and the adapters
share their shape and nothing else.
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

PINNED_REVISION = "8c8f870"
"""The source revision every ``source@`` provenance below is read at: an
unreleased commit on upstream's main, 8c8f870801c700f22413581f35683096782329a0;
``build/image.json`` pins the same one."""

CONCURRENCY = Ceiling(64, f"source@{PINNED_REVISION}")
"""The port's own width when unsilenced: ``listing.DefaultWorkers = 64``
(``go/internal/listing/listing.go:276-279``, applied when ``--workers`` is
unset at ``:311-313`` for a provider that states no default of its own, as S3
states none, ``:338-345`` @8c8f870), "whatever the CPU count". The flag has
no upper bound: it refuses only a count below 1
(``go/cmd/swath/flags.go:127``, ``go/internal/source/flags.go:291-308``).

A ceiling, as the Java capsule's AIMD 64 is, because ``--workers N`` sets both
the listing goroutines and the maximum of the request limiter
(``go/internal/source/objhttp/admission.go:25-34``,
``go/internal/source/objhttp/limiter.go:70-80,127-135``): with the adaptive
limit on (the default, ``go/internal/source/keyrange/flags.go:61-69``) the
limit starts at N, is cut by x0.7 when at least 15 % of a sliding window's
answers are throttles, and grows back one permit per second while under
7.5 % or after 10 s without one (``limiter.go:13-67``), so the achieved width
is a fact about the run and belongs in evidence. Unlike the Java engine the
limit starts at N rather than ``min(4, N)``; ``--adaptive-limit-slow-start``
is the opt-in Java ramp, a diagnostic flag, and is not rendered here.
``adaptive_limit: false`` removes the limiter altogether: the N workers still
wait on one page each, but seed listings and structure probes run beside them
(``keyrange/flags.go:62-67,76-78``), so N no longer bounds the requests in
flight.

What a campaign asks for is plan content, never a number this capsule decides.
"""

FIELDS = ("key", "size", "etag", "mtime", "storage_class")
"""All five, in every mode: the S3 schema ``swath/s3-objects/v1`` carries them as
columns (``go/docs/dataset.md:158-161``), and the TSV file carries the same
columns under the same names (``go/docs/output.md:35-43``)."""

AXES = {"concurrency": CONCURRENCY}
"""No ``heap_percent``: a Go binary has no managed heap the harness's share is
rendered into, and its sort buffer is the separate ``--sort-buffer`` flag."""

LISTING = "listing"
"""The logical name every mode publishes its listing under."""

DATASET_NAME = "listing"
"""The dataset directory under the engine's sink: ``-o <sink>/listing``. A name
without a text or data-file extension is the Parquet dataset
(``go/cmd/swath/output.go:35-57``)."""

TSV_NAME = "listing.tsv"
TSV_ZSTD_NAME = "listing.tsv.zst"
"""The one text file a TSV mode writes: ``-o`` decides the format and the
compression by its name (``output.go:17-33``). The port has no directory of
text parts; the Java capsule's ``recursive-tsv-zstd`` wrote one, so the two
capsules' products of that mode differ in shape."""

REPORT_NAME = "swath-go-report.json"
"""Where ``--report`` writes the run report, beside the product in the sink.

Upstream refuses a report path on the dataset directory's own entries and
accepts one of its own name inside it (``go/docs/reference/run-report.md:26-31``);
the capsule keeps it beside the product, outside ``listing/``, so the dataset
directory holds only what the port publishes there. It is a different document
from the Java capsule's ``_swath_summary.json`` and deliberately does not borrow
that name; the harness keeps it on every attempt because ``RUN_REPORT_NAMES``
lists it."""

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
        "adaptive_limit",
        "cpu_profile",
        "encoders",
        "max_duration",
        "part_interval",
        "part_size",
        "seed",
        "sort_buffer",
        "spill_budget",
        "structure",
    }
)
"""Tool-specific knobs a plan may state under ``config:``; anything else is refused.

``max_duration`` matters operationally: upstream says a hosted runner needs a
finite ``--max-duration`` on every attempt and its own deadline above it plus
20 s (``go/docs/production.md:20-21``). The adapter cannot see the harness's
``timeout_s``, so a plan states the value below ``timeout_s - 20 s`` and that
relation is the plan's to keep."""

PARQUET_ONLY_KEYS = frozenset({"part_interval", "part_size"})
"""Part controls: a text run writes one file and has no parts, so a TSV mode
refuses them rather than rendering a flag with no effect."""

DURATION_RE = re.compile(r"\A(?:0|(?:[0-9]+(?:\.[0-9]+)?(?:ns|us|µs|ms|s|m|h|d|w))+)\Z")
"""Go's ``time.ParseDuration`` grammar with the port's ``d`` and ``w`` units,
without a sign: the duration flags refuse a negative value, and 0 keeps each
flag's documented meaning, off for ``--max-duration`` and ``--part-interval``
(``go/internal/source/flags.go:200-216,260-270``)."""

SIZE_RE = re.compile(r"\A[0-9]+(?:\.[0-9]+)?(?:b|k|kb|kib|m|mb|mib|g|gb|gib|t|tb|tib)?\Z", re.I)
"""``filter.ParseSize`` (``go/internal/filter/filter.go:346-371`` @8c8f870), the
port's one size syntax: a decimal number and an optional unit in any case,
coming to a whole number of bytes, which only the binary can check. ``k``,
``m``, ``g``, ``t`` and the ``-ib`` units are binary; ``kb``, ``mb``, ``gb`` and
``tb`` are decimal at this pin (they were binary at the capsule's earlier
pins), so a plan states sizes in ``kib``/``mib``/``gib``: lowercase, since a
config value becomes part of the case label, which refuses capitals."""

CPU_PROFILE_NAME = "swath-go-cpu.pprof"
"""Where ``cpu_profile: on`` has the port write its Go CPU profile: the sink's
top level, beside the run report and outside the dataset directory, where the
port's side-path check accepts a profile (``go/cmd/swath/sidepaths.go:28-57``)
and ``PROFILE_NAMES`` in ``benchmark/src/benchmark/measure.py`` keeps it on
every attempt, retained products or not, up to that module's size bound."""

POSITIVE_SIZE_KEYS = frozenset({"part_size", "sort_buffer"})
"""Sizes whose flag refuses 0 (``(*size).Set``, ``go/cmd/swath/main.go:1221-1231``);
``--spill-budget`` is a ``zsize`` and takes 0 for its derived default
(``main.go:1201-1213``, ``go/internal/listing/listing.go:171``). The binary
also refuses a sort buffer below 128 KiB (``listing.go:284-286,332-334``)."""

STRUCTURE_VALUES = ("auto", "on", "off")
"""``--structure``'s values (``keyrange/flags.go:140-152``). It is a diagnostic
flag, registered only on ``swath diag list`` (``flags.go:79-87``), so a case
stating it runs ``diag list``; without it the plain ``list`` runs at the
default, ``auto``. The same holds for the seed switch, ``--no-seed``
(``flags.go:82-83``): ``seed: false`` runs ``diag list --no-seed``, and
``seed: true`` renders nothing, since the plain ``list`` has no seed switch and
always seeds."""


def _concurrency(request: CommandRequest) -> str:
    value = request.config.get("concurrency", CONCURRENCY.value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CommandAdapterError(f"{TOOL} concurrency must be a positive integer; got: {value!r}")
    return str(value)


def _bool_value(request: CommandRequest, key: str) -> bool | None:
    value = request.config.get(key)
    if value is not None and not isinstance(value, bool):
        raise CommandAdapterError(f"{TOOL} {key} must be true or false; got: {value!r}")
    return value


def _adaptive_limit(request: CommandRequest) -> tuple[str, ...]:
    """``--adaptive-limit`` or ``--no-adaptive-limit``; absent renders nothing.

    Both spellings are registered on every command: ``--no-adaptive-limit`` by
    the range engine's flag group and ``--adaptive-limit`` as its undo spelling,
    hidden except on ``swath resume`` (``go/cmd/swath/flags.go:253-267``).
    """
    value = _bool_value(request, "adaptive_limit")
    if value is None:
        return ()
    return ("--adaptive-limit" if value else "--no-adaptive-limit",)


def _encoders(request: CommandRequest) -> tuple[str, ...]:
    """``--encoders N``: the Parquet encoders, or a ``.zst`` file's compression jobs.

    Registered on ``list`` and ``diag list`` (``go/cmd/swath/flags.go:130-132``)
    and accepted again by ``swath resume`` (``go/cmd/swath/main.go:562``); a
    count below 1 is refused by the flag (``go/internal/source/flags.go:291,301-306``).
    Absent, the port takes ``min(GOMAXPROCS, what the sort buffer admits)`` and
    may raise a Parquet run's maximum after listing; an explicit count is kept
    as the maximum too (``go/internal/listing/listing.go:315-321``). A TSV file's
    rows are formatted by as many goroutines (``listing.go:679``), so every mode
    takes it.
    """
    value = request.config.get("encoders")
    if value is None:
        return ()
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CommandAdapterError(f"{TOOL} encoders must be a positive integer; got: {value!r}")
    return "--encoders", str(value)


def _no_seed(request: CommandRequest) -> tuple[str, ...]:
    """``--no-seed`` for ``seed: false``; nothing for true or absent (always seeded)."""
    return ("--no-seed",) if _bool_value(request, "seed") is False else ()


def _duration_option(request: CommandRequest, key: str, flag: str) -> tuple[str, ...]:
    value = request.config.get(key)
    if value is None:
        return ()
    if not isinstance(value, str) or DURATION_RE.fullmatch(value) is None:
        raise CommandAdapterError(
            f"{TOOL} {key} must be a Go duration such as '2h'; got: {value!r}"
        )
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
    # Written with '=': the bare flag means on, so a separate word would be
    # read as the next argument (`flags.go:84-87`).
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
    no_seed = _no_seed(request)
    uri = f"s3://{request.bucket}" + (f"/{request.prefix}" if request.prefix else "")
    return (
        *(("diag", "list") if structure or no_seed else ("list",)),
        uri,
        "-o",
        output,
        "--report",
        report,
        "--s3-region",
        request.region,
        *((), ("--anonymous",))[not request.signed],
        "--workers",
        _concurrency(request),
        # Path-style by default with a custom endpoint, as the Java capsule's
        # --endpoint-url gets (`go/internal/source/s3/register.go:113-115`).
        *(("--endpoint-url", request.endpoint_url) if request.endpoint_url else ()),
        *_adaptive_limit(request),
        *no_seed,
        *structure,
        *_encoders(request),
        *_size_option(request, "sort_buffer", "--sort-buffer"),
        *_size_option(request, "spill_budget", "--spill-budget"),
        *_duration_option(request, "max_duration", "--max-duration"),
        *_size_option(request, "part_size", "--part-size"),
        *_duration_option(request, "part_interval", "--part-interval"),
    )


RESUME_FROZEN_KEYS = frozenset({"seed", "structure", "part_size"})
"""Config a resume may not restate: the seed and structure policy and the part
size are part of the recorded command and its resume identity, and ``swath
resume`` accepts only the runtime and operational flags
(``go/docs/commands/swath-resume.md:38-47``, ``go/cmd/swath/main.go:560-570``
@8c8f870), so a case stating one on the resume mode is refused rather than
silently run at the journal's value."""


def _resume_tail(request: CommandRequest, dataset: str, report: str) -> tuple[str, ...]:
    """``swath resume DIR``: the journal replays the recorded command, with overrides.

    ``swath resume`` takes the runtime flags (``--workers``, ``--sort-buffer``,
    ``--spill-budget``, ``--part-interval``, ``--report``, ``--max-duration`` and
    others) and the operational ones (``--anonymous``, ``--no-adaptive-limit``
    and others) again, the last value winning, with each switch's other
    spelling undoing a recorded one; anything else is a usage error. So every
    stated key of that kind is rendered, and the signing choice always is
    (``--anonymous`` or ``--no-anonymous``, which undoes a recorded one), so
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
        "--no-anonymous" if request.signed else "--anonymous",
        *_adaptive_limit(request),
        *_encoders(request),
        *_size_option(request, "sort_buffer", "--sort-buffer"),
        *_size_option(request, "spill_budget", "--spill-budget"),
        *_duration_option(request, "max_duration", "--max-duration"),
        *_duration_option(request, "part_interval", "--part-interval"),
    )


def _cpu_profile(request: CommandRequest) -> bool:
    """``cpu_profile``: ``on`` or ``off`` (absent is off). A string, as the Java
    capsule's ``engine_readahead`` is, because a plan's ``config`` carries only
    strings and integers."""
    value = request.config.get("cpu_profile", "off")
    if value not in ("on", "off"):
        raise CommandAdapterError(f"{TOOL} cpu_profile must be 'on' or 'off'; got: {value!r}")
    return value == "on"


def build_env(request: CommandRequest) -> dict[str, str]:
    """``SWATH_CPUPROFILE=SINK/swath-go-cpu.pprof`` for ``cpu_profile: on``; else nothing.

    The pin's one profiling hook is this environment variable (and
    ``SWATH_MEMPROFILE``, not exposed here): there is no flag. The command reads
    it with the report's side-path check (``go/cmd/swath/sidepaths.go:28-57``),
    starts ``pprof.StartCPUProfile`` before the listing begins and stops it when
    ``listing.Run`` returns, before the outcome is classified and the report
    written (``go/cmd/swath/main.go:849-852,913-917,1154-1167``); upstream
    documents it in ``go/docs/commands/swath.md:213``. It is a development
    hook: Go's sampling profiler at its default 100 Hz, and a profiled wall is
    diagnostic, never a timing row.
    """
    if not _cpu_profile(request):
        return {}
    return {"SWATH_CPUPROFILE": f"{_sink(request)}/{CPU_PROFILE_NAME}"}


def build_command(request: CommandRequest) -> tuple[str, ...]:
    if request.mode not in MODES:
        raise CommandAdapterError(f"unknown mode: {request.mode}")
    sink = _sink(request)
    report = f"{sink}/{REPORT_NAME}"
    # The profile rides in the environment (build_env); a malformed value is
    # refused here too, so compiling a case is enough to reject it.
    _cpu_profile(request)
    if request.mode == RESUME_MODE:
        return *SWATH_GO.argv, *_resume_tail(request, f"{sink}/{DATASET_NAME}", report)
    output = f"{sink}/{TEXT_MODES.get(request.mode, DATASET_NAME)}"
    return *SWATH_GO.argv, *_list_tail(request, output, report)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="swath-go command adapter"))
