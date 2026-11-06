#!/usr/bin/env python3
"""tools/swath/go/adapter/normalize.py <mode> [prefix] --dataset DIR — Swath (Go port) adapter.

Reads one committed format-2 Swath dataset and writes contract v2 on stdout,
one record per line::

    key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class

Both modes publish the same thing, a directory dataset, so neither reads stdin.

Format 2 (``go/docs/upgrade.md`` and ``go/docs/internals/parquet-output.md`` at
the pinned revision) is read the way upstream specifies a reader should, not by
globbing the directory:

- ``manifest.json`` is the commit. It is installed last, by an atomic rename;
  without it there is no dataset, only a killed run's early-published parts,
  which would normalize into a short listing the verifier would blame on the
  tool. So a missing manifest is refused, and there is no ``_SUCCESS`` to look
  for: format 2 removed it.
- The manifest must say ``"format": "swath-dataset"``, ``"format_version": 2``,
  schema ``swath/s3-objects/v1`` and source kind ``s3``. A format-1 (Java)
  manifest is refused rather than read through a fallback: it is the other
  capsule's product.
- Coverage is accepted only when ``complete`` is true, ``stop_reason`` is
  ``completed``, ``skipped_entries`` is 0 and ``filters`` is present and empty;
  a missing field refuses. The scope must be the case's own: ``whole`` for an
  empty prefix, otherwise ``prefix`` with exactly that prefix.
- The parts are the manifest's ``parts``, read in manifest order (which is key
  order: parts are disjoint and ascending), each a relative path under ``data/``
  whose size on disk equals the manifest's ``bytes``. DuckDB reads that list,
  and the count must equal the sum of the manifest's ``rows``.

Columns: ``key`` is BYTE_ARRAY annotated STRING in the S3 schema (S3 keys are
UTF-8), so DuckDB returns VARCHAR and the emit boundary re-encodes it exactly.
``last_modified`` is ``TIMESTAMP(MICROS, UTC)``; rendering it through UTC with
whole seconds gives the contract's ``YYYY-MM-DDTHH:MM:SSZ``. Format 2 carries no
``row_type``: every row is a current object, so there is nothing to filter.

What this does not check: the per-part ``md5`` and the key bounds in the
manifest, and the Parquet footer stamps. The row count and file sizes are
checked against the manifest; byte-level part integrity is not.

``prefix`` is not used to rebuild keys: the port lists ``s3://bucket/prefix``
and stores whole keys. It runs after the timed window, so DuckDB is fair game.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import IO, Any

from benchmark.runtime.duckdb_adapter import connect, emit_result
from benchmark.runtime.normalizer_cli import normalizer_main

UNKNOWN_MODE_EXIT = 2
UNREADABLE_EXIT = 1

MODES = frozenset({"recursive-parquet-sorted", "resume-parquet-sorted"})
DATASET_MODES = MODES
"""Both modes write the same directory dataset; neither has a stdout product."""

DATASET_NAME = "listing"
MANIFEST = "manifest.json"
FORMAT = "swath-dataset"
FORMAT_VERSION = 2
SCHEMA_ID = "swath/s3-objects/v1"

PARQUET_QUERY = """
    SELECT "key", CAST("size" AS VARCHAR), "etag",
           strftime("last_modified" AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ'),
           "storage_class"
    FROM read_parquet($paths)
"""

COUNT_QUERY = "SELECT count(*) FROM read_parquet($paths)"


class DatasetRefused(ValueError):
    """The directory is not a committed, whole-scope format-2 S3 dataset."""


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


def _check_coverage(manifest: Mapping[str, Any]) -> None:
    coverage = _mapping(_field(manifest, "coverage", "manifest"), "manifest coverage")
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


def _check_scope(manifest: Mapping[str, Any], prefix: str) -> None:
    scope = _mapping(_field(manifest, "scope", "manifest"), "manifest scope")
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
            f"no {MANIFEST} under {root}: the run did not commit (in format 2 the manifest "
            "is the commit, and parts published before it are not a dataset)"
        )
    try:
        manifest = _mapping(json.loads(manifest_path.read_text(encoding="utf-8")), "manifest")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DatasetRefused(f"{manifest_path} is unreadable: {exc}") from exc
    if "format" not in manifest:
        raise DatasetRefused(
            f"{manifest_path} is not a format-2 manifest (no 'format' field); "
            "a format-1 dataset is the Java capsule's product"
        )
    if manifest["format"] != FORMAT or manifest.get("format_version") != FORMAT_VERSION:
        raise DatasetRefused(
            f"manifest format {manifest['format']!r} version "
            f"{manifest.get('format_version')!r}; want {FORMAT!r} version {FORMAT_VERSION}"
        )
    if manifest.get("schema_id") != SCHEMA_ID:
        raise DatasetRefused(f"manifest schema {manifest.get('schema_id')!r}; want {SCHEMA_ID!r}")
    source = _mapping(_field(manifest, "source", "manifest"), "manifest source")
    if source.get("kind") != "s3":
        raise DatasetRefused(f"manifest source kind {source.get('kind')!r}; want 's3'")
    _check_coverage(manifest)
    _check_scope(manifest, prefix)
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


def _count(paths: list[Path]) -> int:
    if not paths:
        return 0
    row = connect().execute(COUNT_QUERY, {"paths": [str(path) for path in paths]}).fetchone()
    if row is None or not isinstance(row[0], int):  # pragma: no cover - DuckDB invariant
        raise RuntimeError("DuckDB count query returned no integer row")
    return row[0]


def count_rows(data: bytes, mode: str, prefix: str = "", native_root: str = "") -> int:
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    import duckdb

    try:
        paths, expected = committed_parts(native_root, prefix)
        counted = _count(paths)
    except duckdb.Error as exc:
        raise ValueError(f"dataset is not countable Parquet: {exc}") from exc
    if counted != expected:
        raise ValueError(f"dataset parts hold {counted} rows; the manifest says {expected}")
    return counted


def normalize(
    out: IO[bytes],
    data: bytes,
    mode: str,
    prefix: str = "",
    dataset: str = "",
    config: Mapping[str, object] | None = None,
) -> int:
    if mode not in MODES:
        print(f"normalize.py: unknown mode: {mode}", file=sys.stderr)
        return UNKNOWN_MODE_EXIT
    import duckdb

    try:
        paths, _expected = committed_parts(dataset, prefix)
    except DatasetRefused as exc:
        print(f"normalize.py: {exc}", file=sys.stderr)
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
