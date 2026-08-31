#!/usr/bin/env python3
"""tools/s5cmd/adapter/normalize.py <mode> [prefix] — s5cmd output adapter.

Reads one s5cmd listing mode's RAW output on stdin, writes contract v2 on
stdout, one record per line::

    key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class   (`-` where unexposed)

Every mode is a SELECT: ``--json`` is NDJSON and the text sinks are regular line
formats one expression reaches. Framing and validation stay in ``contract`` via
``emit_result``.

KEY RECONSTRUCTION. s5cmd's text ``ls`` prints paths RELATIVE to the query prefix
(``storage/url/url.go:170`` ``Relative`` -> ``parseBatch``), so ``prefix``
(argv[2], passed by the verifier from ``run.meta``) is prepended to rebuild the
full bucket key; for a full-bucket run it is empty and the printed path already
is the full key. ``--json`` and ``--show-fullpath`` instead print the ABSOLUTE
``s3://bucket/key`` URL, so those two strip the scheme and bucket and do NOT
prepend the prefix.

TIMEZONE. Text ``ls`` prints ModTime as ``2006/01/02 15:04:05`` with no offset
(``command/ls.go:248``). Containers run ``TZ=UTC`` pinned and the SDK parses S3
LastModified as UTC, so the instant is UTC by construction and we restamp it as
``YYYY-MM-DDTHH:MM:SSZ``. JSON already emits RFC3339 ``…Z``. The ETag is emitted
UNQUOTED.

KEY-BYTE FIDELITY — scope. The text sinks are whitespace-separated with no
quoting, so a key containing runs of spaces, a TAB or a newline is not
recoverable from them, and a ``jq @tsv`` JSON path would C-escape
TAB/NEWLINE/BACKSLASH. So a key the framing cannot carry is REFUSED at the emit
boundary rather than altered, and
a key with interior single spaces still round-trips. This is exact for the NOAA
smoke keyspace (``[A-Za-z0-9._/-]``, no whitespace); full weird-key fidelity is
deferred with the edge-case fixture (``EDGE_BUCKET=none``). Keys are compared as
TEXT rather than as bytes under ``LC_ALL=C``; every key the study lists is ASCII,
so the two orderings agree.

The adapter runs on the HOST, AFTER the wrapper's clock stops, so a DuckDB query
is fair game here — never inside a timed window.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from typing import IO

from benchmark.runtime.duckdb_adapter import (
    connect,
    count_lf_lines,
    count_query,
    emit_result,
    staged,
)
from benchmark.runtime.normalizer_cli import normalizer_main

UNKNOWN_MODE_EXIT = 2

# Text, batch/recursive, `-e -s`. Columns: date time SC etag size relkey…
# `rootkeys` is the remainder adapter for the fan-out `--scope` union: the same
# request as `delimiter`, but only the unprefixed OBJECT rows are kept — the
# union's remainder is verified against the manifest's unprefixed keys, so a DIR
# pseudo-key would read as an out-of-scope extra.
RECURSIVE_MODES = frozenset({"recursive", "listv1", "rootkeys"})
DIRECTORY_RECURSIVE_MODES = frozenset(
    {"recursive-with-dirs", "fanout-with-dirs", "fanout-fixture-with-dirs"}
)

# Declared rather than inferred, so the equivalence harness can name a mode no
# committed payload exercises — untested by construction, and invisible otherwise.
MODES = (
    RECURSIVE_MODES
    | DIRECTORY_RECURSIVE_MODES
    | {
        "allversions",
        "delimiter",
        "fullpath",
        "json",
    }
)

LINES = "(SELECT unnest(str_split(content, chr(10))) AS line FROM read_text($path))"

# Whitespace-split the way awk splits: leading and trailing blanks ignored,
# runs collapsed. A DIR row is `<blanks> DIR <relkey/>`.
FIELDS = rf"""
    (SELECT str_split_regex(trim(line), '[ \t]+') AS f FROM {LINES} WHERE trim(line) <> '')"""

S5CMD_JSON_COLUMNS = """{'key': 'VARCHAR', 'type': 'VARCHAR', 'size': 'BIGINT',
                         'etag': 'VARCHAR', 'last_modified': 'VARCHAR',
                         'storage_class': 'VARCHAR'}"""


def object_row(last: str) -> str:
    """The five contract columns of one text OBJECT row, keyed from field 6 to ``last``.

    ``--all-versions`` appends a trailing versionID token (``null`` on a
    non-versioned bucket) after the key, so its last key field is ``len(f) - 1``.
    The version axis is dropped: contract v2 and the verifier are keyed on ``key``
    alone, so this mode is comparable to a current-object manifest only on an
    unversioned bucket.
    """
    return f"""
        SELECT $pfx || array_to_string(list_slice(f, 6, {last}), ' '),
               f[5],
               replace(f[4], '"', ''),
               replace(f[1], '/', '-') || 'T' || f[2] || 'Z',
               f[3]
        FROM {FIELDS} WHERE f[1] <> 'DIR'
    """


QUERIES = {
    "recursive": object_row("len(f)"),
    # The delimiter-free recursive request has no CommonPrefixes. A DIR line in
    # this mode is s5cmd's representation of a trailing-slash Contents object,
    # so preserve it as key-only evidence rather than filtering it out.
    "recursive-with-dirs": f"""
        SELECT $pfx || CASE WHEN f[1] = 'DIR'
                            THEN array_to_string(list_slice(f, 2, len(f)), ' ')
                            ELSE array_to_string(list_slice(f, 6, len(f)), ' ') END,
               CASE WHEN f[1] <> 'DIR' THEN f[5] END,
               CASE WHEN f[1] <> 'DIR' THEN replace(f[4], '"', '') END,
               CASE WHEN f[1] <> 'DIR'
                    THEN replace(f[1], '/', '-') || 'T' || f[2] || 'Z' END,
               CASE WHEN f[1] <> 'DIR' THEN f[3] END
        FROM {FIELDS}
    """,
    "allversions": object_row("len(f) - 1"),
    # Delimiter listing: CommonPrefixes as DIR rows interleaved with objects, in
    # the order s5cmd printed them. A DIR row carries no size, etag, mtime or
    # storage class.
    "delimiter": f"""
        SELECT $pfx || CASE WHEN f[1] = 'DIR'
                            THEN array_to_string(list_slice(f, 2, len(f)), ' ')
                            ELSE array_to_string(list_slice(f, 6, len(f)), ' ') END,
               CASE WHEN f[1] <> 'DIR' THEN f[5] END,
               CASE WHEN f[1] <> 'DIR' THEN replace(f[4], '"', '') END,
               CASE WHEN f[1] <> 'DIR'
                    THEN replace(f[1], '/', '-') || 'T' || f[2] || 'Z' END,
               CASE WHEN f[1] <> 'DIR' THEN f[3] END
        FROM {FIELDS}
    """,
    # `--show-fullpath`: one absolute s3://bucket/key URL per line and nothing
    # else, so the mode exposes no size, etag, mtime or storage class.
    "fullpath": f"""
        SELECT regexp_replace(line, '^s3://[^/]*/', ''), NULL, NULL, NULL, NULL
        FROM {LINES} WHERE line <> ''
    """,
    # NDJSON. `.key` is the absolute URL; `.last_modified` is already RFC3339
    # `…Z`. Columns are declared, not sniffed, so a payload missing a field
    # throughout still binds. dir-type records (none in a recursive listing) are
    # dropped; a record with no type at all is an object.
    "json": f"""
        SELECT regexp_replace("key", '^s3://[^/]*/', ''), CAST("size" AS VARCHAR),
               "etag", "last_modified", "storage_class"
        FROM read_json($path, format = 'newline_delimited', columns = {S5CMD_JSON_COLUMNS})
        WHERE "type" IS DISTINCT FROM 'dir'
    """,
}


def _sql_for(mode: str) -> str | None:
    # Every mode that count_rows counts cheaply (without SQL) must still have
    # a query here: count_rows resolves the query before choosing a counter, so
    # a count-only mode with no query would be refused instead of counted.
    if mode in RECURSIVE_MODES:
        return QUERIES["recursive"]
    if mode in DIRECTORY_RECURSIVE_MODES:
        return QUERIES["recursive-with-dirs"]
    return QUERIES.get(mode)


def count_rows(data: bytes, mode: str, prefix: str = "", native_root: str = "") -> int:
    sql = _sql_for(mode)
    if sql is None:
        raise ValueError(f"unknown mode: {mode}")
    if mode == "fullpath":
        return count_lf_lines(data, bool)
    if mode in RECURSIVE_MODES | DIRECTORY_RECURSIVE_MODES | {"allversions", "delimiter"}:
        fields = re.compile(rb"[ \t]+")

        def selected(line: bytes) -> bool:
            stripped = line.strip(b" ")
            if not stripped:
                return False
            first = fields.split(stripped, maxsplit=1)[0]
            return mode in DIRECTORY_RECURSIVE_MODES | {"delimiter"} or first != b"DIR"

        return count_lf_lines(data, selected)
    with staged(data) as path:
        return count_query(connect(), sql, {"path": path})


def normalize(
    out: IO[bytes], data: bytes, mode: str, prefix: str, config: Mapping[str, object] | None = None
) -> int:
    sql = _sql_for(mode)
    if sql is None:
        print(f"normalize.py: unknown mode: {mode}", file=sys.stderr)
        return UNKNOWN_MODE_EXIT
    with staged(data) as path:
        # DuckDB rejects a named parameter the statement does not reference, and
        # the two absolute-URL modes reconstruct nothing.
        params = {"path": path} | ({"pfx": prefix} if "$pfx" in sql else {})
        emit_result(out, connect().execute(sql, params))
    return 0


def main(argv: list[str] | None = None) -> int:
    return normalizer_main(
        normalize, modes=MODES, prog="s5cmd normalize", argv=argv, error_exit=UNKNOWN_MODE_EXIT
    )


if __name__ == "__main__":
    sys.exit(main())
