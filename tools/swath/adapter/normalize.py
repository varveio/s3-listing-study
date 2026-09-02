#!/usr/bin/env python3
"""tools/swath/adapter/normalize.py <mode> [prefix] — swath output adapter.

Reads one swath listing mode's RAW output on stdin, writes contract v2 on
stdout, one record per line::

    key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class   (`-` where unexposed)

Every mode is a SELECT: the TSV and JSONL sinks are formats DuckDB reads
directly, and the aligned sink is fixed-width columns ``substr`` addresses in one
expression. Framing and validation stay in ``contract`` via ``emit_result``.

Field exposure — ``recursive-tsv`` / ``seed-none`` / ``recursive-jsonl`` carry all
five fields. ``recursive-table`` (TableFormatter) prints size, last_modified
and key only, so etag and storage_class are `-`.

mtime: since 0.3.0 the text sinks write the endpoint's own ``LastModified``
text, which real S3 spells ``YYYY-MM-DDTHH:MM:SS.000Z``; before that, swath
rendered ``ISO_INSTANT`` and S3's whole seconds came out as
``YYYY-MM-DDTHH:MM:SSZ``. Every query strips a trailing fraction, so both
spellings normalize to the contract's whole-second Zulu form. The aligned sink
gives the instant a 24-character column that the S3 spelling exactly fills; an
endpoint spelling it longer would overflow the columns and be refused.

``prefix`` (argv[2]) is accepted per the adapter contract and unused: swath lists
``s3://bucket/prefix`` and returns WHOLE keys, so nothing needs reconstructing.
The adapter runs on the HOST, AFTER the wrapper's clock stops, so a DuckDB query
is fair game here — never inside a timed window.

Row types — the TSV and JSONL sinks carry a ``row_type`` column, and only
``OBJECT`` rows are records (COMMON_PREFIX and DELETE_MARKER are dropped; a
recursive listing produces none anyway). The two sinks spell "absent" differently
and the filter follows each: TSV keeps a row whose ``row_type`` is empty, JSONL
defaults a missing ``row_type`` to ``OBJECT``. The TSV filter also drops the
header line, whose sixth field is the literal ``row_type``.

Keys are compared as TEXT here rather than as bytes under ``LC_ALL=C``, and
``substr`` on the aligned format counts characters, not bytes. Every key in every
bucket the study lists is ASCII, so the two orderings agree; a multi-byte key
would need the aligned sink's column arithmetic revisited.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import IO

import zstandard

from benchmark.runtime.contract import ContractViolation, emit
from benchmark.runtime.duckdb_adapter import (
    connect,
    count_query,
    emit_result,
    iter_lf_lines,
    staged,
)
from benchmark.runtime.normalizer_cli import normalizer_main

UNKNOWN_MODE_EXIT = 2
UNREADABLE_EXIT = 1

# Declared rather than inferred, so the equivalence harness can name a mode no
# committed payload exercises — untested by construction, and invisible otherwise.
STREAM_TSV_MODES = frozenset({"recursive-tsv", "seed-none"})
TSV_DATASET_MODES = frozenset({"recursive-tsv-dataset", "recursive-tsv-zstd"})
TSV_MODES = STREAM_TSV_MODES | TSV_DATASET_MODES

PARQUET_MODES = frozenset({"recursive-parquet", "recursive-parquet-sorted"})
"""Modes read from the published dataset directory rather than from stdin.

