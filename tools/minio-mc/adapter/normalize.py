#!/usr/bin/env python3
"""tools/minio-mc/adapter/normalize.py <mode> [prefix] — minio/mc output adapter.

Reads one ``mc`` listing mode's RAW output on stdin, writes contract v2 on
stdout, one record per line::

    key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class   (`-` where unexposed)

Every mode is a SELECT: ``--json`` is NDJSON, ``find`` is one path per line, and
the text ``ls`` sink is a regular line format ``regexp_extract`` reaches in one
expression. Framing and validation stay in ``contract`` via ``emit_result``.

mtime is ``YYYY-MM-DDTHH:MM:SSZ`` UTC. Containers run ``TZ=UTC``, so mc's text
sink stamping ``… UTC`` and its JSON stamping RFC3339 ``Z`` are both genuinely
UTC by construction.

``prefix`` (argv[2], passed by the verifier from ``run.meta``) is the scope the
run used. ``mc ls`` prints keys RELATIVE to the listed target, so a scoped run
prepends it to rebuild full keys; at the bucket root it is empty and the keys are
already full. ``mc find`` is the exception in both directions: it prints the
ALIAS-prefixed absolute path ``<alias>/<bucket>/<fullkey>``, so those two modes
strip the first two path segments and must NOT prepend the prefix.

Field exposure by mode
----------------------
``*-json``   all five — the fidelity path, exact sizes and real ETags.
``find-json``  key, size, mtime; ``find`` fetches neither ETag nor storage class.
``find``       key alone.
``recursive`` / ``shallow``  key and mtime, plus storage_class where the text
  columns make it unambiguous. The text sink humanises the size (``36KiB``), which
  is LOSSY and therefore `-`, and prints no ETag at all.

Folders (common prefixes) carry a synthetic mtime — mc stamps ``time.Now()`` —
and no ETag or size, so every value field is `-`. In JSON they are ``type ==
"folder"``; in text they are only recognisable by a key ending in ``/``.

Text parsing is BEST-EFFORT by construction: the columns are space-separated with
no quoting, so a key beginning with a known storage-class token followed by a
space is indistinguishable from the storage-class column. The storage-class set
below is kept current with the AWS enum for exactly that reason. Use a ``*-json``
mode for authoritative keys, sizes and ETags. Keys are compared as TEXT rather
than as bytes under ``LC_ALL=C``; every key the study lists is ASCII, so the two
orderings agree.

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

UNKNOWN_MODE_EXIT = 3

JSON_MODES = frozenset({"recursive-json", "shallow-json", "versions-json"})

TEXT_MODES = frozenset({"recursive", "shallow"})

# Declared rather than inferred, so the equivalence harness can name a mode no
# committed payload exercises — untested by construction, and invisible otherwise.
MODES = JSON_MODES | TEXT_MODES | {"find", "find-json"}

LINES = "(SELECT unnest(str_split(content, chr(10))) AS line FROM read_text($path))"

# Columns are declared, not sniffed: a listing whose entries carry no
# storageClass (a folder-only page, or `find --json`, which fetches none) would
# otherwise have no such column and the SELECT would fail to bind.
MC_JSON_COLUMNS = """{'key': 'VARCHAR', 'type': 'VARCHAR', 'size': 'BIGINT',
                      'etag': 'VARCHAR', 'lastModified': 'VARCHAR',
                      'storageClass': 'VARCHAR'}"""

# `[YYYY-MM-DD HH:MM:SS UTC]<pad><humansize><rest>`. mc formats the size with
# `%7s` and prints NO separator after `]` (cmd/ls.go String()), so a size exactly
# seven characters wide ("1006KiB") leaves a zero-width gap — `\s*`, not `\s+`,
# or such rows vanish silently.
TEXT_ROW = r"""regexp_extract(line,
    '^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\]\s*(\S+)(.*)$',
    ['mtime', 'size', 'rest'])"""

# The AWS storage-class enum. A leading token from this set is the storage-class
# column; anything else is the first word of the key.
STORAGE_CLASSES = """['STANDARD', 'STANDARD_IA', 'ONEZONE_IA', 'INTELLIGENT_TIERING',
                      'GLACIER', 'DEEP_ARCHIVE', 'REDUCED_REDUNDANCY', 'GLACIER_IR',
                      'OUTPOSTS', 'SNOW', 'EXPRESS_ONEZONE', 'FSX_OPENZFS', 'FSX_ONTAP']"""

QUERIES = {
    "json": f"""
        SELECT $pfx || "key",
               CASE WHEN "type" IS DISTINCT FROM 'folder' THEN CAST("size" AS VARCHAR) END,
               nullif("etag", ''),
               CASE WHEN "type" IS DISTINCT FROM 'folder' THEN "lastModified" END,
               nullif("storageClass", '')
        FROM read_json($path, format = 'newline_delimited', columns = {MC_JSON_COLUMNS})
    """,
    # `mc find --json`: `.key` is `<alias>/<bucket>/<fullkey>`, already absolute,
    # so the first two path segments come off and the scope prefix stays out —
    # prepending it would double the scope.
    "find-json": f"""
        SELECT regexp_replace("key", '^[^/]*/[^/]*/', ''), CAST("size" AS VARCHAR),
               nullif("etag", ''), nullif("lastModified", ''), nullif("storageClass", '')
        FROM read_json($path, format = 'newline_delimited', columns = {MC_JSON_COLUMNS})
    """,
    # `mc find` default: the whole line is that same alias-prefixed absolute path.
    "find": f"""
        SELECT regexp_replace(line, '^[^/]*/[^/]*/', ''), NULL, NULL, NULL, NULL
        FROM {LINES} WHERE NOT regexp_matches(line, '^[[:space:]]*$')
    """,
    # Text `ls`. A row that does not match the timestamped shape is not a record
    # and is dropped, blank lines included. `rest` is exactly ONE separator space,
    # then optionally "<SC> ", then the key. Only that single separator is
    # consumed (never a trim), so a genuine leading space in a key survives the
    # raw-key contract; likewise only one space after the storage class.
    "text": f"""
        WITH matched AS (
            SELECT r.mtime AS "mtime",
                   CASE WHEN starts_with(r.rest, ' ') THEN substr(r.rest, 2) ELSE r.rest END
                       AS "rest"
            FROM (SELECT {TEXT_ROW} AS r FROM {LINES}) WHERE r.mtime <> ''
        ), tagged AS (
            SELECT "mtime", "rest", "space",
                   CASE WHEN "space" > 1
                             AND list_contains({STORAGE_CLASSES}, substr("rest", 1, "space" - 1))
                        THEN substr("rest", 1, "space" - 1) END AS "sc"
            FROM (SELECT "mtime", "rest", position(' ' IN "rest") AS "space" FROM matched)
        ), scoped AS (
            SELECT "mtime", "sc",
                   $pfx || CASE WHEN "sc" IS NULL THEN "rest"
                                ELSE substr("rest", "space" + 1) END AS "key"
            FROM tagged
        )
        SELECT "key", NULL, NULL,
               CASE WHEN NOT ends_with("key", '/')
                    THEN replace("mtime", ' ', 'T') || 'Z' END,
               CASE WHEN NOT ends_with("key", '/') THEN "sc" END
        FROM scoped
    """,
}


def _sql_for(mode: str) -> str | None:
    # Every mode that count_rows counts cheaply (without SQL) must still have
    # a query here: count_rows resolves the query before choosing a counter, so
    # a count-only mode with no query would be refused instead of counted.
    if mode in JSON_MODES:
        return QUERIES["json"]
    if mode in TEXT_MODES:
        return QUERIES["text"]
    return QUERIES.get(mode)


def count_rows(data: bytes, mode: str, prefix: str = "", native_root: str = "") -> int:
    sql = _sql_for(mode)
    if sql is None:
        raise ValueError(f"unknown mode: {mode}")
    if mode == "find":
        blank = re.compile(rb"^[ \t\r\v\f]*$")
        return count_lf_lines(data, lambda line: blank.match(line) is None)
    if mode in TEXT_MODES:
        row = re.compile(rb"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC\]\s*\S+.*$")
        return count_lf_lines(data, lambda line: row.match(line) is not None)
    with staged(data) as path:
        params = {"path": path} | ({"pfx": prefix.removeprefix("/")} if "$pfx" in sql else {})
        return count_query(connect(), sql, params)


def normalize(
    out: IO[bytes], data: bytes, mode: str, prefix: str, config: Mapping[str, object] | None = None
) -> int:
    sql = _sql_for(mode)
    if sql is None:
        print(f"normalize.py: unknown mode: {mode}", file=sys.stderr)
        return UNKNOWN_MODE_EXIT
    with staged(data) as path:
        # DuckDB rejects a named parameter the statement does not reference, and
        # the two `find` modes reconstruct nothing.
        params = {"path": path} | ({"pfx": prefix.removeprefix("/")} if "$pfx" in sql else {})
        emit_result(out, connect().execute(sql, params))
    return 0


def main(argv: list[str] | None = None) -> int:
    return normalizer_main(
        normalize, modes=MODES, prog="minio-mc normalize", argv=argv, error_exit=UNKNOWN_MODE_EXIT
    )


if __name__ == "__main__":
    sys.exit(main())
