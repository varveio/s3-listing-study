"""Build a deterministic replay reference manifest from sorted fixture Parquet.

The fixture is the replay server's input; this module derives the independent
correctness oracle from those bytes without loading the listing into memory.
Input parts are read in lexical path order and must already contain strictly
increasing OBJECT keys in that order.  Output is contract-v2 TSV wrapped in a
reproducible gzip stream.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

import duckdb

from benchmark import gcs
from benchmark.runtime.contract import RECORD_SEPARATOR, ContractViolation, Record

READ_BATCH = 10_000
REQUIRED_SCHEMA = {
    "key": "BLOB",
    "size": "BIGINT",
    "last_modified": "TIMESTAMP WITH TIME ZONE",
    "etag": "VARCHAR",
    "storage_class": "VARCHAR",
    "row_type": "VARCHAR",
}

PARQUET_QUERY = """
    SELECT "key", CAST("size" AS VARCHAR), "etag",
           strftime("last_modified" AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ'),
           "storage_class"
    FROM read_parquet($parts)
    WHERE coalesce("row_type", 'OBJECT') = 'OBJECT'
"""


class ManifestError(Exception):
    """The fixture cannot safely produce a reference manifest."""


class BinaryWriter(Protocol):
    def write(self, data: bytes) -> int: ...


def fixture_parts(inputs: Sequence[Path]) -> tuple[Path, ...]:
    """Resolve directories and explicit parts into one lexical, duplicate-free list."""
    found: list[Path] = []
    for item in inputs:
        if item.is_dir():
            found.extend(path for path in item.rglob("*.parquet") if path.is_file())
        elif item.is_file() and item.suffix == ".parquet":
            found.append(item)
        else:
            raise ManifestError(f"fixture input is not a Parquet file or directory: {item}")
    ordered = tuple(sorted((path.resolve() for path in found), key=os.fspath))
    if not ordered:
        raise ManifestError("fixture is empty: no Parquet parts")
    if len(set(ordered)) != len(ordered):
        raise ManifestError("fixture names the same Parquet part more than once")
    return ordered


def fixture_digest(parts: Iterable[Path]) -> str:
    """sha256 of complete part bytes concatenated in the supplied lexical order."""
    digest = hashlib.sha256()
    for part in parts:
        with part.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _validate_schema(connection: duckdb.DuckDBPyConnection, parts: Sequence[Path]) -> None:
    baseline: dict[str, str] | None = None
    for part in parts:
        try:
            rows = connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [os.fspath(part)]
            ).fetchall()
        except duckdb.Error as exc:
            raise ManifestError(f"fixture part is not readable Parquet: {part}: {exc}") from exc
        schema = {str(row[0]): str(row[1]) for row in rows}
        wrong = {
            name: (schema.get(name), expected)
            for name, expected in REQUIRED_SCHEMA.items()
            if schema.get(name) != expected
        }
        if wrong:
            details = ", ".join(
                f"{name}={actual!r} (expected {expected})"
                for name, (actual, expected) in sorted(wrong.items())
            )
            raise ManifestError(f"fixture schema mismatch in {part}: {details}")
        required_projection = {name: schema[name] for name in REQUIRED_SCHEMA}
        if baseline is None:
            baseline = required_projection
        elif required_projection != baseline:  # pragma: no cover - guarded by exact types above
            raise ManifestError(f"fixture schema mismatch between parts at {part}")


def _write_rows(
    connection: duckdb.DuckDBPyConnection, parts: Sequence[Path], output: BinaryWriter
) -> int:
    try:
        result = connection.execute(PARQUET_QUERY, {"parts": [os.fspath(p) for p in parts]})
    except duckdb.Error as exc:
        raise ManifestError(f"fixture parts cannot be read as one dataset: {exc}") from exc
    previous: bytes | None = None
    row_count = 0
    while rows := result.fetchmany(READ_BATCH):
        for row in rows:
            key, size, etag, mtime, storage_class = row
            try:
                record = Record(
                    key=key,
                    size=size,
                    etag=etag,
                    mtime=mtime,
                    storage_class=storage_class,
                )
            except ContractViolation as exc:
                raise ManifestError(
                    f"fixture row {row_count + 1} violates contract v2: {exc}"
                ) from exc
            if previous is not None and record.key <= previous:
                kind = "duplicate" if record.key == previous else "out-of-order"
                raise ManifestError(
                    f"fixture has {kind} OBJECT key at row {row_count + 1}: {record.key!r}"
                )
            output.write(record.to_line() + RECORD_SEPARATOR)
            previous = record.key
            row_count += 1
    if row_count == 0:
        raise ManifestError("fixture is empty: it contains no OBJECT rows")
    return row_count


def _write_descriptor(path: Path, descriptor: dict[str, object]) -> None:
    data = json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    try:
        with path.open("xb") as stream:
            stream.write(data)
    except FileExistsError:
        raise ManifestError(f"descriptor already exists: {path}") from None


def build_manifest(
    inputs: Sequence[Path],
    output: Path,
    *,
    supplied_fixture_sha256: str | None = None,
    descriptor_path: Path | None = None,
) -> dict[str, object]:
    """Build one local create-only manifest and return its canonical descriptor."""
    parts = fixture_parts(inputs)
    output = output.resolve()
    if output.exists():
        raise ManifestError(f"output already exists: {output}")
    if descriptor_path is not None and descriptor_path.exists():
        raise ManifestError(f"descriptor already exists: {descriptor_path}")
    actual_fixture_sha256 = fixture_digest(parts)
    if supplied_fixture_sha256 is not None and supplied_fixture_sha256 != actual_fixture_sha256:
        raise ManifestError(
            "fixture sha256 mismatch: "
            f"expected {supplied_fixture_sha256}, found {actual_fixture_sha256}"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent, prefix=".manifest-", delete=False
        ) as raw:
            temporary = Path(raw.name)
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0
            ) as packed:
                connection = duckdb.connect()
                try:
                    connection.execute("SET preserve_insertion_order = true")
                    _validate_schema(connection, parts)
                    row_count = _write_rows(connection, parts, packed)
                finally:
                    connection.close()
        manifest_sha256 = fixture_digest((temporary,))
        try:
            os.link(temporary, output)
        except FileExistsError:
            raise ManifestError(f"output already exists: {output}") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    descriptor: dict[str, object] = {
        "fixture_sha256": actual_fixture_sha256,
        "local_output": output.name,
        "manifest_sha256": manifest_sha256,
        "row_count": row_count,
    }
    if descriptor_path is not None:
        _write_descriptor(descriptor_path, descriptor)
    return descriptor


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="fixture directory or Parquet parts")
    parser.add_argument("--output", required=True, type=Path, help="create-only .tsv.gz output")
    parser.add_argument("--fixture-sha256", help="expected digest over lexical part bytes")
    parser.add_argument("--descriptor", type=Path, help="optional create-only JSON descriptor")
    parser.add_argument(
        "--upload",
        help="optional create-only gs:// URI for the compressed manifest",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        descriptor = build_manifest(
            args.inputs,
            args.output,
            supplied_fixture_sha256=args.fixture_sha256,
        )
        if args.upload:
            if not args.upload.startswith("gs://"):
                raise ManifestError("--upload must be a gs:// URI")
            gcs.upload_file(args.output, args.upload, create_only=True)
            descriptor["reference_manifest_uri"] = args.upload
        if args.descriptor is not None:
            _write_descriptor(args.descriptor, descriptor)
    except Exception as exc:
        if not isinstance(exc, ManifestError):
            exc = ManifestError(f"manifest publication failed: {exc}")
        print(f"replay-manifest: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(descriptor, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
