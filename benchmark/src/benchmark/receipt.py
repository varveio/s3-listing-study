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
    open_ledger,
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


def _replay_summary(row: sqlite3.Row, result: Mapping[str, object]) -> dict[str, object] | None:
    if row["replay"] is None:
        return None
    evidence = result.get("replay_evidence")
    refusals = replay_contract.replay_refusals(
        str(row["replay"]), evidence, purpose=str(row["purpose"])
    )
    if not isinstance(evidence, Mapping):
        return {"state": "REFUSED", "refusals": list(refusals)}
    samples = evidence.get("samples")
    resources = evidence.get("resource_samples")
    readiness = evidence.get("readiness")
    return {
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


def _evidence(row: sqlite3.Row) -> dict[str, object]:
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
        "replay": _replay_summary(row, result),
        "verification": _verification_summary(
            row, result_sha256, load_json_at(str(row["result_prefix"]), "verify.json")
        ),
    }


def build_receipt(con: sqlite3.Connection, group_id: str) -> dict[str, object]:
    rows = attempt_rows(con, group_id=group_id)
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
                "evidence": _evidence(row),
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.output.exists() and any(args.output.iterdir()):
            raise ReceiptError(f"output directory {args.output} is not empty")
        con = open_ledger(args.state, readonly=True)
        try:
            document = build_receipt(con, args.group)
        finally:
            con.close()
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
