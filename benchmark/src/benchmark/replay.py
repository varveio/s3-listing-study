"""The one resolved replay contract shared by plan, provider, worker, and reports."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from benchmark.contract import canonical_json

REPLAY_SPEC_VERSION = 3
REPLAY_ENDPOINT_PORT = 19090
REPLAY_METRICS_PORT = 19192
REPLAY_ENDPOINT_URL = f"http://127.0.0.1:{REPLAY_ENDPOINT_PORT}"
REPLAY_METRICS_URL = f"http://127.0.0.1:{REPLAY_METRICS_PORT}"
REPLAY_BLOCK_FIELDS = (
    "server_image_uri",
    "serving_mode",
    "latency_model",
)
REPLAY_FIXTURE_FIELDS = ("fixture_sha256", "fixture_uri")
REPLAY_OPTIONAL_FIELDS = ("warmup", "server_implementation", "fixture_adopted_sha256")
LATENCY_MODEL_FIELDS = ("deadlines_ms", "scale", "jitter")
WARMUP_COUNT_FIELDS = ("structure_probes", "pivot_probes", "worker_pages")
WARMUP_FIELDS = (*WARMUP_COUNT_FIELDS, "in_flight")
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
# Stated only when a case pins it; absent means the server's own default, and an
# absent field is omitted from the identity so every earlier identity holds.
REPLAY_OPTIONAL_INTEGER_FIELDS = (
    "replay_delimiter_connections",
    "replay_response_buffer_budget_bytes",
    "replay_list_index_max_bytes",
)
# Go-only, like the budget above: stated, it is rendered as ``--list-index=...``
# and enters identity; absent, the Go server's own default (on) applies.
REPLAY_OPTIONAL_BOOLEAN_FIELDS = ("replay_list_index",)
REPLAY_OPTIONAL_ALLOCATION_FIELDS = (
    *REPLAY_OPTIONAL_INTEGER_FIELDS,
    *REPLAY_OPTIONAL_BOOLEAN_FIELDS,
)
# Go-only allocation fields: each is refused for the Java server.
GO_ONLY_ALLOCATION_FIELDS = (
    "replay_response_buffer_budget_bytes",
    "replay_list_index",
    "replay_list_index_max_bytes",
)
CAPACITY_STATUSES = ("uncalibrated", "calibrated")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_PINNED_IMAGE_RE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}")
# A plan may name a server image that is not built yet. It loads and dry-renders
# so the plan can be reviewed, and ``campaign submit`` refuses it.
REPLAY_IMAGE_PLACEHOLDER = "REPLAY_GO_IMAGE_TBD"
_PLACEHOLDER_IMAGE_RE = re.compile(r"[^\s@]+@sha256:" + REPLAY_IMAGE_PLACEHOLDER)
# A staged fixture is either Java-era Parquet parts (one object or the bounded
# ``part-*.parquet`` set) or one SeekParquet dataset root: its ``manifest.json``
# plus the ``data/`` parts that manifest names.
FIXTURE_LAYOUT_PARTS = "parts"
FIXTURE_LAYOUT_SEEKPARQUET = "seekparquet"
SEEKPARQUET_MANIFEST_NAME = "manifest.json"
_GCS_PATTERN_META = frozenset("*?[]")
REQUEST_COUNTER = "swath.replay.http.requests"
LIST_INDEX_SKIPS_COUNTER = "swath.replay.delimiter.index.skips"
LIST_INDEX_DECLINES_COUNTER = "swath.replay.delimiter.index.declines"
LIST_INDEX_DECLINE_REASONS = ("missing_group", "dense_page", "unsafe_run")
LIST_INDEX_BUILD_LATENCY_TIMER = "swath.replay.delimiter.index.build.latency"
LIST_INDEX_BUILD_GAUGES: tuple[tuple[str, str, dict[str, str]], ...] = (
    ("groups_indexed", "swath.replay.delimiter.index.build.groups", {"outcome": "indexed"}),
    ("groups_skipped", "swath.replay.delimiter.index.build.groups", {"outcome": "skipped"}),
    ("groups_failed", "swath.replay.delimiter.index.build.groups", {"outcome": "failed"}),
    ("pages", "swath.replay.delimiter.index.build.pages", {}),
    ("entries", "swath.replay.delimiter.index.entries", {}),
    ("bytes", "swath.replay.delimiter.index.bytes", {}),
    ("max_bytes", "swath.replay.delimiter.index.max_bytes", {}),
    ("budget_exhausted", "swath.replay.delimiter.index.budget_exhausted", {}),
)
LIST_INDEX_LINE_PREFIX = "swath_replay list_index "
ERROR_COUNTER = "swath.replay.http.errors"
REQUEST_LATENCY_TIMER = "swath.replay.request.latency"
INJECT_OVERRUN_COUNTER = "swath.replay.inject.overrun"
TIMING_VALID = "TIMING_VALID"
PRESSURE_DEGRADED = "PRESSURE_DEGRADED"
CAPACITY_FAILED = "CAPACITY_FAILED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
NOT_APPLICABLE = "NOT_APPLICABLE"
REPLAY_TIMING_STATES = (
    TIMING_VALID,
    PRESSURE_DEGRADED,
    CAPACITY_FAILED,
    INSUFFICIENT_EVIDENCE,
    NOT_APPLICABLE,
)
TIMING_GATE_VERSION = 1
TIMING_GATE_MIN_RESOURCE_SAMPLES = 5
TIMING_GATE_HIGH_CPU = 0.90
TIMING_GATE_SUSTAINED_CPU_FRACTION = 0.20
TIMING_GATE_VALID_OVERRUN_FRACTION = 0.01
TIMING_GATE_FAILED_OVERRUN_FRACTION = 0.10
TIMING_GATE_VALID_MEAN_RATIO = 1.10
TIMING_GATE_FAILED_MEAN_RATIO = 1.25


class ReplayError(ValueError):
    """A replay document is incomplete, non-canonical, or cannot run."""


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
    warmup: tuple[tuple[str, int], ...] | None = None
    server_implementation: str | None = None
    fixture_adopted_sha256: str | None = None

    @property
    def fixture_layout(self) -> str | None:
        """How a staged fixture is laid out, or ``None`` for an image-bundled one."""
        return None if self.fixture_uri is None else fixture_layout(self.fixture_uri)

    @property
    def image_is_placeholder(self) -> bool:
        """The plan names a server image that has not been built yet."""
        return _PLACEHOLDER_IMAGE_RE.fullmatch(self.server_image_uri) is not None

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
        if self.fixture_adopted_sha256 is not None:
            # The Java-era staging digest this SeekParquet dataset was converted
            # from, so a converted case stays linkable to the release fixture.
            document["fixture_adopted_sha256"] = self.fixture_adopted_sha256
        if self.server_implementation is not None:
            document["server_implementation"] = self.server_implementation
        if self.warmup is not None:
            # Declared warm-up is part of the treatment: a warmed server is a different
            # instrument from a cold one. Absent means none, so earlier identities hold.
            document["warmup"] = dict(self.warmup)
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
    replay_delimiter_connections: int | None = None
    replay_response_buffer_budget_bytes: int | None = None
    replay_list_index_max_bytes: int | None = None
    replay_list_index: bool | None = None

    def as_dict(self) -> dict[str, object]:
        document: dict[str, object] = {field: getattr(self, field) for field in REPLAY_FIELDS}
        for field in REPLAY_OPTIONAL_ALLOCATION_FIELDS:
            value = getattr(self, field)
            if value is not None:
                document[field] = value
        return document


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

    def __post_init__(self) -> None:
        allocation = self.allocation
        if self.backend.server_implementation == "go":
            if allocation.replay_delimiter_connections is not None:
                raise ReplayError("Go replay does not support replay_delimiter_connections")
            if allocation.replay_max_concurrent_requests < 8:
                raise ReplayError("Go replay requires at least 8 concurrent requests")
            if (
                allocation.replay_list_index is False
                and allocation.replay_list_index_max_bytes is not None
            ):
                raise ReplayError("replay_list_index_max_bytes requires replay_list_index on")
        else:
            for field in GO_ONLY_ALLOCATION_FIELDS:
                if getattr(allocation, field) is not None:
                    raise ReplayError(f"{field} requires Go replay")
            if self.backend.fixture_layout == FIXTURE_LAYOUT_SEEKPARQUET:
                raise ReplayError("a SeekParquet dataset fixture requires Go replay")

    @property
    def list_index_enabled(self) -> bool:
        """Whether the server is asked to build the SeekParquet list index.

        Go builds it unless the case turns it off; Java has no such index. The
        server reports ``state=unsupported`` for a fixture that is not a
        SeekParquet dataset, so this is the request, not the outcome.
        """
        if self.backend.server_implementation != "go":
            return False
        return self.allocation.replay_list_index is not False

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


def _meter(
    observation: object, name: str, kind: str, tags: Mapping[str, str]
) -> Mapping[str, object] | None:
    if not isinstance(observation, Mapping):
        return None
    metrics = observation.get("metrics")
    if not isinstance(metrics, Mapping):
        return None
    meters = metrics.get("meters")
    if not isinstance(meters, Sequence) or isinstance(meters, str | bytes):
        return None
    matches = [
        meter
        for meter in meters
        if isinstance(meter, Mapping)
        and meter.get("name") == name
        and meter.get("type") == kind
        and meter.get("tags") == tags
    ]
    return matches[0] if len(matches) == 1 else None


def _finite_nonnegative(value: object) -> float | None:
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    ):
        return float(value)
    return None


def _meter_delta(
    before: object,
    after: object,
    *,
    name: str,
    kind: str,
    tags: Mapping[str, str],
    field: str,
    absent_is_zero: bool = False,
) -> float | None:
    values: list[float] = []
    for observation in (before, after):
        meter = _meter(observation, name, kind, tags)
        if meter is None and absent_is_zero:
            values.append(0.0)
            continue
        value = _finite_nonnegative(meter.get(field) if meter is not None else None)
        if value is None:
            return None
        values.append(value)
    delta = values[1] - values[0]
    return delta if delta >= 0 else None


def replay_timing_assessment(config: ReplayConfig, evidence: object) -> dict[str, object]:
    """Classify replay pressure without changing the attempt's functional verdict.

    This is a derived reporting gate over the raw meters retained in
    ``result.json``. It deliberately does not become another persisted verdict:
    historical and future evidence are classified by the same code.
    """
    assessment: dict[str, object] = {
        "gate_version": TIMING_GATE_VERSION,
        "state": INSUFFICIENT_EVIDENCE,
        "shapes": {},
        "resource": None,
        "reasons": [],
    }
    deadlines = config.backend.latency_deadlines_ms
    if deadlines is None:
        return {**assessment, "state": NOT_APPLICABLE, "reasons": []}
    if not isinstance(evidence, Mapping):
        return {**assessment, "reasons": ["replay evidence is not an object"]}

    reasons: list[str] = []
    recorded_errors = evidence.get("errors")
    if not isinstance(recorded_errors, list):
        reasons.append("replay evidence errors is not a list")
    elif recorded_errors:
        reasons.append("replay evidence records collection errors")

    before, after = evidence.get("before"), evidence.get("after")
    shapes: dict[str, object] = {}
    assert config.backend.latency_scale is not None
    for shape, base_deadline_ms in deadlines:
        requested_ms = base_deadline_ms * config.backend.latency_scale
        tags = {"shape": shape}
        requests = _meter_delta(
            before,
            after,
            name=REQUEST_LATENCY_TIMER,
            kind="timer",
            tags=tags,
            field="count",
        )
        total_ms = _meter_delta(
            before,
            after,
            name=REQUEST_LATENCY_TIMER,
            kind="timer",
            tags=tags,
            field="sum_ms",
        )
        overruns = _meter_delta(
            before,
            after,
            name=INJECT_OVERRUN_COUNTER,
            kind="counter",
            tags=tags,
            field="count",
            # Micrometer does not publish a tagged counter until its first
            # event. Absence on either boundary therefore means zero overruns,
            # not missing evidence; the request timer is the activity proof.
            absent_is_zero=True,
        )
        if requests is None or total_ms is None or overruns is None:
            reasons.append(f"{shape} request-latency meters are missing or non-monotonic")
            continue
        if requests == 0:
            continue
        if overruns > requests:
            reasons.append(f"{shape} overrun count exceeds request count")
            continue
        request_mean_ms = total_ms / requests
        shapes[shape] = {
            "requested_ms": requested_ms,
            "requests": requests,
            "request_mean_ms": request_mean_ms,
            "mean_ratio": request_mean_ms / requested_ms,
            "overruns": overruns,
            "overrun_fraction": overruns / requests,
        }
    if not shapes:
        reasons.append("injected replay attempt has no observed request shape")

    raw_resources = evidence.get("resource_samples")
    utilizations: list[float] = []
    if not isinstance(raw_resources, list):
        reasons.append("replay resource samples is not a list")
    else:
        for sample in raw_resources:
            utilization = (
                _finite_nonnegative(sample.get("server_cpuset_utilization"))
                if isinstance(sample, Mapping)
                else None
            )
            if utilization is None or utilization > 1:
                reasons.append("replay resource sample has invalid server utilization")
                break
            utilizations.append(utilization)
    high_cpu_samples = sum(value >= TIMING_GATE_HIGH_CPU for value in utilizations)
    high_cpu_fraction = high_cpu_samples / len(utilizations) if utilizations else 0.0
    resource = {
        "samples": len(utilizations),
        "high_cpu_samples": high_cpu_samples,
        "high_cpu_fraction": round(high_cpu_fraction, 6),
        "high_cpu_threshold": TIMING_GATE_HIGH_CPU,
    }
    if len(utilizations) < TIMING_GATE_MIN_RESOURCE_SAMPLES:
        reasons.append(f"replay has fewer than {TIMING_GATE_MIN_RESOURCE_SAMPLES} resource samples")

    assessment.update({"shapes": shapes, "resource": resource, "reasons": reasons})
    if reasons:
        return assessment

    capacity_failed = high_cpu_fraction >= TIMING_GATE_SUSTAINED_CPU_FRACTION or any(
        isinstance(shape, Mapping)
        and (
            float(shape["overrun_fraction"]) > TIMING_GATE_FAILED_OVERRUN_FRACTION
            or float(shape["mean_ratio"]) > TIMING_GATE_FAILED_MEAN_RATIO
        )
        for shape in shapes.values()
    )
    timing_valid = high_cpu_fraction < TIMING_GATE_SUSTAINED_CPU_FRACTION and all(
        isinstance(shape, Mapping)
        and float(shape["overrun_fraction"]) < TIMING_GATE_VALID_OVERRUN_FRACTION
        and float(shape["mean_ratio"]) <= TIMING_GATE_VALID_MEAN_RATIO
        for shape in shapes.values()
    )
    assessment["state"] = (
        CAPACITY_FAILED if capacity_failed else TIMING_VALID if timing_valid else PRESSURE_DEGRADED
    )
    return assessment


_LIST_INDEX_PAIR_RE = re.compile(r'([a-z_]+)=("(?:[^"\\]|\\.)*"|\S+)')
_LIST_INDEX_INT_FIELDS = (
    "max_bytes",
    "workers",
    "groups_indexed",
    "groups_skipped",
    "groups_failed",
    "parts_failed",
    "pages",
    "entries",
    "bytes",
)
_LIST_INDEX_STATES = ("built", "off", "unsupported", "failed")


def parse_list_index_line(text: str) -> dict[str, object] | None:
    """The Go server's one ``swath_replay list_index ...`` line, typed.

    ``text`` is server output as exported from the job's logs; a log
    exporter may prefix each line, so the line is found by its marker. Absent
    returns ``None`` (a server that predates the index); more than one, or a
    malformed one, is refused rather than guessed at.
    """
    found = [
        line[line.index(LIST_INDEX_LINE_PREFIX) + len(LIST_INDEX_LINE_PREFIX) :]
        for line in text.splitlines()
        if LIST_INDEX_LINE_PREFIX in line
    ]
    if not found:
        return None
    if len(found) != 1:
        raise ReplayError(f"server output has {len(found)} list_index lines, expected one")
    body = found[0].strip()
    pairs = _LIST_INDEX_PAIR_RE.findall(body)
    if " ".join(f"{key}={value}" for key, value in pairs) != body:
        raise ReplayError("list_index line is not a sequence of key=value pairs")
    raw = dict(pairs)
    if len(raw) != len(pairs):
        raise ReplayError("list_index line repeats a key")
    parsed: dict[str, object] = {"line": LIST_INDEX_LINE_PREFIX + body}
    try:
        state = raw.pop("state")
        if state not in _LIST_INDEX_STATES:
            raise ReplayError(f"list_index state {state!r} is not one of the server's states")
        parsed["state"] = state
        for field in _LIST_INDEX_INT_FIELDS:
            parsed[field] = int(raw.pop(field))
        exhausted = raw.pop("budget_exhausted")
        if exhausted not in ("true", "false"):
            raise ReplayError("list_index budget_exhausted is not a boolean")
        parsed["budget_exhausted"] = exhausted == "true"
        seconds = float(raw.pop("seconds"))
        if not math.isfinite(seconds) or seconds < 0:
            raise ReplayError(f"list_index seconds is not a finite duration: {seconds!r}")
        parsed["seconds"] = seconds
    except KeyError as exc:
        raise ReplayError(f"list_index line is missing {exc.args[0]}") from None
    except ValueError as exc:
        if isinstance(exc, ReplayError):
            raise
        raise ReplayError(f"list_index line has a malformed number: {exc}") from None
    if "error" in raw:
        quoted = raw.pop("error")
        try:
            parsed["error"] = json.loads(quoted)
        except json.JSONDecodeError:
            parsed["error"] = quoted
    if raw:
        raise ReplayError(f"list_index line has unknown field(s): {', '.join(sorted(raw))}")
    return parsed


def _gauge(observation: object, name: str, tags: Mapping[str, str]) -> int | float | None:
    meter = _meter(observation, name, "gauge", tags)
    value = _finite_nonnegative(meter.get("value") if meter is not None else None)
    if value is None:
        return None
    return int(value) if value.is_integer() else value


def list_index_summary(config: ReplayConfig, evidence: object) -> dict[str, object] | None:
    """What the Go server's list index request, build and use look like in raw meters.

    Derived, like the timing assessment, from the ``before``/``after`` metrics
    observations the worker already retains; never persisted as a verdict. The
    build gauges are fixed at server open, so they are read from ``before``
    (the scrape taken just before the subject clock). Skips and declines are
    interval deltas over the subject run. ``None`` for the Java server, which
    has no list index.
    """
    if config.backend.server_implementation != "go":
        return None
    observations = evidence if isinstance(evidence, Mapping) else {}
    before, after = observations.get("before"), observations.get("after")
    build: dict[str, object] = {
        field: _gauge(before, name, tags) for field, name, tags in LIST_INDEX_BUILD_GAUGES
    }
    latency = _meter(before, LIST_INDEX_BUILD_LATENCY_TIMER, "timer", {})
    recorded = _finite_nonnegative(latency.get("count") if latency is not None else None)
    total_ms = _finite_nonnegative(latency.get("sum_ms") if latency is not None else None)
    build["builds_recorded"] = None if recorded is None else int(recorded)
    build["seconds"] = round(total_ms / 1000, 6) if recorded == 1 and total_ms is not None else None
    interval: dict[str, object] = {
        "skips": _meter_delta(
            before, after, name=LIST_INDEX_SKIPS_COUNTER, kind="counter", tags={}, field="count"
        ),
        "declines": {
            reason: _meter_delta(
                before,
                after,
                name=LIST_INDEX_DECLINES_COUNTER,
                kind="counter",
                tags={"reason": reason},
                field="count",
            )
            for reason in LIST_INDEX_DECLINE_REASONS
        },
    }
    requested_max = config.allocation.replay_list_index_max_bytes
    reasons: list[str] = []
    inconclusive: list[str] = []
    if any(build[field] is None for field, _name, _tags in LIST_INDEX_BUILD_GAUGES):
        reasons.append("list-index build meters are absent; the server predates the list index")
    elif recorded is None:
        inconclusive.append(LIST_INDEX_TIMER_MISSING)
    elif config.list_index_enabled:
        if config.backend.fixture_layout == FIXTURE_LAYOUT_SEEKPARQUET:
            if recorded != 1:
                reasons.append("list index was requested over a SeekParquet dataset but not built")
            else:
                # The build timer records a failed build too, and a valid flat
                # fixture builds an empty index: only the server line can say
                # the build succeeded.
                inconclusive.append(LIST_INDEX_BUILD_UNCONFIRMED)
        if requested_max is not None and build["max_bytes"] != requested_max:
            reasons.append("list-index max_bytes differs from the requested budget")
    elif recorded != 0:
        reasons.append("list index was turned off but the server recorded a build")
    return {
        "requested": {"enabled": config.list_index_enabled, "max_bytes": requested_max},
        "build": build,
        "interval": interval,
        "delivered": _delivered(reasons, inconclusive),
        "reasons": reasons,
        "inconclusive": inconclusive,
        "provenance": (
            "replay /metrics swath.replay.delimiter.index.* meters in replay_evidence; "
            "the server's list_index stderr line is in the job's Cloud Logging"
        ),
    }


LIST_INDEX_TIMER_MISSING = "list-index build timer is missing; the build count is unobserved"
LIST_INDEX_BUILD_UNCONFIRMED = (
    "meters record one build attempt but cannot tell built from failed; attach the server line"
)
# The server logs build seconds with three decimals.
LIST_INDEX_SECONDS_TOLERANCE = 0.0005 + 1e-9


def _delivered(reasons: list[str], inconclusive: list[str]) -> bool | None:
    """``False`` on any refusal, ``None`` while the evidence is inconclusive, else ``True``."""
    if reasons:
        return False
    return None if inconclusive else True


def attach_list_index_line(summary: dict[str, object], line: Mapping[str, object] | None) -> None:
    """Fold an exported server line into a meter summary and revise its delivery.

    A line that disagrees with the meters, or whose state is not the requested
    outcome (``built`` over a SeekParquet dataset with the index on, ``off``
    with it off), refuses delivery. Only a ``built`` line confirms a build the
    meters alone cannot tell from a failed one.
    """
    reasons = _str_list(summary.get("reasons"))
    inconclusive = _str_list(summary.get("inconclusive"))
    if line is None:
        disagreements = ["server log has no list_index line"]
    else:
        disagreements = list_index_line_disagreements(line, summary)
        requested = summary.get("requested")
        enabled = isinstance(requested, Mapping) and requested.get("enabled") is True
        expected = None
        if not enabled:
            expected = "off"
        elif LIST_INDEX_BUILD_UNCONFIRMED in inconclusive or line.get("state") == "failed":
            expected = "built"
        state = line.get("state")
        if expected is not None and state != expected:
            reasons.append(f"server line reports state={state!r}, not {expected!r}")
        elif state == "built" and LIST_INDEX_BUILD_UNCONFIRMED in inconclusive:
            inconclusive.remove(LIST_INDEX_BUILD_UNCONFIRMED)
    reasons.extend(disagreements)
    summary["server_line"] = line
    summary["server_line_disagreements"] = disagreements
    summary["reasons"] = reasons
    summary["inconclusive"] = inconclusive
    summary["delivered"] = _delivered(reasons, inconclusive)


def _str_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def list_index_line_disagreements(
    line: Mapping[str, object], summary: Mapping[str, object]
) -> list[str]:
    """Where an exported list_index line and the meters disagree about one build."""
    build = summary.get("build")
    if not isinstance(build, Mapping):
        return ["list-index summary has no build"]
    errors = [
        f"list_index line {field}={line.get(field)!r} but meters say {build.get(field)!r}"
        for field in (
            "groups_indexed",
            "groups_skipped",
            "groups_failed",
            "pages",
            "entries",
            "bytes",
            "max_bytes",
        )
        if line.get(field) != build.get(field)
    ]
    exhausted = 1 if line.get("budget_exhausted") is True else 0
    if build.get("budget_exhausted") != exhausted:
        errors.append("list_index line and meters disagree on budget_exhausted")
    ran = line.get("state") in ("built", "failed")
    if ran != (build.get("builds_recorded") == 1):
        errors.append(f"list_index state={line.get('state')!r} disagrees with the build timer")
    line_seconds, meter_seconds = line.get("seconds"), build.get("seconds")
    if ran and (
        not isinstance(line_seconds, int | float)
        or not isinstance(meter_seconds, int | float)
        or abs(line_seconds - meter_seconds) > LIST_INDEX_SECONDS_TOLERANCE
    ):
        errors.append(f"list_index line seconds={line_seconds!r} but meters say {meter_seconds!r}")
    return errors


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
        legacy_server = format_cpuset(range(config.allocation.replay_vcpus))
        legacy_subject = format_cpuset(
            range(
                config.allocation.replay_vcpus,
                config.allocation.replay_vcpus + config.allocation.subject_vcpus,
            )
        )
        if not isinstance(resources, list) or not any(
            isinstance(sample, Mapping)
            and _resource_sample_matches_allocation(
                config.allocation,
                sample,
                legacy_server=legacy_server,
                legacy_subject=legacy_subject,
            )
            for sample in resources
        ):
            errors.append("calibrated replay measurement has no matching cpuset resource sample")
    return tuple(errors)


def replay_refusals(
    document: str | Mapping[str, object] | ReplayConfig,
    evidence: object,
    *,
    purpose: str,
) -> tuple[str, ...]:
    """Parse recorded replay intent and return its evidence refusals."""
    config = document if isinstance(document, ReplayConfig) else parse_document(document)
    return evidence_errors(config, evidence, purpose=purpose)


def fixture_layout(uri: str) -> str:
    """``seekparquet`` for a dataset root (``.../`` or ``.../manifest.json``), else ``parts``."""
    if uri.endswith("/") or uri.endswith("/" + SEEKPARQUET_MANIFEST_NAME):
        return FIXTURE_LAYOUT_SEEKPARQUET
    return FIXTURE_LAYOUT_PARTS


def fixture_root(uri: str) -> str:
    """The directory a staged fixture's companions live in, with a trailing slash.

    For Java-era parts it is the parts' parent; for a SeekParquet dataset it is
    the dataset root that holds ``manifest.json`` and ``data/``.
    """
    if fixture_layout(uri) == FIXTURE_LAYOUT_SEEKPARQUET:
        return uri.removesuffix(SEEKPARQUET_MANIFEST_NAME)
    parent, separator, _leaf = uri.rpartition("/")
    if not separator:
        raise ReplayError("replay fixture_uri has no parent directory")
    return parent + "/"


def _valid_fixture_uri(uri: object) -> bool:
    if (
        not isinstance(uri, str)
        or not uri.startswith("gs://")
        or any(character.isspace() for character in uri)
    ):
        return False
    pattern_meta = {character for character in uri if character in _GCS_PATTERN_META}
    if fixture_layout(uri) == FIXTURE_LAYOUT_SEEKPARQUET:
        # One dataset root below a bucket: no wildcard, no empty or dot segment.
        root = uri.removeprefix("gs://").removesuffix(SEEKPARQUET_MANIFEST_NAME).rstrip("/")
        segments = root.split("/")
        return (
            not pattern_meta
            and len(segments) >= 2
            and all(segment not in ("", ".", "..") for segment in segments)
        )
    if not uri.endswith(".parquet"):
        return False
    return not pattern_meta or (
        pattern_meta == {"*"} and uri.count("*") == 1 and uri.endswith("/part-*.parquet")
    )


def parse_backend(value: object) -> ReplayBackend:
    if not isinstance(value, Mapping):
        raise ReplayError("replay backend is not an object")
    allowed = {*REPLAY_BLOCK_FIELDS, *REPLAY_FIXTURE_FIELDS, *REPLAY_OPTIONAL_FIELDS}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ReplayError(f"replay backend has unknown field(s): {', '.join(map(str, unknown))}")
    warmup = parse_warmup(value.get("warmup"))
    missing = sorted(set(REPLAY_BLOCK_FIELDS) - set(value))
    if missing:
        raise ReplayError(f"replay backend is missing {', '.join(missing)}")
    image = value.get("server_image_uri")
    if not isinstance(image, str) or (
        _PINNED_IMAGE_RE.fullmatch(image) is None and _PLACEHOLDER_IMAGE_RE.fullmatch(image) is None
    ):
        raise ReplayError("replay server_image_uri is not digest-pinned")
    fixture_sha256 = value.get("fixture_sha256")
    fixture_uri = value.get("fixture_uri")
    if not isinstance(fixture_sha256, str) or _HEX64_RE.fullmatch(fixture_sha256) is None:
        raise ReplayError("replay fixture_sha256 is not a sha256 digest")
    if fixture_uri is not None and not _valid_fixture_uri(fixture_uri):
        raise ReplayError(
            "replay fixture_uri must name one exact gs://*.parquet object, one "
            "bounded gs://.../part-*.parquet set, or one SeekParquet dataset root "
            "(gs://.../ or gs://.../manifest.json)"
        )
    adopted = value.get("fixture_adopted_sha256")
    if "fixture_adopted_sha256" in value:
        if not isinstance(adopted, str) or _HEX64_RE.fullmatch(adopted) is None:
            raise ReplayError("replay fixture_adopted_sha256 is not a sha256 digest")
        if not isinstance(fixture_uri, str) or (
            fixture_layout(fixture_uri) != FIXTURE_LAYOUT_SEEKPARQUET
        ):
            raise ReplayError(
                "replay fixture_adopted_sha256 requires a SeekParquet dataset fixture_uri"
            )
    mode = value.get("serving_mode")
    if mode not in SERVING_MODES:
        raise ReplayError(f"replay serving_mode must be one of {', '.join(SERVING_MODES)}")
    implementation = value.get("server_implementation")
    if "server_implementation" in value and implementation not in ("java", "go"):
        raise ReplayError("replay server_implementation must be java or go")
    if implementation == "go" and mode != "sorted":
        raise ReplayError("Go replay supports only sorted serving")
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
            warmup=warmup,
            server_implementation=implementation,
            fixture_adopted_sha256=adopted,
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
        warmup=warmup,
        server_implementation=implementation,
        fixture_adopted_sha256=adopted,
    )


def parse_warmup(value: object) -> tuple[tuple[str, int], ...] | None:
    """Parse the optional declared warm-up the worker drives before the subject clock starts.

    The request counts are issued against the replay endpoint after readiness and before the
    ``before`` metrics snapshot, so they warm the server's JIT, reader pools, and page indexes
    without entering any treatment meter. ``structure_probes`` walk the fixture's delimiter tree
    breadth-first from the root; ``pivot_probes`` and ``worker_pages`` use keys that walk returned
    as ``start-after`` anchors.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(map(str, value)) != set(WARMUP_FIELDS):
        raise ReplayError(f"replay warmup must contain exactly {', '.join(WARMUP_FIELDS)}")
    counts: list[tuple[str, int]] = []
    for name in WARMUP_COUNT_FIELDS:
        count = value[name]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ReplayError(f"replay warmup {name} must be a non-negative integer")
        counts.append((name, count))
    if all(count == 0 for _, count in counts):
        raise ReplayError("replay warmup must request at least one request; omit it for none")
    counts.append(("in_flight", _positive(value["in_flight"], "replay warmup in_flight")))
    return tuple(counts)


