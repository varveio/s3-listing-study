"""Walk campaign.db plus each latest-submission attempt's result.json/verify.json
and print one Markdown summary table.

The report is human-readable Markdown derived from bound attempt evidence.

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
    report.py --state campaign.db
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

from benchmark import verify
from benchmark.campaign import (
    STATE_FILENAME,
    TERMINAL_STATES,
    attempt_rows,
    open_ledger,
)
from benchmark.contract import VERDICT_EXIT_CODES
from benchmark.verify import has_result_marker, read_bytes_at, resolve_leaf, verdict_for

COLUMNS = (
    "tool",
    "mode",
    "case_id",
    "run_ordinal",
    "bucket",
    "region",
    "machine_type",
    "vcpus",
    "memory_gb",
    "container_memory_gb",
    "image",
    "job_state",
    "evidence_state",
    "exit",
    "row_count",
    "wall_seconds",
    "max_rss_kb",
    "verdict",
)
FINAL_REPORT_STATES = {"SUCCEEDED", "CANCELLED", "ACCEPTED"}


def load_json_at(leaf: str, name: str) -> tuple[dict[str, object], bytes] | None:
    try:
        raw = read_bytes_at(leaf, name)
        value = json.loads(raw)
        return (value, raw) if isinstance(value, dict) else None
    except Exception:
        # Missing is the common, expected case for verify.json (comparison
        # not yet run); any other read failure degrades to "unavailable"
        # the same way rather than crashing a summary over one bad leaf.
        return None


def recorded_worker_options(row: sqlite3.Row) -> dict[str, str]:
    """Extract the immutable worker CLI options from the recorded Batch request."""
    document = json.loads(row["job_json"])
    commands = document["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]["commands"]
    if not isinstance(commands, list) or not all(isinstance(value, str) for value in commands):
        raise ValueError("recorded worker commands are malformed")
    options: dict[str, str] = {}
    index = 0
    while index < len(commands):
        name = commands[index]
        if not name.startswith("--") or index + 1 >= len(commands):
            raise ValueError("recorded worker commands are not flag/value pairs")
        value = commands[index + 1]
        if name in options and name != "--case-env":
            raise ValueError(f"recorded worker commands repeat {name}")
        options[name] = value
        index += 2
    return options


def result_binding_errors(row: sqlite3.Row, result: dict[str, object]) -> list[str]:
    try:
        options = recorded_worker_options(row)
        container_memory = options["--container-memory-gb"]
        resources: dict[str, object] = {
            "machine_type": options["--machine-type"],
            "vcpus": int(options["--vcpus"]),
            "memory_gb": int(options["--memory-gb"]),
            "container_memory_gb": None if container_memory == "none" else int(container_memory),
        }
        expected = {
            "campaign_id": row["campaign_id"],
            "job_id": row["job_id"],
            "case_id": row["case_id"],
            "case_fingerprint": row["fingerprint"],
            "image": row["image_uri"],
            "image_set_sha256": row["image_set_sha256"],
            "tool": row["tool"],
            "mode": row["mode"],
            "bucket": row["bucket"],
            "region": row["region"],
            "run_ordinal": row["rep"],
            "submission_number": row["submission"],
            "tool_recipe_sha256": options["--tool-recipe-sha256"],
            "tool_build_inputs_sha256": options["--tool-build-inputs-sha256"],
            "toolbox_manifest_sha256": options["--toolbox-manifest-sha256"],
            "toolbox_recipe_sha256": options["--toolbox-recipe-sha256"],
            "tool_version": options["--tool-version"],
            "tool_build_sha256": options["--tool-build-sha256"],
            "adapter_bundle_sha256": options["--adapter-bundle-sha256"],
            "harness_revision": options["--harness-revision"],
            "subject_workdir": options["--subject-workdir"],
            "applied_subject_workdir": options["--subject-workdir"],
            "declared_resources": resources,
        }
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return ["job_json"]
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
    verification: dict[str, object],
    result: dict[str, object],
    result_raw: bytes,
    leaf: str,
) -> list[str]:
    expected = {
        "actual_leaf": leaf,
        "actual_result_sha256": hashlib.sha256(result_raw).hexdigest(),
        "tool": result["tool"],
        "mode": result["mode"],
    }
    errors = [name for name, value in expected.items() if verification.get(name) != value]
    if verification.get("verdict") not in {"PASS", "DRIFT", "FAIL"}:
        errors.append("verdict")
    for name in ("actual_tsv_sha256", "reference_tsv_sha256", "reference_result_sha256"):
        value = verification.get(name)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            errors.append(name)
    for name in ("reference_leaf", "reference_tool", "reference_mode"):
        if not isinstance(verification.get(name), str) or not verification[name]:
            errors.append(name)
    reference_leaf = verification.get("reference_leaf")
    if isinstance(reference_leaf, str):
        try:
            reference_raw = read_bytes_at(reference_leaf, "result.json")
            reference_result = json.loads(reference_raw)
        except Exception:
            errors.append("reference_result")
        else:
            if hashlib.sha256(reference_raw).hexdigest() != verification.get(
                "reference_result_sha256"
            ):
                errors.append("reference_result_sha256")
            if not isinstance(reference_result, dict):
                errors.append("reference_result")
            else:
                for name in ("tool", "mode"):
                    if verification.get(f"reference_{name}") != reference_result.get(name):
                        errors.append(f"reference_{name}")
                for name in ("bucket", "region", "prefix"):
                    if reference_result.get(name) != result.get(name):
                        errors.append(f"reference_{name}")
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


def parent_destination(leaf: str) -> str:
    return leaf.rstrip("/").rsplit("/", 1)[0] + "/"


def recompute_verification(
    verification: dict[str, object], result: dict[str, object], leaf: str, adapter_root: str
) -> bool:
    reference_leaf = verification.get("reference_leaf")
    if not isinstance(reference_leaf, str):
        return False
    code, recomputed = verify.verify_leaves(
        tool=str(result["tool"]),
        bucket=str(result["bucket"]),
        prefix=str(result["prefix"]),
        mode=str(result["mode"]),
        actual_destination=parent_destination(leaf),
        reference_destination=parent_destination(reference_leaf),
        adapter_root=adapter_root,
        expected_actual={
            "campaign_id": result["campaign_id"],
            "job_id": result["job_id"],
            "case_id": result["case_id"],
            "case_fingerprint": result["case_fingerprint"],
            "image": result["image"],
            "image_set_sha256": result["image_set_sha256"],
        },
        expected_reference={
            "tool": verification["reference_tool"],
            "mode": verification["reference_mode"],
        },
        write_record=False,
    )
    return code in VERDICT_EXIT_CODES.values() and recomputed == verification


def row_for(row: sqlite3.Row, *, adapter_root: str = "tools") -> dict[str, object]:
    try:
        options = recorded_worker_options(row)
        recorded_resources: dict[str, object] = {
            "machine_type": options["--machine-type"],
            "vcpus": int(options["--vcpus"]),
            "memory_gb": int(options["--memory-gb"]),
            "container_memory_gb": (
                "-"
                if options["--container-memory-gb"] == "none"
                else int(options["--container-memory-gb"])
            ),
        }
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        recorded_resources = {
            "machine_type": "-",
            "vcpus": "-",
            "memory_gb": "-",
            "container_memory_gb": "-",
        }
    base = {
        "tool": row["tool"],
        "mode": row["mode"],
        "case_id": row["case_id"],
        "run_ordinal": row["rep"],
        "bucket": row["bucket"],
        "region": row["region"],
        **recorded_resources,
        "image": row["image_uri"],
        "job_state": row["state"],
        "evidence_state": "UNAVAILABLE",
        "exit": "-",
        "row_count": "-",
        "wall_seconds": "-",
        "max_rss_kb": "-",
        "verdict": "-",
    }
    leaf = resolve_leaf(row["destination"])
    if leaf is None:
        return {**base, "evidence_state": "AMBIGUOUS_LEAF"}
    if not has_result_marker(leaf):
        return {**base, "evidence_state": "INCOMPLETE_LEAF"}

    loaded_result = load_json_at(leaf, "result.json")
    if loaded_result is None:
        return {**base, "evidence_state": "RESULT_UNAVAILABLE"}
    result, result_raw = loaded_result
    if result_binding_errors(row, result):
        return {**base, "evidence_state": "RESULT_MISMATCH"}
    loaded_verify = load_json_at(leaf, "verify.json")
    measured = {
        **base,
        "exit": result.get("exit_code", "-"),
        "row_count": result.get("row_count", "-"),
        "wall_seconds": result.get("wall_seconds", "-"),
        "max_rss_kb": result.get("max_rss_kb", "-"),
    }
    resources = result.get("declared_resources")
    if isinstance(resources, dict):
        measured.update(
            {
                "machine_type": resources.get("machine_type", "-"),
                "vcpus": resources.get("vcpus", "-"),
                "memory_gb": resources.get("memory_gb", "-"),
                "container_memory_gb": resources.get("container_memory_gb", "-"),
            }
        )
    if loaded_verify is None:
        return {**measured, "evidence_state": "VERIFY_UNAVAILABLE"}
    verify_output, _verify_raw = loaded_verify
    if verify_binding_errors(verify_output, result, result_raw, leaf):
        return {**measured, "evidence_state": "VERIFY_MISMATCH"}
    if not recompute_verification(verify_output, result, leaf, adapter_root):
        return {**measured, "evidence_state": "VERIFY_MISMATCH"}
    return {
        **measured,
        "evidence_state": "VERIFIED",
        "verdict": verify_output.get("verdict", "-"),
    }


def render_markdown(rows: list[dict[str, object]]) -> str:
    header = "| " + " | ".join(COLUMNS) + " |"
    separator = "| " + " | ".join("---" for _ in COLUMNS) + " |"
    lines = [f"# Campaign report ({datetime.now(UTC).isoformat()})", "", header, separator]
    for row in rows:
        lines.append("| " + " | ".join(str(row[c]) for c in COLUMNS) + " |")
    lines.append("")
    lines.append(summary_line(rows))
    return "\n".join(lines)


def summary_line(rows: list[dict[str, object]]) -> str:
    verdict_counts: dict[str, int] = {}
    verified_timings = 0
    for row in rows:
        verdict = str(row["verdict"])
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        if (
            row["exit"] == 0
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


def report_exit_code(
    rows: list[dict[str, object]], *, all_job_states: list[str] | None = None
) -> int:
    """Gate finality separately from the operational outcomes being reported."""
    if not rows:
        return 1
    states = (
        all_job_states if all_job_states is not None else [str(row["job_state"]) for row in rows]
    )
    if any(state not in TERMINAL_STATES for state in states):
        return 1
    if any(row["job_state"] not in FINAL_REPORT_STATES for row in rows):
        return 1
    bound_result_states = {"VERIFY_UNAVAILABLE", "VERIFY_MISMATCH", "VERIFIED"}
    if any(
        row["job_state"] == "SUCCEEDED" and row.get("evidence_state") not in bound_result_states
        for row in rows
    ):
        return 1
    return 0


# Anchored to the repository, not the working directory. A relative default
# resolves against wherever the operator happened to stand, and a missing
# adapter directory then fails every recompute -- reporting a verified campaign
# as VERIFY_MISMATCH, which is indistinguishable from evidence that genuinely
# disagreed with its verdict.
DEFAULT_ADAPTER_ROOT = str(Path(__file__).resolve().parents[3] / "tools")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print a Markdown summary of a campaign's results."
    )
    parser.add_argument("--state", default=STATE_FILENAME, help="campaign.db path (sqlite3).")
    parser.add_argument("--adapter-root", default=DEFAULT_ADAPTER_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not Path(args.adapter_root).is_dir():
        print(
            f"report: adapter root {args.adapter_root} is not a directory; refusing to "
            "report every attempt as a verification mismatch",
            file=sys.stderr,
        )
        return 1

    con = open_ledger(args.state, readonly=True)
    try:
        all_rows = attempt_rows(con)
        rows = [row_for(db_row, adapter_root=args.adapter_root) for db_row in all_rows]
    finally:
        con.close()

    print(render_markdown(rows))
    return report_exit_code(rows, all_job_states=[row["state"] for row in all_rows])


if __name__ == "__main__":
    sys.exit(main())
