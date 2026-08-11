#!/usr/bin/env python3
"""tools/s3p/adapter/normalize.py <mode> [prefix] — s3p output adapter.

Reads one s3p listing mode's RAW stdout on stdin, writes contract v2 on stdout,
one record per line::

    key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class   (`-` where unexposed)

Every mode is a SELECT: ``ls --raw`` is NDJSON and the other two are regular line
formats. Framing and validation stay in ``contract`` via ``emit_result``.

s3p writes object lines to STDOUT and routes progress, heartbeat and final-stats
lines through art-standard-lib ``log`` to STDERR, so stdout is clean object data.

Output contract per mode ([SRC]: s3p's ``ls`` command in ``S3PCliCommands.caf``
and the ListObjectsV2 ``Contents`` shape)::

    ls         one KEY per line (the default onItem prints `item.Key`). Key only.
    ls-raw     one JSON object per line — a Contents element
               {Key,LastModified,ETag,Size,StorageClass[,Owner]}. All five fields.
    ls-long    "<yyyy-mm-dd HH:MM:ss> <human-size> <Key>". The size is
               human-rounded and therefore LOSSY, and the line is space-joined, so
               only the key is emitted and the rest is `-`.
    summarize  an aggregate report with NO per-object records. It normalises to
               nothing, and verification is N/A for the mode.

``prefix`` (argv[2]) is accepted per the adapter contract and unused: s3p prints
FULL keys in every mode. The adapter runs on the HOST, AFTER the wrapper's clock
stops, so a DuckDB query is fair game here — never inside a timed window.

``ls-raw`` must not be decoded through a ``@tsv``-style escaper: that escapes
BACKSLASH, and backslash is inside s3p's 95-character supported alphabet, so a
legal key would come back altered. The key travels from the JSON to the emit
boundary instead, where a key the framing cannot carry is refused rather than
escaped.

Provenance: these contracts are [SRC]-derived. They could not be confirmed
against a live listing — s3p cannot make anonymous requests and the campaign ran
``CREDS=none``, so every committed receipt is a capability probe whose stdout is a
credentials error rather than a listing.

Malformed JSON exits 5
----------------------
``ls-raw``'s only committed payload is one of those probes, so its bytes are not
JSON at all. ``jq`` exits 5 on a parse error and the committed receipt binds that
status, so an unreadable ``ls-raw`` payload exits 5 here too rather than letting
an exception exit 1. It is an ERROR either way — "no verdict was formed", which
is the truth about a payload that is not a listing — but the two adapters have to
agree on the number, and the receipt already fixed which number it is.
"""

from __future__ import annotations

import re
import sys
from typing import IO

from s3_listing_study.manager.duckdb_adapter import (
    connect,
    count_lf_lines,
    count_query,
    emit_result,
    staged,
)
from s3_listing_study.manager.normalizer_cli import normalizer_main

UNKNOWN_MODE_EXIT = 64
MALFORMED_JSON_EXIT = 5

# Declared rather than inferred, so the equivalence harness can name a mode no
# committed payload exercises — untested by construction, and invisible otherwise.
MODES = frozenset({"ls", "ls-long", "ls-raw", "summarize"})

LINES = "(SELECT unnest(str_split(content, chr(10))) AS line FROM read_text($path))"

S3P_JSON_COLUMNS = """{'Key': 'VARCHAR', 'Size': 'BIGINT', 'ETag': 'VARCHAR',
                       'LastModified': 'VARCHAR', 'StorageClass': 'VARCHAR'}"""

QUERIES = {
    "ls": f"""
        SELECT line, NULL, NULL, NULL, NULL FROM {LINES} WHERE line <> ''
    """,
    # "<date> <time> <human-size> <key>": the key is field 4 onward, space-joined.
    # LOSSY — a key containing runs of spaces cannot be rebuilt from this human
    # format, which is why ls-long is not a verification mode. Use ls-raw.
    "ls-long": rf"""
        SELECT array_to_string(list_slice(f, 4, len(f)), ' '), NULL, NULL, NULL, NULL
        FROM (SELECT str_split_regex(trim(line), '[ \t]+') AS f
              FROM {LINES} WHERE trim(line) <> '')
        WHERE len(f) >= 4
    """,
    # The ETag arrives wrapped in literal quotes; LastModified is ISO8601 with
    # milliseconds (`….SSSZ`) and is canonicalised to the whole second. Columns
    # are declared, not sniffed, so a page missing a field throughout still binds.
    "ls-raw": rf"""
        SELECT "Key", CAST("Size" AS VARCHAR), replace("ETag", '"', ''),
               regexp_replace("LastModified", '\.[0-9]+Z$', 'Z'), "StorageClass"
        FROM read_json($path, format = 'newline_delimited', columns = {S3P_JSON_COLUMNS})
    """,
}


def count_rows(data: bytes, mode: str, prefix: str = "", native_root: str = "") -> int:
    import duckdb

    if mode == "summarize":
        return 0
    if mode == "ls":
        return count_lf_lines(data, bool)
    if mode == "ls-long":
        fields = re.compile(rb"[ \t]+")

        def selected(line: bytes) -> bool:
            stripped = line.strip(b" ")
            return bool(stripped) and len(fields.split(stripped)) >= 4

        return count_lf_lines(data, selected)
    if mode not in QUERIES:
        raise ValueError(f"unknown mode: {mode}")
    with staged(data) as path:
        try:
            return count_query(connect(), QUERIES[mode], {"path": path})
        except duckdb.Error as exc:
            if mode != "ls-raw":
                raise
            raise ValueError(f"input is not the NDJSON ls --raw writes: {exc}") from exc


def normalize(out: IO[bytes], data: bytes, mode: str, prefix: str = "") -> int:
    import duckdb

    if mode == "summarize":
        return 0
    if mode not in QUERIES:
        print(f"normalize.py: unknown mode: {mode}", file=sys.stderr)
        return UNKNOWN_MODE_EXIT
    with staged(data) as path:
        connection = connect()
        try:
            result = connection.execute(QUERIES[mode], {"path": path})
        except duckdb.Error as exc:
            if mode != "ls-raw":
                raise
            print(f"normalize.py: stdin is not the NDJSON ls --raw writes: {exc}", file=sys.stderr)
            return MALFORMED_JSON_EXIT
        emit_result(out, result)
    return 0


def main(argv: list[str] | None = None) -> int:
    return normalizer_main(
        normalize, modes=MODES, prog="s3p normalize", argv=argv, error_exit=UNKNOWN_MODE_EXIT
    )


if __name__ == "__main__":
    sys.exit(main())
