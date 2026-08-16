from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from benchmark import campaign, report, verify
from benchmark.contract import sha256_of
from benchmark.plan import Plan

ROOT = Path(__file__).parents[2]
WORKER_VALUES = {
    "--machine-type": "n4-standard-2",
    "--vcpus": "2",
    "--memory-gb": "8",
    "--container-memory-gb": "none",
    "--toolbox-manifest-sha256": "9" * 64,
    "--toolbox-recipe-sha256": "8" * 64,
    "--tool-recipe-sha256": "7" * 64,
    "--tool-build-inputs-sha256": "6" * 64,
    "--tool-version": "1",
    "--tool-build-sha256": "d" * 64,
    "--adapter-bundle-sha256": "e" * 64,
    "--harness-revision": "f" * 40,
    "--subject-workdir": "/aws",
}


def job_document(*, omit: str | None = None) -> dict[str, object]:
    values = {name: value for name, value in WORKER_VALUES.items() if name != omit}
    commands = [item for pair in values.items() for item in pair]
    return {"taskGroups": [{"taskSpec": {"runnables": [{"container": {"commands": commands}}]}}]}


def recorded_row(tmp_path: Path, document: dict[str, object]) -> tuple[sqlite3.Row, Path]:
    case = Plan.load(ROOT / "benchmark/plans/buckets/noaa-ghcn-pds.yaml").cases[0]
    destination = tmp_path / "job"
    leaf = destination / "attempt"
    leaf.mkdir(parents=True)
    con = campaign.open_db(str(tmp_path / "campaign.db"))
    campaign.record_intent(
        con,
        base_job_id="c-job",
        submission=1,
        job_id="c-job",
        campaign_id="2026-08-16-candidate",
        project="p",
        location="l",
        case=case,
        rep=1,
        bucket="bucket",
        region="region",
        image_uri="registry/toolbox@sha256:" + "a" * 64,
        image_set_sha256="b" * 64,
        destination=str(destination),
        job_dict=document,
    )
    campaign.update_submission_state(con, "c-job", "SUCCEEDED")
    return campaign.latest_submissions(con)[0], leaf


@pytest.mark.parametrize("missing_flag", sorted(WORKER_VALUES))
def test_report_treats_missing_recorded_worker_flags_as_result_mismatch(
    tmp_path: Path, missing_flag: str
) -> None:
    row, leaf = recorded_row(tmp_path, job_document(omit=missing_flag))
    (leaf / "result.json").write_text("{}")
    assert report.row_for(row)["evidence_state"] == "RESULT_MISMATCH"


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"taskGroups": []},
        {"taskGroups": [{"taskSpec": {"runnables": [{"container": {"commands": "bad"}}]}}]},
        {
            "taskGroups": [
                {"taskSpec": {"runnables": [{"container": {"commands": ["--machine-type"]}}]}}
            ]
        },
    ],
)
def test_report_treats_malformed_recorded_request_as_result_mismatch(
    tmp_path: Path, document: dict[str, object]
) -> None:
    row, leaf = recorded_row(tmp_path, document)
    (leaf / "result.json").write_text("{}")
    assert report.row_for(row)["evidence_state"] == "RESULT_MISMATCH"


