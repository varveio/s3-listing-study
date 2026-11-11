#!/usr/bin/env python3
"""tools/swath-go/adapter/normalize.py <mode> [prefix] [--dataset DIR | --input FILE].

Swath (Go port) output adapter.

Reads one Swath (Go port) product and writes contract v2 on stdout, one record
per line::

    key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class

Two products, read differently.

**The sorted dataset** (``recursive-parquet-sorted``, ``resume-parquet-sorted``)
is a directory, so those modes read ``--dataset`` and never stdin. It is a swath
dataset: a SeekParquet dataset (``format`` ``seekparquet``, ``format_version``
0, ``draft_revision`` 1) carrying a registered swath schema id and the
``extensions.swath`` member (``go/docs/dataset.md`` at the pinned revision). It
is read the way upstream specifies a reader should, not by globbing the
directory:

- ``manifest.json`` is the commit. It is installed last, by an atomic rename;
  without it there is no dataset, only a killed run's early-published parts,
  which would normalize into a short listing the verifier would blame on the
  tool. So a missing manifest is refused, and there is no ``_SUCCESS`` to look
  for.
- The manifest must say ``"format": "seekparquet"``, ``"format_version": 0``,
  ``"draft_revision": 1``, schema ``swath/s3-objects/v1`` and key column
  ``key``, and carry ``extensions.swath`` version 1 with source kind ``s3``. A
  Java (format-1) manifest, and the ``swath-dataset`` format-2 manifest the
  port wrote at the capsule's earlier pins, are refused rather than read
  through a fallback: neither is this revision's product.
- Coverage (``extensions.swath.coverage``) is accepted only when ``complete`` is
  true, ``stop_reason`` is ``completed``, ``skipped_entries`` is 0 and
  ``filters`` is present and empty; a missing field refuses. The scope
  (``extensions.swath.scope``) must be the case's own: ``whole`` for an empty
  prefix, otherwise ``prefix`` with exactly that prefix.
- The parts are the manifest's ``parts``, read in manifest order (which is key
  order: parts are disjoint and ascending), each a relative path under ``data/``
  whose size on disk equals the manifest's ``bytes``. DuckDB reads that list by
  column name (``union_by_name``, as upstream tells a DuckDB reader to), and
  the count must equal the sum of the manifest's ``rows``.

Columns: ``key`` is BYTE_ARRAY annotated STRING in the S3 schema (S3 keys are
UTF-8), so DuckDB returns VARCHAR and the emit boundary re-encodes it exactly.
``last_modified`` is ``TIMESTAMP(MICROS, UTC)``; rendering it through UTC with
whole seconds gives the contract's ``YYYY-MM-DDTHH:MM:SSZ``. The schema has no
``row_type``: every row is a current object, so there is nothing to filter.

What this does not check: the per-part ``sha256`` and the key bounds in the
manifest, and the Parquet footer stamps. The row count and file sizes are
checked against the manifest; byte-level part integrity is not.

**The TSV file** (``recursive-tsv``, plain; ``recursive-tsv-zstd``, one zstd
stream) is one file the port wrote itself, so it arrives as the input stream
(``--input`` or stdin), never as a dataset. Its shape is ``go/docs/output.md``
§ TSV at the pinned revision and ``go/internal/textout/textout.go``:

- One header line, exactly the S3 schema's nine column names, ``key size
  last_modified etag storage_class owner_id owner_display_name
  checksum_algorithm checksum_type`` (tab-separated). Anything else is refused
  rather than guessed at, and a file without it is not this tool's output.
- Every row has those nine fields. A field that is exactly ``\\N`` is null.
  Fields are unescaped by the port's TSV grammar: ``\\\\`` a backslash,
  ``\\t``, ``\\n`` and ``\\r`` those bytes, ``\\xHH`` (two lowercase hex
  digits) the byte HH; any other backslash is refused. A key whose bytes the
  contract cannot frame (a tab, LF or CR) is refused at the emit boundary, as
  it is for every tool, and a null key is refused.
- ``last_modified`` is printed in UTC with six fraction digits and ``Z``; it is
  cut to whole seconds. ``etag`` is stored without S3's quotes, as in the
  dataset, and a quoted one is refused.
- There is no ``row_type``: every row is an object. The checksum and owner
  columns have no contract field and are ignored.

``count_rows`` counts the data rows with DuckDB, which reads the zstd stream
directly, and refuses the same files ``normalize`` refuses for their header or
for a key that is null or carries an escape that is unknown or would unescape
to an unframeable byte, so the two never disagree about whether a listing is
usable.

``prefix`` is not used to rebuild keys: the port lists ``s3://bucket/prefix``
and stores whole keys. Everything here runs after the timed window.
"""

