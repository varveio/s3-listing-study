"""Replay-only evidence and immutable-manifest verification primitives.

The generic verifier owns the campaign roster, subject normalization, and the
cross-subject comparison used for real S3.  Replay has one extra source of
truth: the immutable manifest declared in the resolved replay document.  This
module owns that binding and the server-observation protocol, so those rules do
not leak into the real-S3 path.
"""

from __future__ import annotations

import gzip
import math
import shutil
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import IO, cast
from urllib.parse import unquote, urlparse

from benchmark import gcs
from benchmark.contract import sha256_of
from benchmark.replay import ReplayConfig
from benchmark.runtime.contract import ContractViolation, read_records

REQUEST_COUNTER = "swath.replay.http.requests"
ERROR_COUNTER = "swath.replay.http.errors"


class ManifestError(Exception):
    """A replay manifest cannot be bound, decompressed, or parsed safely."""


def replay_manifest_identity(config: ReplayConfig) -> tuple[str, str, str]:
    """Return the fixture/manifest binding from a resolved replay document."""
    backend = config.backend
    if backend.reference_manifest_uri is None or backend.reference_manifest_sha256 is None:
        raise ManifestError("replay configuration has no reference manifest binding")
    return (
        backend.fixture_sha256,
        backend.reference_manifest_uri,
        backend.reference_manifest_sha256,
    )


def _local_uri_path(uri: str) -> Path:
    if uri.startswith("file://"):
        parsed = urlparse(uri)
        if parsed.netloc not in ("", "localhost"):
            raise ManifestError(f"unsupported local manifest URI authority: {uri}")
        return Path(unquote(parsed.path))
    return Path(uri)


def stage_replay_manifest(uri: str, expected_sha256: str, work_dir: Path) -> Path:
    """Bind compressed artifact bytes, then validate and decompress contract-v2."""
    work_dir.mkdir(parents=True, exist_ok=True)
    compressed = work_dir / "reference-manifest.tsv.gz"
    if uri.startswith("gs://"):
        bucket_name, object_name = gcs.parse_gs_uri(uri)
        gcs.client().bucket(bucket_name).blob(object_name).download_to_filename(str(compressed))
    else:
        source = _local_uri_path(uri)
        if not source.is_file():
            raise ManifestError(f"reference manifest does not exist: {uri}")
        shutil.copyfile(source, compressed)
    actual_sha256 = sha256_of(compressed)
    if actual_sha256 != expected_sha256:
        raise ManifestError(
            f"reference manifest sha256 mismatch: expected {expected_sha256}, found {actual_sha256}"
        )

    plain = work_dir / "reference-manifest.tsv"
    previous: bytes | None = None
    rows = 0
    try:
        with gzip.open(compressed, "rb") as packed, plain.open("xb") as output:
            for record in read_records(cast(IO[bytes], packed)):
                if previous is not None and record.key <= previous:
                    kind = "duplicate" if record.key == previous else "out-of-order"
                    raise ManifestError(f"reference manifest has {kind} key: {record.key!r}")
                output.write(record.to_line() + b"\n")
                previous = record.key
                rows += 1
    except (gzip.BadGzipFile, EOFError, OSError, ContractViolation) as exc:
        raise ManifestError(f"reference manifest is not valid contract-v2 gzip: {exc}") from exc
    if rows == 0:
        raise ManifestError("reference manifest contains no OBJECT rows")
    return plain


def _observation(
    value: object, *, phase: str, with_elapsed: bool
) -> tuple[datetime, tuple[str, ...]]:
    expected = {"observed_at", "metrics"} | ({"elapsed_s"} if with_elapsed else set())
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"replay {phase} observation has invalid fields")
    observed_at = value["observed_at"]
    metrics = value["metrics"]
    if not isinstance(observed_at, str) or not isinstance(metrics, Mapping):
        raise ValueError(f"replay {phase} observation is malformed")
    try:
        instant = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"replay {phase} observed_at is not ISO-8601") from None
    if instant.tzinfo is None:
        raise ValueError(f"replay {phase} observed_at has no timezone")
    if with_elapsed:
        elapsed = value["elapsed_s"]
        if isinstance(elapsed, bool) or not isinstance(elapsed, int | float) or elapsed < 0:
            raise ValueError(f"replay {phase} elapsed_s is invalid")
    meters = metrics.get("meters")
    if not isinstance(meters, list):
        raise ValueError(f"replay {phase} metrics has no meters list")
    names: set[str] = set()
    for meter in meters:
        if not isinstance(meter, Mapping) or not isinstance(meter.get("name"), str):
            raise ValueError(f"replay {phase} metrics contains a malformed meter")
        name = meter["name"]
        if not name:
            raise ValueError(f"replay {phase} metrics contains an empty meter name")
        names.add(name)
    return instant, tuple(sorted(names))


