#!/usr/bin/env python3
"""Compile rclone listing parameters into exact in-image argv."""

from benchmark.runtime.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    Default,
    Executable,
    Inert,
    Mode,
    command_adapter_main,
)

TOOL = "rclone"
RCLONE = Executable("rclone", ("/usr/local/bin/rclone",))
EXECUTABLES = (RCLONE,)
SUPPORTS_UNSIGNED = True
"""The s3 backend falls back to anonymous credentials when none are configured;
env_auth=true is what makes it read the credential the engine populated."""

CHECKERS = Default(8, "help")
"""`--checkers`: the concurrent-directory fan-out of a genuine hierarchical walk.

`in := make(chan listJob, ci.Checkers)` (`fs/walk/walk.go:380` @ 5bc93a2a7), so
the width is across directories and not within one. The 8 is upstream's own
default, receipted from the pinned binary's help output
(`receipts/smoke/_build/version.md`), which is why the provenance is `help`
rather than the `fs/config.go:60-61` reading that agrees with it.
"""

LSJSON_FIELDS = ("key", "size", "mtime", "storage_class")
"""What `normalize.py` selects out of an `lsjson` payload. etag is absent by
design: rclone's S3 listing path never surfaces the raw ETag."""

LSF_FIELDS = ("key", "size")
"""`lsf --format ps` prints path and size only, so it cannot be ranked against
the `lsjson` modes -- it would win partly by emitting less."""

WALK_AXES = {"concurrency": CHECKERS}
"""Only `--disable ListR` reaches `walk.Walk`, and only there does the pool exist."""

FLAT_AXES = {"concurrency": Inert()}
"""Every other mode: `--checkers` is accepted and bounds nothing.

`lsjson`/`lsf` call `walk.ListR` directly, which takes the flat backend `ListR`
-- one serial continuation chain -- whenever recursion is unbounded, never
consulting `--fast-list` (`fs/walk/walk.go:149-163` @ 5bc93a2a7); traced at zero
`delimiter=` requests in `receipts/smoke/_capability/debug`. `delimiter-shallow`
is inert for the other reason: bounded recursion does reach the walk, but a
single delimiter level descends into no directory, so the pool has one job.
"""

LISTING = "listing"
"""The logical name every mode here publishes its listing under."""

JSON = {LISTING: "listing.json"}
TEXT = {LISTING: "listing.txt"}
"""`lsjson` prints a JSON array and `lsf` prints separated columns, both on
stdout: rclone's listing subcommands take no output destination, so the worker
lands fd 1 in the declared file. The debug modes' `-vv --dump` goes to stderr and
leaves the product alone."""

MODES = {
    "recursive-fastlist": Mode(
        product="text",
        fields=LSJSON_FIELDS,
        axes=FLAT_AXES,
        artifacts=JSON,
        product_artifact=LISTING,
    ),
    "recursive-hierarchical": Mode(
        product="text",
        fields=LSJSON_FIELDS,
        axes=FLAT_AXES,
        artifacts=JSON,
        product_artifact=LISTING,
    ),
    "recursive-walk": Mode(
        product="text",
        fields=LSJSON_FIELDS,
        axes=WALK_AXES,
        artifacts=JSON,
        product_artifact=LISTING,
    ),
    "delimiter-shallow": Mode(
        product="text",
        fields=LSJSON_FIELDS,
        axes=FLAT_AXES,
        artifacts=JSON,
        product_artifact=LISTING,
    ),
    "listv1": Mode(
        product="text",
        fields=LSJSON_FIELDS,
        axes=FLAT_AXES,
        artifacts=JSON,
        product_artifact=LISTING,
    ),
    "lsf": Mode(
        product="text",
        fields=LSF_FIELDS,
        axes=FLAT_AXES,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    # `-vv --dump headers` prints every request line on stderr, which perturbs
    # the timing it exists to explain.
    "debug": Mode(
        product="text",
        fields=LSJSON_FIELDS,
        axes=FLAT_AXES,
        purpose_ceiling="diagnostic",
        artifacts=JSON,
        product_artifact=LISTING,
    ),
    "walk-debug": Mode(
        product="text",
        fields=LSJSON_FIELDS,
        axes=WALK_AXES,
        purpose_ceiling="diagnostic",
        artifacts=JSON,
        product_artifact=LISTING,
    ),
}


def _checkers(request: CommandRequest) -> str:
    """Render the asked-for fan-out; declared in :data:`MODES`, never pinned here."""
    value = request.config.get("concurrency", CHECKERS.value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CommandAdapterError(f"{TOOL} concurrency must be a positive integer; got: {value!r}")
    return str(value)


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    backend = f"s3,provider=AWS,region={request.region}"
    if request.mode == "listv1":
        backend += ",list_version=1"
    if request.signed:
        # rclone's s3 backend falls back to anonymous credentials when none
        # are configured (backend/s3/s3.go:1508-1511); env_auth=true is what
        # tells it to actually read AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY
        # from the process environment the engine already populated.
        backend += ",env_auth=true"
    remote = f":{backend}:{request.bucket}"
    if request.prefix:
        remote += f"/{request.prefix}"
    standard = ("--files-only", "--use-server-modtime", "--no-mimetype")
    walk = ("--disable", "ListR", "--checkers", _checkers(request))
    commands = {
        "recursive-fastlist": ("lsjson", "--fast-list", *standard, "-R", remote),
        "recursive-hierarchical": ("lsjson", *standard, "-R", remote),
        "recursive-walk": ("lsjson", *standard, *walk, "-R", remote),
        "delimiter-shallow": ("lsjson", "--use-server-modtime", "--no-mimetype", remote),
        "listv1": ("lsjson", "--fast-list", *standard, "-R", remote),
        "lsf": (
            "lsf",
            "--fast-list",
            "--files-only",
            "--format",
            "ps",
            "--separator",
            ";",
            "-R",
            remote,
        ),
        "debug": ("lsjson", "--fast-list", *standard, "-R", "-vv", "--dump", "headers", remote),
        "walk-debug": (
            "lsjson",
            *standard,
            *walk,
            "-R",
            "-vv",
            "--dump",
            "headers",
            remote,
        ),
    }
    try:
        return commands[request.mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {request.mode}") from None


def build_command(request: CommandRequest) -> tuple[str, ...]:
    return *RCLONE.argv, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="rclone command adapter"))
