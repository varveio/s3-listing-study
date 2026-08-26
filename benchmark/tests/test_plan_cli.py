"""The resolved-plan review surface, including replay's derived allocation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark import plan_cli

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    ("selector", "expected_replay"),
    (
        (("--path", str(ROOT / "benchmark/plans/canaries/runner-replay-canary.yaml")), True),
        (("--bucket", "noaa-ghcn-pds"), False),
    ),
)
def test_resolve_plan_exposes_resolved_replay_contract(
    selector: tuple[str, str], expected_replay: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    assert plan_cli.resolve_plan_main([*selector, "--skip-roster", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    rows = payload["cases"]
    assert rows

    if not expected_replay:
        assert all(row["replay"] is None and row["replay_allocation"] is None for row in rows)
        assert plan_cli.resolve_plan_main([*selector, "--skip-roster"]) == 0
        assert "S3" in capsys.readouterr().out
        return

    assert len(rows) == 3
    row = rows[0]
    replay = row["replay"]
    allocation = row["replay_allocation"]
    assert replay == {
        "backend": {
            "server_image_uri": (
                "us-east1-docker.pkg.dev/varve-oss/s3-listing-study/replay-canary@sha256:"
                "015c272bb6fb2c1f719644d5d8122297814d502dc3820abd94953bb00fafbc3a"
            ),
            "fixture_sha256": "6e1c2d47a92bbd1062469fb323f95b1d0f127b4e601b93f0d94576ab16d7c8b4",
            "serving_mode": "duckdb",
            "latency_model": "none",
        },
        "allocation": {
            "subject_vcpus": 1,
            "replay_vcpus": 2,
            "replay_memory_gb": 2,
            "replay_parquet_connections": 4,
            "replay_max_concurrent_requests": 16,
            "replay_heap_percent": 75,
            "replay_prefetch": False,
            "replay_prefetch_max_windows": 96,
        },
        "capacity_status": "uncalibrated",
    }
    assert allocation == {
        "server_cpuset": "0-1",
        "subject_cpuset": "2",
        "host_vcpus": 1,
        "host_memory_headroom_gb": 4,
    }
    assert [
        (
            candidate["replay"]["allocation"]["replay_vcpus"],
            candidate["replay"]["allocation"]["subject_vcpus"],
        )
        for candidate in rows
    ] == [(2, 1)] * 3
    assert all(candidate["replay_allocation"]["host_vcpus"] == 1 for candidate in rows)
    assert plan_cli.resolve_plan_main([*selector, "--skip-roster"]) == 0
    table = capsys.readouterr().out
    assert "UNCALIBRATED" in table
    assert "latency=none" in table
    assert "3 cases, 3 attempts" in table
    assert "host=1vCPU/4GiB" in table