def test_report_refuses_poisoned_result_and_verify_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = Plan.load(ROOT / "benchmark/plans/buckets/noaa-ghcn-pds.yaml").cases[0]
    destination = tmp_path / "job"
    leaf = destination / "attempt"
    leaf.mkdir(parents=True)
    con = campaign.open_db(str(tmp_path / "campaign.db"))
    campaign.record_intent(
        con,
        base_job_id="c-job",
        submission=1,
        job_id="c-job",
        campaign_id="2026-08-16-candidate",
        project="p",
        location="l",
        case=case,
        rep=1,
        bucket="bucket",
        region="region",
        image_uri="registry/derived@sha256:" + "a" * 64,
        image_set_sha256="b" * 64,
        destination=str(destination),
        job_dict=job_document(),
    )
    campaign.update_submission_state(con, "c-job", "SUCCEEDED")
    row = campaign.latest_submissions(con)[0]
    result: dict[str, object] = {
        "campaign_id": "2026-08-16-candidate",
        "job_id": "c-job",
        "case_id": case.case_id,
        "case_fingerprint": case.fingerprint,
        "image": "registry/derived@sha256:" + "a" * 64,
        "image_set_sha256": "b" * 64,
        "tool": case.tool,
        "mode": case.mode,
        "bucket": "bucket",
        "region": "region",
        "prefix": "",
        "run_ordinal": 1,
        "submission_number": 1,
        "exit_code": 0,
        "row_count": 10,
        "wall_seconds": 1.5,
        "max_rss_kb": 2048,
        "row_count_error": None,
        "timed_out": False,
        "execution": {
            "elapsed_ns": 1_500_000_000,
            "max_rss_kb": 2048,
            "timed_out": False,
            "process_group_empty": True,
            "descendants_empty": True,
            "process_tree_clean": True,
            "subreaper_enabled": True,
            "cgroup": {"oom_delta": 0, "oom_kill_delta": 0},
        },
        "tool_recipe_sha256": "7" * 64,
        "tool_build_inputs_sha256": "6" * 64,
        "toolbox_manifest_sha256": "9" * 64,
        "toolbox_recipe_sha256": "8" * 64,
        "tool_version": "1",
        "tool_build_sha256": "d" * 64,
        "adapter_bundle_sha256": "e" * 64,
        "harness_revision": "f" * 40,
        "subject_workdir": "/aws",
        "applied_subject_workdir": "/aws",
        "declared_resources": {
            "machine_type": "n4-standard-2",
            "vcpus": 2,
            "memory_gb": 8,
            "container_memory_gb": None,
        },
    }

    poisoned = {**result, "image": "registry/poison@sha256:" + "c" * 64}
    (leaf / "result.json").write_text(json.dumps(poisoned))
    rendered = report.row_for(row)
    assert rendered["evidence_state"] == "RESULT_MISMATCH"
    assert rendered["wall_seconds"] == "-"

    result_raw = json.dumps(result).encode()
    (leaf / "result.json").write_bytes(result_raw)
    verification: dict[str, object] = {
        "tool": case.tool,
        "mode": case.mode,
        "actual_leaf": str(leaf) + "/",
        "actual_result_sha256": "0" * 64,
        "verdict": "PASS",
    }
    (leaf / "verify.json").write_text(json.dumps(verification))
    rendered = report.row_for(row)
    assert rendered["evidence_state"] == "VERIFY_MISMATCH"
    assert rendered["verdict"] == "-"
    assert "0 verified timing" in report.summary_line([rendered])

    verification["actual_result_sha256"] = hashlib.sha256(result_raw).hexdigest()
    reference_leaf = tmp_path / "reference"
    reference_leaf.mkdir()
    reference_result = json.dumps(
        {
            "tool": "aws-cli",
            "mode": "s3api-v2-text",
            "bucket": "bucket",
            "region": "region",
            "prefix": "",
        }
    ).encode()
    (reference_leaf / "result.json").write_bytes(reference_result)
    verification.update(
        {
            "reference_tool": "aws-cli",
            "reference_mode": "s3api-v2-text",
            "reference_leaf": str(reference_leaf) + "/",
            "reference_result_sha256": hashlib.sha256(reference_result).hexdigest(),
            "actual_tsv_sha256": "d" * 64,
            "reference_tsv_sha256": "e" * 64,
            "diff": {
                "missing": [],
                "extra": [],
                "duplicates": [],
                "reference_duplicates": [],
                "mismatches": [],
            },
        }
    )
    (leaf / "verify.json").write_text(json.dumps(verification))
    monkeypatch.setattr(report, "recompute_verification", lambda *_args, **_kwargs: True)
    rendered = report.row_for(row)
    assert rendered["evidence_state"] == "VERIFIED"
    assert rendered["verdict"] == "PASS"
    assert rendered["machine_type"] == "n4-standard-2"
    assert rendered["max_rss_kb"] == 2048
    assert "1 verified timing" in report.summary_line([rendered])

    (reference_leaf / "result.json").write_bytes(b"changed")
    assert report.row_for(row)["evidence_state"] == "VERIFY_MISMATCH"

    malformed = {**result, "max_rss_kb": -1}
    (leaf / "result.json").write_text(json.dumps(malformed))
    assert report.row_for(row)["evidence_state"] == "RESULT_MISMATCH"


