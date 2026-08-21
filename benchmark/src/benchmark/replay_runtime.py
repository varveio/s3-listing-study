"""Raw worker-side replay readiness, metric, and host-resource observations."""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from benchmark import gcs
from benchmark.replay import ReplayConfig

REPLAY_ENDPOINT_URL = "http://127.0.0.1:19090"
REPLAY_METRICS_URL = "http://127.0.0.1:19192"
REPLAY_READINESS_TIMEOUT_S = 600
REPLAY_HTTP_TIMEOUT_S = 5.0
REPLAY_READINESS_POLL_S = 1.0
REPLAY_SAMPLE_INTERVAL_S = 10.0
REPLAY_HEARTBEAT_INTERVAL_S = 60.0


def evidence() -> dict[str, object]:
    return {
        "readiness": None,
        "before": None,
        "samples": [],
        "resource_samples": [],
        "after": None,
        "errors": [],
    }


def _http_get(url: str, *, expect_json: bool) -> dict[str, object] | None:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=REPLAY_HTTP_TIMEOUT_S) as response:
        if response.status != 200:
            raise OSError(f"HTTP {response.status}")
        if not expect_json:
            response.read()
            return None
        document = json.load(response)
    if not isinstance(document, dict):
        raise ValueError("metrics response is not a JSON object")
    return document


def wait_for_replay() -> dict[str, object]:
    """Wait for the sidecar before the subject clock starts."""
    started = time.monotonic()
    attempts = 0
    last_error: str | None = None
    while time.monotonic() - started < REPLAY_READINESS_TIMEOUT_S:
        attempts += 1
        try:
            _http_get(f"{REPLAY_METRICS_URL}/healthz", expect_json=False)
            return {
                "state": "ready",
                "wait_ms": round((time.monotonic() - started) * 1000),
                "attempts": attempts,
                "last_error": last_error,
            }
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = str(exc)
        remaining = REPLAY_READINESS_TIMEOUT_S - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(min(REPLAY_READINESS_POLL_S, remaining))
    return {
        "state": "failed",
        "wait_ms": round((time.monotonic() - started) * 1000),
        "attempts": attempts,
        "last_error": last_error,
    }


def scrape_metrics(
    evidence: dict[str, object], phase: str, *, elapsed_s: float | None = None
) -> dict[str, object] | None:
    try:
        metrics = _http_get(f"{REPLAY_METRICS_URL}/metrics", expect_json=True)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        errors = evidence["errors"]
        assert isinstance(errors, list)
        errors.append({"phase": phase, "error": str(exc)})
        return None
    observation: dict[str, object] = {
        "observed_at": datetime.now(UTC).isoformat(),
        "metrics": metrics,
    }
    if elapsed_s is not None:
        observation["elapsed_s"] = round(elapsed_s, 3)
    return observation


def _cpuset_jiffies(cpus: set[int], proc_stat: Path = Path("/proc/stat")) -> tuple[int, int]:
    busy = total = 0
    found: set[int] = set()
    for line in proc_stat.read_text().splitlines():
        name, separator, rest = line.partition(" ")
        if not separator or not name.startswith("cpu") or name == "cpu":
            continue
        try:
            index = int(name.removeprefix("cpu"))
        except ValueError:
            continue
        if index not in cpus:
            continue
        fields = [int(value) for value in rest.split()]
        if len(fields) < 4:
            raise ValueError(f"{proc_stat} has a short {name} row")
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        total += sum(fields)
        busy += sum(fields) - idle
        found.add(index)
    if found != cpus:
        raise ValueError(f"{proc_stat} has no rows for CPU(s) {sorted(cpus - found)}")
    return busy, total


def _host_memory_and_load(proc_root: Path = Path("/proc")) -> tuple[int, float]:
    available_kb = next(
        (
            int(line.split()[1])
            for line in (proc_root / "meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")
        ),
        None,
    )
    if available_kb is None:
        raise ValueError(f"{proc_root / 'meminfo'} has no MemAvailable row")
    load1 = float((proc_root / "loadavg").read_text().split()[0])
    if not math.isfinite(load1) or load1 < 0:
        raise ValueError(f"{proc_root / 'loadavg'} has an invalid one-minute load")
    return available_kb, load1