def parse_allocation(value: object) -> ReplayAllocation:
    if not isinstance(value, Mapping) or not (
        set(REPLAY_FIELDS) <= set(value) <= {*REPLAY_FIELDS, *REPLAY_OPTIONAL_ALLOCATION_FIELDS}
    ):
        raise ReplayError("replay allocation has invalid fields")
    integers = {field: _positive(value[field], field) for field in REPLAY_INTEGER_FIELDS}
    optional = {
        field: _positive(value[field], field)
        for field in REPLAY_OPTIONAL_INTEGER_FIELDS
        if field in value
    }
    for field in ("replay_prefetch", *REPLAY_OPTIONAL_BOOLEAN_FIELDS):
        if field in value and not isinstance(value[field], bool):
            raise ReplayError(f"{field} must be a boolean")
    if integers["replay_heap_percent"] > 100:
        raise ReplayError("replay_heap_percent must be between 1 and 100")
    list_index = value.get("replay_list_index")
    return ReplayAllocation(
        **integers,
        **optional,
        replay_prefetch=value["replay_prefetch"],
        replay_list_index=list_index if isinstance(list_index, bool) else None,
    )


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
    allowed = {
        *REPLAY_BLOCK_FIELDS,
        *REPLAY_FIXTURE_FIELDS,
        *REPLAY_OPTIONAL_FIELDS,
        "capacity_status",
    }
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
    server_cpus, subject_cpus = allocation_cpu_sets(allocation, box_vcpus=box_vcpus)
    return ReplayAllocationSummary(
        server_cpuset=format_cpuset(server_cpus),
        subject_cpuset=format_cpuset(subject_cpus),
        host_vcpus=host_vcpus,
        host_memory_headroom_gb=host_memory,
    )


