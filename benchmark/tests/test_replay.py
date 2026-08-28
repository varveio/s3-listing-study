from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pytest

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
        "resource_samples": [
            {"box_vcpus": 6, "server_cpuset": "0,3", "subject_cpuset": "1,4"}
        ],
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
    observed["resource_samples"] = [
        {"box_vcpus": 4, "server_cpuset": "0", "subject_cpuset": "1"}
    ]
    assert (
        replay.evidence_errors(replay.parse_document(document), observed, purpose="measurement")
        == ()
    )


@pytest.mark.parametrize(
    ("box_vcpus", "replay_vcpus", "subject_vcpus", "expected_server", "expected_subject"),
    (
        (16, 8, 2, (0, 1, 2, 3, 8, 9, 10, 11), (4, 12)),
        (
            32,
            16,
            8,
            (*range(0, 8), *range(16, 24)),
            (*range(8, 12), *range(24, 28)),
        ),
    ),
)
def test_n4_allocation_keeps_replay_and_subject_on_separate_physical_cores(
    box_vcpus: int,
    replay_vcpus: int,
    subject_vcpus: int,
    expected_server: tuple[int, ...],
    expected_subject: tuple[int, ...],
) -> None:
    document = config().as_dict()
    allocation = document["allocation"]
    assert isinstance(allocation, dict)
    allocation["replay_vcpus"] = replay_vcpus
    allocation["subject_vcpus"] = subject_vcpus

    server, subject = replay.allocation_cpu_sets(
        replay.parse_document(document).allocation, box_vcpus=box_vcpus
    )

    assert server == expected_server
    assert subject == expected_subject
    assert set(server).isdisjoint(subject)


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


def test_staged_fixture_accepts_one_bounded_part_set() -> None:
    document = config().as_dict()
    backend = document["backend"]
    assert isinstance(backend, dict)
    backend["fixture_uri"] = "gs://fixtures/case/part-*.parquet"

    parsed = replay.parse_document(document)

    assert parsed.backend.fixture_uri == "gs://fixtures/case/part-*.parquet"


@pytest.mark.parametrize(
    "uri",
    (
        "gs://fixtures/case/*.parquet",
        "gs://fixtures/*/part-*.parquet",
        "gs://fixtures/case/part-**.parquet",
        "gs://fixtures/case/part-?.parquet",
        "gs://fixtures/case/part-[0-9].parquet",
        "gs://fixtures/case/data-*.parquet",
    ),
)
def test_staged_fixture_refuses_unbounded_patterns(uri: str) -> None:
    document = config().as_dict()
    backend = document["backend"]
    assert isinstance(backend, dict)
    backend["fixture_uri"] = uri
    with pytest.raises(replay.ReplayError, match="bounded"):
        replay.parse_document(document)


def test_generated_runner_fixture_is_small_paginated_and_digest_bound(tmp_path: Path) -> None:
    source = Path(__file__).parents[2] / "benchmark/fixtures/replay-canary"
    assert list(source.glob("*.parquet")) == []
    fixture = tmp_path / "fixture"
    parquet = fixture / "part-00000.parquet"
    replay_fixture.generate_parquet(source / "generate.sql", parquet)
    digest, rows = replay_fixture.fixture_manifest(fixture)
    assert digest == "6e1c2d47a92bbd1062469fb323f95b1d0f127b4e601b93f0d94576ab16d7c8b4"
    assert len(rows) == 1

    with duckdb.connect() as connection:
        row = connection.execute(
            "SELECT count(*), min(decode(key)), max(decode(key)), min(etag), max(etag) "
            "FROM read_parquet(?)",
            [str(parquet)],
        ).fetchone()
    assert row == (
        2048,
        "group-00/object-000000.dat",
        "group-15/object-002047.dat",
        "00000000000000000000000000000000",
        "000000000000000000000000000007ff",
    )
