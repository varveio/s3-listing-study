"""Walk campaign.db plus each latest-submission attempt's result.json/verify.json
and print one Markdown summary table.

This is a SKETCH standing in for the real report renderer (which produces a
byte-identical verify.md as part of the audit record, with a frozen template
and stable field ordering the acceptance tests pin down). This one just
prints Markdown to stdout; if two runs produce a differently-ordered table,
nothing here cares.

Opens campaign.db read-only (mode=ro): report.py only ever reads the ledger,
so a stray write here is a bug this should fail loudly on, not tolerate.

Like verify.py, a job's destination is a *prefix*, not the attempt itself:
report.py resolves it to the one attempt leaf underneath (see
verify.resolve_leaf), then reads result.json/verify.json off that leaf
through verify.read_bytes_at -- which already dispatches on gs:// vs local,
so --attempts-root gs://... needs no separate code path here.

Three columns never share one vocabulary: job_state is Batch's own state
(or a leaf-resolution failure, AMBIGUOUS_LEAF/INCOMPLETE_LEAF, which is a
job-level fact, not the tool's); exit is the subject's own exit code from
result.json; verdict is verify.json's verdict, or "-" if no verify.json
exists yet (never the job or subject state). The summary line's average
wall time is over verified attempts only (exit 0 and a PASS/DRIFT verdict)
-- a crashed or unverified attempt's wall time is not a listing-speed
number, and averaging it in would quietly make the average mean something
else.

Usage:
    report.py --state campaign.db --attempts-root gs://results-bucket
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime

from campaign import STATE_FILENAME, latest_submissions, open_db
from verify import has_result_marker, read_bytes_at, resolve_leaf

COLUMNS = ("tool", "mode", "job_state", "exit", "row_count", "wall_seconds", "verdict")


def load_json_at(leaf: str, name: str) -> dict | None:
    try:
        return json.loads(read_bytes_at(leaf, name))
    except Exception:
        # Missing is the common, expected case for verify.json (comparison
        # not yet run); any other read failure degrades to "unavailable"
        # the same way rather than crashing a summary over one bad leaf.
        return None


def _destination_for(attempts_root: str, job_id: str) -> str:
    if attempts_root.startswith("gs://"):
        return attempts_root.rstrip("/") + "/" + job_id + "/"
    return attempts_root.rstrip("/") + "/" + job_id


def row_for(row: sqlite3.Row, attempts_root: str) -> dict:
    base = {"tool": row["tool"], "mode": row["mode"], "exit": "-", "row_count": "-", "wall_seconds": "-", "verdict": "-"}
    leaf = resolve_leaf(_destination_for(attempts_root, row["job_id"]))
    if leaf is None:
        return {**base, "job_state": "AMBIGUOUS_LEAF"}
    if not has_result_marker(leaf):
        return {**base, "job_state": "INCOMPLETE_LEAF"}

    result = load_json_at(leaf, "result.json") or {}
    verify_output = load_json_at(leaf, "verify.json") or {}
    return {
        "tool": row["tool"],
        "mode": row["mode"],
        "job_state": row["state"],
        "exit": result.get("exit_code", "-"),
        "row_count": result.get("row_count", "-"),
        "wall_seconds": result.get("wall_seconds", "-"),
        "verdict": verify_output.get("verdict", "-"),
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
    verified_wall_times = []
    for row in rows:
        verdict_counts[row["verdict"]] = verdict_counts.get(row["verdict"], 0) + 1
        if row["exit"] == 0 and row["verdict"] in ("PASS", "DRIFT") and isinstance(row["wall_seconds"], (int, float)):
            verified_wall_times.append(row["wall_seconds"])
    counts = ", ".join(f"{verdict}={count}" for verdict, count in sorted(verdict_counts.items()))
    average = f"{sum(verified_wall_times) / len(verified_wall_times):.1f}s" if verified_wall_times else "-"
    return (
        f"**{len(rows)} attempt(s)** -- {counts} -- "
        f"average wall time over {len(verified_wall_times)} verified attempt(s): {average}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print a Markdown summary of a campaign's results.")
    parser.add_argument("--state", default=STATE_FILENAME, help="campaign.db path (sqlite3).")
    parser.add_argument(
        "--attempts-root", required=True,
        help="Local directory or gs:// bucket mirroring each job's destination prefix, keyed by job id.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    con = open_db(args.state, readonly=True)
    try:
        rows = [row_for(db_row, args.attempts_root) for db_row in latest_submissions(con)]
    finally:
        con.close()

    print(render_markdown(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