def allocation_cpu_sets(
    allocation: ReplayAllocation, *, box_vcpus: int
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Allocate whole N4 physical cores to replay and subject where possible.

    GCP Batch exposes N4 SMT siblings in two equal logical-CPU halves (for
    example 0,16 on n4-highcpu-32). Contiguous first-half/second-half cpusets
    therefore colocate the two controlled processes on the same physical cores.
    Each group receives both threads of consecutive cores; an odd requested
    count receives one thread of its final core, whose sibling is not assigned
    to the other controlled group.
    """
    if box_vcpus < 2 or box_vcpus % 2:
        raise ReplayError("replay CPU isolation requires an even N4 vCPU count")
    sibling_stride = box_vcpus // 2

    def group(start_core: int, count: int) -> tuple[tuple[int, ...], int]:
        whole_cores, odd_thread = divmod(count, 2)
        cores_used = whole_cores + odd_thread
        if start_core + cores_used > sibling_stride:
            raise ReplayError("replay and subject allocations exceed the N4 physical cores")
        first_threads = list(range(start_core, start_core + whole_cores))
        cpus = first_threads + [cpu + sibling_stride for cpu in first_threads]
        if odd_thread:
            cpus.append(start_core + whole_cores)
        return tuple(sorted(cpus)), start_core + cores_used

    server_cpus, next_core = group(0, allocation.replay_vcpus)
    subject_cpus, _ = group(next_core, allocation.subject_vcpus)
    return server_cpus, subject_cpus


def _resource_sample_matches_allocation(
    allocation: ReplayAllocation,
    sample: Mapping[str, object],
    *,
    legacy_server: str,
    legacy_subject: str,
) -> bool:
    box_vcpus = sample.get("box_vcpus")
    if isinstance(box_vcpus, int) and not isinstance(box_vcpus, bool):
        try:
            server, subject = allocation_cpu_sets(allocation, box_vcpus=box_vcpus)
        except ReplayError:
            return False
        return sample.get("server_cpuset") == format_cpuset(server) and sample.get(
            "subject_cpuset"
        ) == format_cpuset(subject)
    # Receipts written before topology-aware allocation did not retain the box
    # width. Continue to read those historical resource samples; new samples
    # always carry box_vcpus and must match the topology-aware allocation above.
    return (
        sample.get("server_cpuset") == legacy_server
        and sample.get("subject_cpuset") == legacy_subject
    )


def format_cpuset(cpus: Iterable[int]) -> str:
    """Render a cpuset as the compact ``N,M-K`` string docker/the sampler expect.

    Public and shared: evidence_errors above and replay_runtime's resource
    sampler both format a cpuset from the same allocation, and a divergence
    between two copies would silently refuse every calibrated measurement.
    """
    ordered = sorted(set(cpus))
    if not ordered:
        raise ReplayError("cpuset cannot be empty")
    ranges: list[str] = []
    start = previous = ordered[0]
    for cpu in ordered[1:]:
        if cpu == previous + 1:
            previous = cpu
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = cpu
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)
