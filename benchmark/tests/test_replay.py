from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from benchmark import replay, replay_fixture, replay_runtime


def test_server_rss_binds_process_to_replay_cpu_allocation(tmp_path: Path) -> None:
    process = tmp_path / "123"
    process.mkdir()
    (process / "cmdline").write_bytes(
        b"\0".join((b"/opt/swath-replay/swath-replay", b"serve", b"--metrics-port", b"19192", b""))
    )
    # Two replay threads on a six-vCPU N4 are SMT siblings 0 and 3.
    status = "Cpus_allowed_list:\t0,3\nVmRSS:\t1024 kB\nVmHWM:\t2048 kB\n"
    (process / "status").write_text(status)
    assert replay_runtime.server_memory(config(), 6, tmp_path)["peak_rss_kb"] == 2048
    (process / "status").write_text(status.replace("0,3", "1,4"))
    assert replay_runtime.server_memory(config(), 6, tmp_path)["state"] == "unavailable"
    (process / "status").write_text(status)
    other = tmp_path / "456"
    other.mkdir()
    (other / "status").write_text(status)
    (other / "cmdline").write_bytes((process / "cmdline").read_bytes())
    assert replay_runtime.server_memory(config(), 6, tmp_path)["state"] == "unavailable"


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


def test_go_backend_roundtrip_and_incompatible_controls() -> None:
    legacy = json.loads(config().canonical_json())
    assert "server_implementation" not in legacy["backend"]
    document = json.loads(config().canonical_json())
    document["backend"]["server_implementation"] = "go"
    document["allocation"]["replay_response_buffer_budget_bytes"] = 1073741824
    go = replay.parse_document(document)
    assert replay.parse_document(go.canonical_json()) == go
    assert go.canonical_json() != config().canonical_json()
    for section, field, value in (
        ("backend", "server_implementation", "rust"),
        ("backend", "serving_mode", "duckdb"),
        ("allocation", "replay_delimiter_connections", 16),
        ("allocation", "replay_max_concurrent_requests", 4),
        ("allocation", "replay_response_buffer_budget_bytes", 0),
    ):
        changed = json.loads(go.canonical_json())
        changed[section][field] = value
        with pytest.raises(replay.ReplayError):
            replay.parse_document(changed)
    document["backend"]["server_implementation"] = "java"
    with pytest.raises(replay.ReplayError, match="requires Go"):
        replay.parse_document(document)


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
        "resource_samples": [{"box_vcpus": 6, "server_cpuset": "0,3", "subject_cpuset": "1,4"}],
        "after": metrics(after_requests, after_errors),
        "errors": [],
    }


def timing_config() -> replay.ReplayConfig:
    document = config().as_dict()
    backend = document["backend"]
    assert isinstance(backend, dict)
    backend["latency_model"] = {
        "deadlines_ms": {
            "worker_page": 100,
            "pivot_probe": 50,
            "structure_probe": 50,
        },
        "scale": 1.0,
        "jitter": "none",
    }
    return replay.parse_document(document)


