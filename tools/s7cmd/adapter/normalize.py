#!/usr/bin/env python3
"""tools/s7cmd/adapter/normalize.py <mode> [prefix] — s7cmd output adapter.

Reads one s7cmd ``ls`` mode's RAW output on stdin, writes contract v2 on stdout,
one record per line::

    key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class   (`-` where unexposed)

Every mode is a SELECT: the TSV family is tab-separated, ``--json`` is NDJSON,
and the aligned and ``-1`` sinks are regular line formats one expression reaches.
Framing and validation stay in ``contract`` via ``emit_result``.

mtime is s7cmd's native ``YYYY-MM-DDTHH:MM:SSZ`` (chrono ``SecondsFormat::Secs``,
already zoned ``Z``), passed through unchanged; containers run ``TZ=UTC``. The
ETag is UNQUOTED — the TSV formatter already trims the quotes, the JSON value
keeps them and this adapter strips them.

``prefix`` (argv[2]) is accepted per the adapter contract and unused: none of
these modes pass ``--show-relative-path``, so every key printed is already the
full bucket key. The adapter runs on the HOST, AFTER the wrapper's clock stops.

Field exposure by mode — the TSV family (``--tsv --show-storage-class
--show-etag``) carries all five. ``recursive-aligned`` is the default sink and
prints date, size and key only, so etag and storage_class are `-`.
``recursive-one`` (``-1``) prints the key alone. A CommonPrefix (``PRE``) row
carries a key and nothing else; a delete marker (``DELETE``, versions listings)
carries a key and its timestamp.

Versions: ``all-versions`` keys on the object KEY and DISCARDS VersionId and
IsLatest, because the contract-v2 manifest and the verifier have no version axis.
On a versioned bucket that would collapse several versions of one key onto a
single record, so the mode is validated only against the non-versioned smoke
bucket. A versioned corpus needs a version-aware manifest first
(``EDGE_BUCKET=none``).

Keys are compared as TEXT here rather than as bytes under ``LC_ALL=C``. Every key
in every bucket the study lists is ASCII, so the two orderings agree. s7cmd also
escapes control bytes to ``\\xNN`` by default, which is an identity on this
corpus and is NOT de-escaped here.
"""

from __future__ import annotations

import sys
from typing import IO

from s3_listing_study.duckdb_adapter import connect, emit_result, staged

UNKNOWN_MODE_EXIT = 3

# `--tsv --show-storage-class --show-etag`. Columns (columns.rs):
#   DATE  SIZE  STORAGE_CLASS  ETAG  [VERSION_ID]  KEY
# The key is always the LAST field, which is what makes the extra VERSION_ID
# column `--all-versions` inserts a non-event.
TSV_MODES = frozenset(
    {"recursive-tsv", "recursive-tsv-nosort", "all-versions", "max-depth", "shallow-tsv"}
)

# Declared rather than inferred, so the equivalence harness can name a mode no
# committed payload exercises — untested by construction, and invisible otherwise.
MODES = TSV_MODES | {"recursive-aligned", "recursive-json", "recursive-one"}

LINES = "(SELECT unnest(str_split(content, chr(10))) AS line FROM read_text($path))"

# Split on tabs rather than declaring CSV columns: the TSV family's column count
# is 5 or 6 depending on `--all-versions`, and every row's key is its last field.
TSV_FIELDS = f"""
    (SELECT str_split(line, chr(9)) AS f FROM {LINES} WHERE line <> '')"""

# Default sink: whitespace-aligned DATE SIZE KEY, or `PRE <key>` for a common
# prefix (the empty date column collapses under a whitespace split).
ALIGNED_FIELDS = rf"""
    (SELECT str_split_regex(trim(line), '[ \t]+') AS f FROM {LINES} WHERE trim(line) <> '')"""

S7CMD_JSON_COLUMNS = """{'Key': 'VARCHAR', 'Size': 'BIGINT', 'ETag': 'VARCHAR',
                         'LastModified': 'VARCHAR', 'StorageClass': 'VARCHAR',
                         'Prefix': 'VARCHAR'}"""

QUERIES = {
    # A `PRE` row is `(empty) PRE (empty) (empty) KEY` and exposes the key alone;
    # a `DELETE` row (a delete marker in a versions listing) adds its timestamp.
    "tsv": f"""
        SELECT f[-1],
               CASE WHEN f[2] NOT IN ('PRE', 'DELETE') THEN f[2] END,
               CASE WHEN f[2] NOT IN ('PRE', 'DELETE') THEN f[4] END,
               CASE WHEN f[2] <> 'PRE' THEN f[1] END,
               CASE WHEN f[2] NOT IN ('PRE', 'DELETE') THEN f[3] END
        FROM {TSV_FIELDS}
    """,
    "recursive-aligned": f"""
        SELECT CASE WHEN f[1] = 'PRE' THEN f[2] ELSE f[3] END,
               CASE WHEN f[1] <> 'PRE' THEN f[2] END,
               NULL,
               CASE WHEN f[1] <> 'PRE' THEN f[1] END,
               NULL
        FROM {ALIGNED_FIELDS}
        WHERE f[1] = 'PRE' OR len(f) >= 3
    """,
    # NDJSON: one object per line, `{{"Key",…}}` or `{{"Prefix":…}}` for a common
    # prefix. Columns are declared, not sniffed, so a payload of nothing but
    # CommonPrefixes still binds every column the SELECT names.
    "recursive-json": f"""
        SELECT coalesce("Prefix", "Key"),
               CASE WHEN "Prefix" IS NULL THEN CAST("Size" AS VARCHAR) END,
               CASE WHEN "Prefix" IS NULL THEN replace("ETag", '"', '') END,
               CASE WHEN "Prefix" IS NULL THEN "LastModified" END,
               CASE WHEN "Prefix" IS NULL THEN "StorageClass" END
        FROM read_json($path, format = 'newline_delimited', columns = {S7CMD_JSON_COLUMNS})
    """,
    # `-1`: the key (or prefix) alone, one per line.
    "recursive-one": f"""
        SELECT line, NULL, NULL, NULL, NULL FROM {LINES} WHERE line <> ''
    """,
}


def normalize(out: IO[bytes], data: bytes, mode: str) -> int:
    if mode in TSV_MODES:
        sql = QUERIES["tsv"]
    elif mode in QUERIES:
        sql = QUERIES[mode]
    else:
        print(f"normalize.py: unknown mode {mode}", file=sys.stderr)
        return UNKNOWN_MODE_EXIT
    with staged(data) as path:
        emit_result(out, connect().execute(sql, {"path": path}))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("normalize.py: mode required", file=sys.stderr)
        return UNKNOWN_MODE_EXIT
    return normalize(sys.stdout.buffer, sys.stdin.buffer.read(), argv[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
