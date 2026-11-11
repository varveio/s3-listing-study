"""Export one campaign group as a deterministic, reviewable receipt draft."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from benchmark import replay as replay_contract
from benchmark.ledger import (
    STATE_FILENAME,
    TERMINAL_STATES,
    attempt_rows,
    ledger,
    pending_rows,
)
from benchmark.report import load_json_at
from benchmark.verify import expected_result_binding, result_binding_errors

RESULT_FIELDS = (
    "started_at",
    "finished_at",
    "exit_code",
    "worker_exit_code",
    "timed_out",
    "wall_seconds",
    "max_rss_kb",
    "row_count",
    "row_count_error",
    "product",
    "product_error",
    "stdout",
    "stderr",
    "artifacts_size_bytes",
)


class ReceiptError(ValueError):
    """A group is absent, unsettled, or cannot be exported safely."""


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _list_index(
    config: replay_contract.ReplayConfig, evidence: object, server_log: str | None
) -> dict[str, object] | None:
    """The Go list index as the meters show it, plus the exported stderr line if given.

    The worker cannot read the server's stderr (a separate scratch-image
    container whose output goes to Cloud Logging), so the line is attached here
    from an operator export and checked against the meters the worker bound.
    """
    summary = replay_contract.list_index_summary(config, evidence)
    if summary is None:
        if server_log is not None:
            raise ReceiptError("a server log was given for an attempt without Go replay")
        return None
    if server_log is not None:
        replay_contract.attach_list_index_line(
            summary, replay_contract.parse_list_index_line(server_log)
        )
        summary["server_line_provenance"] = "operator export of the job's Cloud Logging"
    return summary


def _replay_summary(
    row: sqlite3.Row, result: Mapping[str, object], server_log: str | None = None
) -> dict[str, object] | None:
    if row["replay"] is None:
        if server_log is not None:
            raise ReceiptError(f"{row['attempt_id']}: a server log was given for a non-replay row")
        return None
    evidence = result.get("replay_evidence")
    config = replay_contract.parse_document(str(row["replay"]))
    refusals = replay_contract.replay_refusals(config, evidence, purpose=str(row["purpose"]))
    list_index = _list_index(config, evidence, server_log)
    if not isinstance(evidence, Mapping):
        refused: dict[str, object] = {"state": "REFUSED", "refusals": list(refusals)}
        if list_index is not None:
            refused["list_index"] = list_index
        return refused
    samples = evidence.get("samples")
    resources = evidence.get("resource_samples")
    readiness = evidence.get("readiness")
    summary: dict[str, object] = {
        "state": "REFUSED" if refusals else "COMPLETE",
        "refusals": list(refusals),
        "readiness": readiness.get("state") if isinstance(readiness, Mapping) else None,
        "sample_count": len(samples) if isinstance(samples, list) else None,
        "resource_sample_count": len(resources) if isinstance(resources, list) else None,
        "requests": {
            "before": replay_contract.counter_value(
                evidence.get("before"), replay_contract.REQUEST_COUNTER
            ),
            "after": replay_contract.counter_value(
                evidence.get("after"), replay_contract.REQUEST_COUNTER
            ),
        },
        "errors": {
            "before": replay_contract.counter_value(
                evidence.get("before"), replay_contract.ERROR_COUNTER
            ),
            "after": replay_contract.counter_value(
                evidence.get("after"), replay_contract.ERROR_COUNTER
            ),
        },
    }
    if list_index is not None:
        summary["list_index"] = list_index
    return summary


def _verification_summary(
    row: sqlite3.Row, result_sha256: str, loaded: tuple[dict[str, Any], bytes] | None
) -> dict[str, object] | None:
    if loaded is None:
        return None
    document, raw = loaded
    errors: list[str] = []
    if document.get("attempt_id") != row["attempt_id"]:
        errors.append("verify attempt_id does not match the recorded attempt")
    if document.get("actual_result_sha256") != result_sha256:
        errors.append("verify actual_result_sha256 does not match result.json")
    for field in ("reference_attempt_id", "reference_result_sha256"):
        value = document.get(field)
        if not isinstance(value, str) or not value:
            errors.append(f"verify {field} is missing")
    if document.get("verdict") not in {"PASS", "FAIL"}:
        errors.append("verify verdict is not PASS or FAIL")
    if not isinstance(document.get("diff"), Mapping):
        errors.append("verify diff is not an object")
    summary: dict[str, object] = {
        "state": "REFUSED" if errors else "BOUND",
        "binding_errors": errors,
        "uri": f"{str(row['result_prefix']).rstrip('/')}/verify.json",
        "sha256": _digest(raw),
    }
    if not errors:
        summary["verdict"] = document["verdict"]
        summary["reference_attempt_id"] = document["reference_attempt_id"]
        summary["reference_result_sha256"] = document["reference_result_sha256"]
    return summary


def _evidence(row: sqlite3.Row, server_log: str | None = None) -> dict[str, object]:
    loaded = load_json_at(str(row["result_prefix"]), "result.json")
    if loaded is None:
        result_uri = f"{str(row['result_prefix']).rstrip('/')}/result.json"
        return {"state": "MISSING", "result_uri": result_uri}
    result, raw = loaded
    binding_errors = result_binding_errors(
        expected_result_binding(row), result, purpose=str(row["purpose"])
    )
    result_sha256 = _digest(raw)
    return {
        "state": "BOUND" if not binding_errors else "REFUSED",
        "binding_errors": binding_errors,
        "result_uri": f"{str(row['result_prefix']).rstrip('/')}/result.json",
        "result_sha256": result_sha256,
        "result": {field: result.get(field) for field in RESULT_FIELDS},
        "argv": result.get("argv"),
        "replay": _replay_summary(row, result, server_log),
        "verification": _verification_summary(
            row, result_sha256, load_json_at(str(row["result_prefix"]), "verify.json")
        ),
    }


def build_receipt(
    con: sqlite3.Connection,
    group_id: str,
    *,
    server_logs: Mapping[str, str] | None = None,
) -> dict[str, object]:
    rows = attempt_rows(con, group_id=group_id)
    logs = dict(server_logs or {})
    unknown_logs = sorted(set(logs) - {str(row["attempt_id"]) for row in rows})
    if unknown_logs:
        raise ReceiptError(f"server logs name attempts outside group: {', '.join(unknown_logs)}")
    slots = pending_rows(con, group_id=group_id)
    if not rows and not slots:
        raise ReceiptError(f"group {group_id!r} is not in this ledger")
    unsettled = [str(row["attempt_id"]) for row in rows if row["state"] not in TERMINAL_STATES]
    blocked = [f"{row['group_id']}/{row['slot']}" for row in slots if row["state"] == "BLOCKED"]
    if unsettled or blocked:
        raise ReceiptError(
            f"group {group_id!r} is not settled (attempts={unsettled}, blocked_slots={blocked})"
        )

    cases: dict[str, object] = {}
    attempts: list[dict[str, object]] = []
    for row in rows:
        case_id = str(row["case_id"])
        cases.setdefault(case_id, json.loads(row["case_inputs"]))
        attempts.append(
            {
                "attempt_id": row["attempt_id"],
                "case_id": case_id,
                "group_id": row["group_id"],
                "tool": row["tool"],
                "mode": row["mode"],
                "purpose": row["purpose"],
                "statistic": row["statistic"],
                "origin": row["origin"],
                "state": row["state"],
                "state_detail": row["state_detail"],
                "recorded_at": row["recorded_at"],
                "settled_at": row["settled_at"],
                "result_prefix": row["result_prefix"],
                "request": json.loads(row["request_json"]),
                "evidence": _evidence(row, logs.get(str(row["attempt_id"]))),
            }
        )
    meta = con.execute("SELECT suite, schema_version, created_at FROM meta WHERE id=1").fetchone()
    return {
        "schema_version": 1,
        "kind": "campaign-receipt-draft",
        "suite": meta["suite"],
        "ledger_schema_version": meta["schema_version"],
        "ledger_created_at": meta["created_at"],
        "group_id": group_id,
        "purposes": sorted({str(row["purpose"]) for row in rows}),
        "resolved_cases": {key: cases[key] for key in sorted(cases)},
        "slots": [dict(row) for row in slots],
        "attempts": attempts,
    }


def render_markdown(document: Mapping[str, Any]) -> str:
    lines = [
        f"# Campaign receipt draft: {document['group_id']}",
        "",
        f"- Suite: `{document['suite']}`",
        f"- Purpose labels: {', '.join(document['purposes'])}",
        f"- Resolved cases: {len(document['resolved_cases'])}",
        f"- Attempts: {len(document['attempts'])}",
        "",
        "| attempt | tool | mode | purpose | provider | evidence | subject exit | "
        "worker exit | rows | wall s |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for attempt in document["attempts"]:
        evidence = attempt["evidence"]
        result = evidence.get("result", {})
        lines.append(
            "| {attempt_id} | {tool} | {mode} | {purpose} | {state} | {evidence_state} | "
            "{subject} | {worker} | {rows} | {wall} |".format(
                **attempt,
                evidence_state=evidence["state"],
                subject=result.get("exit_code", "-"),
                worker=result.get("worker_exit_code", "-"),
                rows=result.get("row_count", "-"),
                wall=result.get("wall_seconds", "-"),
            )
        )
    lines.extend(
        (
            "",
            "> Draft only: this is a factual export of recorded state and bound evidence. "
            "It does not promote a claim or turn diagnostics into measurements.",
            "",
        )
    )
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export one settled campaign group.")
    parser.add_argument("--state", default=STATE_FILENAME)
    parser.add_argument("--group", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--server-log",
        action="append",
        default=[],
        metavar="ATTEMPT_ID=PATH",
        help="an exported replay server log for one Go attempt; its list_index line is "
        "attached and checked against the bound meters (repeatable)",
    )
    return parser.parse_args(argv)


def _server_logs(specs: list[str]) -> dict[str, str]:
    logs: dict[str, str] = {}
    for spec in specs:
        attempt_id, separator, path = spec.partition("=")
        if not separator or not attempt_id or not path:
            raise ReceiptError("--server-log must be ATTEMPT_ID=PATH")
        if attempt_id in logs:
            raise ReceiptError(f"--server-log names {attempt_id} more than once")
        logs[attempt_id] = Path(path).read_text(encoding="utf-8")
    return logs


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.output.exists() and any(args.output.iterdir()):
            raise ReceiptError(f"output directory {args.output} is not empty")
        with ledger(args.state, readonly=True) as con:
            document = build_receipt(con, args.group, server_logs=_server_logs(args.server_log))
        args.output.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        (args.output / "receipt.json").write_text(payload)
        (args.output / "README.md").write_text(render_markdown(document))
    except (OSError, ReceiptError, replay_contract.ReplayError) as exc:
        print(f"receipt: {exc}", file=sys.stderr)
        return 1
    print(f"receipt: wrote {args.output / 'receipt.json'} and {args.output / 'README.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