def _untagged_counter(observation: object, *, phase: str, name: str) -> float:
    """Read one untagged counter from a server metrics observation.

    Counter tags would make one value a subset selected by server-internal
    dimensions, rather than the whole endpoint.  The endpoint's untagged
    request and error totals are the only values that establish that this
    particular attempt reached the replay server.
    """
    assert isinstance(observation, Mapping)
    metrics = observation["metrics"]
    assert isinstance(metrics, Mapping)
    meters = metrics["meters"]
    assert isinstance(meters, list)
    candidates: list[float] = []
    for meter in meters:
        assert isinstance(meter, Mapping)
        if meter.get("name") != name:
            continue
        tags = meter.get("tags", {})
        if tags != {}:
            continue
        if set(meter) != {"name", "type", "tags", "count"} or meter["type"] != "counter":
            raise ValueError(f"replay {phase} {name} counter has invalid fields")
        count = meter["count"]
        if (
            isinstance(count, bool)
            or not isinstance(count, int | float)
            or not math.isfinite(count)
            or count < 0
        ):
            raise ValueError(f"replay {phase} {name} counter is not finite and non-negative")
        candidates.append(float(count))
    if not candidates:
        raise ValueError(f"replay {phase} metrics has no untagged {name} counter")
    if len(candidates) != 1:
        raise ValueError(f"replay {phase} metrics has multiple untagged {name} counters")
    return candidates[0]


def _result_instant(result: Mapping[str, object], name: str) -> datetime:
    value = result.get(name)
    if not isinstance(value, str):
        raise ValueError(f"result {name} is missing for replay evidence")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"result {name} is not ISO-8601") from None
    if instant.tzinfo is None:
        raise ValueError(f"result {name} has no timezone")
    return instant


def _resource_instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("replay resource observed_at is missing")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("replay resource observed_at is not ISO-8601") from None
    if instant.tzinfo is None:
        raise ValueError("replay resource observed_at has no timezone")
    return instant


def _cpuset_string(start: int, count: int) -> str:
    end = start + count - 1
    return str(start) if start == end else f"{start}-{end}"


