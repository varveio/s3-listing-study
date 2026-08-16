#!/usr/bin/env python3
"""tools/s3-fast-list/adapter/normalize.py <mode> [prefix] — s3-fast-list adapter.

Reads the tool's RAW output on stdin, writes contract v2 on stdout, one record
per line::

    key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class   (`-` where unexposed)

What s3-fast-list emits is a PARQUET file, which the attempt pipeline routes to
``/dev/stdout``, so stdin here is a raw parquet byte stream. Its Arrow schema
(``s3-fast-list/src/utils.rs @ 6c72f59``) is::

    Key(Utf8)  Size(UInt64)  LastModified(UInt64)  ETag(Utf8)  DiffFlag(UInt8)

``Key`` is the FULL object key (``core.rs`` round-trips the encode/decode),
``LastModified`` is Unix epoch SECONDS (``last_modified().secs()``), and ``ETag``
is lowercase hex, UNQUOTED, ``<hex>-<parts>`` for a multipart object — the same
spelling the manifest carries. StorageClass is not captured by the tool at all,
so it is `-`.

``prefix`` (argv[2]) is accepted per the adapter contract and unused: the ``Key``
column is already absolute.

The parquet is read in-process. Going out to the ``duckdb`` CLI and reading back
its ``-list`` output would not work here: that output is unquoted and uses TAB as
the field separator and NEWLINE as the record separator, so a key containing
either byte silently becomes an extra field or an extra record. In-process, the
key goes from the file to the emit boundary, where a key the framing cannot carry
is REFUSED rather than mangled. It also removes the host's
``duckdb`` binary from the adapter's dependencies.

An EMPTY stream is zero keys, not a broken payload: a run that listed nothing
still writes a valid 0-row parquet, and a 0-byte stdin (the shell's ``[ ! -s ]``
branch) exits clean with no output. Anything non-empty that is not readable
parquet is refused at exit 1, which the verifier reports as ERROR — "no verdict
was formed". Every committed receipt for this tool is BLOCKED for exactly that
reason: the wrapper captured the binary stream through ``docker logs``, which
corrupted it, so the payloads on record are not valid parquet and both adapters
refuse all four identically.
"""

from __future__ import annotations

import sys
from typing import IO

from benchmark.runtime.duckdb_adapter import (
    connect,
    count_query,
    emit_result,
    staged,
)
from benchmark.runtime.normalizer_cli import normalizer_main

UNKNOWN_MODE_EXIT = 3
UNREADABLE_EXIT = 1

# Declared rather than inferred, so the equivalence harness can name a mode no
# committed payload exercises — untested by construction, and invisible otherwise.
MODES = frozenset({"list"})

# make_timestamp() takes MICROseconds; the epoch is UTC and the timestamp is
# tz-naive, so the formatted components are UTC by construction and the `Z` is
# stamped rather than converted.
QUERY = """
    SELECT "Key", CAST("Size" AS VARCHAR), "ETag",
           strftime(make_timestamp(CAST("LastModified" AS BIGINT) * 1000000),
                    '%Y-%m-%dT%H:%M:%SZ'),
           NULL
    FROM read_parquet($path)
"""


def count_rows(data: bytes, mode: str, prefix: str = "", native_root: str = "") -> int:
    import duckdb

    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    if not data:
        return 0
    with staged(data) as path:
        try:
            return count_query(connect(), "SELECT * FROM read_parquet($path)", {"path": path})
        except duckdb.Error as exc:
            raise ValueError(f"input is not readable parquet: {exc}") from exc


def normalize(out: IO[bytes], data: bytes, mode: str, prefix: str = "") -> int:
    import duckdb

    if mode not in MODES:
        print(f"normalize.py: unknown mode: {mode}", file=sys.stderr)
        return UNKNOWN_MODE_EXIT
    if not data:
        return 0
    with staged(data) as path:
        try:
            result = connect().execute(QUERY, {"path": path})
        except duckdb.Error as exc:
            print(f"normalize.py: stdin is not readable parquet: {exc}", file=sys.stderr)
            return UNREADABLE_EXIT
        emit_result(out, result)
    return 0


def main(argv: list[str] | None = None) -> int:
    return normalizer_main(
        normalize,
        modes=MODES,
        prog="s3-fast-list normalize",
        argv=argv,
        error_exit=UNKNOWN_MODE_EXIT,
    )


if __name__ == "__main__":
    sys.exit(main())