def timing_evidence(
    *, mean_ratio: float, overruns: int, cpu: tuple[float, ...]
) -> dict[str, object]:
    def observation(*, requests: int, sum_ms: float, overrun_count: int) -> dict[str, object]:
        meters: list[dict[str, object]] = []
        for shape in replay.INJECT_SHAPES:
            active = shape == "worker_page"
            meters.append(
                {
                    "name": replay.REQUEST_LATENCY_TIMER,
                    "type": "timer",
                    "tags": {"shape": shape},
                    "count": requests if active else 0,
                    "sum_ms": sum_ms if active else 0.0,
                }
            )
        if overrun_count:
            meters.append(
                {
                    "name": replay.INJECT_OVERRUN_COUNTER,
                    "type": "counter",
                    "tags": {"shape": "worker_page"},
                    "count": overrun_count,
                }
            )
        return {"metrics": {"meters": meters}}

    return {
        "before": observation(requests=0, sum_ms=0, overrun_count=0),
        "after": observation(
            requests=100,
            sum_ms=100 * 100 * mean_ratio,
            overrun_count=overruns,
        ),
        "resource_samples": [{"server_cpuset_utilization": utilization} for utilization in cpu],
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
    observed["resource_samples"] = [{"box_vcpus": 4, "server_cpuset": "0", "subject_cpuset": "1"}]
    assert (
        replay.evidence_errors(replay.parse_document(document), observed, purpose="measurement")
        == ()
    )


@pytest.mark.parametrize(
    ("mean_ratio", "overruns", "cpu", "expected"),
    (
        (1.05, 0, (0.5,) * 5, replay.TIMING_VALID),
        (1.15, 5, (0.5,) * 5, replay.PRESSURE_DEGRADED),
        (1.30, 0, (0.5,) * 5, replay.CAPACITY_FAILED),
        (1.05, 0, (0.91, 0.5, 0.5, 0.5, 0.5), replay.CAPACITY_FAILED),
        (1.05, 0, (0.5,) * 4, replay.INSUFFICIENT_EVIDENCE),
    ),
)
def test_replay_timing_gate_classifies_request_service_time_and_cpu_pressure(
    mean_ratio: float, overruns: int, cpu: tuple[float, ...], expected: str
) -> None:
    assessment = replay.replay_timing_assessment(
        timing_config(), timing_evidence(mean_ratio=mean_ratio, overruns=overruns, cpu=cpu)
    )

    assert assessment["state"] == expected
    shapes = assessment["shapes"]
    assert isinstance(shapes, dict)
    worker = shapes["worker_page"]
    assert isinstance(worker, dict)
    assert worker["requests"] == 100
    assert worker["request_mean_ms"] == pytest.approx(100 * mean_ratio)


def test_replay_timing_gate_does_not_apply_without_latency_injection() -> None:
    assert replay.replay_timing_assessment(config(), evidence())["state"] == replay.NOT_APPLICABLE


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


def test_format_cpuset_is_the_one_formatter_replay_runtime_shares() -> None:
    """The sampler's resource_samples and evidence_errors' cpuset comparison must
    read off one formatter, or a silent divergence would refuse every
    calibrated measurement (`replay_runtime.sample_metrics`,
    `replay._resource_sample_matches_allocation`).
    """
    from benchmark import replay_runtime

    assert replay.format_cpuset({4, 1, 2, 7, 8, 9}) == "1-2,4,7-9"
    assert replay_runtime.format_cpuset is replay.format_cpuset


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


def test_declared_warmup_is_part_of_the_treatment_identity() -> None:
    backend = {
        "server_image_uri": "registry/replay@sha256:" + "a" * 64,
        "fixture_sha256": "b" * 64,
        "serving_mode": "sorted",
        "latency_model": "none",
    }
    cold = replay.parse_backend(backend)
    warm = replay.parse_backend(
        {
            **backend,
            "warmup": {
                "structure_probes": 2000,
                "pivot_probes": 200,
                "worker_pages": 200,
                "in_flight": 64,
            },
        }
    )
    assert cold.warmup is None and "warmup" not in cold.as_dict()
    assert warm.as_dict()["warmup"] == {
        "structure_probes": 2000,
        "pivot_probes": 200,
        "worker_pages": 200,
        "in_flight": 64,
    }
    assert cold.as_dict() != warm.as_dict()
    plan = replay.parse_plan({**backend, "capacity_status": "uncalibrated", "warmup": None})
    assert plan.backend.warmup is None


@pytest.mark.parametrize(
    "warmup",
    [
        {"structure_probes": 1},
        {"structure_probes": 0, "pivot_probes": 0, "worker_pages": 0, "in_flight": 8},
        {"structure_probes": -1, "pivot_probes": 0, "worker_pages": 0, "in_flight": 8},
        {"structure_probes": 1, "pivot_probes": 0, "worker_pages": 0, "in_flight": 0},
        {"structure_probes": True, "pivot_probes": 0, "worker_pages": 0, "in_flight": 8},
    ],
)
def test_malformed_warmup_is_refused(warmup: dict[str, object]) -> None:
    with pytest.raises(replay.ReplayError):
        replay.parse_warmup(warmup)


def test_delimiter_connections_is_optional_and_identity_bearing_only_when_stated() -> None:
    allocation = {
        "subject_vcpus": 2,
        "replay_vcpus": 2,
        "replay_memory_gb": 4,
        "replay_parquet_connections": 4,
        "replay_max_concurrent_requests": 32,
        "replay_heap_percent": 75,
        "replay_prefetch_max_windows": 96,
        "replay_prefetch": False,
    }
    absent = replay.parse_allocation(allocation)
    assert absent.replay_delimiter_connections is None
    assert "replay_delimiter_connections" not in absent.as_dict()

    stated = replay.parse_allocation({**allocation, "replay_delimiter_connections": 128})
    assert stated.replay_delimiter_connections == 128
    assert stated.as_dict()["replay_delimiter_connections"] == 128
    assert stated.as_dict() != absent.as_dict()

    with pytest.raises(replay.ReplayError):
        replay.parse_allocation({**allocation, "replay_delimiter_connections": 0})
    with pytest.raises(replay.ReplayError):
        replay.parse_allocation({**allocation, "replay_reader_width": 8})


SEEKPARQUET_ROOT = "gs://fixtures/aws-public-blockchain/seekparquet-c4a42eb4e7fb/"
REAL_LIST_INDEX_LINE = (
    "swath_replay list_index state=built max_bytes=67108864 workers=4 groups_indexed=371 "
    "groups_skipped=0 groups_failed=0 parts_failed=0 pages=139818 entries=8288 bytes=725664 "
    "budget_exhausted=false seconds=9.719"
)


def go_document(**allocation: object) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(config().canonical_json())
    document["backend"]["server_implementation"] = "go"
    document["allocation"]["replay_max_concurrent_requests"] = 32
    document["allocation"].update(allocation)
    return document


def seekparquet_document(uri: str = SEEKPARQUET_ROOT, **backend: object) -> dict[str, Any]:
    document = go_document()
    document["backend"].update(
        {"fixture_uri": uri, "fixture_sha256": "c" * 64, "fixture_adopted_sha256": "f" * 64}
    )
    document["backend"].update(backend)
    return document


def test_seekparquet_root_is_identified_by_its_manifest_and_links_its_source() -> None:
    root = replay.parse_document(seekparquet_document())
    manifest = replay.parse_document(seekparquet_document(SEEKPARQUET_ROOT + "manifest.json"))
    assert root.backend.fixture_layout == manifest.backend.fixture_layout == "seekparquet"
    assert replay.fixture_root(manifest.backend.fixture_uri or "") == SEEKPARQUET_ROOT
    assert replay.parse_document(root.canonical_json()) == root
    # The adopted Java-era digest is identity: a dataset converted from another
    # release fixture is another treatment even with the same manifest.
    relinked = replay.parse_document(seekparquet_document(fixture_adopted_sha256="e" * 64))
    assert relinked.canonical_json() != root.canonical_json()
    unlinked = seekparquet_document()
    del unlinked["backend"]["fixture_adopted_sha256"]
    assert "fixture_adopted_sha256" not in replay.parse_document(unlinked).canonical_json()


@pytest.mark.parametrize(
    ("backend", "match"),
    (
        ({"fixture_uri": "gs://fixtures/"}, "SeekParquet dataset root"),
        ({"fixture_uri": "gs://fixtures/*/"}, "SeekParquet dataset root"),
        ({"fixture_uri": "gs://fixtures/a/../b/"}, "SeekParquet dataset root"),
        ({"fixture_uri": "gs://fixtures/a/part-*.parquet"}, "requires a SeekParquet"),
        ({"server_implementation": "java"}, "requires Go"),
        ({"fixture_adopted_sha256": "F" * 64}, "sha256"),
    ),
)
def test_seekparquet_fixture_refusals(backend: dict[str, Any], match: str) -> None:
    with pytest.raises(replay.ReplayError, match=match):
        replay.parse_document(seekparquet_document(**backend))


def test_list_index_controls_are_go_only_optional_and_identity_bearing() -> None:
    wave1 = replay.parse_document(go_document())
    assert wave1.list_index_enabled
    assert "replay_list_index" not in wave1.canonical_json()
    stated = replay.parse_document(
        go_document(replay_list_index=True, replay_list_index_max_bytes=67108864)
    )
    assert stated.canonical_json() != wave1.canonical_json()
    assert not replay.parse_document(go_document(replay_list_index=False)).list_index_enabled
    for allocation in (
        {"replay_list_index": 1},
        {"replay_list_index_max_bytes": 0},
        {"replay_list_index": False, "replay_list_index_max_bytes": 1024},
    ):
        with pytest.raises(replay.ReplayError):
            replay.parse_document(go_document(**allocation))
    for field, value in (("replay_list_index", True), ("replay_list_index_max_bytes", 1024)):
        java = json.loads(config().canonical_json())
        java["allocation"][field] = value
        with pytest.raises(replay.ReplayError, match="requires Go"):
            replay.parse_document(java)


def test_list_index_line_parses_the_server_format_and_refuses_ambiguity() -> None:
    parsed = replay.parse_list_index_line("2026-11-10T14:00:00Z " + REAL_LIST_INDEX_LINE + "\n")
    assert parsed is not None
    assert (parsed["state"], parsed["groups_indexed"], parsed["bytes"]) == ("built", 371, 725664)
    assert parsed["budget_exhausted"] is False and parsed["seconds"] == 9.719
    failed = REAL_LIST_INDEX_LINE.replace("built", "failed") + ' error="open: \\"x\\" gone"'
    assert replay.parse_list_index_line(failed) == {
        **replay.parse_list_index_line(failed.replace(' error="open: \\"x\\" gone"', "")),  # type: ignore[dict-item]
        "line": failed,
        "error": 'open: "x" gone',
    }
    assert replay.parse_list_index_line("swath_replay listening port=1") is None
    # The merged server (swath 545a10cb) adds has_index before seconds.
    merged = REAL_LIST_INDEX_LINE.replace(" seconds=", " has_index=true seconds=")
    merged_line = replay.parse_list_index_line(merged)
    assert merged_line is not None and merged_line["has_index"] is True
    with pytest.raises(replay.ReplayError):
        replay.parse_list_index_line(merged.replace("has_index=true", "has_index=1"))
    for bad in (
        REAL_LIST_INDEX_LINE + "\n" + REAL_LIST_INDEX_LINE,
        REAL_LIST_INDEX_LINE.replace("state=built", "state=maybe"),
        REAL_LIST_INDEX_LINE.replace(" pages=139818", ""),
        REAL_LIST_INDEX_LINE + " surprise=1",
        REAL_LIST_INDEX_LINE.replace("seconds=9.719", "seconds=NaN"),
        REAL_LIST_INDEX_LINE.replace("seconds=9.719", "seconds=Inf"),
        REAL_LIST_INDEX_LINE.replace("seconds=9.719", "seconds=-1.000"),
    ):
        with pytest.raises(replay.ReplayError):
            replay.parse_list_index_line(bad)


def list_index_meters(*, skips: int, dense: int, built: bool = True) -> list[dict[str, object]]:
    values = dict.fromkeys((field for field, _name, _tags in replay.LIST_INDEX_BUILD_GAUGES), 0)
    if built:
        values.update(groups_indexed=371, pages=139818, entries=8288, bytes=725664)
    values["max_bytes"] = 67108864
    meters: list[dict[str, object]] = [
        {"name": name, "type": "gauge", "tags": tags, "value": float(values[field])}
        for field, name, tags in replay.LIST_INDEX_BUILD_GAUGES
    ]
    meters.append(
        {
            "name": replay.LIST_INDEX_BUILD_LATENCY_TIMER,
            "type": "timer",
            "tags": {},
            "count": 1 if built else 0,
            "sum_ms": 9719.0 if built else 0.0,
        }
    )
    meters.append(
        {"name": replay.LIST_INDEX_SKIPS_COUNTER, "type": "counter", "tags": {}, "count": skips}
    )
    meters.extend(
        {
            "name": replay.LIST_INDEX_DECLINES_COUNTER,
            "type": "counter",
            "tags": {"reason": reason},
            "count": dense if reason == "dense_page" else 0,
        }
        for reason in replay.LIST_INDEX_DECLINE_REASONS
    )
    return meters


def test_timing_gate_and_list_index_summary_read_one_go_evidence() -> None:
    """The new meters sit beside the timing meters without moving the grade."""
    document = seekparquet_document(
        latency_model=timing_config().backend.as_dict()["latency_model"]
    )
    document["allocation"].update(replay_list_index=True, replay_list_index_max_bytes=67108864)
    go = replay.parse_document(document)
    observed = timing_evidence(mean_ratio=1.05, overruns=0, cpu=(0.5,) * 5)
    for phase, (skips, dense) in (("before", (0, 0)), ("after", (4096, 7))):
        observation = observed[phase]
        assert isinstance(observation, dict)
        observation["metrics"]["meters"].extend(list_index_meters(skips=skips, dense=dense))

    assert replay.replay_timing_assessment(go, observed)["state"] == replay.TIMING_VALID
    summary = replay.list_index_summary(go, observed)
    # A build attempt alone cannot tell built from failed: inconclusive.
    assert summary is not None
    assert _delivered(summary) is None
    assert summary["interval"] == {
        "skips": 4096.0,
        "declines": {"missing_group": 0.0, "dense_page": 7.0, "unsafe_run": 0.0},
    }
    line = replay.parse_list_index_line(REAL_LIST_INDEX_LINE)
    assert line is not None
    assert replay.list_index_line_disagreements(line, summary) == []
    assert replay.list_index_line_disagreements({**line, "entries": 1}, summary)
    assert replay.list_index_line_disagreements({**line, "has_index": True}, summary) == []
    assert replay.list_index_line_disagreements({**line, "has_index": False}, summary)
    replay.attach_list_index_line(summary, line)
    assert _delivered(summary) is True

    # An old Go image publishes none of these meters; a requested index that
    # never built over a SeekParquet dataset is not the treatment asked for.
    assert replay.list_index_summary(go, timing_evidence(mean_ratio=1.0, overruns=0, cpu=()))[
        "reasons"  # type: ignore[index]
    ] == ["list-index build meters are absent; the server predates the list index"]
    unbuilt = timing_evidence(mean_ratio=1.0, overruns=0, cpu=())
    for phase in ("before", "after"):
        observation = unbuilt[phase]
        assert isinstance(observation, dict)
        observation["metrics"]["meters"].extend(list_index_meters(skips=0, dense=0, built=False))
    assert replay.list_index_summary(go, unbuilt)["delivered"] is False  # type: ignore[index]
    assert replay.list_index_summary(timing_config(), observed) is None


def test_seekparquet_identity_recomputes_the_release_fixture_digest(tmp_path: Path) -> None:
    """A footer-only conversion keeps a link to the parts it was adopted from."""
    original = tmp_path / "current"
    dataset = tmp_path / "seekparquet"
    original.mkdir()
    (dataset / "data").mkdir(parents=True)
    parts = []
    adopted = []
    for index in range(2):
        name = f"part-{index:05d}.parquet"
        (original / name).write_bytes(b"java-era footer %d" % index)
        (dataset / "data" / name).write_bytes(b"seekparquet footer %d" % index)
        source = (original / name).read_bytes()
        converted = (dataset / "data" / name).read_bytes()
        adopted.append(
            {
                "input": name,
                "input_bytes": len(source),
                "input_sha256": hashlib.sha256(source).hexdigest(),
            }
        )
        parts.append(
            {
                "path": f"data/{name}",
                "bytes": len(converted),
                "sha256": hashlib.sha256(converted).hexdigest(),
            }
        )
    manifest = {
        "format": "seekparquet",
        "parts": parts,
        "extensions": {"swath-replay": {"adopted_from": {"parts": adopted}}},
    }
    raw = json.dumps(manifest).encode()
    (dataset / "manifest.json").write_bytes(raw)

    digest, adopted_sha256 = replay_fixture.seekparquet_identity(dataset)

    assert digest == hashlib.sha256(raw).hexdigest()
    assert adopted_sha256 == replay_fixture.fixture_manifest(original)[0]
    (dataset / "data" / "part-00001.parquet").write_bytes(b"tampered")
    with pytest.raises(replay.ReplayError, match="does not match"):
        replay_fixture.seekparquet_identity(dataset)


def _go_list_index(
    *, enabled: bool, built: bool, timer: bool = True, entries: int = 8288
) -> tuple[replay.ReplayConfig, dict[str, object]]:
    allocation: dict[str, object] = {"replay_list_index": enabled}
    document = seekparquet_document()
    document["allocation"].update(allocation)
    go = replay.parse_document(document)
    meters = list_index_meters(skips=0, dense=0, built=built)
    for meter in meters:
        if meter["name"] == "swath.replay.delimiter.index.entries" and built:
            meter["value"] = float(entries)
        if meter["name"] == "swath.replay.delimiter.index.max_bytes" and not enabled:
            meter["value"] = 0.0
    if not timer:
        meters = [m for m in meters if m["name"] != replay.LIST_INDEX_BUILD_LATENCY_TIMER]
    observation = {"metrics": {"meters": meters}}
    return go, {"before": observation, "after": observation, "errors": []}


def _delivered(summary: dict[str, object]) -> object:
    return summary["delivered"]


def _summary(go: replay.ReplayConfig, observed: dict[str, object]) -> dict[str, object]:
    summary = replay.list_index_summary(go, observed)
    assert summary is not None
    return summary


def test_a_failed_list_index_build_is_never_delivered() -> None:
    go, observed = _go_list_index(enabled=True, built=True)
    summary = _summary(go, observed)
    assert _delivered(summary) is None
    failed = REAL_LIST_INDEX_LINE.replace("state=built", "state=failed") + ' error="build failed"'
    replay.attach_list_index_line(summary, replay.parse_list_index_line(failed))
    assert _delivered(summary) is False
    assert summary["server_line_disagreements"] == []

    # A flat fixture builds an empty index; a built line still confirms it.
    flat, flat_observed = _go_list_index(enabled=True, built=True, entries=0)
    flat_summary = _summary(flat, flat_observed)
    replay.attach_list_index_line(
        flat_summary, replay.parse_list_index_line(REAL_LIST_INDEX_LINE.replace("8288", "0"))
    )
    assert _delivered(flat_summary) is True


@pytest.mark.parametrize(
    ("seconds", "agrees"), (("9.720", True), ("9.719", False), ("999.000", False))
)
def test_list_index_line_seconds_are_compared_at_log_precision(seconds: str, agrees: bool) -> None:
    go, observed = _go_list_index(enabled=True, built=True)
    summary = _summary(go, observed)  # meters: 9.719 s
    build = summary["build"]
    assert isinstance(build, dict)
    build["seconds"] = 9.7196
    line = replay.parse_list_index_line(
        REAL_LIST_INDEX_LINE.replace("seconds=9.719", f"seconds={seconds}")
    )
    assert line is not None
    disagreements = replay.list_index_line_disagreements(line, summary)
    assert (disagreements == []) is agrees
    replay.attach_list_index_line(summary, line)
    assert (_delivered(summary) is True) is agrees


def test_index_off_delivery_needs_an_observed_zero_build_count() -> None:
    go, observed = _go_list_index(enabled=False, built=False)
    assert _summary(go, observed)["delivered"] is True
    missing, missing_observed = _go_list_index(enabled=False, built=False, timer=False)
    summary = _summary(missing, missing_observed)
    assert _delivered(summary) is None
    assert summary["build"]["builds_recorded"] is None  # type: ignore[index]
    off_line = REAL_LIST_INDEX_LINE.replace("state=built", "state=off")
    for field in ("groups_indexed=371", "pages=139818", "entries=8288", "bytes=725664"):
        off_line = off_line.replace(field, field.split("=")[0] + "=0")
    off_line = off_line.replace("max_bytes=67108864", "max_bytes=0")
    replay.attach_list_index_line(summary, replay.parse_list_index_line(off_line))
    assert _delivered(summary) is None
