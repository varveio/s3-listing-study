"""Walk the attempts ledger plus each attempt's evidence and print one Markdown report.

Opens campaign.db read-only (mode=ro): report.py only ever reads the ledger, so
a stray write here is a bug this should fail loudly on, not tolerate.

Evidence lands directly under an attempt's `result_prefix` -- there is no leaf
to resolve -- and a row's evidence is refused unless the identity recorded in
`result.json` agrees with the row and with the prefix it was found under
(`verify.identity_errors`).

`state` is the ledger's attempt state; `exit`, `row_count`, timing, and RSS come
from the bound `result.json`. Routine reporting deliberately reads no raw
listing and no derived `verify.json`: raw products are retained for manual
investigation, not consumed by the campaign reporting path.

What a comparison is scoped to, and what it is not:

- **Per target bucket.** Listings of different corpora are not comparable, so
  attempts are sectioned by bucket and never pooled.
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
import json
import math
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmark import adapters
from benchmark import replay as replay_contract
from benchmark.ledger import (
    STATE_FILENAME,
    TERMINAL_STATES,
    attempt_rows,
    open_ledger,
    pending_rows,
    producer_summary,
    slot_owed_reason,
)
from benchmark.verify import has_result_marker, identity_errors, read_bytes_at

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
    "declared_server_allocation",
    "declared_subject_allocation",
    "derived_host_headroom",
    "capacity_status",
    "purpose",
    "statistic",
    "state",
    "evidence_state",
    "replay_state",
    "exit",
    "worker_exit",
    "row_count",
    "wall_seconds",
    "prep_seconds",
    "max_rss_kb",
    # The fork-inherited floor under the figure beside it. Rendered rather than
    # left in the marker because the reset that shrinks it can fail on a kernel
    # or a procfs that refuses the write, and a contaminated RSS column that
    # says so is worth more than a clean-looking one that does not.
    "max_rss_floor_kb",
)
FINAL_REPORT_STATES = {"SUCCEEDED", "CANCELLED", "ACCEPTED"}
BOUND_EVIDENCE_STATES = {"RESULT_BOUND"}
HEX64 = set("0123456789abcdef")


def load_json_at(result_prefix: str, name: str) -> tuple[dict[str, object], bytes] | None:
    try:
        raw = read_bytes_at(result_prefix, name)
        value = json.loads(raw)
        return (value, raw) if isinstance(value, dict) else None
    except Exception:
        # Any read failure degrades to "unavailable" rather than crashing a
        # summary over one bad prefix.
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
        "replay": None if row["replay"] is None else json.loads(row["replay"]),
        "declared_resources": {
            "machine_type": row["machine_type"],
            "vcpus": row["vcpus"],
            "memory_gb": row["memory_gb"],
            "container_memory_gb": row["container_memory_gb"],
        },
    }
    errors = [
        name for name, value in expected.items() if name not in result or result.get(name) != value
    ]
    errors.extend(result_semantic_errors(result))
    return errors


def result_semantic_errors(result: dict[str, object]) -> list[str]:
    errors: list[str] = []
    exit_code = result.get("exit_code")
    worker_exit_code = result.get("worker_exit_code")
    timed_out = result.get("timed_out")
    row_count = result.get("row_count")
    row_count_error = result.get("row_count_error")
    execution = result.get("execution")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        errors.append("exit_code")
    if isinstance(worker_exit_code, bool) or not isinstance(worker_exit_code, int):
        errors.append("worker_exit_code")
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
        replay_evidence = result.get("replay_evidence")
        replay_refusal = (
            isinstance(result.get("replay"), dict)
            and isinstance(replay_evidence, dict)
            and bool(replay_evidence.get("errors"))
        )
        if not isinstance(result.get("setup"), dict) and not replay_refusal:
            errors.append("setup")
        if exit_code == 0:
            errors.append("exit_code")
        errors.extend(
            name
            for name in (
                "wall_seconds",
                "max_rss_kb",
                "row_count",
                "row_count_error",
                "product",
                "product_error",
            )
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
    errors.extend(result_capture_errors(result))
    counted = exit_code == 0 and timed_out is False and result.get("product_error") is None
    if counted and row_count_error is None and row_count is None:
        errors.append("row_count")
    if not counted and row_count is not None:
        errors.append("row_count")
    return errors


def result_capture_errors(result: dict[str, object]) -> list[str]:
    """Where the marker fails to say what this attempt published, and how.

    A stdout capture may be absent, and only for the one reason: the mode's
    product travels on fd 1, so those bytes are the product and there is no
    second thing to log. Anything else absent is a marker that cannot be read.
    """
    errors: list[str] = []
    minimal = result.get("evidence_profile") == "minimal-replay"
    product = result.get("product")
    if product is not None:
        errors.extend(
            f"product.{name}"
            for name in _artifact_errors(product, digest_optional=True, minimal=minimal)
        )
    product_error = result.get("product_error")
    if product_error is not None and not isinstance(product_error, str):
        errors.append("product_error")
    for stem in ("stdout", "stderr"):
        capture = result.get(stem)
        if capture is None:
            if stem == "stderr" or product is None:
                errors.append(stem)
            continue
        errors.extend(f"{stem}.{name}" for name in _artifact_errors(capture, minimal=minimal))
    return errors


def _artifact_errors(
    block: object, *, digest_optional: bool = False, minimal: bool = False
) -> list[str]:
    """The name/size/digest every published artifact is recorded by."""
    if not isinstance(block, dict):
        return ["shape"]
    errors: list[str] = []
    name = block.get("name")
    if not isinstance(name, str) or not name:
        errors.append("name")
    size = block.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        errors.append("size_bytes")
    digest = block.get("sha256")
    if digest is None:
        if not minimal and (not digest_optional or block.get("channel") != "dataset"):
            errors.append("sha256")
    elif not isinstance(digest, str) or len(digest) != 64 or set(digest) - HEX64:
        errors.append("sha256")
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


def _declared_replay_allocations(row: sqlite3.Row) -> tuple[str, str, str, str]:
    raw = row["replay"]
    if raw is None:
        return "-", "-", "-", "-"
    try:
        config = replay_contract.parse_document(str(raw))
        summary = replay_contract.allocation_summary(
            config,
            box_vcpus=int(row["vcpus"]),
            box_memory_gb=int(row["memory_gb"]),
            container_memory_gb=row["container_memory_gb"],
        )
        subject_memory = (
            "uncapped" if row["container_memory_gb"] is None else f"{row['container_memory_gb']}GiB"
        )
        host_memory = (
            "unreserved"
            if summary.host_memory_headroom_gb is None
            else f"{summary.host_memory_headroom_gb}GiB"
        )
        return (
            f"cpus={summary.server_cpuset};memory={config.allocation.replay_memory_gb}GiB",
            f"cpus={summary.subject_cpuset};memory={subject_memory}",
            f"vcpus={summary.host_vcpus};memory={host_memory}",
            config.capacity_status.upper(),
        )
    except (TypeError, ValueError, replay_contract.ReplayError):
        return "malformed", "malformed", "malformed", "malformed"


def row_for(row: sqlite3.Row, *, adapter_root: str) -> dict[str, Any]:
    product, fields = stratum_for(row, adapter_root)
    declared_server, declared_subject, host_headroom, capacity_status = (
        _declared_replay_allocations(row)
    )
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
        "declared_server_allocation": declared_server,
        "declared_subject_allocation": declared_subject,
        "derived_host_headroom": host_headroom,
        "capacity_status": capacity_status,
        "purpose": row["purpose"],
        "statistic": row["statistic"],
        "produced_by": row["produced_by"],
        "state": row["state"],
        "evidence_state": "UNAVAILABLE",
        "replay_state": "-",
        "exit": "-",
        "worker_exit": "-",
        "row_count": "-",
        "wall_seconds": "-",
        "prep_seconds": "-",
        "max_rss_kb": "-",
        "max_rss_floor_kb": "-",
    }
    if not has_result_marker(row["result_prefix"]):
        return {**base, "evidence_state": "MISSING_EVIDENCE"}
    loaded_result = load_json_at(row["result_prefix"], "result.json")
    if loaded_result is None:
        return {**base, "evidence_state": "RESULT_UNAVAILABLE"}
    result, _result_raw = loaded_result
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
    replay_evidence = result.get("replay_evidence")
    replay_state = "-"
    if row["replay"] is not None:
        try:
            replay_config = replay_contract.parse_document(row["replay"])
            replay_refusals = replay_contract.evidence_errors(
                replay_config, replay_evidence, purpose=str(row["purpose"])
            )
        except replay_contract.ReplayError:
            replay_refusals = ("recorded replay document is malformed",)
        replay_state = "REFUSED" if replay_refusals else "COMPLETE"
    measured = {
        **base,
        "replay_state": replay_state,
        "exit": result.get("exit_code", "-"),
        "worker_exit": result.get("worker_exit_code", "-"),
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
    return {**measured, "evidence_state": "RESULT_BOUND"}


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


def is_publishable_measurement(row: dict[str, Any]) -> bool:
    """A replay measurement is publishable only after its capacity is calibrated."""
    return bool(row["purpose"] == "measurement" and row["capacity_status"] != "UNCALIBRATED")


def is_timing(row: dict[str, Any]) -> bool:
    return bool(is_publishable_measurement(row) and row["statistic"] == "timing")


def subject_succeeded(row: dict[str, Any]) -> bool:
    """Whether a settled listing produced one complete, accepted result."""
    return bool(
        row["state"] == "SUCCEEDED"
        and row["evidence_state"] == "RESULT_BOUND"
        and row["exit"] == 0
        and row["worker_exit"] == 0
        and row["replay_state"] in {"-", "COMPLETE"}
        and not isinstance(row["row_count"], bool)
        and isinstance(row["row_count"], int)
    )


def rate_lines(rows: list[dict[str, Any]]) -> list[str]:
    """One line per rate case: successes over settled attempts, and the size.

    Never a mean duration over the survivors -- for these cases the failures are
    the measurement, so a mean over what happened to succeed would be a
    survivorship result dressed as a timing.
    """
    cases: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if is_publishable_measurement(row) and row["statistic"] == "rate":
            cases.setdefault(row["case_id"], []).append(row)
    lines = []
    for case_id, attempts in sorted(cases.items()):
        settled = [a for a in attempts if a["state"] in TERMINAL_STATES]
        successes = sum(subject_succeeded(attempt) for attempt in settled)
        rate = f"{successes / len(settled):.4f}" if settled else "-"
        first = attempts[0]
        lines.append(
            f"- `{case_id}` ({first['tool']} {first['mode']}): {successes}/{len(settled)} "
            f"succeeded, rate {rate} over {len(attempts)} attempt(s)"
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


def slot_note(con: sqlite3.Connection, slot: sqlite3.Row) -> str:
    """One blocked slot, and whether anything can still pay it.

    A slot no attempt in its group can ever satisfy is a measurement quietly
    absent -- the failure a slot exists to make visible -- so it is reported as
    owed rather than as merely waiting.
    """
    owed = slot["awaiting"] or producer_summary(str(slot["producer"]))
    note = f"{slot['slot']} ({slot['tool']}) awaiting {owed}"
    reason = slot_owed_reason(con, slot)
    if reason is None:
        return note
    return f"{note} -- UNSATISFIABLE, nothing in this group can pay it: {reason}"


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
            ("Rate cases", rate_lines(members)),
            ("Preparations", preparation_lines(members)),
        ):
            if section:
                lines.extend(["", f"### {title}", "", *section])
        lines.append("")
    lines.append(summary_line(rows))
    return "\n".join(lines)


def summary_line(rows: list[dict[str, Any]]) -> str:
    successful_timings = sum(
        1
        for row in rows
        if is_timing(row)
        and subject_succeeded(row)
        and not isinstance(row["wall_seconds"], bool)
        and isinstance(row["wall_seconds"], (int, float))
        and math.isfinite(row["wall_seconds"])
    )
    return (
        f"**{len(rows)} attempt(s)** -- {successful_timings} successful timing(s); "
        "row counts are reported from result.json; no content comparison"
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
    if any(
        row["state"] == "SUCCEEDED"
        and (row["worker_exit"] != 0 or row["replay_state"] not in {"-", "COMPLETE"})
        for row in rows
    ):
        return 1
    if any(
        row["state"] == "SUCCEEDED"
        and row["purpose"] != "preparation"
        and row["statistic"] != "rate"
        and not subject_succeeded(row)
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
            slot_note(con, slot)
            for slot in pending_rows(con, group_id=args.group)
            if slot["state"] == "BLOCKED"
        ]
    finally:
        con.close()

    print(render_markdown(rows, blocked=blocked))
    return report_exit_code(rows, blocked=blocked)


if __name__ == "__main__":
    sys.exit(main())
