"""Walk the attempts ledger plus each attempt's evidence and print one Markdown report.

Opens campaign.db read-only (mode=ro): report.py only ever reads the ledger, so
a stray write here is a bug this should fail loudly on, not tolerate.

Evidence lands directly under an attempt's `result_prefix` -- there is no leaf
to resolve -- and a row's evidence is refused unless the identity recorded in
`result.json` agrees with the row and with the prefix it was found under
(`verify.identity_errors`).

Three columns never share one vocabulary: `state` is the ledger's own attempt
state; `exit` is the subject's exit code from result.json; `verdict` is
verify.json's, or "-" where no comparison has been run. What report does NOT do
is re-normalize an attempt to re-derive a verdict: it binds verify.json's hashes
to the evidence it read and recomputes the verdict from the recorded diff, which
catches an edited record without re-running eleven capsules per report.

What a comparison is scoped to, and what it is not:

- **Per target bucket.** Listings of different corpora are not comparable, so
  attempts are sectioned by bucket and never pooled.
- **Per stratum**, `(product, fields)` resolved from the capsule's mode
  manifest, so a text listing is not ranked against a Parquet dataset and a
  key-only mode is not ranked against one emitting five fields.
- **`purpose = 'measurement'` only.** A preparation, canary or diagnostic is not
  in the population -- but a preparation's duration IS recorded, and every
  measurement it stands behind carries that cost, because publishing a 60-second
  listing that needed 40 seconds of hinting states something false.
- **A `statistic: rate` case renders as a rate over its attempts and a sample
  size**, never a mean duration over the survivors, which would be a
  survivorship result dressed as a timing.

Usage:
    report.py --state campaign.db [--group g20260817-120000]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmark import adapters
from benchmark.campaign import (
    STATE_FILENAME,
    TERMINAL_STATES,
    attempt_rows,
    open_ledger,
    pending_rows,
)
from benchmark.verify import (
    has_result_marker,
    identity_errors,
    read_bytes_at,
    verdict_for,
)

COLUMNS = (
    "tool",
    "mode",
    "product",
    "fields",
    "concurrency",
    "case_id",
    "attempt",
    "machine_type",
    "vcpus",
    "memory_gb",
    "container_memory_gb",
    "purpose",
    "statistic",
    "state",
    "evidence_state",
    "exit",
    "row_count",
    "wall_seconds",
    "prep_seconds",
    "max_rss_kb",
    # The fork-inherited floor under the figure beside it. Rendered rather than
    # left in the marker because the reset that shrinks it can fail on a kernel
    # or a procfs that refuses the write, and a contaminated RSS column that
    # says so is worth more than a clean-looking one that does not.
    "max_rss_floor_kb",
    "verdict",
)
FINAL_REPORT_STATES = {"SUCCEEDED", "CANCELLED", "ACCEPTED"}
BOUND_EVIDENCE_STATES = {"VERIFY_UNAVAILABLE", "VERIFIED"}
HEX64 = set("0123456789abcdef")


def load_json_at(result_prefix: str, name: str) -> tuple[dict[str, object], bytes] | None:
    try:
        raw = read_bytes_at(result_prefix, name)
        value = json.loads(raw)
        return (value, raw) if isinstance(value, dict) else None
    except Exception:
        # Missing is the common, expected case for verify.json (comparison not
        # yet run); any other read failure degrades to "unavailable" the same
        # way rather than crashing a summary over one bad prefix.
        return None


def result_binding_errors(row: sqlite3.Row, result: dict[str, object]) -> list[str]:
    """Where evidence disagrees with the row that launched it."""
    expected: dict[str, object] = {
        "group_id": row["group_id"],
        "job_name": row["job_name"],
        "case_id": row["case_id"],
        "attempt_id": row["attempt_id"],
        "tool": row["tool"],
        "mode": row["mode"],
        "bucket": row["target_bucket"],
        "region": row["target_region"],
        "prefix": row["target_prefix"],
        "auth_role": row["auth_role"],
        "image": row["image_uri"],
        "image_set_sha256": row["image_set_sha256"],
        "config": json.loads(row["config"]),
        "declared_resources": {
            "machine_type": row["machine_type"],
            "vcpus": row["vcpus"],
            "memory_gb": row["memory_gb"],
            "container_memory_gb": row["container_memory_gb"],
        },
    }
    errors = [name for name, value in expected.items() if result.get(name) != value]
    errors.extend(result_semantic_errors(result))
    return errors


def result_semantic_errors(result: dict[str, object]) -> list[str]:
    errors: list[str] = []
    exit_code = result.get("exit_code")
    timed_out = result.get("timed_out")
    row_count = result.get("row_count")
    row_count_error = result.get("row_count_error")
    execution = result.get("execution")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        errors.append("exit_code")
    if not isinstance(timed_out, bool):
        errors.append("timed_out")
    if row_count is not None and (
        isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0
    ):
        errors.append("row_count")
    if row_count_error is not None and not isinstance(row_count_error, str):
        errors.append("row_count_error")
    if execution is None:
        # The subject never ran: an inline setup exec failed ahead of it, and
        # the setup block is the account of why. Nothing here to check, and
        # every measured field must be absent rather than a zero.
        if not isinstance(result.get("setup"), dict):
            errors.append("setup")
        if exit_code == 0:
            errors.append("exit_code")
        errors.extend(
            name
            for name in ("wall_seconds", "max_rss_kb", "row_count", "row_count_error")
            if result.get(name) is not None
        )
        return errors
    if not isinstance(execution, dict):
        return [*errors, "execution"]
    max_rss_kb = result.get("max_rss_kb")
    execution_rss = execution.get("max_rss_kb")
    if (
        isinstance(max_rss_kb, bool)
        or not isinstance(max_rss_kb, int)
        or max_rss_kb < 0
        or max_rss_kb != execution_rss
    ):
        errors.append("max_rss_kb")
    elapsed_ns = execution.get("elapsed_ns")
    wall_seconds = result.get("wall_seconds")
    if isinstance(elapsed_ns, bool) or not isinstance(elapsed_ns, int) or elapsed_ns < 0:
        errors.append("execution.elapsed_ns")
    if (
        isinstance(wall_seconds, bool)
        or not isinstance(wall_seconds, (int, float))
        or not math.isfinite(wall_seconds)
    ):
        errors.append("wall_seconds")
    elif (
        isinstance(elapsed_ns, int)
        and not isinstance(elapsed_ns, bool)
        and wall_seconds != round(elapsed_ns / 1_000_000_000, 6)
    ):
        errors.append("wall_seconds/elapsed_ns")
    for name in (
        "timed_out",
        "process_group_empty",
        "descendants_empty",
        "process_tree_clean",
        "subreaper_enabled",
    ):
        if not isinstance(execution.get(name), bool):
            errors.append(f"execution.{name}")
    if execution.get("timed_out") != timed_out:
        errors.append("execution.timed_out")
    cgroup = execution.get("cgroup")
    if not isinstance(cgroup, dict):
        errors.append("execution.cgroup")
    else:
        for name in ("oom_delta", "oom_kill_delta"):
            value = cgroup.get(name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                errors.append(f"execution.cgroup.{name}")
    if exit_code == 0 and timed_out is False and row_count_error is None and row_count is None:
        errors.append("row_count")
    if (exit_code != 0 or timed_out is True) and row_count is not None:
        errors.append("row_count")
    return errors


def verify_binding_errors(
    verification: dict[str, object], row: sqlite3.Row, result_raw: bytes
) -> list[str]:
    """Where verify.json fails to bind to the attempt whose prefix it sits under."""
    expected = {
        "attempt_id": row["attempt_id"],
        "tool": row["tool"],
        "mode": row["mode"],
        "actual_result_sha256": hashlib.sha256(result_raw).hexdigest(),
    }
    errors = [name for name, value in expected.items() if verification.get(name) != value]
    if verification.get("verdict") not in {"PASS", "DRIFT", "FAIL"}:
        errors.append("verdict")
    for name in ("actual_tsv_sha256", "reference_tsv_sha256", "reference_result_sha256"):
        value = verification.get(name)
        if not isinstance(value, str) or len(value) != 64 or set(value) - HEX64:
            errors.append(name)
    for name in ("reference_attempt_id", "reference_tool", "reference_mode", "product"):
        if not isinstance(verification.get(name), str) or not verification[name]:
            errors.append(name)
    fields = verification.get("fields")
    if not isinstance(fields, list) or not all(isinstance(name, str) for name in fields):
        errors.append("fields")
    diff = verification.get("diff")
    required_lists = ("missing", "extra", "duplicates", "reference_duplicates", "mismatches")
    if (
        not isinstance(diff, dict)
        or set(diff) != set(required_lists)
        or not all(isinstance(diff.get(name), list) for name in required_lists)
    ):
        errors.append("diff")
    else:
        try:
            if verdict_for(diff) != verification.get("verdict"):
                errors.append("verdict")
        except (KeyError, TypeError):
            errors.append("diff")
    return errors


def stratum_for(row: sqlite3.Row, adapter_root: str) -> tuple[str, str]:
    """`(product, fields)` from the capsule, which is where they are defined.

    Unresolvable -- a capsule that no longer declares the mode -- renders as "-"
    and drops the attempt out of every stratum: what cannot be classified must
    not be ranked.
    """
    try:
        manifest = adapters.mode_manifest(
            adapters.adapter_dir_for(row["tool"], adapter_root), row["tool"], row["mode"]
        )
    except adapters.AdapterError:
        return "-", "-"
    return manifest.product, ",".join(manifest.fields)


def row_for(row: sqlite3.Row, *, adapter_root: str) -> dict[str, Any]:
    product, fields = stratum_for(row, adapter_root)
    base: dict[str, Any] = {
        "tool": row["tool"],
        "mode": row["mode"],
        "product": product,
        "fields": fields,
        "concurrency": "-" if row["concurrency"] is None else row["concurrency"],
        "case_id": row["case_id"],
        "attempt": row["attempt"],
        "attempt_id": row["attempt_id"],
        "bucket": row["target_bucket"],
        "machine_type": row["machine_type"],
        "vcpus": row["vcpus"],
        "memory_gb": row["memory_gb"],
        "container_memory_gb": (
            "-" if row["container_memory_gb"] is None else row["container_memory_gb"]
        ),
        "purpose": row["purpose"],
        "statistic": row["statistic"],
        "produced_by": row["produced_by"],
        "state": row["state"],
        "evidence_state": "UNAVAILABLE",
        "exit": "-",
        "row_count": "-",
        "wall_seconds": "-",
        "prep_seconds": "-",
        "max_rss_kb": "-",
        "max_rss_floor_kb": "-",
        "verdict": "-",
    }
    if not has_result_marker(row["result_prefix"]):
        return {**base, "evidence_state": "MISSING_EVIDENCE"}
    loaded_result = load_json_at(row["result_prefix"], "result.json")
    if loaded_result is None:
        return {**base, "evidence_state": "RESULT_UNAVAILABLE"}
    result, result_raw = loaded_result
    if identity_errors(
        result,
        attempt_id=row["attempt_id"],
        case_id=row["case_id"],
        result_prefix=row["result_prefix"],
    ):
        return {**base, "evidence_state": "IDENTITY_MISMATCH"}
    if result_binding_errors(row, result):
        return {**base, "evidence_state": "RESULT_MISMATCH"}
    execution = result.get("execution")
    measured = {
        **base,
        "exit": result.get("exit_code", "-"),
        "row_count": result.get("row_count", "-"),
        "wall_seconds": result.get("wall_seconds", "-"),
        "max_rss_kb": result.get("max_rss_kb", "-"),
        # Only the execution block carries the floor: it is a fact about the
        # invocation, like the cgroup peak beside it, and never mirrored onto
        # the subject fields.
        "max_rss_floor_kb": (
            execution.get("max_rss_floor_kb", "-") if isinstance(execution, dict) else "-"
        ),
    }
    loaded_verify = load_json_at(row["result_prefix"], "verify.json")
    if loaded_verify is None:
        return {**measured, "evidence_state": "VERIFY_UNAVAILABLE"}
    verification, _raw = loaded_verify
    if verify_binding_errors(verification, row, result_raw):
        return {**measured, "evidence_state": "VERIFY_MISMATCH"}
    return {
        **measured,
        "evidence_state": "VERIFIED",
        "verdict": verification.get("verdict", "-"),
    }


def attach_preparations(rows: list[dict[str, Any]]) -> None:
    """Fill `prep_seconds` with the cost of the chain behind each attempt.

    A preparation is measured though never compared, so the total cost of a path
    that needs one is recoverable -- and the report says which attempts had one.
    """
    by_attempt = {row["attempt_id"]: row for row in rows}
    for row in rows:
        chain: list[str] = []
        current, outside = row["produced_by"], None
        while current is not None and current not in chain:
            if current not in by_attempt:
                # A preparation another group made, reused by this one: the rows
                # it would be summed from are not in this report, and a sum over
                # the links that are is a smaller number wearing a total's name.
                outside = current
                break
            chain.append(current)
            current = by_attempt[current]["produced_by"]
        if not chain and outside is None:
            continue
        durations = [by_attempt[a]["wall_seconds"] for a in chain]
        row["preparations"] = chain
        row["crosses_group"] = outside
        row["prep_seconds"] = (
            round(sum(durations), 6)
            if outside is None
            and all(isinstance(d, (int, float)) and not isinstance(d, bool) for d in durations)
            else "-"
        )


def report_rows(db_rows: list[sqlite3.Row], *, adapter_root: str) -> list[dict[str, Any]]:
    rows = [row_for(db_row, adapter_root=adapter_root) for db_row in db_rows]
    attach_preparations(rows)
    return rows


def is_timing(row: dict[str, Any]) -> bool:
    return bool(row["purpose"] == "measurement" and row["statistic"] == "timing")


def rate_lines(rows: list[dict[str, Any]]) -> list[str]:
    """One line per rate case: successes over settled attempts, and the size.

    Never a mean duration over the survivors -- for these cases the failures are
    the measurement, so a mean over what happened to succeed would be a
    survivorship result dressed as a timing.
    """
    cases: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["purpose"] == "measurement" and row["statistic"] == "rate":
            cases.setdefault(row["case_id"], []).append(row)
    lines = []
    for case_id, attempts in sorted(cases.items()):
        settled = [a for a in attempts if a["state"] in TERMINAL_STATES]
        successes = sum(1 for a in settled if a["state"] == "SUCCEEDED")
        rate = f"{successes / len(settled):.4f}" if settled else "-"
        first = attempts[0]
        lines.append(
            f"- `{case_id}` ({first['tool']} {first['mode']}): {successes}/{len(settled)} "
            f"succeeded, rate {rate} over {len(attempts)} attempt(s)"
        )
    return lines


def stratum_lines(rows: list[dict[str, Any]]) -> list[str]:
    """The comparison scopes within one bucket, and what each holds."""
    strata: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if is_timing(row) and row["product"] != "-":
            strata.setdefault((row["product"], row["fields"]), []).append(row)
    lines = []
    for (product, fields), members in sorted(strata.items()):
        subjects = ", ".join(sorted(f"{m['tool']}/{m['mode']}" for m in members))
        verdicts = sorted({str(m["verdict"]) for m in members})
        lines.append(
            f"- **{product}** [{fields}]: {len(members)} attempt(s) -- {subjects} "
            f"-- verdicts {', '.join(verdicts)}"
        )
    return lines


def preparation_lines(rows: list[dict[str, Any]]) -> list[str]:
    lines = []
    for row in rows:
        if not row.get("preparations") and row.get("crosses_group") is None:
            continue
        behind = ", ".join(row["preparations"]) or "no attempt in this report"
        line = f"- `{row['attempt_id']}` ran behind {behind} "
        if row.get("crosses_group") is not None:
            line += (
                f"and `{row['crosses_group']}`, which is outside this report — the chain "
                "crosses a group boundary, so its preparation cost is unknown"
            )
        else:
            line += f"({row['prep_seconds']}s of preparation)"
        lines.append(line)
    return lines


def render_markdown(rows: list[dict[str, Any]], *, blocked: list[str]) -> str:
    header = "| " + " | ".join(COLUMNS) + " |"
    separator = "| " + " | ".join("---" for _ in COLUMNS) + " |"
    lines = [f"# Campaign report ({datetime.now(UTC).isoformat()})", ""]
    for slot in blocked:
        lines.append(f"> **Blocked slot**: {slot} -- this group is incomplete.")
    if blocked:
        lines.append("")
    for bucket in sorted({str(row["bucket"]) for row in rows}):
        members = [row for row in rows if row["bucket"] == bucket]
        lines.extend([f"## {bucket}", "", header, separator])
        lines.extend("| " + " | ".join(str(row[c]) for c in COLUMNS) + " |" for row in members)
        for title, section in (
            ("Comparison strata", stratum_lines(members)),
            ("Rate cases", rate_lines(members)),
            ("Preparations", preparation_lines(members)),
        ):
            if section:
                lines.extend(["", f"### {title}", "", *section])
        lines.append("")
    lines.append(summary_line(rows))
    return "\n".join(lines)


def summary_line(rows: list[dict[str, Any]]) -> str:
    verdict_counts: dict[str, int] = {}
    verified_timings = 0
    for row in rows:
        verdict = str(row["verdict"])
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        if (
            is_timing(row)
            and row["exit"] == 0
            and row["verdict"] in ("PASS", "DRIFT")
            and not isinstance(row["wall_seconds"], bool)
            and isinstance(row["wall_seconds"], (int, float))
            and math.isfinite(row["wall_seconds"])
        ):
            verified_timings += 1
    counts = ", ".join(f"{verdict}={count}" for verdict, count in sorted(verdict_counts.items()))
    return (
        f"**{len(rows)} attempt(s)** -- {counts} -- {verified_timings} verified timing(s); "
        "no cross-case timing aggregate"
    )


def report_exit_code(rows: list[dict[str, Any]], *, blocked: list[str]) -> int:
    """Gate finality separately from the operational outcomes being reported."""
    if not rows or blocked:
        return 1
    if any(row["state"] not in TERMINAL_STATES for row in rows):
        return 1
    if any(row["state"] not in FINAL_REPORT_STATES for row in rows):
        return 1
    if any(
        row["state"] == "SUCCEEDED" and row["evidence_state"] not in BOUND_EVIDENCE_STATES
        for row in rows
    ):
        return 1
    return 0


# Anchored to the repository, not the working directory. A relative default
# resolves against wherever the operator happened to stand, and a missing
# adapter directory then leaves every attempt unclassified -- which is
# indistinguishable from a capsule that dropped the mode.
DEFAULT_ADAPTER_ROOT = str(Path(__file__).resolve().parents[3] / "tools")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print a Markdown summary of a campaign's attempts."
    )
    parser.add_argument("--state", default=STATE_FILENAME, help="campaign.db path (sqlite3).")
    parser.add_argument("--group", help="Report one group; omitted reports the whole file.")
    parser.add_argument("--adapter-root", default=DEFAULT_ADAPTER_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not Path(args.adapter_root).is_dir():
        print(
            f"report: adapter root {args.adapter_root} is not a directory; refusing to "
            "report every attempt as unclassified",
            file=sys.stderr,
        )
        return 1

    con = open_ledger(args.state, readonly=True)
    try:
        rows = report_rows(attempt_rows(con, group_id=args.group), adapter_root=args.adapter_root)
        blocked = [
            f"slot {slot['slot']} ({slot['tool']}) awaiting {slot['awaiting']}"
            for slot in pending_rows(con, group_id=args.group)
            if slot["state"] == "BLOCKED"
        ]
    finally:
        con.close()

    print(render_markdown(rows, blocked=blocked))
    return report_exit_code(rows, blocked=blocked)


if __name__ == "__main__":
    sys.exit(main())
