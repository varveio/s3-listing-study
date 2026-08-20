#!/usr/bin/env python3
"""Compile s3p listing parameters into exact in-image argv."""

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

TOOL = "s3p"
S3P = Executable("s3p", ("/usr/local/bin/s3p",))
EXECUTABLES = (S3P,)
SUPPORTS_UNSIGNED = False
"""No unsigned request path; it signs with the credential in the environment."""

CONCURRENCY = Ceiling(100, "source@5a23b22e")
"""s3p's LIFO worker pool default, `S3Comprehensions.caf:246` (`PromiseWorkerPool`
at `PromiseWorkerPool.caf:26-48`), exposed as `--list-concurrency`
(`S3PCli.caf:94`).

A ceiling, not a plain default: the pool caps simultaneous LIST requests, while
bisection grows the request tree from one root node, so the achieved width
starts low and only reaches the cap once enough range nodes are open at once --
a fact about the run, not the flag, so it belongs in evidence, not `config`.

**Receipt owed.** `5a23b22e` is v3.6.0 source, and the newest git-tagged
revision there is -- `master` HEAD is an untagged 3.6.1 and npm's `latest` is
3.7.2, which is what `build/image.json` actually installs (`docs/mechanism.md`,
`research/report.md`). The 100 stands on a reading of a revision the built
image was never cut from, the same gap swath's 64 carries against its own jar.

What a campaign asks s3p for is plan content -- the historical cap of 8 is a
`concurrency` row field in `benchmark/plans/buckets/*.yaml`, where it is visible
and reviewable, not a number this capsule decides.
"""

TEXT_FIELDS = ("key", "size", "etag", "mtime", "storage_class")
KEY_ONLY = ("key",)
"""ls and ls-long normalize to the key alone; ls-long's rest is space-joined and
human-rounded, so only the key survives."""

AXES = {"concurrency": CONCURRENCY, "heap_percent": Fixed(HEAP_PERCENT)}
"""s3p is a V8 process, so every mode feels the heap share; every command
funnels through the same bisection LIST, so every mode takes the concurrency
flag."""

LISTING = "listing"
SUMMARY = "summary"
"""The logical names a mode publishes its product under: a per-key listing, or
the aggregate report that is not one."""

TEXT = {LISTING: "listing.txt"}
SUMMARY_TEXT = {SUMMARY: "summary.txt"}
"""s3p prints and takes no output destination, so the worker lands fd 1 in the
declared file. `summarize` publishes under its own name because what it holds is
an aggregate report with no per-object records in it at all."""

MODES = {
    "ls": Mode(
        product="text",
        fields=KEY_ONLY,
        axes=AXES,
        executable=S3P.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    # Lossy: size is human-rounded and the line is space-joined, so only the
    # key survives normalization. Not a verification mode.
    "ls-long": Mode(
        product="text",
        fields=KEY_ONLY,
        axes=AXES,
        purpose_ceiling="diagnostic",
        executable=S3P.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "ls-raw": Mode(
        product="text",
        fields=TEXT_FIELDS,
        axes=AXES,
        executable=S3P.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    # An aggregate report with no per-object records at all -- honest as the
    # `ls - summarize` emit-cost instrument, dishonest as a leaderboard row.
    "summarize": Mode(
        product="text",
        fields=KEY_ONLY,
        axes=AXES,
        purpose_ceiling="diagnostic",
        executable=S3P.name,
        artifacts=SUMMARY_TEXT,
        product_artifact=SUMMARY,
    ),
}


def build_env(request: CommandRequest) -> dict[str, str]:
    """Render the harness's heap share into what V8 reads.

    ``--max-old-space-size`` is an absolute MiB, not a percentage, so it needs
    the container ceiling the harness resolved. Where there is none, sizing a
    heap from nothing would be a guess that can raise SIGKILL risk rather than
    lower it, so this refuses instead.
    """
    if request.visible_memory_gb is None:
        raise CommandAdapterError(
            f"{TOOL}: no visible memory ceiling to size --max-old-space-size from"
        )
    heap_mib = int(request.visible_memory_gb * 1024 * request.heap_percent / 100)
    env = {"NODE_OPTIONS": f"--max-old-space-size={heap_mib}"}
    if request.endpoint_url:
        env["S3_ENDPOINT"] = request.endpoint_url
    return env


def _concurrency(request: CommandRequest) -> str:
    """Render the asked-for cap; declared in :data:`MODES`, never pinned here."""
    value = request.config.get("concurrency", CONCURRENCY.value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CommandAdapterError(f"{TOOL} concurrency must be a positive integer; got: {value!r}")
    return str(value)


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    heads = {
        "ls": ("ls",),
        "ls-long": ("ls", "--long"),
        "ls-raw": ("ls", "--raw"),
        "summarize": ("summarize",),
    }
    try:
        head = heads[request.mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {request.mode}") from None
    common = [
        "--bucket",
        request.bucket,
        "--region",
        request.region,
        "--list-concurrency",
        _concurrency(request),
    ]
    if request.prefix:
        common.extend(("--prefix", request.prefix))
    return *head, *common


def build_command(request: CommandRequest) -> tuple[str, ...]:
    return *S3P.argv, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s3p command adapter"))
