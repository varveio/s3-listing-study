"""The resolved-plan review surface, including replay's derived allocation."""

from __future__ import annotations

import json

import pytest

from benchmark import plan_cli


@pytest.mark.parametrize(
    ("bucket", "expected_replay"),
    (("idc-open-data", True), ("noaa-ghcn-pds", False)),
)
def test_resolve_plan_exposes_resolved_replay_contract(
    bucket: str, expected_replay: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    assert plan_cli.resolve_plan_main(["--bucket", bucket, "--skip-roster", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    rows = payload["cases"]
    assert rows

    if not expected_replay:
        assert all(row["replay"] is None and row["replay_allocation"] is None for row in rows)
        assert plan_cli.resolve_plan_main(["--bucket", bucket, "--skip-roster"]) == 0
        assert "S3" in capsys.readouterr().out
        return

    assert len(rows) == 6
    row = rows[0]
    replay = row["replay"]
    allocation = row["replay_allocation"]
    assert replay == {
        "backend": {
            "server_image_uri": (
                "us-east1-docker.pkg.dev/varve-oss/s3-listing-study/replay-server@sha256:"
                "78a22c71cb25792a5f28af3e1e43afeab766f5161d939004bed4c8b50e97ca91"
            ),
            "fixture_sha256": "943786a189afa827cb78a74ff0f0cc9f08ae13b5dbd547d3a19f60e0a3de304c",
            "serving_mode": "sorted",
            "latency_model": "none",
        },
        "allocation": {
            "subject_vcpus": 4,
            "replay_vcpus": 10,
            "replay_memory_gb": 8,
            "replay_parquet_connections": 20,
            "replay_max_concurrent_requests": 2048,
            "replay_heap_percent": 75,
            "replay_prefetch": False,
            "replay_prefetch_max_windows": 96,
        },
        "capacity_status": "uncalibrated",
    }
    assert allocation == {
        "server_cpuset": "0-9",
        "subject_cpuset": "10-13",
        "host_vcpus": 2,
        "host_memory_headroom_gb": 20,
    }
    assert [
        (
            candidate["replay"]["allocation"]["replay_vcpus"],
            candidate["replay"]["allocation"]["subject_vcpus"],
        )
        for candidate in rows
    ] == [(10, 4), (9, 5), (8, 6), (7, 7), (6, 8), (4, 10)]
    assert all(candidate["replay_allocation"]["host_vcpus"] == 2 for candidate in rows)
    assert plan_cli.resolve_plan_main(["--bucket", bucket, "--skip-roster"]) == 0
    table = capsys.readouterr().out
    assert "UNCALIBRATED" in table
    assert "latency=none" in table
    assert "6 cases, 6 attempts" in table
    assert "host=2vCPU/20GiB" in table