from __future__ import annotations

import io
import json
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import IO, Any

import zstandard

from benchmark.runtime.contract import ContractViolation, emit
from benchmark.runtime.duckdb_adapter import connect, emit_result, iter_lf_lines, staged
from benchmark.runtime.normalizer_cli import normalizer_main

UNKNOWN_MODE_EXIT = 2
UNREADABLE_EXIT = 1

DATASET_MODES = frozenset({"recursive-parquet-sorted", "resume-parquet-sorted"})
"""Both write the same directory dataset; neither has a stream product."""

TSV_COMPRESSION = {"recursive-tsv": "none", "recursive-tsv-zstd": "zstd"}
"""The file modes and how their one file is compressed (the port's ``-o`` name)."""

MODES = DATASET_MODES | frozenset(TSV_COMPRESSION)

DATASET_NAME = "listing"
MANIFEST = "manifest.json"
FORMAT = "seekparquet"
FORMAT_VERSION = 0
DRAFT_REVISION = 1
SWATH_EXTENSION_VERSION = 1
SCHEMA_ID = "swath/s3-objects/v1"
KEY_COLUMN = "key"

TSV_COLUMNS = (
    "key",
    "size",
    "last_modified",
    "etag",
    "storage_class",
    "owner_id",
    "owner_display_name",
    "checksum_algorithm",
    "checksum_type",
)
TSV_HEADER = "\t".join(TSV_COLUMNS).encode("ascii")

NULL = b"\\N"
"""The port's TSV null: a field that is exactly backslash-N."""

ESCAPE = re.compile(rb"\\(?:([\\tnr])|x([0-9a-f]{2}))")
SIMPLE_ESCAPES = {b"\\": b"\\", b"t": b"\t", b"n": b"\n", b"r": b"\r"}

SAFE_KEY_RE = r"^(?:[^\\]|\\\\|\\x(?:0[0-8bcef]|[1-9a-f][0-9a-f]))*$"
"""A key whose escapes are all known and none unescapes to a tab, LF or CR: the
keys ``normalize`` can emit, as an RE2 pattern DuckDB applies while counting.
The null field ``\\N`` does not match, so a null key is refused too."""

LAST_MODIFIED_RE = re.compile(
    rb"\A([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})\.[0-9]{6}Z\Z"
)

PARQUET_QUERY = """
    SELECT "key", CAST("size" AS VARCHAR), "etag",
           strftime("last_modified" AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ'),
           "storage_class"
    FROM read_parquet($paths, union_by_name = true)
"""

COUNT_QUERY = "SELECT count(*) FROM read_parquet($paths, union_by_name = true)"

TSV_READ_COLUMNS = "{" + ", ".join(f"'{name}': 'VARCHAR'" for name in TSV_COLUMNS) + "}"

TSV_COUNT_QUERY = """
    SELECT count(*),
           count(*) FILTER (WHERE "key" IS NULL OR NOT regexp_full_match("key", $safe_key))
    FROM read_csv($path, delim = '\t', quote = '', escape = '', header = true,
                  auto_detect = false, strict_mode = true, compression = '{compression}',
                  columns = {columns})
"""
"""Declared, never sniffed, as the Java capsule's TSV count is. ``header = true``
skips the first line; :func:`_check_header` has already read it and refused
anything but the port's header."""


class DatasetRefused(ValueError):
    """The directory is not a committed, whole-scope swath S3 dataset."""


# --- the sorted dataset ---------------------------------------------------------------------