def test_report_finality_requires_terminal_provider_states() -> None:
    accepted: list[dict[str, object]] = [
        {"job_state": "ACCEPTED_FAILED", "evidence_state": "UNAVAILABLE"}
    ]
    assert report.report_exit_code(accepted) == 0
    assert report.report_exit_code([{"job_state": "FAILED"}]) == 1
    assert report.report_exit_code([{**accepted[0], "job_state": "RUNNING"}]) == 1
    assert (
        report.report_exit_code([{"job_state": "SUCCEEDED", "evidence_state": "INCOMPLETE_LEAF"}])
        == 1
    )
    assert (
        report.report_exit_code(
            [{"job_state": "SUCCEEDED", "evidence_state": "VERIFY_UNAVAILABLE"}],
            all_job_states=["FAILED", "SUCCEEDED"],
        )
        == 0
    )
    assert (
        report.report_exit_code(
            [{"job_state": "SUCCEEDED", "evidence_state": "VERIFIED"}],
            all_job_states=["RUNNING", "SUCCEEDED"],
        )
        == 1
    )
    assert report.report_exit_code([]) == 1


def test_report_recomputes_verification_instead_of_trusting_claimed_diff(tmp_path: Path) -> None:
    adapter_root = tmp_path / "adapters"
    normalizer = (
        "from pathlib import Path\n"
        "import sys\n"
        "p=Path(sys.argv[sys.argv.index('--input')+1])\n"
        "sys.stdout.buffer.write(p.read_bytes())\n"
    )
    for tool in ("actual", "reference"):
        adapter = adapter_root / tool / "adapter"
        adapter.mkdir(parents=True)
        (adapter / "normalize.py").write_text(normalizer)

    destinations = {}
    for tool in ("actual", "reference"):
        destination = tmp_path / f"{tool}-job"
        leaf = destination / "attempt"
        leaf.mkdir(parents=True)
        stdout = leaf / "stdout.log.gz"
        stderr = leaf / "stderr.log.gz"
        with gzip.open(stdout, "wb") as stream:
            stream.write(b"key\t1\tetag\t2026-01-01T00:00:00Z\tSTANDARD\n")
        with gzip.open(stderr, "wb") as stream:
            stream.write(b"")
        result = {
            "tool": tool,
            "mode": "mode",
            "bucket": "bucket",
            "region": "region",
            "prefix": "",
            "campaign_id": "2026-08-16-candidate",
            "job_id": f"{tool}-job",
            "case_id": tool,
            "case_fingerprint": tool[0] * 64,
            "image": f"registry/{tool}@sha256:" + tool[0] * 64,
            "image_set_sha256": "f" * 64,
            "exit_code": 0,
            "timed_out": False,
            "execution": {
                "timed_out": False,
                "subreaper_enabled": True,
                "process_tree_clean": True,
                "process_group_empty": True,
                "descendants_empty": True,
                "cgroup": {"oom_kill_delta": 0},
            },
            "stdout_gz": stdout.name,
            "stdout_gz_sha256": sha256_of(stdout),
            "stderr_gz": stderr.name,
            "stderr_gz_sha256": sha256_of(stderr),
            "native_manifest": {},
        }
        (leaf / "result.json").write_text(json.dumps(result))
        destinations[tool] = (destination, leaf, result)

    code, verification = verify.verify_leaves(
        tool="actual",
        bucket="bucket",
        prefix="",
        mode="mode",
        actual_destination=str(destinations["actual"][0]),
        reference_destination=str(destinations["reference"][0]),
        adapter_root=str(adapter_root),
        write_record=False,
    )
    assert code == 0
    actual_leaf, actual_result = destinations["actual"][1:]
    assert report.recompute_verification(
        verification, actual_result, str(actual_leaf) + "/", str(adapter_root)
    )
    forged = {**verification, "actual_tsv_sha256": "0" * 64}
    assert not report.recompute_verification(
        forged, actual_result, str(actual_leaf) + "/", str(adapter_root)
    )
