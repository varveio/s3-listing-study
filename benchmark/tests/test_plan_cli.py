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
            "latency_model": {
                "deadlines_ms": {"worker_page": 107, "pivot_probe": 41, "structure_probe": 49},
                "scale": 1.0,
                "jitter": "none",
            },
        },
        "allocation": {
            "subject_vcpus": 1,
            "replay_vcpus": 2,
            "replay_memory_gb": 4,
            "replay_parquet_connections": 20,
            "replay_max_concurrent_requests": 64,
            "replay_heap_percent": 75,
            "replay_prefetch": False,
        },
        "capacity_status": "uncalibrated",
    }
    assert allocation == {
        "server_cpuset": "0-1",
        "subject_cpuset": "2-2",
        "host_vcpus": 1,
        "host_memory_headroom_gb": 8,
    }
    assert plan_cli.resolve_plan_main(["--bucket", bucket, "--skip-roster"]) == 0
    table = capsys.readouterr().out
    assert "UNCALIBRATED" in table
    assert "host=1vCPU/8GiB" in table
