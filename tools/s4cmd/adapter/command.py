#!/usr/bin/env python3
"""Compile s4cmd listing parameters into exact in-image argv."""

from benchmark.runtime.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    Default,
    Executable,
    Mode,
    command_adapter_main,
)

TOOL = "s4cmd"
S4CMD = Executable("s4cmd", ("/usr/local/bin/s4cmd",))
EXECUTABLES = (S4CMD,)
SUPPORTS_UNSIGNED = False
"""No unsigned request path; it signs with the credential in the environment."""

CONCURRENCY = Default(32, "source@80059bf")
"""s4cmd's own ``-c/--num-threads`` default when unsilenced: ``cpu_count() * 4``
(``s4cmd.py:121,1859``), a formula rather than a portable constant.

``32`` is the value that formula produces on the study's own 8-core smoke
runner (``docs/mechanism.md`` § "Modes and tunables"; also `NOTES.md`), the only
instantiation this capsule has a committed receipt for. It is not a subject
fact independent of machine shape: a differently-sized runner yields a
different number, and this capsule has no visibility into the container's
actual vCPU allocation to compute the honest value it would run at.

The historical ``CONCURRENCY_RANGE = (1, 8)`` guard that used to clamp this
render is dropped: it was arbitrary by accident (bracketing a 32-thread
default against an 8-core runner where the native default is exactly 32, not
a fact about s4cmd) and would reject this very default.
"""

AXES = {"concurrency": CONCURRENCY}

TEXT_FIELDS = ("key", "size")
"""``normalize.py``'s QUERY only ever emits ``key`` and ``size``; etag, mtime
and storage_class are never printed by ``ls`` and are always NULL."""

MODES = {
    "recursive": Mode(product="text", fields=TEXT_FIELDS, axes=AXES, executable=S4CMD.name),
    "shallow": Mode(product="text", fields=TEXT_FIELDS, axes=AXES, executable=S4CMD.name),
    "show-directory": Mode(
        product="text", fields=TEXT_FIELDS, axes=AXES, executable=S4CMD.name
    ),
    # du emits an aggregate size only -- normalize.py is a documented no-op,
    # zero per-key rows, so it can never be ranked against a per-key listing.
    "du": Mode(
        product="text",
        fields=("size",),
        axes=AXES,
        purpose_ceiling="diagnostic",
        executable=S4CMD.name,
    ),
}


def _concurrency(request: CommandRequest) -> str:
    """Render the asked-for thread count; declared in :data:`MODES`, never pinned here."""
    value = request.config.get("concurrency", CONCURRENCY.value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CommandAdapterError(f"{TOOL} concurrency must be a positive integer; got: {value!r}")
    return str(value)


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    threads_arg = _concurrency(request)
    url = f"s3://{request.bucket}/{request.prefix}"
    commands = {
        "recursive": ("ls", "-r", "-c", threads_arg, url),
        "shallow": ("ls", "-c", threads_arg, url),
        "show-directory": ("ls", "-d", "-c", threads_arg, url),
        "du": ("du", "-r", "-c", threads_arg, url),
    }
    try:
        return commands[request.mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {request.mode}") from None


def build_command(request: CommandRequest) -> tuple[str, ...]:
    return *S4CMD.argv, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s4cmd command adapter"))
