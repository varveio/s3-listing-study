from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "simple"))
import verify  # type: ignore[import-not-found]


def test_reference_duplicates_can_never_pass(tmp_path: Path) -> None:
    actual = tmp_path / "actual.tsv"
    reference = tmp_path / "reference.tsv"
    actual.write_text("key\t1\te\t2026-01-01T00:00:00Z\tSTANDARD\n")
    reference.write_text(
        "key\t1\te\t2026-01-01T00:00:00Z\tSTANDARD\nkey\t1\te\t2026-01-01T00:00:00Z\tSTANDARD\n"
    )
    con = duckdb.connect()
    try:
        verify.load_tables(con, reference, actual)
        diff = verify.compute_diff(con)
    finally:
        con.close()
    assert diff["reference_duplicates"] == ["key"]
    assert verify.verdict_for(diff) == "FAIL"


def test_two_empty_listings_pass(tmp_path: Path) -> None:
    actual = tmp_path / "actual.tsv"
    reference = tmp_path / "reference.tsv"
    actual.write_bytes(b"")
    reference.write_bytes(b"")
    con = duckdb.connect()
    try:
        verify.load_tables(con, reference, actual)
        diff = verify.compute_diff(con)
    finally:
        con.close()
    assert diff == {
        "missing": [],
        "extra": [],
        "duplicates": [],
        "reference_duplicates": [],
        "mismatches": [],
    }
    assert verify.verdict_for(diff) == "PASS"


def test_provenance_binding_checks_every_expected_field() -> None:
    result = {
        "campaign_id": "2026-08-16-one",
        "job_id": "c-one",
        "case_fingerprint": "a" * 64,
        "image": "registry/tool@sha256:" + "b" * 64,
    }
    assert verify.check_binding(result, dict(result)) == []
    expected = dict(result)
    expected["image"] = "registry/tool@sha256:" + "c" * 64
    assert verify.check_binding(result, expected) == [
        f"image: leaf={result['image']!r} expected={expected['image']!r}"
    ]


def test_oom_kill_evidence_refuses_a_zero_exit_subject() -> None:
    result = {
        "exit_code": 0,
        "timed_out": False,
        "execution": {
            "timed_out": False,
            "subreaper_enabled": True,
            "process_tree_clean": True,
            "process_group_empty": True,
            "descendants_empty": True,
            "cgroup": {"oom_kill_delta": 1},
        },
    }
    assert "OOM kill" in (verify.check_failed_subject(result) or "")


def test_dirty_descendant_tree_refuses_a_zero_exit_subject() -> None:
    result = {
        "exit_code": 0,
        "timed_out": False,
        "execution": {
            "timed_out": False,
            "subreaper_enabled": True,
            "process_tree_clean": False,
            "process_group_empty": True,
            "descendants_empty": True,
            "cgroup": {"oom_kill_delta": 0},
        },
    }
    assert "live descendant" in (verify.check_failed_subject(result) or "")


def test_missing_execution_evidence_refuses_a_zero_exit_subject() -> None:
    assert "execution evidence" in (
        verify.check_failed_subject({"exit_code": 0, "timed_out": False}) or ""
    )


def test_negative_oom_delta_refuses_a_zero_exit_subject() -> None:
    result = {
        "exit_code": 0,
        "timed_out": False,
        "execution": {
            "timed_out": False,
            "subreaper_enabled": True,
            "process_tree_clean": True,
            "process_group_empty": True,
            "descendants_empty": True,
            "cgroup": {"oom_kill_delta": -1},
        },
    }
    assert "invalid" in (verify.check_failed_subject(result) or "")


def test_artifact_hashes_bind_stream_and_native_bytes(tmp_path: Path) -> None:
    leaf = tmp_path / "leaf"
    native = leaf / "native/data"
    native.mkdir(parents=True)
    stdout = leaf / "stdout.log.gz"
    stderr = leaf / "stderr.log.gz"
    part = native / "part.parquet"
    stdout.write_bytes(b"stdout")
    stderr.write_bytes(b"stderr")
    part.write_bytes(b"native")
    result: dict[str, object] = {
        "stdout_gz": stdout.name,
        "stdout_gz_sha256": verify.sha256_of(stdout),
        "stderr_gz": stderr.name,
        "stderr_gz_sha256": verify.sha256_of(stderr),
        "native_manifest": {"data/part.parquet": verify.sha256_of(part)},
    }
    verify.validate_captured_artifacts(leaf, result)
    part.write_bytes(b"mutated")
    with pytest.raises(verify.adapters.AdapterError, match="native"):
        verify.validate_captured_artifacts(leaf, result)
