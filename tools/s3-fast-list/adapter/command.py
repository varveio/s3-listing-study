#!/usr/bin/env python3
"""Compile s3-fast-list parameters into exact standalone argv."""

from pathlib import Path

from benchmark.runtime.command_adapter import (
    Ceiling,
    CommandAdapterError,
    CommandRequest,
    Executable,
    Inert,
    Mode,
    Stated,
    command_adapter_main,
)

TOOL = "s3-fast-list"
S3_FAST_LIST = Executable("s3-fast-list", ("/usr/bin/s3-fast-list",))
KS_TOOL = Executable("ks-tool", ("/usr/bin/ks-tool",))
"""The hints generator, the second crate of the same Cargo workspace.

``build/Dockerfile:45`` copies it beside the lister, so it is in the image the
receipts were taken from — but ``build/image.json`` registers one executable and
the loader cross-checks that one only, so nothing outside this line binds
``/usr/bin/ks-tool`` to a build. No committed receipt has ever run it.
"""
EXECUTABLES = (S3_FAST_LIST, KS_TOOL)
SUPPORTS_UNSIGNED = True
"""--no-sign-request lists anonymously. The signed path drops the flag and has
not been exercised by a committed run."""

SEGMENTS = Stated()
"""How many key ranges the cut points divide the bucket into.

The hinted path's *second* axis: the effective width of `list-hinted` is
``min(concurrency, N+1)``, so the segment count shapes the measurement even
though only `ks-split`'s argv carries it. `Stated` because upstream records no
default this capsule could cite, and a preparation that quietly chose one would
freeze the whole sweep at it — the plan states it, it enters both identities,
and a sweep builds one hints file per value.
"""

CONCURRENCY = Ceiling(100, "source@6c72f59")
"""``-c/--concurrency`` as the subject runs it unsilenced (``main.rs:33``).

A ceiling because the flag caps in-flight range tasks rather than creating them:
``flat_reactor_task`` pulls from ``hints.next()`` only while ``joins.len() <
flat_concurrency`` (``tasks_s3.rs:36``), so the effective width is
``min(c, N+1)`` for an N-cut-point hints file and any ``c <= N+1`` lists the
same file identically. What a campaign asks for is plan content.
"""

FIELDS = ("key", "size", "etag", "mtime")
"""What ``normalize.py`` can populate: the Arrow schema carries Key, Size,
LastModified and ETag, and no StorageClass at all."""

CUT_POINT_FIELDS = ("key",)
"""CHAFE. A hints file is one key-range cut point per line — a key *prefix*,
carrying no object and populating no listing column. The contract's `fields`
vocabulary has no word for that, so this declares the nearest honest thing and
``purpose_ceiling="preparation"`` keeps the mode out of every listing table.
Read as "key-shaped", not as "emits keys".
"""

MODES = {
    # `-c` is accepted and statically inert here: with no hints file N=0, so
    # there is exactly one range pair and one flat-list task (`main.rs:191-218`).
    "list": Mode(
        product="parquet",
        fields=FIELDS,
        axes={"concurrency": Inert()},
        executable=S3_FAST_LIST.name,
    ),
    "ks-split": Mode(
        product="text",
        fields=CUT_POINT_FIELDS,
        axes={"segments": SEGMENTS},
        purpose_ceiling="preparation",
        executable=KS_TOOL.name,
    ),
    # `segments` is declared here too, though this argv never carries it: the
    # cut count is what the run listed under, so it belongs in the measurement
    # identity — and the inline `ks-split` exec inherits the stated value, which
    # is the argv that does carry it.
    "list-hinted": Mode(
        product="parquet",
        fields=FIELDS,
        axes={"concurrency": CONCURRENCY, "segments": SEGMENTS},
        executable=S3_FAST_LIST.name,
        inline="ks-split",
    ),
}

REQUIRES = {"list-hinted": ("list",)}
"""The hinted path needs a full listing first: only a `list` emits the `.ks` key
distribution the cut points are computed from. That bootstrap listing is not
overhead — it *is* the unhinted arm, which is why it keeps a `measurement`
ceiling and only the split carries `preparation`.

`ks-split` is not a link here. Cutting an existing `.ks` into ranges is a
sub-second local transform whose output one measurement consumes and nothing
else, so it runs as that measurement's inline setup exec (`MODES`, `inline`)
rather than buying a slot, a job and a VM of its own. It stays a declared mode:
it is what the inline exec runs, and a plan may still name it directly."""

KS_NAME = "keyspace.ks"
"""The key distribution a listing drops into the engine's sink, and the artifact
the hinted measurement's inline `ks-split` consumes. Written only when the engine
offers a sink: a listing with nowhere to publish it discards it, as every
committed receipt did."""

HINTS_NAME = "hints.input"
"""The cut points `ks-split` publishes into the sink it is given — the attempt's
own setup directory when it runs inline, an ordinary preparation sink when a plan
names the mode directly."""


