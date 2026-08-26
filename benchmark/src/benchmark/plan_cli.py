"""``python -m benchmark.plan_cli`` — expand a bucket plan and show every case.

A plan's cases are generated, so the only way to review what a campaign would
actually submit is to resolve it and look. This command is that dry run: it
never contacts a bucket, submits anything, or writes a file.

The resolved allocation is printed per case rather than the layer each value
came from, because the question a reader has is "what box does this run on",
and a value's provenance is answered by reading the plan next to it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from benchmark import plan as bench
from benchmark import replay as replay_contract


def repo_root() -> Path:
    return bench.buckets_dir().parents[2]


def registered_tools(root: Path | None = None) -> set[str]:
    """Every tool carrying a toolbox build declaration."""
    base = repo_root() if root is None else root
    return {path.parents[1].name for path in base.glob("tools/*/build/image.json")}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmark.plan_cli", allow_abbrev=False, add_help=True
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bucket", help="plan under benchmark/plans/buckets")
    source.add_argument("--path", help="path to a plan file")
    parser.add_argument(
        "--json", action="store_true", help="emit the resolved cases as JSON instead of a table"
    )
    parser.add_argument(
        "--skip-roster",
        action="store_true",
        help="do not require every registered tool to be run or excluded",
    )
    return parser


def _replay_projection(
    case: bench.Case,
) -> tuple[dict[str, object] | None, dict[str, object] | None, str]:
    """One review-oriented replay projection from the resolved contract.

    `Plan.load` has already parsed and validated this value.  The CLI only
    projects it, including the host remainder that the shared contract derives.
    """
    if case.replay is None:
        return None, None, "S3"
    config = case.replay
    summary = replay_contract.allocation_summary(
        config,
        box_vcpus=case.resources.vcpus,
        box_memory_gb=case.resources.memory_gb,
        container_memory_gb=case.resources.container_memory_gb,
    )
    backend, allocation = config.backend, config.allocation
    image = backend.server_image_uri.rsplit("@sha256:", 1)
    image_summary = f"{image[0].rsplit('/', 1)[-1]}@sha256:{image[1][:12]}…"
    if backend.latency_deadlines_ms is None:
        latency = "none"
    else:
        profile = ",".join(f"{shape}={delay}ms" for shape, delay in backend.latency_deadlines_ms)
        latency = f"{profile} scale={backend.latency_scale} jitter={backend.latency_jitter}"
    fixture = f"sha256:{backend.fixture_sha256[:12]}…"
    if backend.fixture_uri is not None:
        fixture = f"{backend.fixture_uri} ({fixture})"
    subject_memory = (
        "uncapped"
        if case.resources.container_memory_gb is None
        else f"{case.resources.container_memory_gb}GiB"
    )
    host_memory = (
        "unreserved"
        if summary.host_memory_headroom_gb is None
        else f"{summary.host_memory_headroom_gb}GiB"
    )
    compact = (
        f"{config.capacity_status.upper()} server={image_summary} "
        f"fixture={fixture} "
        f"{backend.serving_mode} latency={latency} "
        f"server={allocation.replay_vcpus}vCPU/{allocation.replay_memory_gb}GiB "
        f"subject={allocation.subject_vcpus}vCPU/{subject_memory} "
        f"host={summary.host_vcpus}vCPU/{host_memory}"
    )
    return config.as_dict(), asdict(summary), compact


def _rows(loaded: bench.Plan) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for case in loaded.cases:
        replay, replay_allocation, replay_summary = _replay_projection(case)
        rows.append(
            {
                "tool": case.tool,
                "case": case.label,
                "mode": case.mode,
                "purpose": case.purpose,
                "statistic": case.statistic,
                "vcpus": case.resources.vcpus,
                "memory_gb": case.resources.memory_gb,
                "machine_type": case.resources.machine_type,
                "container_memory_gb": case.resources.container_memory_gb or "-",
                "docker_options": list(case.resources.docker_options),
                "config": dict(case.config),
                # Derived, and carried because they are what a Batch job is told.
                "memory_mib": case.resources.memory_mib,
                "cpu_milli": case.resources.cpu_milli,
                "reps": case.reps,
                "timeout_s": case.timeout_s,
                # The share, not the variable: the capsule renders the flag its own
                # runtime reads, and nine of eleven tools read none.
                "heap_percent": case.heap_percent,
                # Full canonical config plus the derived allocation in JSON; the
                # table uses `replay_summary` so it stays scannable.
                "replay": replay,
                "replay_allocation": replay_allocation,
                "replay_summary": replay_summary,
            }
        )
    return rows


def _render(loaded: bench.Plan, rows: Sequence[dict[str, object]]) -> str:
    columns = (
        "tool",
        "case",
        "purpose",
        "machine_type",
        "container_memory_gb",
        "heap_percent",
        "reps",
        "timeout_s",
        "replay_summary",
    )
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in columns} if rows else {}
    lines = [
        f"{loaded.bucket} ({loaded.region}) — {len(rows)} cases, "
        f"{sum(int(str(r['reps'])) for r in rows)} attempts at {loaded.path}",
        "",
        "  ".join(c.ljust(widths[c]) for c in columns),
        "  ".join("-" * widths[c] for c in columns),
    ]
    lines.extend("  ".join(str(row[c]).ljust(widths[c]) for c in columns) for row in rows)
    for exclusion in loaded.exclusions:
        lines.append(f"excluded: {exclusion.tool} — {exclusion.reason}")
    return "\n".join(lines)


def resolve_plan_main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = bench.default_path(args.bucket) if args.bucket else Path(args.path)
    try:
        loaded = bench.Plan.load(path)
        if not args.skip_roster:
            bench.check_roster(loaded, registered_tools())
    except bench.PlanError as exc:
        print(f"resolve-plan: {exc}", file=sys.stderr)
        return 1

    rows = _rows(loaded)
    if args.json:
        print(
            json.dumps(
                {
                    "bucket": loaded.bucket,
                    "region": loaded.region,
                    "plan_sha256": loaded.digest,
                    "cases": rows,
                    "exclusions": [{"tool": e.tool, "reason": e.reason} for e in loaded.exclusions],
                },
                indent=2,
            )
        )
    else:
        print(_render(loaded, rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(resolve_plan_main())
