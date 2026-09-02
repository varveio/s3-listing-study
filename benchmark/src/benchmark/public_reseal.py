"""Re-derive a release's generated files from its committed public files and reseal it.

The exporter needs the private ledger, which lives on one machine. Three kinds
of change do not: an edit to the handwritten `REPORT.md`, a chart-spec edit,
and a change to one of the exporter's pure projections (`summary.csv`, the
chart CSVs and SVGs, the manifest's fixture and file entries, the release-level
disclosures declared in the spec). This module redoes exactly those from
`attempts.jsonl`, `fixtures.json` and the specs, using the exporter's own
functions, then rewrites `manifest.json` and `checksums.sha256`.

It never touches `attempts.jsonl`, `fixtures.json`, `subjects.json` or
`REPORT.md`. A new row, a fixture's staged metadata, or a disclosure the
exporter computes from the ledger waits for the next export.

Usage:
    python -m benchmark.public_reseal --release-dir results/<release-id> \
        [--spec benchmark/publication/<release-id>.yaml] \
        [--charts benchmark/publication/charts.yaml]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from benchmark import public_export, public_render

GENERATED_HERE = ("summary.csv", "manifest.json", "checksums.sha256")


def reseal(release_dir: Path, *, spec_path: Path | None, charts_path: Path) -> list[str]:
    rows = [
        json.loads(line)
        for line in (release_dir / "attempts.jsonl").read_text().splitlines()
        if line.strip()
    ]
    fixtures = json.loads((release_dir / "fixtures.json").read_text())
    manifest = json.loads((release_dir / "manifest.json").read_text())
    release_id = str(manifest["release_id"])
    if spec_path is None:
        spec_path = Path("benchmark/publication") / f"{release_id}.yaml"
    spec = public_export.load_release_spec(spec_path)
    if spec.release_id != release_id:
        raise public_export.ExportError(
            f"{spec_path} declares release {spec.release_id}; the directory is {release_id}"
        )

    (release_dir / "summary.csv").write_text(public_export.summary_csv(rows, fixtures))
    public_render.render_charts(
        charts_path, rows=rows, output_dir=release_dir / "charts", fixtures=fixtures
    )

    manifest["fixtures"] = [
        {"id": key, "sha256": value["manifest_sha256"], "object_count": value["object_count"]}
        for key, value in fixtures.items()
    ]
    # Release-level disclosures come from the spec, so they can be refreshed
    # here; the exporter's own (computed from the ledger) are kept as they are.
    declared = {entry["id"]: entry for entry in spec.disclosures}
    kept = [entry for entry in manifest.get("disclosures") or [] if entry["id"] not in declared]
    manifest["disclosures"] = [*kept, *spec.disclosures]

    sealed = sorted(
        (
            path
            for path in release_dir.rglob("*")
            if path.is_file() and path.name not in GENERATED_HERE[1:]
        ),
        key=lambda path: path.relative_to(release_dir).as_posix(),
    )
    manifest["files"] = [
        {
            "path": path.relative_to(release_dir).as_posix(),
            "sha256": public_export.sha256_hex(path.read_bytes()),
            "bytes": path.stat().st_size,
        }
        for path in sealed
    ]
    (release_dir / "manifest.json").write_bytes(public_export.json_bytes(manifest))
    with_manifest = sorted(
        [*sealed, release_dir / "manifest.json"],
        key=lambda path: path.relative_to(release_dir).as_posix(),
    )
    (release_dir / "checksums.sha256").write_text(
        "".join(
            f"{public_export.sha256_hex(path.read_bytes())}  "
            f"{path.relative_to(release_dir).as_posix()}\n"
            for path in with_manifest
        )
    )
    return [path.relative_to(release_dir).as_posix() for path in with_manifest]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reseal one release from its public files.")
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--spec", type=Path, default=None)
    parser.add_argument("--charts", type=Path, default=Path("benchmark/publication/charts.yaml"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        sealed = reseal(args.release_dir, spec_path=args.spec, charts_path=args.charts)
    except (OSError, ValueError, KeyError, public_export.ExportError) as exc:
        print(f"public-reseal: {exc}", file=sys.stderr)
        return 1
    print(f"public-reseal: resealed {len(sealed)} file(s) under {args.release_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
