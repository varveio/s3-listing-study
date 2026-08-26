"""Generate and identify a small replay fixture outside the checkout."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import duckdb

from benchmark.replay import ReplayError

DUCKDB_WRITER_VERSION = "1.5.5"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fixture_manifest(directory: Path) -> tuple[str, tuple[str, ...]]:
    """Return the staged-fixture digest and the canonical rows it covers."""
    paths = sorted(path for path in directory.glob("*.parquet") if path.is_file())
    if not paths:
        raise ReplayError(f"{directory} has no immediate *.parquet fixture files")
    rows = tuple(f"{path.name}\t{path.stat().st_size}\t{_sha256_file(path)}\n" for path in paths)
    return hashlib.sha256("".join(rows).encode()).hexdigest(), rows


def generate_parquet(query_path: Path, output_path: Path) -> None:
    """Materialize one deterministic fixture part from a committed SELECT."""
    if duckdb.__version__ != DUCKDB_WRITER_VERSION:
        raise ReplayError(
            f"fixture writer requires DuckDB {DUCKDB_WRITER_VERSION}, found {duckdb.__version__}"
        )
    if output_path.exists():
        raise ReplayError(f"refusing to overwrite existing fixture part {output_path}")
    query = query_path.read_text(encoding="utf-8").strip().removesuffix(";")
    if not query:
        raise ReplayError(f"fixture query {query_path} is empty")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as connection:
        connection.execute(
            f"COPY ({query}) TO ? (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 1024)",
            [str(output_path)],
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Hash immediate *.parquet children using the replay staging-manifest contract."
        )
    )
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--generate-query",
        type=Path,
        help="generate part-00000.parquet from this SELECT before hashing",
    )
    parser.add_argument("--expect", help="refuse unless the generated manifest has this digest")
    parser.add_argument(
        "--show-manifest",
        action="store_true",
        help="print the canonical manifest rows before their digest",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.generate_query is not None:
            generate_parquet(args.generate_query, args.directory / "part-00000.parquet")
        digest, rows = fixture_manifest(args.directory)
    except (OSError, ReplayError, duckdb.Error) as exc:
        print(f"replay-fixture: {exc}", file=sys.stderr)
        return 1
    if args.expect is not None and digest != args.expect:
        print(
            f"replay-fixture: manifest digest {digest} does not match expected {args.expect}",
            file=sys.stderr,
        )
        return 1
    if args.show_manifest:
        sys.stdout.writelines(rows)
    print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
