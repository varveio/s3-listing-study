#!/usr/bin/env python3
"""Compile MinIO Client listing parameters into exact in-image argv."""

from benchmark.runtime.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    Executable,
    Mode,
    command_adapter_main,
)

TOOL = "minio-mc"
MC = Executable(TOOL, ("/usr/bin/mc",))
EXECUTABLES = (MC,)
SUPPORTS_UNSIGNED = True
"""The keyless MC_HOST_s3 alias issues unsigned requests."""
SUPPORTS_SIGNED = False
"""FUNCTIONAL_ENV is a static, request-independent declaration, so this
mechanism has no way to put a credential into a per-request alias URL.
Signing mc needs a different mechanism, not a different flag."""
FUNCTIONAL_ENV = {"MC_HOST_s3": "https://s3.amazonaws.com"}
"""Defines the ad-hoc alias `s3` mc's argv below targets.

mc has no ``--no-sign-request`` flag; it resolves credentials from a named
alias, and this one carries none, so minio-go issues unsigned requests
(anonymous). Endpoint configuration mc structurally requires — not a
credential, not a traffic redirect. See ``docs/running.md`` § Anonymous
wiring. Whether an authenticated attempt can reuse this same keyless alias
(minio-go falling back to ambient ``AWS_ACCESS_KEY_ID``/
``AWS_SECRET_ACCESS_KEY``) is unverified — confirm before trusting an
authenticated mc receipt; this mechanism has no way to embed a credential
into a per-request alias URL, since ``FUNCTIONAL_ENV`` is a static,
request-independent declaration.
"""

FULL_FIELDS = ("key", "size", "etag", "mtime", "storage_class")
"""``*-json`` modes: the fidelity path, exact sizes and real ETags
(``normalize.py`` JSON query)."""
TEXT_FIELDS = ("key", "mtime", "storage_class")
"""``recursive``/``shallow``: the text sink humanises size (LOSSY) and prints
no ETag at all, so ``normalize.py`` emits both as NULL (``normalize.py``
``QUERIES["text"]``)."""
FIND_JSON_FIELDS = ("key", "size", "mtime")
"""``find --json`` fetches neither ETag nor storage class (``normalize.py``
``QUERIES["find-json"]``)."""
FIND_FIELDS = ("key",)
"""``find`` prints one path per line and nothing else."""

# No concurrency axis: minio-go's List() issues one ListObjectsV2 request at a
# time -- concurrency is structurally 1, not a settable knob (`api-list.go:
# 100-165`). Absence is the declaration.
LISTING = "listing"
"""The logical name every mode here publishes its listing under."""

TEXT = {LISTING: "listing.txt"}
JSON = {LISTING: "listing.json"}
"""mc prints its listing and has no flag that writes one to a path, so the worker
lands fd 1 in the declared file; the global `--json` chooses which of these it is."""

MODES = {
    "recursive": Mode(
        product="text",
        fields=TEXT_FIELDS,
        executable=MC.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "recursive-json": Mode(
        product="text",
        fields=FULL_FIELDS,
        executable=MC.name,
        artifacts=JSON,
        product_artifact=LISTING,
    ),
    "shallow": Mode(
        product="text",
        fields=TEXT_FIELDS,
        executable=MC.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "shallow-json": Mode(
        product="text",
        fields=FULL_FIELDS,
        executable=MC.name,
        artifacts=JSON,
        product_artifact=LISTING,
    ),
    "versions-json": Mode(
        product="text",
        fields=FULL_FIELDS,
        executable=MC.name,
        artifacts=JSON,
        product_artifact=LISTING,
    ),
    "find": Mode(
        product="text",
        fields=FIND_FIELDS,
        executable=MC.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    "find-json": Mode(
        product="text",
        fields=FIND_JSON_FIELDS,
        executable=MC.name,
        artifacts=JSON,
        product_artifact=LISTING,
    ),
}


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    prefix = request.prefix[1:] if request.prefix.startswith("/") else request.prefix
    target = f"s3/{request.bucket}/{prefix}"
    commands = {
        "recursive": ("ls", "--recursive", target),
        "recursive-json": ("--json", "ls", "--recursive", target),
        "shallow": ("ls", target),
        "shallow-json": ("--json", "ls", target),
        "versions-json": ("--json", "ls", "--versions", "--recursive", target),
        "find": ("find", target),
        "find-json": ("--json", "find", target),
    }
    try:
        return commands[request.mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {request.mode}") from None


def build_command(request: CommandRequest) -> tuple[str, ...]:
    return *MC.argv, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="minio-mc command adapter"))
