"""The one resolved replay contract shared by plan, provider, worker, and reports."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

REPLAY_SPEC_VERSION = 3
REPLAY_BLOCK_FIELDS = (
    "server_image_uri",
    "serving_mode",
    "latency_model",
)
REPLAY_FIXTURE_FIELDS = ("fixture_sha256", "fixture_uri")
LATENCY_MODEL_FIELDS = ("deadlines_ms", "scale", "jitter")
INJECT_SHAPES = ("worker_page", "pivot_probe", "structure_probe")
SERVING_MODES = ("sorted", "duckdb")
REPLAY_INTEGER_FIELDS = (
    "subject_vcpus",
    "replay_vcpus",
    "replay_memory_gb",
    "replay_parquet_connections",
    "replay_max_concurrent_requests",
    "replay_heap_percent",
    "replay_prefetch_max_windows",
)
REPLAY_BOOLEAN_FIELDS = ("replay_prefetch",)
REPLAY_FIELDS = (*REPLAY_INTEGER_FIELDS, *REPLAY_BOOLEAN_FIELDS)
CAPACITY_STATUSES = ("uncalibrated", "calibrated")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_PINNED_IMAGE_RE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}")
_GCS_PATTERN_META = frozenset("*?[]")
REQUEST_COUNTER = "swath.replay.http.requests"
ERROR_COUNTER = "swath.replay.http.errors"


class ReplayError(ValueError):
    """A replay document is incomplete, non-canonical, or cannot run."""


def canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _positive(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReplayError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class ReplayBackend:
    """The plan-wide server implementation, fixture, and latency treatment."""

    server_image_uri: str
    fixture_sha256: str
    fixture_uri: str | None
    serving_mode: str
    latency_deadlines_ms: tuple[tuple[str, int], ...] | None
    latency_scale: float | None
    latency_jitter: str | None

    @property
    def profile_spec(self) -> str | None:
        if self.latency_deadlines_ms is None:
            return None
        return ",".join(f"{shape}={delay}ms" for shape, delay in self.latency_deadlines_ms)

    def as_dict(self) -> dict[str, object]:
        latency_model: object
        if self.latency_deadlines_ms is None:
            latency_model = "none"
        else:
            latency_model = {
                "deadlines_ms": dict(self.latency_deadlines_ms),
                "scale": self.latency_scale,
                "jitter": self.latency_jitter,
            }
        document: dict[str, object] = {
            "server_image_uri": self.server_image_uri,
            "fixture_sha256": self.fixture_sha256,
            "serving_mode": self.serving_mode,
            "latency_model": latency_model,
        }
        if self.fixture_uri is not None:
            document["fixture_uri"] = self.fixture_uri
        return document


@dataclass(frozen=True)
class ReplayAllocation:
    """The independent per-case server/subject execution controls."""

    subject_vcpus: int
    replay_vcpus: int
    replay_memory_gb: int
    replay_parquet_connections: int
    replay_max_concurrent_requests: int
    replay_heap_percent: int
    replay_prefetch_max_windows: int
    replay_prefetch: bool

    def as_dict(self) -> dict[str, object]:
        return {field: getattr(self, field) for field in REPLAY_FIELDS}


@dataclass(frozen=True)
class ReplayPlan:
    """The plan-wide backend and its explicitly declared capacity eligibility."""

    backend: ReplayBackend
    capacity_status: str


@dataclass(frozen=True)
class ReplayConfig:
    """One complete resolved replay case, serializable at every boundary."""

    backend: ReplayBackend
    allocation: ReplayAllocation
    capacity_status: str

    def as_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend.as_dict(),
            "allocation": self.allocation.as_dict(),
            "capacity_status": self.capacity_status,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.as_dict())


@dataclass(frozen=True)
class ReplayAllocationSummary:
    """Derived display and validation facts; never separate identity inputs."""

    server_cpuset: str
    subject_cpuset: str
    host_vcpus: int
    host_memory_headroom_gb: int | None


def counter_value(observation: object, name: str) -> float | None:
    """Read one untagged replay counter from a raw metrics observation."""
    if not isinstance(observation, Mapping):
        return None
    metrics = observation.get("metrics")
    if not isinstance(metrics, Mapping):
        return None
    meters = metrics.get("meters")
    if not isinstance(meters, Sequence) or isinstance(meters, str | bytes):
        return None
    matches: list[float] = []
    for meter in meters:
        if not isinstance(meter, Mapping):
            continue
        count = meter.get("count")
        if (
            meter.get("name") == name
            and meter.get("type") == "counter"
            and meter.get("tags") == {}
            and isinstance(count, int | float)
            and not isinstance(count, bool)
            and math.isfinite(count)
            and count >= 0
        ):
            matches.append(float(count))
    return matches[0] if len(matches) == 1 else None


def evidence_errors(config: ReplayConfig, evidence: object, *, purpose: str) -> tuple[str, ...]:
    """Return every reason raw replay evidence cannot support this attempt.

    This is the one acceptance rule shared by the worker and readers. Raw
    observations remain raw; the returned reasons are derived and need not be
    persisted as a second verdict protocol.
    """
    if not isinstance(evidence, Mapping):
        return ("replay_evidence is not an object",)
    errors: list[str] = []
    readiness = evidence.get("readiness")
    if not isinstance(readiness, Mapping) or readiness.get("state") != "ready":
        errors.append("replay server was not ready before timing")
    recorded_errors = evidence.get("errors")
    if not isinstance(recorded_errors, list):
        errors.append("replay evidence errors is not a list")
    elif recorded_errors:
        errors.append("replay evidence records collection errors")

    before_requests = counter_value(evidence.get("before"), REQUEST_COUNTER)
    after_requests = counter_value(evidence.get("after"), REQUEST_COUNTER)
    if before_requests is None or after_requests is None:
        errors.append("replay request counter is missing or ambiguous")
    elif after_requests <= before_requests:
        errors.append("replay request counter did not increase during the subject interval")

    before_errors = counter_value(evidence.get("before"), ERROR_COUNTER)
    after_errors = counter_value(evidence.get("after"), ERROR_COUNTER)
    if before_errors is None or after_errors is None:
        errors.append("replay error counter is missing or ambiguous")
    elif after_errors != before_errors:
        errors.append("replay error counter changed during the subject interval")

    if purpose == "measurement" and config.capacity_status == "calibrated":
        samples = evidence.get("samples")
        resources = evidence.get("resource_samples")
        if not isinstance(samples, list) or not samples:
            errors.append("calibrated replay measurement has no interval metrics sample")
        expected_server = _cpuset(0, config.allocation.replay_vcpus)
        expected_subject = _cpuset(config.allocation.replay_vcpus, config.allocation.subject_vcpus)
        if not isinstance(resources, list) or not any(
            isinstance(sample, Mapping)
            and sample.get("server_cpuset") == expected_server
            and sample.get("subject_cpuset") == expected_subject
            for sample in resources
        ):
            errors.append("calibrated replay measurement has no matching cpuset resource sample")
    return tuple(errors)


def parse_backend(value: object) -> ReplayBackend:
    if not isinstance(value, Mapping):
        raise ReplayError("replay backend is not an object")
    allowed = {*REPLAY_BLOCK_FIELDS, *REPLAY_FIXTURE_FIELDS}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ReplayError(f"replay backend has unknown field(s): {', '.join(map(str, unknown))}")
    missing = sorted(set(REPLAY_BLOCK_FIELDS) - set(value))
    if missing:
        raise ReplayError(f"replay backend is missing {', '.join(missing)}")
    image = value.get("server_image_uri")
    if not isinstance(image, str) or _PINNED_IMAGE_RE.fullmatch(image) is None:
        raise ReplayError("replay server_image_uri is not digest-pinned")
    fixture_sha256 = value.get("fixture_sha256")
    fixture_uri = value.get("fixture_uri")
    if not isinstance(fixture_sha256, str) or _HEX64_RE.fullmatch(fixture_sha256) is None:
        raise ReplayError("replay fixture_sha256 is not a sha256 digest")
    if fixture_uri is not None:
        valid_uri = (
            isinstance(fixture_uri, str)
            and fixture_uri.startswith("gs://")
            and fixture_uri.endswith(".parquet")
            and not any(character.isspace() for character in fixture_uri)
        )
        if valid_uri:
            pattern_meta = {character for character in fixture_uri if character in _GCS_PATTERN_META}
            valid_uri = not pattern_meta or (
                pattern_meta == {"*"}
                and fixture_uri.count("*") == 1
                and fixture_uri.endswith("/part-*.parquet")
            )
        if not valid_uri:
            raise ReplayError(
                "replay fixture_uri must name one exact gs://*.parquet object or one "
                "bounded gs://.../part-*.parquet set"
            )
    mode = value.get("serving_mode")
    if mode not in SERVING_MODES:
        raise ReplayError(f"replay serving_mode must be one of {', '.join(SERVING_MODES)}")
    latency = value.get("latency_model")
    if latency == "none":
        return ReplayBackend(
            server_image_uri=image,
            fixture_sha256=fixture_sha256,
            fixture_uri=fixture_uri,
            serving_mode=mode,
            latency_deadlines_ms=None,
            latency_scale=None,
            latency_jitter=None,
        )
    if not isinstance(latency, Mapping) or set(latency) != set(LATENCY_MODEL_FIELDS):
        raise ReplayError("replay latency_model has invalid fields")
    profile = latency.get("deadlines_ms")
    if not isinstance(profile, Mapping) or set(map(str, profile)) != set(INJECT_SHAPES):
        raise ReplayError("replay latency_model.deadlines_ms has invalid shapes")
    deadlines = tuple(
        (shape, _positive(profile[shape], f"latency deadline {shape}")) for shape in INJECT_SHAPES
    )
    scale = latency.get("scale")
    if (
        isinstance(scale, bool)
        or not isinstance(scale, int | float)
        or not math.isfinite(scale)
        or scale <= 0
    ):
        raise ReplayError("replay latency_model.scale must be finite and positive")
    if latency.get("jitter") != "none":
        raise ReplayError("replay latency_model.jitter must be 'none'")
    return ReplayBackend(
        server_image_uri=image,
        fixture_sha256=fixture_sha256,
        fixture_uri=fixture_uri,
        serving_mode=mode,
        latency_deadlines_ms=deadlines,
        latency_scale=float(scale),
        latency_jitter="none",
    )


def parse_allocation(value: object) -> ReplayAllocation:
    if not isinstance(value, Mapping) or set(value) != set(REPLAY_FIELDS):
        raise ReplayError("replay allocation has invalid fields")
    integers = {field: _positive(value[field], field) for field in REPLAY_INTEGER_FIELDS}
    if not isinstance(value["replay_prefetch"], bool):
        raise ReplayError("replay_prefetch must be a boolean")
    if integers["replay_heap_percent"] > 100:
        raise ReplayError("replay_heap_percent must be between 1 and 100")
    return ReplayAllocation(**integers, replay_prefetch=value["replay_prefetch"])


def parse_document(raw: str | Mapping[str, object]) -> ReplayConfig:
    try:
        value: object = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except json.JSONDecodeError as exc:
        raise ReplayError("replay document is not JSON") from exc
    if not isinstance(value, Mapping) or set(value) != {"backend", "allocation", "capacity_status"}:
        raise ReplayError("replay document has invalid fields")
    capacity_status = value["capacity_status"]
    if capacity_status not in CAPACITY_STATUSES:
        raise ReplayError(f"replay capacity_status must be one of {', '.join(CAPACITY_STATUSES)}")
    config = ReplayConfig(
        parse_backend(value["backend"]), parse_allocation(value["allocation"]), capacity_status
    )
    if isinstance(raw, str) and raw != config.canonical_json():
        raise ReplayError("replay document is not canonical JSON")
    return config


def parse_plan(value: object) -> ReplayPlan:
    if not isinstance(value, Mapping):
        raise ReplayError("replay plan block is not an object")
    allowed = {*REPLAY_BLOCK_FIELDS, *REPLAY_FIXTURE_FIELDS, "capacity_status"}
    required = {*REPLAY_BLOCK_FIELDS, "capacity_status"}
    if set(value) - allowed or required - set(value):
        unknown = sorted(set(value) - allowed)
        missing = sorted(required - set(value))
        detail = []
        if unknown:
            detail.append(f"unknown field(s): {', '.join(map(str, unknown))}")
        if missing:
            detail.append(f"missing field(s): {', '.join(missing)}")
        raise ReplayError("replay plan block has " + "; ".join(detail))
    status = value["capacity_status"]
    if status not in CAPACITY_STATUSES:
        raise ReplayError(f"replay capacity_status must be one of {', '.join(CAPACITY_STATUSES)}")
    return ReplayPlan(
        parse_backend(
            {
                field: value[field]
                for field in allowed
                if field in value and field != "capacity_status"
            }
        ),
        status,
    )


def make_config(plan: ReplayPlan, values: Mapping[str, object]) -> ReplayConfig:
    return ReplayConfig(
        backend=plan.backend,
        allocation=parse_allocation(values),
        capacity_status=plan.capacity_status,
    )


def allocation_summary(
    config: ReplayConfig,
    *,
    box_vcpus: int,
    box_memory_gb: int,
    container_memory_gb: int | None,
) -> ReplayAllocationSummary:
    """Refuse impossible sidecar allocation and derive the unowned host remainder."""
    allocation = config.allocation
    host_vcpus = box_vcpus - allocation.replay_vcpus - allocation.subject_vcpus
    if host_vcpus < 1:
        raise ReplayError("replay and subject cpusets leave no host CPU remainder")
    memory_outside_replay = box_memory_gb - allocation.replay_memory_gb
    if memory_outside_replay < 1:
        raise ReplayError("replay memory ceiling leaves no memory outside the sidecar")
    host_memory = (
        None if container_memory_gb is None else memory_outside_replay - container_memory_gb
    )
    if host_memory is not None and host_memory < 1:
        raise ReplayError("replay and subject memory ceilings leave no host memory headroom")
    return ReplayAllocationSummary(
        server_cpuset=_cpuset(0, allocation.replay_vcpus),
        subject_cpuset=_cpuset(allocation.replay_vcpus, allocation.subject_vcpus),
        host_vcpus=host_vcpus,
        host_memory_headroom_gb=host_memory,
    )


def _cpuset(start: int, count: int) -> str:
    end = start + count - 1
    return str(start) if start == end else f"{start}-{end}"
