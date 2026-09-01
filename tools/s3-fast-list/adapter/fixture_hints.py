#!/usr/bin/env python3
"""Derive upstream-compatible s3-fast-list cut points from a replay fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb


class FixtureHintsError(ValueError):
    """The fixture cannot produce a safe, meaningful hints artifact."""


@dataclass(frozen=True)
class HintSummary:
    output: str
    requested_segments: int
    object_rows: int
    prefix_groups: int
    cut_points: int
    ranges: int
    sha256: str


OBJECTS_SQL = """
SELECT decode(key) AS key
FROM fixture_source
WHERE row_type = 'OBJECT'
  AND starts_with(decode(key), ?)
"""


def _fixture_files(directory: Path) -> tuple[Path, ...]:
    files = tuple(sorted(path for path in directory.glob("*.parquet") if path.is_file()))
    if not files:
        raise FixtureHintsError(f"{directory} has no immediate *.parquet fixture files")
    return files


def _create_fixture_source(connection: duckdb.DuckDBPyConnection, files: tuple[Path, ...]) -> None:
    """Expose the parts as ``fixture_source`` with a BLOB ``key`` whatever the writer annotated.

    Swath 0.3.1 annotates the key column as a UTF-8 string; earlier captures wrote raw bytes. The
    bytes and their order are identical, so a string key is re-exposed as its bytes.
    """
    connection.from_parquet([str(path) for path in files]).create_view("fixture_parts")
    columns = {
        str(name): str(kind)
        for _, name, kind, *_ in connection.execute("PRAGMA table_info('fixture_parts')").fetchall()
    }
    key_kind = columns.get("key")
    if key_kind not in {"BLOB", "VARCHAR"} or columns.get("row_type") != "VARCHAR":
        raise FixtureHintsError(
            "fixture must expose key BLOB or VARCHAR and row_type VARCHAR columns; "
            f"found key={key_kind!r}, row_type={columns.get('row_type')!r}"
        )
    key_expression = "encode(key)" if key_kind == "VARCHAR" else "key"
    connection.execute(
        "CREATE VIEW fixture_source AS "
        f"SELECT * REPLACE ({key_expression} AS key) FROM fixture_parts"
    )


def _safe_payload(
    connection: duckdb.DuckDBPyConnection, prefix: str, segments: int
) -> tuple[bytes, int, int]:
    object_rows, distinct_keys = connection.execute(
        f"SELECT count(*), count(DISTINCT key) FROM ({OBJECTS_SQL})", [prefix]
    ).fetchone()
    object_rows = int(object_rows)
    distinct_keys = int(distinct_keys)
    if object_rows == 0:
        raise FixtureHintsError("fixture selection contains no OBJECT rows")
    if object_rows != distinct_keys:
        raise FixtureHintsError(
            f"fixture selection contains {object_rows - distinct_keys} duplicate OBJECT key row(s)"
        )

    target = object_rows // segments
    if target == 0:
        raise FixtureHintsError(
            f"{segments} requested segments exceed the {object_rows} selected objects"
        )

    groups = connection.execute(
        f"""
        SELECT CASE
                 WHEN contains(key, '/') THEN regexp_replace(key, '/[^/]*$', '')
                 ELSE '/'
               END AS parent_prefix,
               count(*) AS objects
        FROM ({OBJECTS_SQL})
        GROUP BY parent_prefix
        ORDER BY parent_prefix
        """,
        [prefix],
    )

    cut_points: list[str] = []
    cumulative = 0
    last_prefix = ""
    prefix_groups = 0
    while batch := groups.fetchmany(4096):
        for parent_prefix, count in batch:
            parent_prefix = str(parent_prefix)
            if '"' in parent_prefix or "\n" in parent_prefix or "\r" in parent_prefix:
                raise FixtureHintsError(
                    "fixture contains a parent prefix that the upstream keyspace CSV cannot "
                    f"represent faithfully: {parent_prefix!r}"
                )
            prefix_groups += 1
            cumulative += int(count)
            if cumulative > target:
                cut_points.append(last_prefix)
                cumulative = 0
            last_prefix = parent_prefix

    if not cut_points:
        raise FixtureHintsError("the upstream split algorithm produced no cut point")
    if not cut_points[0]:
        raise FixtureHintsError(
            "the upstream split algorithm produced an empty first cut point, which would add "
            "a duplicate full-range scan"
        )
    if cut_points != sorted(set(cut_points)):
        raise FixtureHintsError("the upstream split algorithm produced unsorted or duplicate cuts")

    connection.execute("CREATE TEMP TABLE generated_hints (hint VARCHAR PRIMARY KEY)")
    connection.executemany(
        "INSERT INTO generated_hints VALUES (?)", [(hint,) for hint in cut_points]
    )
    collisions = int(
        connection.execute(
            f"SELECT count(*) FROM ({OBJECTS_SQL}) JOIN generated_hints ON key = hint", [prefix]
        ).fetchone()[0]
    )
    if collisions:
        raise FixtureHintsError(
            f"{collisions} generated cut point(s) equal an object key; s3-fast-list would omit "
            "those boundary objects"
        )

    return ("".join(f"{hint}\n" for hint in cut_points).encode(), object_rows, prefix_groups)


def _write_new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise FixtureHintsError(f"refusing to overwrite existing output {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as sink:
            sink.write(payload)
            sink.flush()
            os.fsync(sink.fileno())
        if path.exists():
            raise FixtureHintsError(f"refusing to overwrite existing output {path}")
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def generate_hints(fixture: Path, output: Path, *, segments: int, prefix: str = "") -> HintSummary:
    """Write safe cut points with the pinned upstream splitter's exact semantics."""
    if isinstance(segments, bool) or not isinstance(segments, int) or segments < 1:
        raise FixtureHintsError(f"segments must be a positive integer; got {segments!r}")
    if output.exists():
        raise FixtureHintsError(f"refusing to overwrite existing output {output}")
    effective_prefix = "" if prefix == "/" else prefix
    files = _fixture_files(fixture)
    with duckdb.connect() as connection:
        _create_fixture_source(connection, files)
        payload, object_rows, prefix_groups = _safe_payload(connection, effective_prefix, segments)
    _write_new(output, payload)
    cut_points = payload.count(b"\n")
    return HintSummary(
        output=str(output),
        requested_segments=segments,
        object_rows=object_rows,
        prefix_groups=prefix_groups,
        cut_points=cut_points,
        ranges=cut_points + 1,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="Derive s3-fast-list hints from immediate Parquet parts in a replay fixture.",
    )
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--segments", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prefix", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = generate_hints(
            args.fixture, args.output, segments=args.segments, prefix=args.prefix
        )
    except (FixtureHintsError, OSError, UnicodeError, duckdb.Error) as exc:
        print(f"fixture-hints: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(summary), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
