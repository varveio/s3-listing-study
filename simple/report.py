"""Walk campaign.db plus each latest-submission attempt's result.json/verify.json
and print one Markdown summary table.

This is a SKETCH standing in for the real report renderer (which produces a
byte-identical verify.md as part of the audit record, with a frozen template
and stable field ordering the acceptance tests pin down). This one just
prints Markdown to stdout; if two runs produce a differently-ordered table,
nothing here cares.

Opens campaign.db read-only (mode=ro): report.py only ever reads the ledger,
so a stray write here is a bug this should fail loudly on, not tolerate.

Like verify.py, a job's directory under --attempts-root is a *destination
prefix*, not the attempt itself: report.py resolves it to the one attempt
leaf underneath (see verify.resolve_leaf) and reports AMBIGUOUS or
INCOMPLETE rather than guessing when that resolution doesn't land cleanly.

Usage:
    report.py --state campaign.db --attempts-root /local/attempts
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from campaign import STATE_FILENAME, latest_submissions, open_db
from verify import has_result_marker, resolve_leaf

COLUMNS = ("tool", "mode", "wall_seconds", "max_rss_kb", "verdict")


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def row_for(row: sqlite3.Row, attempts_root: Path) -> dict:
    destination = str(attempts_root / row["job_id"])
    leaf = resolve_leaf(destination)
    if leaf is None:
        return {"tool": row["tool"], "mode": row["mode"],
                "wall_seconds": "-", "max_rss_kb": "-", "verdict": "AMBIGUOUS"}
    if not has_result_marker(leaf):
        return {"tool": row["tool"], "mode": row["mode"],
                "wall_seconds": "-", "max_rss_kb": "-", "verdict": "INCOMPLETE"}

    result = load_json(Path(leaf) / "result.json") or {}
    verify = load_json(Path(leaf) / "verify.json") or {}
    return {
        "tool": row["tool"],
        "mode": row["mode"],
        "wall_seconds": result.get("wall_seconds", "-"),
        "max_rss_kb": result.get("max_rss_kb", "-"),
        "verdict": verify.get("verdict", row["state"]),
    }


def render_markdown(rows: list[dict]) -> str:
    header = "| " + " | ".join(COLUMNS) + " |"
    separator = "| " + " | ".join("---" for _ in COLUMNS) + " |"
    lines = [f"# Campaign report ({datetime.now(UTC).isoformat()})", "", header, separator]
    for row in rows:
        lines.append("| " + " | ".join(str(row[c]) for c in COLUMNS) + " |")
    lines.append("")
    lines.append(summary_line(rows))
    return "\n".join(lines)


def summary_line(rows: list[dict]) -> str:
    verdict_counts: dict[str, int] = {}
    wall_times = []
    for row in rows:
        verdict_counts[row["verdict"]] = verdict_counts.get(row["verdict"], 0) + 1
        if isinstance(row["wall_seconds"], (int, float)):
            wall_times.append(row["wall_seconds"])
    counts = ", ".join(f"{verdict}={count}" for verdict, count in sorted(verdict_counts.items()))
    average = f"{sum(wall_times) / len(wall_times):.1f}s" if wall_times else "-"
    return f"**{len(rows)} attempt(s)** -- {counts} -- average wall time {average}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print a Markdown summary of a campaign's results.")
    parser.add_argument("--state", default=STATE_FILENAME, help="campaign.db path (sqlite3).")
    parser.add_argument(
        "--attempts-root", required=True,
        help="Local directory mirroring each job's GCS destination prefix, keyed by job id.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    attempts_root = Path(args.attempts_root)

    con = open_db(args.state, readonly=True)
    try:
        rows = [row_for(db_row, attempts_root) for db_row in latest_submissions(con)]
    finally:
        con.close()

    print(render_markdown(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
