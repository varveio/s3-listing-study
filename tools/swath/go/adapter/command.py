#!/usr/bin/env python3
"""Compile Swath (Go port) listing parameters into exact in-image argv.

The subject is the Go port of Swath at the pinned source revision in
``build/image.json``: one static binary whose only S3 output is a globally
key-sorted Parquet dataset (format 2: ``data/part-NNNNN.parquet`` plus
``manifest.json``, the commit). Every flag rendered here is read from the public
flag set at that revision (``go/docs/cli.md``, generated from the flag set and
test-gated upstream), not from the Java capsule beside this one: the two CLIs
share a name and nothing else.
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

PINNED_REVISION = "63a4256"
"""The source revision every ``source@`` provenance below is read at. It is the
head of upstream's unmerged ``spike/go-local-lister`` branch and moves to the
merge commit when that branch merges; ``build/image.json`` pins the same one."""

CONCURRENCY = Ceiling(64, f"source@{PINNED_REVISION}")
"""The port's own width when unsilenced: ``listing.DefaultWorkers = 64``
(``go/internal/listing/listing.go:146-149``, applied when ``--workers`` is 0 at
``:176-178`` @63a4256), "whatever the CPU count".

A ceiling, as the Java capsule's AIMD 64 is, because ``--workers N`` sets both
the listing goroutines and the maximum of the request limiter
(``go/internal/source/objhttp/limiter.go:31-36,74-82``): with the limiter on
(the default, ``go/internal/source/keyrange/flags.go:54-60``) the limit starts
at N, falls by x0.7 on each throttle answer and grows back one permit per
second after 10 s without one (``limiter.go:13-28``), so the achieved width is
a fact about the run and belongs in evidence. Unlike the Java engine the limit
starts at N rather than ``min(4, N)``; ``--limiter-slow-start`` is the opt-in
Java ramp and is not rendered here. ``limiter: false`` keeps a fixed N requests
in flight, which N still bounds.

What a campaign asks for is plan content, never a number this capsule decides.
"""

FIELDS = ("key", "size", "etag", "mtime", "storage_class")
"""The format-2 S3 schema ``swath/s3-objects/v1`` carries all five (nine
columns: ``go/docs/internals/parquet-output.md`` § Schemas)."""

AXES = {"concurrency": CONCURRENCY}
"""No ``heap_percent``: a Go binary has no managed heap the harness's share is
rendered into, and its row budget is the separate ``--memory`` flag."""

LISTING = "listing"
"""The logical name both modes publish their listing under."""

DATASET_NAME = "listing"
"""The dataset directory under the engine's sink: ``-o <sink>/listing``."""

DATASET = {LISTING: DATASET_NAME}

REPORT_NAME = "swath-go-report.json"
"""Where ``--report`` writes the run report, beside the dataset in the sink.

Upstream asks for the report path to sit outside the output directory
(``go/docs/cli.md`` § Exit status and outcome), so it is not under
``listing/``. It is a different document from the Java capsule's
``_swath_summary.json`` and deliberately does not borrow that name: the worker
keeps only the run reports named in the harness's ``RUN_REPORT_NAMES``, so
retaining this one on a non-retained attempt is a harness change, not a
capsule one."""

LIST_MODE = "recursive-parquet-sorted"
RESUME_MODE = "resume-parquet-sorted"

MODES = {
    LIST_MODE: Mode(
        product="parquet-sorted",
        fields=FIELDS,
        axes=AXES,
        executable=SWATH_GO.name,
        artifacts=DATASET,
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
        artifacts=DATASET,
        product_artifact=LISTING,
        product_channel="dataset",
    ),
}

CONFIG_KEYS = frozenset(
    {
        "limiter",
        "max_duration",
        "part_interval",
        "part_size",
        "request_payer",
        "seed",
    }
)
"""Tool-specific knobs a plan may state under ``config:``; anything else is refused.

``max_duration`` matters operationally: upstream says a hosted runner needs a
finite ``--max-duration`` on every attempt and its own deadline above it plus
20 s (``go/docs/cli.md`` § What a hosted runner needs). The adapter cannot see
the harness's ``timeout_s``, so a plan states the value and its relation to the
row's timeout is the plan's to keep."""

DURATION_RE = re.compile(r"\A(?:0|(?:[0-9]+(?:\.[0-9]+)?(?:ns|us|µs|ms|s|m|h))+)\Z")
"""Go's ``time.ParseDuration`` grammar, without a sign: the duration flags take
non-negative values and 0 means off."""

SIZE_RE = re.compile(r"\A[1-9][0-9]*(?:KiB|MiB|GiB|KB|MB|GB|B)?\Z")
"""``(*size).Set`` at ``go/cmd/swath/main.go:607-624`` @63a4256: a positive
integer with an optional binary or decimal suffix."""


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


def _token_option(
    request: CommandRequest, key: str, flag: str, pattern: re.Pattern[str], what: str
) -> tuple[str, ...]:
    value = request.config.get(key)
    if value is None:
        return ()
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise CommandAdapterError(f"{TOOL} {key} must be {what}; got: {value!r}")
    return flag, value


def _sink_paths(request: CommandRequest) -> tuple[str, str]:
    if not request.sink_dir:
        raise CommandAdapterError(
            f"mode {request.mode!r} writes a directory dataset and requires a sink directory"
        )
    sink = request.sink_dir.rstrip("/")
    return f"{sink}/{DATASET_NAME}", f"{sink}/{REPORT_NAME}"


def _list_tail(request: CommandRequest, dataset: str, report: str) -> tuple[str, ...]:
    uri = f"s3://{request.bucket}" + (f"/{request.prefix}" if request.prefix else "")
    return (
        "list",
        uri,
        "-o",
        dataset,
        "--report",
        report,
        "--region",
        request.region,
        *((), ("--anonymous",))[not request.signed],
        "--workers",
        _concurrency(request),
        *(("--endpoint", request.endpoint_url) if request.endpoint_url else ()),
        *_bool_option(request, "limiter", "--limiter"),
        *_bool_option(request, "seed", "--seed"),
        *_bool_option(request, "request_payer", "--request-payer"),
        *_token_option(
            request, "part_interval", "--part-interval", DURATION_RE, "a Go duration such as '30s'"
        ),
        *_token_option(
            request, "max_duration", "--max-duration", DURATION_RE, "a Go duration such as '2h'"
        ),
        *_token_option(
            request, "part_size", "--part-size", SIZE_RE, "a positive size such as '64MiB'"
        ),
    )


def _resume_tail(request: CommandRequest, dataset: str, report: str) -> tuple[str, ...]:
    """``swath resume DIR --report PATH``: the journal replays the recorded command.

    Every other list flag is refused on a resume except the runtime and
    operational ones (``go/cmd/swath/main.go:289-300``), and the recorded values
    already carry what the list step rendered from this same config, so none is
    restated. The report goes to the same path the list step used; the killed
    step's report must therefore be copied out before the resume starts, since a
    resume that dies before writing its own would leave the earlier one there.
    """
    _concurrency(request)
    return ("resume", dataset, "--report", report)


def build_command(request: CommandRequest) -> tuple[str, ...]:
    if request.mode not in MODES:
        raise CommandAdapterError(f"unknown mode: {request.mode}")
    dataset, report = _sink_paths(request)
    if request.mode == RESUME_MODE:
        return *SWATH_GO.argv, *_resume_tail(request, dataset, report)
    return *SWATH_GO.argv, *_list_tail(request, dataset, report)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="swath-go command adapter"))
