"""Check one committed public release without any private access at all.

The exporter runs where the private ledger and the private evidence store are
reachable, which is exactly one machine. Everything after that is a repository
anyone can clone -- so the gate that says a release is safe and self-consistent
has to run on the committed files alone. This does, and CI runs it on every
change, which means a hand-edit to `attempts.jsonl`, a stale `summary.csv`, a
leaked private path, or a diagnostic relabelled as a measurement fails the build
rather than reaching a reader.

Usage:
    python -m benchmark.public_validate --release-dir results/<release-id>
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from benchmark.public_export import (
    ROW_SCHEMA_VERSION,
    forbidden_hits,
    sha256_hex,
    sort_key,
)
from benchmark.replay import TIMING_VALID

TEXT_SUFFIXES = {".json", ".jsonl", ".csv", ".svg", ".md", ".sha256", ".txt"}


def _files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*") if path.is_file())


def check_forbidden(paths: Sequence[Path], root: Path) -> Iterator[str]:
    """No generated file may carry private estate, whatever produced it."""
    for path in paths:
        if path.suffix not in TEXT_SUFFIXES:
            yield f"{path.relative_to(root)}: unexpected file type in a release"
            continue
        hits = forbidden_hits(path.read_text(encoding="utf-8", errors="replace"))
        if hits:
            yield f"{path.relative_to(root)}: publishes {', '.join(sorted(set(hits)))}"


def check_checksums(directory: Path) -> Iterator[str]:
    manifest_path = directory / "checksums.sha256"
    recorded: dict[str, str] = {}
    for line in manifest_path.read_text().splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        recorded[name] = digest
    present = {
        path.relative_to(directory).as_posix()
        for path in _files(directory)
        if path != manifest_path
    }
    for name in sorted(present - set(recorded)):
        yield f"checksums.sha256 does not cover {name}"
    for name in sorted(set(recorded) - present):
        yield f"checksums.sha256 names {name}, which is not in the release"
    for name in sorted(present & set(recorded)):
        actual = sha256_hex((directory / name).read_bytes())
        if actual != recorded[name]:
            yield f"{name}: sha256 {actual} does not match the recorded {recorded[name]}"


def check_manifest(manifest: Mapping[str, Any], directory: Path) -> Iterator[str]:
    for entry in manifest.get("files") or []:
        path = directory / str(entry["path"])
        if not path.is_file():
            yield f"manifest names {entry['path']}, which is not in the release"
            continue
        if sha256_hex(path.read_bytes()) != entry["sha256"]:
            yield f"manifest sha256 for {entry['path']} does not match the file"
    counts = (manifest.get("counts") or {}).get("attempts")
    if counts is None:
        yield "manifest records no attempt count"
    ceiling = manifest.get("claim_ceiling") or {}
    if not isinstance(ceiling, Mapping) or not ceiling:
        yield "manifest carries no machine-readable claim ceiling"


def check_rows(
    rows: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any], fixtures: Mapping[str, Any]
) -> Iterator[str]:
    identifiers = [str(row["attempt_id"]) for row in rows]
    duplicates = sorted({name for name in identifiers if identifiers.count(name) > 1})
    if duplicates:
        yield f"attempts.jsonl repeats attempt id(s): {', '.join(duplicates)}"
    if identifiers != [str(row["attempt_id"]) for row in sorted(rows, key=sort_key)]:
        yield "attempts.jsonl is not in the release's stable sort order"
    if len(rows) != (manifest.get("counts") or {}).get("attempts"):
        yield "manifest attempt count disagrees with attempts.jsonl"
    for row in rows:
        attempt = row["attempt_id"]
        if row.get("schema_version") != ROW_SCHEMA_VERSION:
            yield f"{attempt}: row schema_version is not {ROW_SCHEMA_VERSION}"
        if row.get("release_id") != manifest.get("release_id"):
            yield f"{attempt}: release_id does not match the manifest"
        classification = row.get("classification") or {}
        if classification.get("publication_status") == "measurement" and not (
            classification.get("purpose") == "measurement"
            and classification.get("capacity_status") == "CALIBRATED"
            and classification.get("replay_timing") == TIMING_VALID
        ):
            yield (
                f"{attempt}: published as a measurement without a calibrated capacity and a "
                "valid delivered timing"
            )
        for metric, value in (row.get("outcome") or {}).items():
            if value == "-" or value == "":
                yield f"{attempt}: outcome.{metric} is a sentinel, not null"
        fixture = row.get("fixture")
        if fixture and str(fixture["id"]) not in fixtures:
            yield f"{attempt}: fixture {fixture['id']} does not resolve in fixtures.json"


def check_summary(directory: Path, rows: Sequence[Mapping[str, Any]]) -> Iterator[str]:
    with (directory / "summary.csv").open(newline="") as handle:
        table = list(csv.DictReader(handle))
    if len(table) != len(rows):
        yield f"summary.csv has {len(table)} row(s); attempts.jsonl has {len(rows)}"
        return
    for line, row in zip(table, rows, strict=True):
        if line["attempt_id"] != row["attempt_id"]:
            yield f"summary.csv is not in attempts.jsonl order at {line['attempt_id']}"
            return


def check_charts(directory: Path, rows: Sequence[Mapping[str, Any]]) -> Iterator[str]:
    known = {str(row["attempt_id"]) for row in rows}
    for path in sorted((directory / "charts").glob("*.csv")):
        with path.open(newline="") as handle:
            for line in csv.DictReader(handle):
                if line.get("attempt_id") and line["attempt_id"] not in known:
                    yield f"charts/{path.name}: {line['attempt_id']} is not in attempts.jsonl"
        svg = path.with_suffix(".svg")
        if not svg.is_file():
            yield f"charts/{path.name} has no figure beside it"
        elif "<script" in svg.read_text():
            yield f"charts/{svg.name} carries a script; release figures are static"


def validate(directory: Path) -> list[str]:
    problems: list[str] = []
    manifest = json.loads((directory / "manifest.json").read_text())
    fixtures = json.loads((directory / "fixtures.json").read_text())
    json.loads((directory / "subjects.json").read_text())
    rows = [
        json.loads(line)
        for line in (directory / "attempts.jsonl").read_text().splitlines()
        if line.strip()
    ]
    scanned = [*_files(directory)]
    for sibling in ("latest.json", "README.md"):
        candidate = directory.parent / sibling
        if candidate.is_file():
            scanned.append(candidate)
    problems.extend(check_forbidden(scanned, directory.parent))
    problems.extend(check_checksums(directory))
    problems.extend(check_manifest(manifest, directory))
    problems.extend(check_rows(rows, manifest, fixtures))
    problems.extend(check_summary(directory, rows))
    problems.extend(check_charts(directory, rows))
    return problems


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a committed public release.")
    parser.add_argument("--release-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        problems = validate(args.release_dir)
    except (OSError, ValueError, KeyError) as exc:
        print(
            f"public-validate: {args.release_dir} is not a readable release: {exc}", file=sys.stderr
        )
        return 1
    for problem in problems:
        print(f"public-validate: {problem}", file=sys.stderr)
    if problems:
        print(f"public-validate: {len(problems)} problem(s) in {args.release_dir}", file=sys.stderr)
        return 1
    print(f"public-validate: {args.release_dir} is consistent and carries nothing private")
    return 0


if __name__ == "__main__":
    sys.exit(main())
