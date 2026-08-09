"""DuckDB-backed listing adapters: stage the payload, emit the result set.

Most listing tools write something DuckDB already reads — JSON, JSONL, TSV, CSV,
Parquet — or a line format ``regexp_extract`` reaches in one expression. For
those an adapter is a SELECT that produces the five contract columns, not a
parser. This module is the two pieces such an adapter must not re-derive:
somewhere for DuckDB to read stdin from, and the emit boundary.

:func:`emit_result` validates through :class:`~s3_listing_study.manager.contract.Record`
and writes through its ``to_line``, so a SQL adapter is held to exactly the bar a
row-at-a-time :func:`~s3_listing_study.manager.contract.emit` adapter is. It is the
set-at-a-time twin of ``emit``, not a second contract.

Text, and bytes where the sink has them
---------------------------------------
A DuckDB ``VARCHAR`` is UTF-8 text, so a key whose bytes are not valid UTF-8
cannot travel a text sink's path. The key column is therefore also accepted as a
``BLOB`` and carried byte-for-byte, which is what a binary sink — Swath's
Parquet dataset — actually holds. The four remaining columns stay ``VARCHAR`` or
``NULL``: a size, an etag, a timestamp and a storage class are ASCII by
construction in every mode the study reads.

What this path does NOT reintroduce is the escaping bug it replaces. A key
containing TAB, NEWLINE or CR arrives here as those bytes and is refused at the
boundary with ``ContractViolation``, exactly as on the bytes path — never
C-escaped into a key the bucket does not hold.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import IO, Any, Protocol

from .contract import FIELD_NAMES, RECORD_SEPARATOR, ContractViolation, Record

FETCH_BATCH = 10_000
"""Rows per ``fetchmany``. Bounds memory on a full-bucket listing."""


class ResultSet(Protocol):
    """The slice of a DuckDB cursor :func:`emit_result` uses."""

    def fetchmany(self, size: int = ...) -> list[Any]: ...


def connect() -> Any:
    """A DuckDB connection configured the way every listing adapter needs it.

    ``preserve_insertion_order`` is DuckDB's default (1.5.5) and is set here
    anyway, pinned against a future release changing that default: row order IS
    the adapter's output and the verifier compares bytes, so a parallel scan
    that finished out of order would be a different ``verify.md`` for the same
    listing. ``tests/test_adapters.py`` asserts the order offline, over a payload
    big enough for the readers to parallelise.
    """
    import duckdb

    connection = duckdb.connect()
    connection.execute("SET preserve_insertion_order = true")
    return connection


@contextmanager
def staged(data: bytes) -> Iterator[str]:
    """Put stdin somewhere DuckDB can read it, and yield the path.

    The adapter contract is "raw tool output on stdin", but DuckDB's readers take
    a path and want to seek, and stdin is a pipe. Staging is one write of bytes
    the caller already holds, in a temp file removed on exit; it never alters the
    payload and never writes anywhere the study reads from.
    """
    with tempfile.NamedTemporaryFile(suffix=".payload") as handle:
        handle.write(data)
        handle.flush()
        yield handle.name


def _text(value: Any, field: str) -> str | None:
    """A ``VARCHAR``/``NULL`` column as ``str``/``None``, or a contract violation.

    ``tools/**`` is outside mypy's reach, so a query that lost a
    ``CAST(… AS VARCHAR)`` hands this boundary a Python ``int`` (or a
    ``datetime``) and nothing static catches it. Coercing it silently would
    launder a query bug into output; letting it through raised ``TypeError`` out
    of a regex — the wrong error class at the emit boundary, where the whole
    point is that a bad record is a ``ContractViolation``: an adapter-contract
    violation, never a tool FAIL and never a PASS.
    """
    if value is None or isinstance(value, str):
        return value
    raise ContractViolation(
        f"column must be VARCHAR or NULL; the query produced "
        f"{type(value).__name__} {value!r} — the adapter's SELECT is missing a CAST",
        field=field,
    )


def _key(value: Any) -> bytes:
    """The key column as raw bytes, from either a ``VARCHAR`` or a ``BLOB``.

    A ``BLOB`` column travels byte-for-byte, so a format that stores the key as
    raw bytes — Swath's Parquet sink does — keeps its fidelity through this path
    instead of being narrowed to what UTF-8 can spell. ``VARCHAR`` stays
    supported because most adapters read a text sink, where the key has already
    been through the tool's own decode.
    """
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    raise ContractViolation(
        f"key column must be VARCHAR, BLOB or NULL; the query produced "
        f"{type(value).__name__} {value!r} — the adapter's SELECT is missing a CAST",
        field=FIELD_NAMES[0],
    )


def _line(row: Any) -> bytes:
    key_value, *rest = row
    size, etag, mtime, storage_class = (
        _text(value, name) for value, name in zip(rest, FIELD_NAMES[1:], strict=True)
    )
    return Record(
        key=_key(key_value),
        size=size,
        etag=etag,
        mtime=mtime,
        storage_class=storage_class,
    ).to_line()


def emit_result(out: IO[bytes], result: ResultSet) -> None:
    """Write a 5-column DuckDB result set as contract-v2 records.

    Columns are positional, in ``FIELD_NAMES`` order: key, size, etag, mtime,
    storage_class. The key is ``VARCHAR``, ``BLOB`` or ``NULL``; the other four
    are ``VARCHAR`` or ``NULL`` — the SQL does its own casting, because the shape
    of a size or a timestamp is a fact about the tool and belongs in that tool's
    query. ``NULL`` and ``'-'`` both mean the mode does not expose the field.
    """
    while True:
        rows = result.fetchmany(FETCH_BATCH)
        if not rows:
            return
        out.write(RECORD_SEPARATOR.join(_line(row) for row in rows) + RECORD_SEPARATOR)