def _sink_path(request: CommandRequest, name: str) -> str:
    if not request.sink_dir:
        raise CommandAdapterError(
            f"mode {request.mode!r} publishes {name} and requires a sink directory"
        )
    return f"{request.sink_dir.rstrip('/')}/{name}"


def _staged_artifact(request: CommandRequest, name: str) -> str:
    """Where the harness staged the artifact this mode reads.

    Refused rather than invented when empty: a path this adapter chose itself
    would name bytes the engine never staged, and one carried in ``config``
    would hash the path where the contract hashes the digest.
    """
    if not request.artifact_path:
        raise CommandAdapterError(
            f"{TOOL} mode {request.mode!r} consumes a staged {name} and requires an artifact path"
        )
    return request.artifact_path


def _concurrency(request: CommandRequest) -> str:
    value = request.config.get("concurrency", CONCURRENCY.value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CommandAdapterError(f"{TOOL} concurrency must be a positive integer; got: {value!r}")
    return str(value)


def _list_tail(request: CommandRequest, hints: tuple[str, ...]) -> tuple[str, ...]:
    # The `.ks` goes to /dev/null unless the engine offers somewhere to publish
    # it: with no `--output-ks-file` the tool writes one into its working
    # directory, which is output no attempt record could account for.
    ks = f"{request.sink_dir.rstrip('/')}/{KS_NAME}" if request.sink_dir else "/dev/null"
    argv = [
        *((), ("--no-sign-request",))[not request.signed],
        "--output-parquet-file",
        "/dev/stdout",
        "--output-ks-file",
        ks,
        *hints,
    ]
    if request.prefix:
        argv.extend(("--prefix", request.prefix))
    argv.extend(("list", "--region", request.region, "--bucket", request.bucket))
    return tuple(argv)


def _segments(request: CommandRequest) -> str:
    """How many segments the cut points divide the keyspace into.

    Stated by the plan and never defaulted here: upstream's own number is not
    recorded in anything this capsule can cite, and a preparation that quietly
    chose one would freeze the whole sweep at it.
    """
    value = request.config.get("segments")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CommandAdapterError(f"{TOOL} segments must be a positive integer; got: {value!r}")
    return str(value)


def _split_tail(request: CommandRequest) -> tuple[str, ...]:
    # Flag-letter trap: `-c` is the segment count on `split` (`--count`) and
    # the listing concurrency on the lister, so it is deliberately not the
    # `concurrency` axis. Spellings verified against `ks-tool split --help`
    # in the toolbox built at source@6c72f59: `-k/--ks`, `-c/--count`,
    # `-o/--output`, exactly as rendered here.
    return (
        "split",
        "-k",
        _staged_artifact(request, "key distribution"),
        "-c",
        _segments(request),
        "-o",
        _sink_path(request, HINTS_NAME),
    )


def build_command(request: CommandRequest) -> tuple[str, ...]:
    if request.mode == "list":
        return *S3_FAST_LIST.argv, *_list_tail(request, ())
    if request.mode == "list-hinted":
        hints = ("-c", _concurrency(request), "-k", _staged_artifact(request, "hints file"))
        return *S3_FAST_LIST.argv, *_list_tail(request, hints)
    if request.mode == "ks-split":
        return *KS_TOOL.argv, *_split_tail(request)
    raise CommandAdapterError(f"unknown mode: {request.mode}")


def _validate_hints(path: Path) -> None:
    """Refuse a hints file that digests cleanly and means nothing.

    ``ks-tool split`` emits ``last_prefix`` each time a running sum crosses
    ``total/count`` (``ks-tool/utils.rs:88-116``), and ``last_prefix`` starts
    empty — so both degeneracies are silent, and a run on either is not a slower
    hinted listing but a measurement of something else entirely.

    The gate the source note asks for has a third clause this signature cannot
    carry: a cut count far below the requested segment count. The requested count
    is config, and a validator is handed a path only.
    """
    lines = path.read_text(encoding="utf-8", errors="surrogateescape").split("\n")
    # A trailing newline is a terminator, not a cut point.
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        raise CommandAdapterError(f"{path}: hints file holds no cut point at all")
    if lines == [""]:
        # `split` can emit no more cut points than the `.ks` has prefixes, so a
        # flat namespace collapses to one empty line whatever `-c` asked for.
        raise CommandAdapterError(
            f"{path}: a single empty cut point is the flat-namespace collapse — two identical "
            f"full-range tasks"
        )
    if not lines[0]:
        # Pair 0 becomes ("", None) — a full-range serial listing running
        # alongside every real segment, so the hinted run can be the slower one.
        raise CommandAdapterError(
            f"{path}: first cut point is empty, so the hinted run would scan the full range "
            f"serially alongside every segment"
        )


VALIDATE_ARTIFACT = {"ks-split": _validate_hints}


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s3-fast-list command adapter"))
