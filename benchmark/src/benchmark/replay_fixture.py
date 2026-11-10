"""Generate and identify a small replay fixture outside the checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
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


def seekparquet_identity(directory: Path) -> tuple[str, str | None]:
    """Return a SeekParquet dataset's ``(manifest sha256, adopted Java-era digest)``.

    ``fixture_sha256`` for a dataset root is the SHA-256 of its ``manifest.json``
    bytes; the manifest names every part's path, size and SHA-256, which are
    checked here against ``data/`` exactly as staging checks them. The second
    value recomputes the Java-era staging digest from
    ``extensions["swath-replay"].adopted_from`` (``None`` when the dataset was
    not converted from one), which a plan records as ``fixture_adopted_sha256``.
    """
    manifest_path = directory / "manifest.json"
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or manifest.get("format") != "seekparquet":
        raise ReplayError(f"{manifest_path} is not a SeekParquet manifest")
    parts = manifest.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ReplayError(f"{manifest_path} names no parts")
    named = sorted(str(part.get("path")) for part in parts if isinstance(part, dict))
    staged = sorted(f"data/{path.name}" for path in (directory / "data").glob("*.parquet"))
    if named != staged or len(named) != len(parts):
        raise ReplayError(f"{directory}/data parts differ from the manifest")
    for part in parts:
        path = directory / str(part["path"])
        if path.stat().st_size != part.get("bytes") or _sha256_file(path) != part.get("sha256"):
            raise ReplayError(f"{path} does not match its manifest entry")
    extensions = manifest.get("extensions")
    replay_extension = extensions.get("swath-replay") if isinstance(extensions, dict) else None
    adopted = replay_extension.get("adopted_from") if isinstance(replay_extension, dict) else None
    adopted_sha256 = None
    if isinstance(adopted, dict):
        rows = sorted(
            f"{item['input']}\t{item['input_bytes']}\t{item['input_sha256']}\n"
            for item in adopted.get("parts", [])
        )
        adopted_sha256 = hashlib.sha256("".join(rows).encode()).hexdigest()
    return hashlib.sha256(raw).hexdigest(), adopted_sha256


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
            "Hash immediate *.parquet children using the replay staging-manifest contract, "
            "or identify a SeekParquet dataset root by its manifest.json."
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
        if (args.directory / "manifest.json").is_file():
            digest, adopted = seekparquet_identity(args.directory)
            if args.expect is not None and digest != args.expect:
                print(
                    f"replay-fixture: manifest digest {digest} does not match expected "
                    f"{args.expect}",
                    file=sys.stderr,
                )
                return 1
            if adopted is not None:
                print(f"fixture_adopted_sha256 {adopted}")
            print(digest)
            return 0
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