def _resource_sample(
    *,
    server_cpus: set[int],
    subject_cpus: set[int],
    previous_server: tuple[int, int],
    previous_subject: tuple[int, int],
    interval_s: float,
    elapsed_s: float,
) -> tuple[dict[str, object], tuple[int, int], tuple[int, int]]:
    server, subject = _cpuset_jiffies(server_cpus), _cpuset_jiffies(subject_cpus)
    available_kb, load1 = _host_memory_and_load()

    def utilization(now: tuple[int, int], before: tuple[int, int]) -> float:
        total_delta, busy_delta = now[1] - before[1], now[0] - before[0]
        if total_delta <= 0 or busy_delta < 0:
            raise ValueError("host CPU counters did not advance monotonically")
        return busy_delta / total_delta

    server_util, subject_util = (
        utilization(server, previous_server),
        utilization(subject, previous_subject),
    )
    return (
        {
            "observed_at": datetime.now(UTC).isoformat(),
            "elapsed_s": round(elapsed_s, 3),
            "interval_s": round(interval_s, 3),
            "server_cpuset": f"0-{len(server_cpus) - 1}",
            "subject_cpuset": f"{len(server_cpus)}-{len(server_cpus) + len(subject_cpus) - 1}",
            "server_cpuset_utilization": round(server_util, 6),
            "server_cores_used": round(server_util * len(server_cpus), 3),
            "subject_cpuset_utilization": round(subject_util, 6),
            "subject_cores_used": round(subject_util * len(subject_cpus), 3),
            "host_mem_available_kb": available_kb,
            "host_load1": load1,
        },
        server,
        subject,
    )


def sample_metrics(
    evidence: dict[str, object],
    stop: threading.Event,
    replay: ReplayConfig,
    heartbeat_destination: str | None = None,
) -> None:
    """Poll outside the subject clock; record raw observations and no verdict."""
    started = previous_at = time.monotonic()
    allocation = replay.allocation
    server_cpus = set(range(allocation.replay_vcpus))
    subject_cpus = set(
        range(allocation.replay_vcpus, allocation.replay_vcpus + allocation.subject_vcpus)
    )
    try:
        previous_server, previous_subject = (
            _cpuset_jiffies(server_cpus),
            _cpuset_jiffies(subject_cpus),
        )
    except (OSError, ValueError) as exc:
        errors = evidence["errors"]
        assert isinstance(errors, list)
        errors.append({"phase": "resource-sample", "error": str(exc)})
        return
    heartbeat_sequence = 0
    next_heartbeat_s = REPLAY_HEARTBEAT_INTERVAL_S
    while not stop.wait(REPLAY_SAMPLE_INTERVAL_S):
        observed = time.monotonic()
        try:
            resource, previous_server, previous_subject = _resource_sample(
                server_cpus=server_cpus,
                subject_cpus=subject_cpus,
                previous_server=previous_server,
                previous_subject=previous_subject,
                interval_s=observed - previous_at,
                elapsed_s=observed - started,
            )
        except (OSError, ValueError) as exc:
            errors = evidence["errors"]
            assert isinstance(errors, list)
            errors.append({"phase": "resource-sample", "error": str(exc)})
            return
        resource_samples = evidence["resource_samples"]
        assert isinstance(resource_samples, list)
        resource_samples.append(resource)
        previous_at = observed
        observation = scrape_metrics(evidence, "sample", elapsed_s=observed - started)
        if observation is not None:
            samples = evidence["samples"]
            assert isinstance(samples, list)
            samples.append(observation)
        elapsed_s = observed - started
        if heartbeat_destination is not None and elapsed_s >= next_heartbeat_s:
            heartbeat_sequence += 1
            document = {
                "schema_version": 1,
                "sequence": heartbeat_sequence,
                "observed_at": datetime.now(UTC).isoformat(),
                "elapsed_s": round(elapsed_s, 3),
                "resource_sample": resource,
                "replay_sample": observation,
            }
            uri = (
                heartbeat_destination.rstrip("/")
                + f"/live/replay-{heartbeat_sequence:06d}.json"
            )
            try:
                gcs.upload_bytes(
                    (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(),
                    uri,
                    content_type="application/json",
                    create_only=True,
                )
            except Exception as exc:
                errors = evidence["errors"]
                assert isinstance(errors, list)
                errors.append({"phase": "heartbeat-upload", "uri": uri, "error": str(exc)})
            next_heartbeat_s += REPLAY_HEARTBEAT_INTERVAL_S