def validate_replay_evidence(
    result: Mapping[str, object], config: ReplayConfig
) -> dict[str, object]:
    """Validate the worker's replay protocol without making a capacity claim.

    This deliberately validates the observations recorded for every replay
    attempt, including a diagnostic canary.  It does not collect per-attempt
    container-limit readback: declared allocation and the one-time provider
    canary own that question.  The retained samples answer the run-specific
    questions (server activity and cpuset utilization) without turning them
    into a second provider contract.
    """
    if result.get("replay") != config.as_dict():
        raise ValueError("result replay config does not exactly match the ledger row")
    evidence = result.get("replay_evidence")
    expected = {"readiness", "before", "samples", "resource_samples", "after", "errors"}
    if not isinstance(evidence, Mapping) or set(evidence) != expected:
        raise ValueError("result replay_evidence is missing or malformed")
    readiness = evidence["readiness"]
    if not isinstance(readiness, Mapping) or set(readiness) != {
        "state",
        "wait_ms",
        "attempts",
        "last_error",
    }:
        raise ValueError("replay readiness evidence is malformed")
    wait_ms = readiness["wait_ms"]
    attempts = readiness["attempts"]
    last_error = readiness["last_error"]
    if (
        readiness["state"] != "ready"
        or isinstance(wait_ms, bool)
        or not isinstance(wait_ms, int)
        or wait_ms < 0
        or isinstance(attempts, bool)
        or not isinstance(attempts, int)
        or attempts < 1
        or (last_error is not None and not isinstance(last_error, str))
    ):
        raise ValueError("replay was not ready before subject timing")
    errors = evidence["errors"]
    if not isinstance(errors, list):
        raise ValueError("replay errors evidence is not a list")
    for error in errors:
        if (
            not isinstance(error, Mapping)
            or set(error) != {"phase", "error"}
            or error["phase"] not in {"readiness", "before", "sample", "resource-sample", "after"}
            or not isinstance(error["error"], str)
            or not error["error"]
        ):
            raise ValueError("replay errors evidence contains a malformed error")
    if errors:
        raise ValueError(f"replay evidence recorded {len(errors)} explicit error(s)")

    before_at, before_names = _observation(evidence["before"], phase="before", with_elapsed=False)
    after_at, after_names = _observation(evidence["after"], phase="after", with_elapsed=False)
    before_requests = _untagged_counter(evidence["before"], phase="before", name=REQUEST_COUNTER)
    after_requests = _untagged_counter(evidence["after"], phase="after", name=REQUEST_COUNTER)
    before_errors = _untagged_counter(evidence["before"], phase="before", name=ERROR_COUNTER)
    after_errors = _untagged_counter(evidence["after"], phase="after", name=ERROR_COUNTER)
    if after_requests <= before_requests:
        raise ValueError("replay request counter did not increase during subject timing")
    if after_errors < before_errors:
        raise ValueError("replay error counter regressed during subject timing")
    if after_errors > before_errors:
        raise ValueError("replay error counter increased during subject timing")
    started_at = _result_instant(result, "started_at")
    finished_at = _result_instant(result, "finished_at")
    if not before_at <= started_at <= finished_at <= after_at:
        raise ValueError("replay before/after metrics do not bracket subject timing")
    samples = evidence["samples"]
    if not isinstance(samples, list):
        raise ValueError("replay samples evidence is not a list")
    if config.capacity_status == "calibrated" and not samples:
        raise ValueError("calibrated replay evidence has no interval metrics sample")
    sample_names: set[str] = set()
    previous = before_at
    for sample in samples:
        observed_at, names = _observation(sample, phase="sample", with_elapsed=True)
        if observed_at < previous or observed_at > after_at:
            raise ValueError("replay sample metrics are out of observation order")
        previous = observed_at
        sample_names.update(names)
    resource_samples = evidence["resource_samples"]
    if not isinstance(resource_samples, list):
        raise ValueError("replay resource_samples evidence is not a list")
    if config.capacity_status == "calibrated" and not resource_samples:
        raise ValueError("calibrated replay evidence has no resource sample")
    allocation = config.allocation
    expected_server_cpuset = _cpuset_string(0, allocation.replay_vcpus)
    expected_subject_cpuset = _cpuset_string(allocation.replay_vcpus, allocation.subject_vcpus)
    previous_resource = before_at
    resource_fields = {
        "observed_at",
        "elapsed_s",
        "interval_s",
        "server_cpuset",
        "subject_cpuset",
        "server_cpuset_utilization",
        "server_cores_used",
        "subject_cpuset_utilization",
        "subject_cores_used",
        "host_mem_available_kb",
        "host_load1",
    }
    for sample in resource_samples:
        if not isinstance(sample, Mapping) or set(sample) != resource_fields:
            raise ValueError("replay resource sample has invalid fields")
        observed_at = _resource_instant(sample["observed_at"])
        if observed_at < previous_resource or observed_at > after_at:
            raise ValueError("replay resource samples are out of observation order")
        previous_resource = observed_at
        if (
            sample["server_cpuset"] != expected_server_cpuset
            or sample["subject_cpuset"] != expected_subject_cpuset
        ):
            raise ValueError("replay resource sample cpusets disagree with allocation")
        for name in (
            "elapsed_s",
            "interval_s",
            "server_cpuset_utilization",
            "server_cores_used",
            "subject_cpuset_utilization",
            "subject_cores_used",
            "host_load1",
        ):
            value = sample[name]
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"replay resource sample {name} is invalid")
        if (
            sample["server_cpuset_utilization"] > 1
            or sample["subject_cpuset_utilization"] > 1
            or sample["server_cores_used"] > allocation.replay_vcpus
            or sample["subject_cores_used"] > allocation.subject_vcpus
            or sample["interval_s"] <= 0
        ):
            raise ValueError("replay resource sample exceeds its declared allocation")
        available = sample["host_mem_available_kb"]
        if isinstance(available, bool) or not isinstance(available, int) or available < 0:
            raise ValueError("replay resource sample host_mem_available_kb is invalid")
    return {
        "readiness_attempts": attempts,
        "readiness_wait_ms": wait_ms,
        "before_metric_names": list(before_names),
        "after_metric_names": list(after_names),
        "sample_metric_names": sorted(sample_names),
        "request_count_before": before_requests,
        "request_count_after": after_requests,
        "error_count_before": before_errors,
        "error_count_after": after_errors,
        "sample_count": len(samples),
        "resource_sample_count": len(resource_samples),
    }