def dataset_root(dataset: str) -> Path:
    """Accept the dataset root itself or the sink that contains it."""
    root = Path(dataset)
    if (root / MANIFEST).is_file() or (root / "data").is_dir() or (root / ".swath").is_dir():
        return root
    listing = root / DATASET_NAME
    if listing.is_dir():
        return listing
    return root


def _field(document: Mapping[str, Any], name: str, where: str) -> Any:
    if name not in document:
        raise DatasetRefused(f"{where} has no {name!r}")
    return document[name]


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise DatasetRefused(f"{where} is not an object")
    return value


def _check_coverage(swath: Mapping[str, Any]) -> None:
    coverage = _mapping(_field(swath, "coverage", "extensions.swath"), "manifest coverage")
    complete = _field(coverage, "complete", "coverage")
    stop_reason = _field(coverage, "stop_reason", "coverage")
    skipped = _field(coverage, "skipped_entries", "coverage")
    filters = _field(coverage, "filters", "coverage")
    if complete is not True or stop_reason != "completed":
        raise DatasetRefused(
            f"dataset coverage is not complete (complete={complete!r}, "
            f"stop_reason={stop_reason!r}); a partial dataset is not a listing"
        )
    if isinstance(skipped, bool) or skipped != 0:
        raise DatasetRefused(f"dataset skipped {skipped!r} entries")
    if filters != []:
        raise DatasetRefused(f"dataset was filtered: {filters!r}")


def _check_scope(swath: Mapping[str, Any], prefix: str) -> None:
    scope = _mapping(_field(swath, "scope", "extensions.swath"), "manifest scope")
    kind = _field(scope, "kind", "scope")
    if not prefix:
        if kind != "whole":
            raise DatasetRefused(f"case lists the whole bucket but the dataset scope is {kind!r}")
        return
    listed = scope.get("prefix")
    if kind != "prefix" or listed != prefix:
        raise DatasetRefused(
            f"case lists prefix {prefix!r} but the dataset scope is {kind!r} {listed!r}"
        )


