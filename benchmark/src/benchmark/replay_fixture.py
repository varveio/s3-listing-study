"""Print the content identity for a locally staged replay fixture."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from benchmark.replay import ReplayError


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Hash immediate *.parquet children using the replay staging-manifest contract."
        )
    )
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--show-manifest",
        action="store_true",
        help="print the canonical manifest rows before their digest",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        digest, rows = fixture_manifest(args.directory)
    except (OSError, ReplayError) as exc:
        print(f"replay-fixture: {exc}", file=sys.stderr)
        return 1
    if args.show_manifest:
        sys.stdout.writelines(rows)
    print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