Swath refuses Parquet on stdout, so these modes have no stream to normalize:
their output is the ``native/`` directory the attempt engine collected. Parts
live under ``data/``; the run's sidecars sit beside it and are not listing rows.
"""

DATASET_MODES = TSV_DATASET_MODES | PARQUET_MODES
MODES = TSV_MODES | {"recursive-jsonl", "recursive-table"} | PARQUET_MODES

CONTROL_ESCAPE = re.compile(rb"\\x[0-9a-fA-F]{2}")

# The TSV sink's column order, which is NOT the contract's: last_modified sits
# third and etag fourth. Declared, never sniffed — a payload whose etag column is
# empty throughout would otherwise sniff to a type the SELECT cannot bind.
SWATH_TSV_COLUMNS = """{'key': 'VARCHAR', 'size': 'VARCHAR', 'last_modified': 'VARCHAR',
                        'etag': 'VARCHAR', 'storage_class': 'VARCHAR', 'row_type': 'VARCHAR'}"""

SWATH_JSONL_COLUMNS = """{'key': 'VARCHAR', 'size': 'BIGINT', 'last_modified': 'VARCHAR',
                          'etag': 'VARCHAR', 'storage_class': 'VARCHAR', 'row_type': 'VARCHAR'}"""

LINES = "(SELECT unnest(str_split(content, chr(10))) AS line FROM read_text($path))"

QUERIES = {
    # size is read as VARCHAR and passed through: the sink already wrote the exact
    # byte count and re-parsing it as a number would only add a way to lose it.
    #
    # auto_detect = false: with the dialect and every column declared there is
    # nothing left to sniff, and the sniffer refuses an EMPTY payload (zero columns
    # where six are declared). An empty payload is ordinary — a `--scope prefix`
    # run over a prefix holding no objects produces one — and the answer is a clean
    # 0-row exit.
    "tsv": f"""
        SELECT "key", "size", nullif("etag", ''),
               regexp_replace("last_modified", '\\.[0-9]+Z$', 'Z'),
               nullif("storage_class", '')
        FROM read_csv($path, delim = '\t', quote = '', escape = '', header = false,
                      auto_detect = false, columns = {SWATH_TSV_COLUMNS})
        WHERE coalesce("row_type", '') IN ('', 'OBJECT')
    """,
    "recursive-jsonl": f"""
        SELECT "key", CAST("size" AS VARCHAR), "etag",
               regexp_replace("last_modified", '\\.[0-9]+Z$', 'Z'), "storage_class"
        FROM read_json($path, format = 'newline_delimited', columns = {SWATH_JSONL_COLUMNS})
        WHERE coalesce("row_type", 'OBJECT') = 'OBJECT'
    """,
    # AlignedFormatter: size right-justified in columns [1,14], two spaces, the
    # instant in [17,40], two spaces, the key from column 43 to end of line. The
    # formatter emits neither etag nor storage class.
    "recursive-table": f"""
        SELECT "key", "size", NULL, regexp_replace("mtime", '\\.[0-9]+Z$', 'Z'), NULL
        FROM (SELECT trim(substr(line, 1, 14)) AS "size",
                     trim(substr(line, 17, 24)) AS "mtime",
                     substr(line, 43) AS "key"
              FROM {LINES})
        WHERE "key" <> '' AND "size" NOT IN ('', '-', 'PRE')
    """,
}


def validate_text_framing(data: bytes, mode: str) -> None:
    """Refuse ambiguous text rows before DuckDB can filter or join fields."""
    if mode in TSV_MODES:
        for number, line in enumerate(data.splitlines(), 1):
            if not line:
                continue
            fields = line.split(b"\t")
            if fields[:3] == [b"key", b"size", b"last_modified"]:
                continue
            if len(fields) != 6:
                raise ContractViolation(
                    f"native TSV line {number} has {len(fields)} fields, expected 6; "
                    "a TAB in a key is unrepresentable",
                    field="key",
                )
            if fields[5] not in (b"", b"OBJECT"):
                continue
            if CONTROL_ESCAPE.search(fields[0]):
                raise ContractViolation(
                    "text-sink key carries an ambiguous swath \\xHH control escape; "
                    "use recursive-jsonl",
                    field="key",
                )
    elif mode == "recursive-table":
        for number, line in enumerate(data.splitlines(), 1):
            if not line:
                continue
            if len(line) < 43:
                raise ContractViolation(f"table line {number} is shorter than 43 bytes")
            if line[14:16] != b"  " or line[40:42] != b"  ":
                raise ContractViolation(f"table line {number} has overflowing fixed-width columns")
            key = line[42:]
            if CONTROL_ESCAPE.search(key):
                raise ContractViolation(
                    "text-sink key carries an ambiguous swath \\xHH control escape; "
                    "use recursive-jsonl",
                    field="key",
                )


# The key column is Parquet BINARY annotated STRING (0.3.0; plain BINARY
# before), so DuckDB returns it as VARCHAR and the emit boundary re-encodes it
# as UTF-8: byte-exact because swath refuses to write a key that is not valid
# UTF-8, which is the one Swath output path with that property. last_modified
# is TIMESTAMP WITH TIME ZONE; rendering it through UTC keeps the contract's
# whole-second Zulu spelling.
PARQUET_QUERY = """
    SELECT "key", CAST("size" AS VARCHAR), "etag",
           strftime("last_modified" AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ'),
           "storage_class"
    FROM read_parquet($glob)
    WHERE coalesce("row_type", 'OBJECT') = 'OBJECT'
"""

PARQUET_COUNT_QUERY = """
    SELECT count(*)
    FROM read_parquet($glob)
    WHERE coalesce("row_type", 'OBJECT') = 'OBJECT'
"""

TSV_DATASET_COUNT_QUERY = f"""
    SELECT count(*) FILTER (
               WHERE coalesce("row_type", '') IN ('', 'OBJECT')
                 AND NOT ("key" = 'key' AND "size" = 'size'
                          AND "last_modified" = 'last_modified')
           ),
           count(*) FILTER (
               WHERE coalesce("row_type", '') IN ('', 'OBJECT')
                 AND NOT ("key" = 'key' AND "size" = 'size'
                          AND "last_modified" = 'last_modified')
                 AND regexp_matches("key", $control_escape)
           )
    FROM read_csv($glob, delim = '\t', quote = '', escape = '', header = false,
                  auto_detect = false, columns = {SWATH_TSV_COLUMNS},
                  compression = 'auto', strict_mode = true)
"""


def _dataset_root(dataset: str) -> Path:
    """Accept either Swath's dataset root or the native parent containing it."""
    root = Path(dataset)
    if (root / "_SUCCESS").is_file() or (root / "data").is_dir():
        return root
    listing = root / "listing"
    if listing.exists():
        return listing
    return root


def _dataset_parts(dataset: str, pattern: str, description: str) -> tuple[Path, list[Path]]:
    root = _dataset_root(dataset)
    if not (root / "_SUCCESS").is_file():
        raise ValueError(
            f"dataset has no _SUCCESS marker under {root}; the swath run did not finish writing it"
        )
    parts = sorted(root.glob(f"data/{pattern}"))
    if not parts:
        raise ValueError(f"no {description} parts under {root / 'data'}")
    return root, parts


@contextmanager
def _open_tsv_part(part: Path) -> Iterator[IO[bytes]]:
    """Open an uncompressed or Zstandard-compressed TSV part as a byte stream."""
    with part.open("rb") as source:
        if part.suffix == ".zst":
            with zstandard.ZstdDecompressor().stream_reader(source) as decoded:
                yield decoded
        else:
            yield source


def _tsv_dataset_parts(dataset: str) -> tuple[Path, list[Path]]:
    root = _dataset_root(dataset)
    if not (root / "_SUCCESS").is_file():
        raise ValueError(
            f"dataset has no _SUCCESS marker under {root}; the swath run did not finish writing it"
        )
    parts = sorted((*root.glob("data/*.tsv"), *root.glob("data/*.tsv.zst")))
    if not parts:
        raise ValueError(f"no TSV or TSV.zst parts under {root / 'data'}")
    return root, parts


def count_rows(data: bytes, mode: str, prefix: str = "", native_root: str = "") -> int:
    if mode in PARQUET_MODES:
        import duckdb

        try:
            _root, parts = _dataset_parts(native_root, "*.parquet", "Parquet")
            row = (
                connect()
                .execute(PARQUET_COUNT_QUERY, {"glob": [str(part) for part in parts]})
                .fetchone()
            )
        except (ValueError, duckdb.Error) as exc:
            raise ValueError(f"dataset is not countable Parquet: {exc}") from exc
        if row is None or not isinstance(row[0], int):  # pragma: no cover - DuckDB invariant
            raise RuntimeError("DuckDB count query returned no integer row")
        return row[0]
    if mode in TSV_DATASET_MODES:
        _root, parts = _tsv_dataset_parts(native_root)
        import duckdb

        try:
            row = (
                connect()
                .execute(
                    TSV_DATASET_COUNT_QUERY,
                    {
                        "glob": [str(part) for part in parts],
                        "control_escape": r"\\x[0-9a-fA-F]{2}",
                    },
                )
                .fetchone()
            )
        except duckdb.Error as exc:
            raise ValueError(f"dataset is not countable TSV: {exc}") from exc
        if row is None or not isinstance(row[0], int):  # pragma: no cover - DuckDB invariant
            raise RuntimeError("DuckDB count query returned no integer row")
        if row[1]:
            raise ContractViolation(
                "text-sink key carries an ambiguous swath \\xHH control escape; "
                "use recursive-jsonl",
                field="key",
            )
        return row[0]
    if mode == "recursive-table":
        count = 0
        for number, line in enumerate(iter_lf_lines(data), 1):
            if not line:
                continue
            if len(line) < 43:
                raise ContractViolation(f"table line {number} is shorter than 43 bytes")
            if line[14:16] != b"  " or line[40:42] != b"  ":
                raise ContractViolation(f"table line {number} has overflowing fixed-width columns")
            key = line[42:]
            if CONTROL_ESCAPE.search(key):
                raise ContractViolation(
                    "text-sink key carries an ambiguous swath \\xHH control escape; "
                    "use recursive-jsonl",
                    field="key",
                )
            size = line[:14].strip(b" ")
            count += bool(key) and size not in (b"", b"-", b"PRE")
        return count
    if mode in TSV_MODES:
        sql = QUERIES["tsv"]
    elif mode in QUERIES:
        sql = QUERIES[mode]
    else:
        raise ValueError(f"unknown mode: {mode}")
    validate_text_framing(data, mode)
    with staged(data) as path:
        return count_query(connect(), sql, {"path": path})


def _normalize_parquet_dataset(out: IO[bytes], mode: str, dataset: str) -> int:
    import duckdb

    # swath writes _SUCCESS last, after the manifest (v0.2.2). Without it the
    # dataset is a killed run's valid-but-short parts, which would normalize
    # cleanly into a short listing the verifier would blame on the tool.
    try:
        _root, parts = _dataset_parts(dataset, "*.parquet", "Parquet")
    except ValueError as exc:
        print(f"normalize.py: {exc}", file=sys.stderr)
        return UNREADABLE_EXIT
    try:
        result = connect().execute(PARQUET_QUERY, {"glob": [str(part) for part in parts]})
    except duckdb.Error as exc:
        print(f"normalize.py: dataset is not readable Parquet: {exc}", file=sys.stderr)
        return UNREADABLE_EXIT
    emit_result(out, result)
    return 0


def _normalize_tsv_dataset(out: IO[bytes], dataset: str) -> int:
    try:
        _root, parts = _tsv_dataset_parts(dataset)
    except ValueError as exc:
        print(f"normalize.py: {exc}", file=sys.stderr)
        return UNREADABLE_EXIT
    for part in parts:
        with _open_tsv_part(part) as source:
            for number, line in enumerate(iter_lf_lines(source), 1):
                if not line:
                    continue
                fields = line.split(b"\t")
                if fields[:3] == [b"key", b"size", b"last_modified"]:
                    continue
                if len(fields) != 6:
                    raise ContractViolation(
                        f"native TSV line {number} has {len(fields)} fields, expected 6; "
                        "a TAB in a key is unrepresentable",
                        field="key",
                    )
                if fields[5] in (b"", b"OBJECT") and CONTROL_ESCAPE.search(fields[0]):
                    raise ContractViolation(
                        "text-sink key carries an ambiguous swath \\xHH control escape; "
                        "use recursive-jsonl",
                        field="key",
                    )
        with _open_tsv_part(part) as source:
            for line in iter_lf_lines(source):
                if not line:
                    continue
                fields = line.split(b"\t")
                if fields[:3] == [b"key", b"size", b"last_modified"]:
                    continue
                if fields[5] not in (b"", b"OBJECT"):
                    continue
                emit(
                    out,
                    fields[0],
                    size=fields[1].decode("ascii"),
                    etag=fields[3].decode("ascii") or None,
                    mtime=re.sub(rb"\.[0-9]+Z$", b"Z", fields[2]).decode("ascii"),
                    storage_class=fields[4].decode("ascii") or None,
                )
    return 0


def normalize(
    out: IO[bytes],
    data: bytes,
    mode: str,
    prefix: str = "",
    dataset: str = "",
    config: Mapping[str, object] | None = None,
) -> int:
    if mode in PARQUET_MODES:
        return _normalize_parquet_dataset(out, mode, dataset)
    if mode in TSV_DATASET_MODES:
        return _normalize_tsv_dataset(out, dataset)
    if mode in STREAM_TSV_MODES:
        sql = QUERIES["tsv"]
    elif mode in QUERIES:
        sql = QUERIES[mode]
    else:
        print(f"normalize.py: unknown mode: {mode}", file=sys.stderr)
        return UNKNOWN_MODE_EXIT
    validate_text_framing(data, mode)
    with staged(data) as path:
        emit_result(out, connect().execute(sql, {"path": path}))
    return 0


def main(argv: list[str] | None = None) -> int:
    return normalizer_main(
        normalize,
        modes=MODES,
        dataset_modes=DATASET_MODES,
        prog="swath normalize",
        argv=argv,
        broken_pipe_is_success=True,
        error_exit=UNKNOWN_MODE_EXIT,
    )


if __name__ == "__main__":
    sys.exit(main())
