#!/usr/bin/env python3
"""tools/rclone/adapter/normalize.py <mode> [prefix] — rclone output adapter.

Reads one rclone listing mode's raw output on stdin, writes contract v2 on
stdout, one record per line::

    key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class   (`-` where unexposed)

``lsjson`` is JSON and ``lsf`` is a line format, so every mode is a query, not a
parser: DuckDB reads the payload, the SELECT produces the five contract columns,
and framing plus validation stay in ``contract`` via ``emit_result``. The shell
predecessor decoded ``lsjson`` through ``jq -r … @tsv``, which silently C-escapes
TAB, NEWLINE, CR and BACKSLASH in a key; here such a key is refused at the emit
boundary instead of being altered. (DuckDB VARCHAR is UTF-8, so a key that is not
valid UTF-8 still cannot travel this path — see ``duckdb_adapter``. Every NOAA key
is ASCII and the edge-key bucket does not exist, ``EDGE_BUCKET=none``.)

Field exposure — what rclone gives without a per-object HEAD. key and size always,
from the listing. mtime is ``LastModified``, exposed only when the run used
``--use-server-modtime`` (otherwise rclone HEADs every object, and we never run it
that way); ``lsf`` omits it by construction. etag is always ``-``: rclone's S3
listing path does not surface the raw ETag (no ``lsf`` format code, no ``lsjson``
field), and ``--hash md5`` equals the ETag only for single-part objects, so we do
not claim it. storage_class comes from ``lsjson``'s ``.Tier``, straight off the
ListObjectsV2 response and the same string source as the manifest's StorageClass
column, so we assert it; ``lsf`` and CommonPrefix rows carry none. The verifier
asserts a field only where this adapter emits a non-``-`` value.

``prefix`` (argv[2]) is the scope the run used, passed by the verifier from
``run.meta``. A scoped run points rclone at ``bucket/<prefix>``, so keys print
RELATIVE to it and we prepend it to rebuild the full bucket key; empty for a
full-bucket or root run. The adapter runs AFTER the measurement clock stops, so a
DuckDB query is fair game here — never on the tool's timed path.
"""

from __future__ import annotations

import sys
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

RECURSIVE_MODES = frozenset(
    {"recursive-fastlist", "recursive-hierarchical", "recursive-walk", "listv1"}
)

# Declared rather than inferred, so the equivalence harness can name a mode no
# committed payload exercises — untested by construction, and invisible otherwise.
MODES = RECURSIVE_MODES | {"delimiter-shallow", "lsf"}

# `lsjson` writes one JSON array of entry objects. Columns are declared, not
# sniffed: a payload where no entry carries `Tier` (nothing but CommonPrefixes)
# would otherwise have no such column and the query would fail to bind.
LSJSON = """read_json(
    $path,
    format = 'array',
    columns = {'Path': 'VARCHAR', 'Size': 'BIGINT',
               'ModTime': 'VARCHAR', 'IsDir': 'BOOLEAN', 'Tier': 'VARCHAR'})"""

# rclone writes RFC3339(Nano) under TZ=UTC (pinned by the wrapper): the datetime is
# the leading 19 characters, then a fraction and a zone. S3 LastModified is
# whole-second, so dropping the fraction and re-stamping Z is exact, never a
# rounding. A short or absent timestamp means the run exposed none.
MTIME = """CASE WHEN length("ModTime") >= 19
                THEN substr("ModTime", 1, 19) || 'Z' END"""

QUERIES = {
    # --files-only recursive: every entry is a file, `Path` relative to the run's
    # prefix, `Tier` the storage class.
    "recursive": f"""
        SELECT $pfx || "Path", CAST("Size" AS VARCHAR), NULL, {MTIME}, nullif("Tier", '')
        FROM {LSJSON}
    """,
    # Non-recursive (a single delimiter level): files AND directories. A directory
    # is a CommonPrefix (IsDir), normalising to its key with a trailing '/' and `-`
    # everywhere else — a CommonPrefix carries no size, mtime or storage class.
    "delimiter-shallow": f"""
        SELECT $pfx || "Path" || CASE WHEN "IsDir" THEN '/' ELSE '' END,
               CASE WHEN NOT "IsDir" THEN CAST("Size" AS VARCHAR) END,
               NULL,
               CASE WHEN NOT "IsDir" THEN {MTIME} END,
               CASE WHEN NOT "IsDir" THEN nullif("Tier", '') END
        FROM {LSJSON}
    """,
    # lsf --format ps --separator ';' --files-only recursive: "path;size" lines. No
    # modtime requested (it would force a per-object HEAD), so mtime is `-`. A key
    # could contain ';', so both patterns anchor to the LAST one: the size is an
    # integer that cannot contain the separator and the key keeps any it has. A
    # line with no separator is not a record.
    "lsf": """
        SELECT $pfx || regexp_extract(line, '^(.*);', 1),
               regexp_extract(line, ';([^;]*)$', 1),
               NULL, NULL, NULL
        FROM (SELECT unnest(str_split(content, chr(10))) AS line FROM read_text($path))
        WHERE contains(line, ';')
    """,
}


def count_rows(data: bytes, mode: str, prefix: str = "", native_root: str = "") -> int:
    if mode == "lsf":
        return count_lf_lines(data, lambda line: b";" in line)
    if mode in RECURSIVE_MODES:
        sql = QUERIES["recursive"]
    elif mode in QUERIES:
        sql = QUERIES[mode]
    else:
        raise ValueError(f"unknown mode: {mode}")
    with staged(data) as path:
        return count_query(connect(), sql, {"path": path, "pfx": prefix})


def normalize(out: IO[bytes], data: bytes, mode: str, prefix: str) -> int:
    if mode in RECURSIVE_MODES:
        sql = QUERIES["recursive"]
    elif mode in QUERIES:
        sql = QUERIES[mode]
    else:
        print(f"normalize.py: unknown mode {mode}", file=sys.stderr)
        return UNKNOWN_MODE_EXIT
    with staged(data) as path:
        connection = connect()
        emit_result(out, connection.execute(sql, {"path": path, "pfx": prefix}))
    return 0


def main(argv: list[str] | None = None) -> int:
    return normalizer_main(
        normalize, modes=MODES, prog="rclone normalize", argv=argv, error_exit=UNKNOWN_MODE_EXIT
    )


if __name__ == "__main__":
    sys.exit(main())
