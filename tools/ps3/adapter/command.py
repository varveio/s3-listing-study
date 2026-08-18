#!/usr/bin/env python3
"""Compile pS3 listing parameters into exact in-image argv."""

from benchmark.runtime.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    Executable,
    Fixed,
    Mode,
    command_adapter_main,
)

TOOL = "ps3"
PS3 = Executable(TOOL, ("/usr/local/bin/pS3",))
EXECUTABLES = (PS3,)
SUPPORTS_UNSIGNED = False
"""No unsigned request path; it signs with the credential in the environment."""

CONCURRENCY = Fixed(256)
"""pS3's own pager/printer-worker width, real and not settable: package `var
maxSemaphore = 256` (`cmd/root.go:44 @ 9428492`), never reassigned and exposed
by no flag -- it bounds both the parallel pager (`listObjectsV2.go:291`) and
`readObjectsV2`'s `numWorkers := maxSemaphore` print fan-out.

Declared on `list` only: `list-objects-v2` is the one subcommand whose source
is in the checkout. `list-object-versions` and `head-objects` have no source at
all, so whether they share this same package var is unconfirmed -- `Fixed`
carries no provenance field to mark that, so it is left undeclared there rather
than asserted.

Prefix *discovery* (`go discoverPrefixes`, `listObjectsV2.go:241`) is unbounded
and has no axis in this vocabulary to record that in.
"""

LIST_FIELDS = ("key", "size", "mtime")
"""`readObjectsV2`'s one fixed print line exposes key, size and mtime only;
etag and storage_class are not printed at all."""

LISTING = "listing"
"""The logical name every mode here publishes its listing under."""

TEXT = {LISTING: "listing.txt"}
"""pS3 prints its listing and takes no output destination, so the worker lands
fd 1 in the declared file."""

MODES = {
    "list": Mode(
        product="text",
        fields=LIST_FIELDS,
        axes={"concurrency": CONCURRENCY},
        executable=PS3.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    # list-object-versions has no command source in the checkout; `normalize.py`
    # assumes it shares list-objects-v2's line shape, so this manifest does too
    # -- unverified, and no worse than the normalizer's own working assumption.
    "list-versions": Mode(
        product="text",
        fields=LIST_FIELDS,
        executable=PS3.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
    # head-objects has no command source either and was never run. Its fields
    # here carry the same unverified assumption as list-versions, for the same
    # reason -- but normalize.py's own MODES excludes "head" outright, so this
    # mode can never be normalized against what it declares. That contradiction
    # is reported, not papered over here; see the dispatch report.
    "head": Mode(
        product="text",
        fields=LIST_FIELDS,
        executable=PS3.name,
        artifacts=TEXT,
        product_artifact=LISTING,
    ),
}


def _build_tail(request: CommandRequest) -> tuple[str, ...]:
    if request.prefix:
        raise CommandAdapterError(
            f"pS3 has no --prefix flag; mode {request.mode!r} cannot address "
            f"prefix {request.prefix!r}"
        )
    operations = {
        "list": "list-objects-v2",
        "list-versions": "list-object-versions",
        "head": "head-objects",
    }
    try:
        operation = operations[request.mode]
    except KeyError:
        raise CommandAdapterError(f"unknown mode: {request.mode}") from None
    return operation, "--bucket", request.bucket, "--region", request.region


def build_command(request: CommandRequest) -> tuple[str, ...]:
    return *PS3.argv, *_build_tail(request)


if __name__ == "__main__":
    raise SystemExit(command_adapter_main(build_command, prog="ps3 command adapter"))
