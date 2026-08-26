from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb

from benchmark import replay, replay_fixture


def config() -> replay.ReplayConfig:
    return replay.parse_document(
        {
            "backend": {
                "server_image_uri": "registry/replay@sha256:" + "a" * 64,
                "fixture_sha256": "b" * 64,
                "serving_mode": "sorted",
                "latency_model": "none",
            },
            "allocation": {
                "subject_vcpus": 2,
                "replay_vcpus": 2,
                "replay_memory_gb": 4,
                "replay_parquet_connections": 4,
                "replay_max_concurrent_requests": 32,
                "replay_heap_percent": 75,
                "replay_prefetch_max_windows": 96,
                "replay_prefetch": False,
            },
            "capacity_status": "calibrated",
        }
    )


def metrics(requests: int, errors: int = 0) -> dict[str, object]:
    return {
        "metrics": {
            "meters": [
                {
                    "name": replay.REQUEST_COUNTER,
                    "type": "counter",
                    "tags": {},
                    "count": requests,
                },
                {
                    "name": replay.ERROR_COUNTER,
                    "type": "counter",
                    "tags": {},
                    "count": errors,
                },
            ]
        }
    }


def evidence(*, after_requests: int = 1, after_errors: int = 0) -> dict[str, object]:
    return {
        "readiness": {"state": "ready"},
        "before": metrics(0),
        "samples": [metrics(1)],
        "resource_samples": [{"server_cpuset": "0-1", "subject_cpuset": "2-3"}],
        "after": metrics(after_requests, after_errors),
        "errors": [],
    }


def test_replay_evidence_refuses_inactive_or_erroring_server() -> None:
    assert "did not increase" in " ".join(
        replay.evidence_errors(config(), evidence(after_requests=0), purpose="measurement")
    )
    assert "error counter changed" in " ".join(
        replay.evidence_errors(config(), evidence(after_errors=1), purpose="measurement")
    )


def test_replay_evidence_accepts_canonical_single_cpu_sets() -> None:
    document = config().as_dict()
    allocation = document["allocation"]
    assert isinstance(allocation, dict)
    allocation["subject_vcpus"] = 1
    allocation["replay_vcpus"] = 1
    observed = evidence()
    observed["resource_samples"] = [{"server_cpuset": "0", "subject_cpuset": "1"}]
    assert (
        replay.evidence_errors(replay.parse_document(document), observed, purpose="measurement")
        == ()
    )


def test_fixture_manifest_binds_names_sizes_and_bytes(tmp_path: Path) -> None:
    (tmp_path / "b.parquet").write_bytes(b"second")
    (tmp_path / "a.parquet").write_bytes(b"first")
    rows = (
        f"a.parquet\t5\t{hashlib.sha256(b'first').hexdigest()}\n",
        f"b.parquet\t6\t{hashlib.sha256(b'second').hexdigest()}\n",
    )
    digest, actual_rows = replay_fixture.fixture_manifest(tmp_path)
    assert actual_rows == rows
    assert digest == hashlib.sha256("".join(rows).encode()).hexdigest()


def test_committed_runner_fixture_is_small_paginated_and_digest_bound() -> None:
    fixture = Path(__file__).parents[2] / "benchmark/fixtures/replay-canary"
    digest, rows = replay_fixture.fixture_manifest(fixture)
    assert digest == "824ec0542f8fcab0102a2b8e9737b1a60acdad84a4c42e5629bd2d2eed38414c"
    assert len(rows) == 1

    parquet = fixture / "part-00000.parquet"
    with duckdb.connect() as connection:
        count, first, last = connection.execute(
            "SELECT count(*), min(decode(key)), max(decode(key)) FROM read_parquet(?)",
            [str(parquet)],
        ).fetchone()
    assert (count, first, last) == (
        2048,
        "group-00/object-000000.dat",
        "group-15/object-002047.dat",
    )
