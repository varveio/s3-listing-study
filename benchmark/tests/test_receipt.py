from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

from benchmark import ledger, receipt


def recorded_attempt(tmp_path: Path) -> tuple[sqlite3.Connection, ledger.Attempt]:
    con = ledger.open_ledger(str(tmp_path / "campaign.db"), suite="receipt-test")

    def build(ordinal: int) -> tuple[ledger.Attempt, str]:
        attempt = ledger.Attempt(
            case_id="alpha.aaaa",
            attempt=ordinal,
            case_inputs=json.dumps({"case": "alpha.aaaa"}),
            group_id="g1",
            tool="alpha",
            auth_role=None,
            executor="gcp-batch",
            location="us-east1",
            machine_type="n4-highcpu-2",
            vcpus=2,
            memory_gb=2,
            container_memory_gb=None,
            heap_percent=75,
            timeout_s=60,
            target_bucket="bucket",
            target_region="us-east-1",
            target_prefix="",
            config='{"mode":"text"}',
            input_artifact_sha256=None,
            produced_by=None,
            tool_slice_sha256="a" * 64,
            platform_sha256="b" * 64,
            image_uri="registry/toolbox@sha256:" + "c" * 64,
            image_set_sha256="d" * 64,
            executor_env="{}",
            service_account="worker@example.com",
            secret_resource=None,
            job_name=f"receipt-alpha-aaaa-s{ordinal}",
            result_prefix=str(tmp_path / f"evidence/alpha.aaaa.s{ordinal}"),
            purpose="diagnostic",
            statistic="timing",
            origin="planned",
        )
        return attempt, '{"provider":"request"}'

    attempt, _ = ledger.journal_intent(
        con,
        case_id="alpha.aaaa",
        case_inputs=json.dumps({"case": "alpha.aaaa"}),
        build=build,
    )
    ledger.set_state(con, attempt.attempt_id, "SUCCEEDED")
    return con, attempt


def write_result(attempt: ledger.Attempt) -> None:
    prefix = Path(attempt.result_prefix)
    prefix.mkdir(parents=True)
    result = {
        "attempt_id": attempt.attempt_id,
        "case_id": attempt.case_id,
        "group_id": attempt.group_id,
        "job_name": attempt.job_name,
        "tool": attempt.tool,
        "mode": "text",
        "bucket": attempt.target_bucket,
        "region": attempt.target_region,
        "prefix": "",
        "auth_role": None,
        "image": attempt.image_uri,
        "image_set_sha256": attempt.image_set_sha256,
        "config": {"mode": "text"},
        "replay": None,
        "declared_resources": {
            "machine_type": attempt.machine_type,
            "vcpus": 2,
            "memory_gb": 2,
            "container_memory_gb": None,
        },
        "argv": ["alpha", "list"],
        "exit_code": 0,
        "worker_exit_code": 0,
        "timed_out": False,
        "execution": {
            "timed_out": False,
            "subreaper_enabled": True,
            "process_tree_clean": True,
            "process_group_empty": True,
            "descendants_empty": True,
            "max_rss_kb": 10,
            "elapsed_ns": 1_000_000_000,
            "cgroup": {"oom_delta": 0, "oom_kill_delta": 0},
        },
        "wall_seconds": 1.0,
        "max_rss_kb": 10,
        "row_count": 1,
        "row_count_error": None,
        "product": {
            "artifact": "listing",
            "name": "native/listing.txt",
            "channel": "stdout",
            "size_bytes": 1,
            "sha256": "e" * 64,
        },
        "product_error": None,
        "stdout": None,
        "stderr": {"name": "stderr.log.gz", "size_bytes": 0, "sha256": "f" * 64},
        "native_manifest": {"listing.txt": "e" * 64},
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:01Z",
        "artifacts_size_bytes": 0,
    }
    (prefix / "result.json").write_text(json.dumps(result))


def test_receipt_export_is_deterministic_and_keeps_both_exit_codes(tmp_path: Path) -> None:
    con, attempt = recorded_attempt(tmp_path)
    write_result(attempt)
    first = receipt.build_receipt(con, "g1")
    second = receipt.build_receipt(con, "g1")
    con.close()

    assert first == second
    exported = cast(list[dict[str, Any]], first["attempts"])[0]
    evidence = cast(dict[str, Any], exported["evidence"])
    result = cast(dict[str, Any], evidence["result"])
    assert exported["request"] == {"provider": "request"}
    assert evidence["state"] == "BOUND"
    assert result["exit_code"] == 0
    assert result["worker_exit_code"] == 0


def test_receipt_refuses_verify_record_for_another_result(tmp_path: Path) -> None:
    con, attempt = recorded_attempt(tmp_path)
    write_result(attempt)
    verify = {
        "attempt_id": attempt.attempt_id,
        "reference_attempt_id": "reference.1",
        "actual_result_sha256": "0" * 64,
        "reference_result_sha256": "1" * 64,
        "verdict": "PASS",
        "diff": {},
    }
    (Path(attempt.result_prefix) / "verify.json").write_text(json.dumps(verify))
    document = receipt.build_receipt(con, "g1")
    con.close()

    exported = cast(list[dict[str, Any]], document["attempts"])[0]
    evidence = cast(dict[str, Any], exported["evidence"])
    verification = cast(dict[str, Any], evidence["verification"])
    assert verification["state"] == "REFUSED"
    assert "actual_result_sha256" in " ".join(verification["binding_errors"])
    assert "verdict" not in verification
