#!/usr/bin/env python3
"""Compile s5cmd listing parameters into exact in-image argv."""

import re

from benchmark.runtime.command_adapter import (
    Ceiling,
    CommandAdapterError,
    CommandRequest,
    Executable,
    Mode,
    command_adapter_main,
)

TOOL = "s5cmd"
S5CMD = Executable(TOOL, ("/s5cmd",))
FANOUT = Executable(
    "s5cmd-fanout",
    ("/usr/bin/python3", "/opt/benchmark/tools/s5cmd/adapter/fanout.py"),
)
EXECUTABLES = (S5CMD, FANOUT)
CONFIG_KEYS = frozenset({"shard_initials", "shard_prefixes"})
SUPPORTS_UNSIGNED = True
"""--no-sign-request lists anonymously; otherwise the credential in the
environment signs."""

FULL_FIELDS = ("key", "size", "etag", "mtime", "storage_class")
KEY_ONLY = ("key",)
"""`--show-fullpath` prints one absolute URL per line and nothing else
(`normalize.py`'s `fullpath` query selects `NULL` for the other four columns)."""

# Every mode here is a single `ls` invocation. `--numworkers` (default 256)
# sizes the `run`/transfer worker pool and is never consumed by the LIST chain
# (`command/app.go:18`, `command/run.go:76`) -- exactly aws-cli's `s3api`/`s3 ls`
# case, so absence is the declaration for the same reason.
LISTING = "listing"
"""The logical name every mode here publishes its listing under."""

TEXT = {LISTING: "listing.txt"}
JSON = {LISTING: "listing.json"}
"""s5cmd prints its listing and has no flag that writes one to a path, so the
worker lands fd 1 in the declared file; `--json` chooses which of these it is."""

MODES = {
    "recursive": Mode(
        product="text",
        fields=FULL_FIELDS,
        executable=S5CMD.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "recursive-with-dirs": Mode(
        product="text",
        fields=FULL_FIELDS,
        executable=S5CMD.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "fanout-with-dirs": Mode(
        product="text",
        fields=FULL_FIELDS,
        axes={"concurrency": Ceiling(256, "help")},
        executable=FANOUT.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "delimiter": Mode(
        product="text",
        fields=FULL_FIELDS,
        executable=S5CMD.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "rootkeys": Mode(
        product="text",
        fields=FULL_FIELDS,
        executable=S5CMD.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "json": Mode(
        product="text",
        fields=FULL_FIELDS,
        executable=S5CMD.name,
        artifacts=JSON,
        product_artifact=LISTING,
    ),
    "listv1": Mode(
        product="text",
        fields=FULL_FIELDS,
        executable=S5CMD.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    # ListObjectVersions on this study's unversioned buckets collapses to
    # current objects, so its throughput is not comparable to the other modes'.
    "allversions": Mode(
        product="text",
        fields=FULL_FIELDS,
        purpose_ceiling="diagnostic",
        executable=S5CMD.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "fullpath": Mode(
        product="text",
        fields=KEY_ONLY,
        executable=S5CMD.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
}


def _auth_flags(request: CommandRequest) -> tuple[str, ...]:
    # Authenticated runs sign requests with the credential the engine put in
    # the child's environment; anonymous runs pin no-sign-request so a subject
    # can never fall back to an ambient credential it should not have.
    return ("--no-sign-request",) if not request.signed else ()


SAFE_INITIALS = re.compile(r"[A-Za-z0-9._-]+")
SAFE_PREFIX = re.compile(r"[A-Za-z0-9._/-]+")


def _fanout_shards(request: CommandRequest) -> tuple[str, ...]:
    raw_prefixes = request.config.get("shard_prefixes")
    raw = request.config.get("shard_initials")
    if raw_prefixes is not None:
        if raw is not None:
            raise CommandAdapterError(
                f"{TOOL} fanout states both shard_initials and shard_prefixes"
            )
        if not isinstance(raw_prefixes, str):
            raise CommandAdapterError(
                f"{TOOL} shard_prefixes must be an underscore-separated string"
            )
        shards = tuple(raw_prefixes.split("_"))
        if not shards or any(SAFE_PREFIX.fullmatch(shard) is None for shard in shards):
            raise CommandAdapterError(
                f"{TOOL} shard_prefixes must contain non-empty safe prefixes: {raw_prefixes!r}"
            )
        if len(set(shards)) != len(shards):
            raise CommandAdapterError(
                f"{TOOL} fanout shard_prefixes repeat a shard: {raw_prefixes!r}"
            )
        return shards
    if not isinstance(raw, str) or SAFE_INITIALS.fullmatch(raw) is None:
        raise CommandAdapterError(
            f"{TOOL} fanout shard_initials must be non-empty safe single-byte characters: {raw!r}"
        )
    shards = tuple(raw)
    if len(set(shards)) != len(shards):
        raise CommandAdapterError(f"{TOOL} fanout shard_initials repeat a shard: {raw!r}")
    return shards


def _fanout_workers(request: CommandRequest) -> str:
    value = request.config.get("concurrency", 256)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CommandAdapterError(f"{TOOL} concurrency must be a positive integer; got: {value!r}")
    return str(value)


def _fanout_command(request: CommandRequest) -> tuple[str, ...]:
    endpoint = ("--endpoint-url", request.endpoint_url) if request.endpoint_url else ()
    unsigned = ("--unsigned",) if not request.signed else ()
    shards = tuple(token for shard in _fanout_shards(request) for token in ("--shard", shard))
    return (
        *FANOUT.argv,
        "--s5cmd",
        S5CMD.argv[0],
        "--bucket",
        request.bucket,
        "--prefix",
        request.prefix,
        *shards,
        "--numworkers",
        _fanout_workers(request),
        *endpoint,
        *unsigned,
    )


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    if request.endpoint_url and request.mode in {"listv1", "allversions"}:
        raise CommandAdapterError(
            f"{TOOL} mode {request.mode!r} cannot run against the replay endpoint because "
            "it does not issue ListObjectsV2"
        )
    target = f"s3://{request.bucket}/{request.prefix}"
    recursive = target + "*"
    auth = _auth_flags(request)
    endpoint = ("--endpoint-url", request.endpoint_url) if request.endpoint_url else ()
    commands = {
        "recursive": (*endpoint, *auth, "ls", "-e", "-s", recursive),
        # The command is deliberately identical to recursive: the distinction
        # is evidence semantics. This mode retains any raw DIR rows; exact
        # verification determines whether they reconstruct trailing-slash keys.
        "recursive-with-dirs": (*endpoint, *auth, "ls", "-e", "-s", recursive),
        "delimiter": (*endpoint, *auth, "ls", "-e", "-s", target),
        "rootkeys": (*endpoint, *auth, "ls", "-e", "-s", target),
        "json": ("--json", *endpoint, *auth, "ls", recursive),
        "listv1": (*endpoint, *auth, "--use-list-objects-v1", "ls", "-e", "-s", recursive),
        "allversions": (*endpoint, *auth, "ls", "--all-versions", "-e", "-s", recursive),
        "fullpath": (*endpoint, *auth, "ls", "--show-fullpath", recursive),
    }
    try:
        return commands[request.mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {request.mode}") from None


def build_command(request: CommandRequest) -> tuple[str, ...]:
    if request.mode == "fanout-with-dirs":
        return _fanout_command(request)
    return *S5CMD.argv, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="s5cmd command adapter"))