def _part_path(root: Path, part: Any, index: int) -> tuple[Path, int]:
    where = f"manifest part {index}"
    entry = _mapping(part, where)
    path = _field(entry, "path", where)
    rows = _field(entry, "rows", where)
    size = _field(entry, "bytes", where)
    if not isinstance(path, str) or not path:
        raise DatasetRefused(f"{where} path is not a string")
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts or relative.parts[:1] != ("data",):
        raise DatasetRefused(f"{where} path {path!r} is not a relative path under data/")
    for name, value in (("rows", rows), ("bytes", size)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DatasetRefused(f"{where} {name} is not a non-negative integer: {value!r}")
    resolved = root.joinpath(*relative.parts)
    if not resolved.is_file():
        raise DatasetRefused(f"{where} {path!r} is missing")
    actual = resolved.stat().st_size
    if actual != size:
        raise DatasetRefused(f"{where} {path!r} is {actual} bytes; the manifest says {size}")
    return resolved, rows


def committed_parts(dataset: str, prefix: str = "") -> tuple[list[Path], int]:
    """The manifest's parts in manifest order, and the rows it says they hold."""
    root = dataset_root(dataset)
    manifest_path = root / MANIFEST
    if not manifest_path.is_file():
        raise DatasetRefused(
            f"no {MANIFEST} under {root}: the run did not commit (the manifest is the "
            "commit, and parts published before it are not a dataset)"
        )
    try:
        manifest = _mapping(json.loads(manifest_path.read_text(encoding="utf-8")), "manifest")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DatasetRefused(f"{manifest_path} is unreadable: {exc}") from exc
    if "format" not in manifest:
        raise DatasetRefused(
            f"{manifest_path} is not a SeekParquet manifest (no 'format' field); "
            "a format-1 dataset is the Java capsule's product"
        )
    identity = (
        manifest["format"],
        manifest.get("format_version"),
        manifest.get("draft_revision"),
    )
    if identity != (FORMAT, FORMAT_VERSION, DRAFT_REVISION):
        raise DatasetRefused(
            f"manifest format {identity[0]!r} version {identity[1]!r} draft revision "
            f"{identity[2]!r}; want {FORMAT!r} version {FORMAT_VERSION} draft revision "
            f"{DRAFT_REVISION} (a 'swath-dataset' manifest is an earlier build's format)"
        )
    if manifest.get("schema_id") != SCHEMA_ID:
        raise DatasetRefused(f"manifest schema {manifest.get('schema_id')!r}; want {SCHEMA_ID!r}")
    key = _mapping(_field(manifest, "key", "manifest"), "manifest key")
    if key.get("column") != KEY_COLUMN:
        raise DatasetRefused(f"manifest key column {key.get('column')!r}; want {KEY_COLUMN!r}")
    extensions = _mapping(_field(manifest, "extensions", "manifest"), "manifest extensions")
    swath = _mapping(_field(extensions, "swath", "manifest extensions"), "extensions.swath")
    version = _field(swath, "version", "extensions.swath")
    if isinstance(version, bool) or version != SWATH_EXTENSION_VERSION:
        raise DatasetRefused(
            f"extensions.swath version {version!r}; want {SWATH_EXTENSION_VERSION}"
        )
    source = _mapping(_field(swath, "source", "extensions.swath"), "manifest source")
    if source.get("kind") != "s3":
        raise DatasetRefused(f"manifest source kind {source.get('kind')!r}; want 's3'")
    _check_coverage(swath)
    _check_scope(swath, prefix)
    parts = _field(manifest, "parts", "manifest")
    if not isinstance(parts, list):
        raise DatasetRefused("manifest parts is not a list")
    paths: list[Path] = []
    total = 0
    for index, part in enumerate(parts):
        path, rows = _part_path(root, part, index)
        paths.append(path)
        total += rows
    return paths, total


def _count_dataset(paths: list[Path]) -> int:
    if not paths:
        return 0
    row = connect().execute(COUNT_QUERY, {"paths": [str(path) for path in paths]}).fetchone()
    if row is None or not isinstance(row[0], int):  # pragma: no cover - DuckDB invariant
        raise RuntimeError("DuckDB count query returned no integer row")
    return row[0]


def _normalize_dataset(out: IO[bytes], prefix: str, dataset: str) -> int:
    import duckdb

    try:
        paths, expected = committed_parts(dataset, prefix)
        counted = _count_dataset(paths)
    except DatasetRefused as exc:
        print(f"normalize.py: {exc}", file=sys.stderr)
        return UNREADABLE_EXIT
    except duckdb.Error as exc:
        print(f"normalize.py: dataset is not readable Parquet: {exc}", file=sys.stderr)
        return UNREADABLE_EXIT
    # The same row check count_rows makes, before anything is emitted: a dataset
    # the worker would refuse to count must not verify either.
    if counted != expected:
        print(
            f"normalize.py: dataset parts hold {counted} rows; the manifest says {expected}",
            file=sys.stderr,
        )
        return UNREADABLE_EXIT
    if not paths:
        return 0
    try:
        result = connect().execute(PARQUET_QUERY, {"paths": [str(path) for path in paths]})
    except duckdb.Error as exc:
        print(f"normalize.py: dataset is not readable Parquet: {exc}", file=sys.stderr)
        return UNREADABLE_EXIT
    emit_result(out, result)
    return 0


# --- the TSV file ---------------------------------------------------------------------------


class _Rejoined:
    """A stream whose first bytes were already read to classify it."""

    def __init__(self, head: bytes, rest: Any) -> None:
        self._head, self._rest = head, rest

    def read(self, size: int = -1) -> bytes:
        if self._head:
            head, self._head = self._head, b""
            if size < 0:
                return head + self._rest.read()
            return head if len(head) >= size else head + self._rest.read(size - len(head))
        return self._rest.read(size)


def _empty_input(data: Any) -> tuple[bool, Any]:
    """Whether the input is the harness's empty listing, and the input to read.

    The port writes the header even for no rows, so the file a TSV run leaves
    is never empty. A zero-byte or newline-only input is what the harness hands
    every normalizer as "nothing listed" (a verify over an empty prefix), and it
    answers zero rows here as it does for every other capsule. Anything longer
    must be the port's file.
    """
    if isinstance(data, bytes):
        return data in (b"", b"\n"), data
    head = data.read(2)
    return head in (b"", b"\n"), _Rejoined(head, data)


@contextmanager
def _decoded(data: Any, mode: str) -> Iterator[Any]:
    """The TSV bytes as a stream, through one zstd decoder for the compressed mode."""
    source = io.BytesIO(data) if isinstance(data, bytes) else data
    if TSV_COMPRESSION[mode] == "none":
        yield source
        return
    reader = zstandard.ZstdDecompressor().stream_reader(source, read_across_frames=True)
    try:
        yield reader
    finally:
        reader.close()


class _Framing:
    """The physical framing both TSV entry points require, checked as bytes stream past.

    One implementation for ``normalize`` and ``count_rows``, plain and decoded
    zstd alike: every line ends with LF (so the file does too), and no line is
    empty, since the port writes neither a blank line nor an unterminated row.
    It counts the LF-terminated lines, which ``count_rows`` holds DuckDB's row
    count to. Memory is one chunk; nothing is buffered beyond it.
    """

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._last = b""
        self.lines = 0

    def read(self, size: int = -1) -> bytes:
        chunk: bytes = self._stream.read(size)
        if chunk:
            if (self._last == b"\n" and chunk[:1] == b"\n") or b"\n\n" in chunk:
                raise ContractViolation("native TSV has an empty line; the port writes none")
            self.lines += chunk.count(b"\n")
            self._last = chunk[-1:]
        return chunk

    def finish(self) -> int:
        """Drain what is left and return the LF-terminated line count."""
        while self.read(LINE_CHUNK):
            pass
        if self._last != b"\n":
            raise ContractViolation("native TSV's last line is not terminated by LF")
        return self.lines


LINE_CHUNK = 1 << 20


def _check_header(line: bytes) -> None:
    if line != TSV_HEADER:
        raise ContractViolation(
            f"native TSV header is {line[:200]!r}; want S3's nine columns {TSV_HEADER!r}"
        )


def unescape_field(field: bytes, number: int, name: str = "key") -> bytes:
    """Reverse the port's TSV escapes; refuse any other backslash."""
    parts: list[bytes] = []
    position = 0
    for match in ESCAPE.finditer(field):
        if b"\\" in field[position : match.start()]:
            break
        parts.append(field[position : match.start()])
        simple, code = match.group(1), match.group(2)
        parts.append(SIMPLE_ESCAPES[simple] if simple else bytes([int(code, 16)]))
        position = match.end()
    rest = field[position:]
    if b"\\" in rest:
        raise ContractViolation(
            f"native TSV line {number}: {name} has a backslash that is not one of the port's "
            f"escapes: {field[:200]!r}",
            field="key" if name == "key" else None,
        )
    parts.append(rest)
    return b"".join(parts)


def _key(value: bytes, number: int) -> bytes:
    if value == NULL:
        raise ContractViolation(f"native TSV line {number}: key is null", field="key")
    return unescape_field(value, number)


def _mtime(value: bytes, number: int) -> str | None:
    if value == NULL:
        return None
    match = LAST_MODIFIED_RE.fullmatch(value)
    if match is None:
        raise ContractViolation(
            f"native TSV line {number}: last_modified {value!r} is not UTC with six "
            "fraction digits and Z",
            field="mtime",
        )
    return match.group(1).decode("ascii") + "Z"


def _text(value: bytes, number: int, name: str) -> str | None:
    """A nullable ASCII field: ``\\N`` is null, anything else unescaped."""
    if value == NULL:
        return None
    return unescape_field(value, number, name).decode("ascii")


def _etag(value: bytes, number: int) -> str | None:
    etag = _text(value, number, "etag")
    if etag is not None and etag[:1] == '"':
        raise ContractViolation(
            f"native TSV line {number}: etag {etag!r} keeps S3's quotes; the port strips them",
            field="etag",
        )
    return etag


def _normalize_tsv(out: IO[bytes], data: Any, mode: str) -> int:
    empty, data = _empty_input(data)
    if empty:
        return 0
    with _decoded(data, mode) as stream:
        framing = _Framing(stream)
        lines = iter_lf_lines(framing)
        _check_header(next(lines))
        pending: bytes | None = None
        number = 1
        for line in lines:
            if pending is not None:
                _emit_row(out, pending, number)
            number += 1
            pending = line
        # The last framing remainder after the final LF is empty; _Framing
        # refuses an unterminated last row before it would be emitted.
        framing.finish()
    return 0


def _emit_row(out: IO[bytes], line: bytes, number: int) -> None:
    fields = line.split(b"\t")
    if len(fields) != len(TSV_COLUMNS):
        raise ContractViolation(
            f"native TSV line {number} has {len(fields)} fields, expected {len(TSV_COLUMNS)}"
        )
    key, size, last_modified, etag, storage_class, _id, _name, _algorithm, _type = fields
    try:
        emit(
            out,
            _key(key, number),
            size=_text(size, number, "size"),
            etag=_etag(etag, number),
            mtime=_mtime(last_modified, number),
            storage_class=_text(storage_class, number, "storage_class"),
        )
    except UnicodeDecodeError as exc:
        raise ContractViolation(f"native TSV line {number} has a non-ASCII field: {exc}") from exc


def _count_tsv(data: Any, mode: str) -> int:
    import duckdb

    # No empty-input shortcut here: a native product the port wrote always
    # decodes and starts with the header, so a zero-byte or LF-only file is a
    # broken product, not an empty listing.
    with _decoded(data, mode) as stream:
        framing = _Framing(stream)
        _check_header(next(iter_lf_lines(framing)))
        lines = framing.finish()
    sql = TSV_COUNT_QUERY.format(compression=TSV_COMPRESSION[mode], columns=TSV_READ_COLUMNS)
    with staged(data) as path:
        try:
            row = connect().execute(sql, {"path": path, "safe_key": SAFE_KEY_RE}).fetchone()
        except duckdb.Error as exc:
            raise ValueError(f"the TSV file is not countable: {exc}") from exc
    if row is None or not isinstance(row[0], int):  # pragma: no cover - DuckDB invariant
        raise RuntimeError("DuckDB count query returned no integer row")
    if row[1]:
        raise ContractViolation(
            f"{row[1]} TSV key(s) are empty, carry an unknown escape or one that unescapes "
            "to a tab, LF or CR, which the contract cannot frame",
            field="key",
        )
    if row[0] != lines - 1:
        raise ContractViolation(
            f"DuckDB read {row[0]} TSV rows where the file has {lines - 1} lines under its header"
        )
    return row[0]


# --- entry points ---------------------------------------------------------------------------


def count_rows(data: Any, mode: str, prefix: str = "", native_root: str = "") -> int:
    if mode in TSV_COMPRESSION:
        return _count_tsv(data, mode)
    if mode not in DATASET_MODES:
        raise ValueError(f"unknown mode: {mode}")
    import duckdb

    try:
        paths, expected = committed_parts(native_root, prefix)
        counted = _count_dataset(paths)
    except duckdb.Error as exc:
        raise ValueError(f"dataset is not countable Parquet: {exc}") from exc
    if counted != expected:
        raise ValueError(f"dataset parts hold {counted} rows; the manifest says {expected}")
    return counted


def normalize(
    out: IO[bytes],
    data: Any,
    mode: str,
    prefix: str = "",
    dataset: str = "",
    config: Mapping[str, object] | None = None,
) -> int:
    if mode in TSV_COMPRESSION:
        return _normalize_tsv(out, data, mode)
    if mode not in DATASET_MODES:
        print(f"normalize.py: unknown mode: {mode}", file=sys.stderr)
        return UNKNOWN_MODE_EXIT
    return _normalize_dataset(out, prefix, dataset)


def main(argv: list[str] | None = None) -> int:
    return normalizer_main(
        normalize,
        modes=MODES,
        dataset_modes=DATASET_MODES,
        prog="swath-go normalize",
        argv=argv,
        broken_pipe_is_success=True,
        error_exit=UNKNOWN_MODE_EXIT,
    )


if __name__ == "__main__":
    sys.exit(main())
