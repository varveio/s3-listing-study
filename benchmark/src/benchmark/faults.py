"""The fault contract shared by plan, provider, worker, and reports.

Two harness-owned treatments, each declared in a plan and each an identity input:

- A **fault schedule** turns one attempt into two child executions of the same
  subject: the measured mode, SIGKILLed at a stated point, then the capsule's
  declared resume mode against the same sink. Both steps' exit codes, wall
  clocks, peak RSS and logs are recorded, and the capsule may derive a
  ``relisted`` figure from what the two steps left behind.
- A **sidecar** is a process the worker runs between the subject and the replay
  server, started before the subject and stopped after it, whose timeline file
  is published as attempt evidence. The fault-injecting proxy is the one it was
  written for.

Like :mod:`benchmark.replay`, this module only parses and renders documents; the
worker-side lifecycle is :mod:`benchmark.fault_runtime`. The design is
``benchmark/docs/model.md`` § *An attempt may be killed and resumed* and
§ *A sidecar sits between the subject and the replay server*.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from benchmark.contract import canonical_json
from benchmark.replay import REPLAY_ENDPOINT_PORT, REPLAY_ENDPOINT_URL, REPLAY_METRICS_PORT

FAULT_KIND = "kill-resume"
FAULT_SIGNAL = "SIGKILL"
"""The one signal a schedule sends. A process that is SIGKILLed has no chance to
flush, checkpoint, or clean up, which is the recovery claim being measured."""

FAULT_ROW_FIELDS = ("fault_kill_at_s", "fault_kill_percent", "fault_expected_wall_s")
"""Flat row and layer fields, resolving through the same three layers as every
other allocation. Either ``fault_kill_at_s`` alone, or ``fault_kill_percent`` of
``fault_expected_wall_s``."""

SIDECAR_FIELDS = (
    "name",
    "executable_uri",
    "executable_sha256",
    "listen",
    "listen_flag",
    "target_flag",
    "timeline_flag",
    "args",
)
SIDECAR_TARGET_URL = REPLAY_ENDPOINT_URL
"""What a sidecar forwards to. Derived, never stated: the replay server is the
only backend the worker can stand a sidecar in front of."""
SIDECAR_TIMELINE_NAME = "timeline.tsv"
SIDECAR_READINESS_TIMEOUT_S = 30.0
SIDECAR_STOP_GRACE_S = 30.0
"""How long a sidecar gets after SIGTERM to write its timeline and exit."""
SIDECAR_RESERVED_PORTS = frozenset({REPLAY_ENDPOINT_PORT, REPLAY_METRICS_PORT})

_NAME_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,40}\Z")
_HEX64_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_FLAG_RE = re.compile(r"\A--?[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_LISTEN_RE = re.compile(r"\A127\.0\.0\.1:([0-9]{1,5})\Z")
_GCS_RE = re.compile(r"\Ags://[^/\s*?\[\]]+/[^\s*?\[\]]+[^/\s*?\[\]]\Z")
_TOTAL_RE = re.compile(r"\A([a-z][a-z0-9_]*)=(-?[0-9]+)\Z")


class FaultError(ValueError):
    """A fault document is incomplete, non-canonical, or cannot run."""


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FaultError(f"{name} must be a positive integer: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class FaultSchedule:
    """When step one of an attempt is killed. Step two is the capsule's resume mode.

    Stated either as a wall-clock second of the first step, or as a percentage
    of the wall the plan expects that step to take uninterrupted. The second
    form exists because the useful kill point moves with the shape under test
    (a 512-worker listing finishes sooner than a 64-worker one), and stating the
    expectation beside the fraction keeps the derivation reviewable in the plan.
    """

    kill_at_s: int | None = None
    kill_percent: int | None = None
    expected_wall_s: int | None = None

    def __post_init__(self) -> None:
        absolute = self.kill_at_s is not None
        relative = self.kill_percent is not None or self.expected_wall_s is not None
        if absolute == relative:
            raise FaultError(
                "a fault schedule states fault_kill_at_s, or fault_kill_percent together "
                "with fault_expected_wall_s, and not both"
            )
        if absolute:
            _positive_int(self.kill_at_s, "fault_kill_at_s")
            return
        if self.kill_percent is None or self.expected_wall_s is None:
            raise FaultError(
                "fault_kill_percent and fault_expected_wall_s are stated together: "
                "a fraction of nothing is not a kill point"
            )
        percent = _positive_int(self.kill_percent, "fault_kill_percent")
        if percent >= 100:
            raise FaultError(
                f"fault_kill_percent is a point inside the expected wall, 1..99: {percent}"
            )
        _positive_int(self.expected_wall_s, "fault_expected_wall_s")

    @property
    def kill_after_s(self) -> float:
        """Seconds after step one starts at which the worker sends SIGKILL."""
        if self.kill_at_s is not None:
            return float(self.kill_at_s)
        assert self.kill_percent is not None and self.expected_wall_s is not None
        return self.expected_wall_s * self.kill_percent / 100

    def as_dict(self) -> dict[str, Any]:
        """The canonical document: what was stated, never the derived second."""
        document: dict[str, Any] = {"kind": FAULT_KIND, "signal": FAULT_SIGNAL}
        if self.kill_at_s is not None:
            document["kill_at_s"] = self.kill_at_s
        else:
            document["kill_percent"] = self.kill_percent
            document["expected_wall_s"] = self.expected_wall_s
        return document


def make_schedule(values: Mapping[str, object]) -> FaultSchedule | None:
    """The schedule a resolved row states, or ``None`` when it states none."""
    stated = {name: values[name] for name in FAULT_ROW_FIELDS if name in values}
    if not stated:
        return None
    return FaultSchedule(
        kill_at_s=stated.get("fault_kill_at_s"),  # type: ignore[arg-type]
        kill_percent=stated.get("fault_kill_percent"),  # type: ignore[arg-type]
        expected_wall_s=stated.get("fault_expected_wall_s"),  # type: ignore[arg-type]
    )


def parse_schedule(raw: str | Mapping[str, object]) -> FaultSchedule:
    """Parse the canonical schedule document a worker is handed, and refuse any other."""
    document = _document(raw, "fault schedule")
    if document.get("kind") != FAULT_KIND or document.get("signal") != FAULT_SIGNAL:
        raise FaultError(f"fault schedule is not a {FAULT_KIND} by {FAULT_SIGNAL}")
    allowed = {"kind", "signal", "kill_at_s", "kill_percent", "expected_wall_s"}
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise FaultError(f"fault schedule carries unknown key(s): {', '.join(unknown)}")
    schedule = FaultSchedule(
        kill_at_s=document.get("kill_at_s"),
        kill_percent=document.get("kill_percent"),
        expected_wall_s=document.get("expected_wall_s"),
    )
    _require_canonical(raw, schedule.as_dict(), "fault schedule")
    return schedule


@dataclass(frozen=True, slots=True)
class Sidecar:
    """A process the worker stands between the subject and the replay server.

    Named by a digest-pinned executable object rather than an image: the worker
    stages and verifies it, runs it as its own child, and stops it with SIGTERM
    before publishing, which is the only way its timeline can be written in time
    to be part of the attempt's create-only evidence (`model.md`). The harness
    renders the three addresses it owns — where the sidecar listens, what it
    forwards to, and where its timeline goes — through the flags the declaration
    names; ``args`` is everything else, forwarded verbatim and hashed.
    """

    name: str
    executable_uri: str
    executable_sha256: str
    listen: str
    listen_flag: str
    target_flag: str
    timeline_flag: str
    args: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME_RE.fullmatch(self.name):
            raise FaultError(f"sidecar name must be a short lowercase slug: {self.name!r}")
        if not isinstance(self.executable_uri, str) or not _GCS_RE.fullmatch(self.executable_uri):
            raise FaultError(
                f"sidecar executable_uri must name one exact gs:// object: {self.executable_uri!r}"
            )
        if not isinstance(self.executable_sha256, str) or not _HEX64_RE.fullmatch(
            self.executable_sha256
        ):
            raise FaultError("sidecar executable_sha256 must be 64 lowercase hex digits")
        match = _LISTEN_RE.fullmatch(self.listen) if isinstance(self.listen, str) else None
        if match is None:
            raise FaultError(
                f"sidecar listen must be 127.0.0.1:<port> on the job's host network: "
                f"{self.listen!r}"
            )
        port = int(match.group(1))
        if not 1024 <= port <= 65535 or port in SIDECAR_RESERVED_PORTS:
            raise FaultError(
                f"sidecar listen port {port} is outside 1024..65535 or is the replay "
                f"server's own ({sorted(SIDECAR_RESERVED_PORTS)})"
            )
        flags = (self.listen_flag, self.target_flag, self.timeline_flag)
        for flag in flags:
            if not isinstance(flag, str) or not _FLAG_RE.fullmatch(flag):
                raise FaultError(f"sidecar flag must be a plain -flag or --flag: {flag!r}")
        if len(set(flags)) != len(flags):
            raise FaultError("sidecar listen, target, and timeline flags must differ")
        if not isinstance(self.args, tuple) or not all(
            isinstance(arg, str) and arg and "\x00" not in arg for arg in self.args
        ):
            raise FaultError("sidecar args must be a list of non-empty strings")
        harness_owned = set(flags)
        stated = {arg.split("=", 1)[0] for arg in self.args}
        if stated & harness_owned:
            raise FaultError(
                f"sidecar args restate a flag the harness renders: "
                f"{', '.join(sorted(stated & harness_owned))}"
            )

    @property
    def endpoint_url(self) -> str:
        """What the subject is pointed at instead of the replay server."""
        return f"http://{self.listen}"

    def argv(self, executable: str, timeline: str) -> tuple[str, ...]:
        return (
            executable,
            *self.args,
            self.listen_flag,
            self.listen,
            self.target_flag,
            SIDECAR_TARGET_URL,
            self.timeline_flag,
            timeline,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "executable_uri": self.executable_uri,
            "executable_sha256": self.executable_sha256,
            "listen": self.listen,
            "listen_flag": self.listen_flag,
            "target_flag": self.target_flag,
            "timeline_flag": self.timeline_flag,
            "args": list(self.args),
        }


def make_sidecar(block: Mapping[str, object]) -> Sidecar:
    """The plan's ``sidecar:`` block, as written by its author."""
    if not isinstance(block, Mapping):
        raise FaultError("sidecar must be a mapping")
    unknown = sorted(set(block) - set(SIDECAR_FIELDS))
    if unknown:
        raise FaultError(f"sidecar carries unknown key(s): {', '.join(unknown)}")
    missing = sorted(set(SIDECAR_FIELDS) - {"args"} - set(block))
    if missing:
        raise FaultError(f"sidecar is missing required key(s): {', '.join(missing)}")
    args = block.get("args", [])
    if not isinstance(args, list):
        raise FaultError("sidecar args must be a list of non-empty strings")
    return Sidecar(
        name=block["name"],  # type: ignore[arg-type]
        executable_uri=block["executable_uri"],  # type: ignore[arg-type]
        executable_sha256=block["executable_sha256"],  # type: ignore[arg-type]
        listen=block["listen"],  # type: ignore[arg-type]
        listen_flag=block["listen_flag"],  # type: ignore[arg-type]
        target_flag=block["target_flag"],  # type: ignore[arg-type]
        timeline_flag=block["timeline_flag"],  # type: ignore[arg-type]
        args=tuple(args),
    )


def parse_sidecar(raw: str | Mapping[str, object]) -> Sidecar:
    """Parse the canonical sidecar document a worker is handed, and refuse any other."""
    document = _document(raw, "sidecar")
    if set(document) != set(SIDECAR_FIELDS):
        raise FaultError(f"sidecar document must carry exactly {SIDECAR_FIELDS}")
    sidecar = make_sidecar(document)
    _require_canonical(raw, sidecar.as_dict(), "sidecar")
    return sidecar


def timeline_summary(text: str) -> dict[str, Any]:
    """Shape and column totals of a TSV timeline, for a reader who will not open it.

    Generic over any header row followed by integer columns, which is what the
    swath fault proxy writes (``t_ms arrived throttled reset delayed
    max_active``, one row per 100 ms bucket since its first request). A column
    whose cells are not all integers has no total; ``max_*`` columns report
    their maximum rather than a sum, since summing a per-bucket peak means
    nothing.
    """
    lines = [line for line in text.splitlines() if line]
    if not lines:
        return {"columns": [], "rows": 0, "totals": {}}
    columns = lines[0].split("\t")
    rows = [line.split("\t") for line in lines[1:]]
    totals: dict[str, int] = {}
    for index, column in enumerate(columns):
        try:
            values = [int(row[index]) for row in rows]
        except (IndexError, ValueError):
            continue
        if column.startswith("t_"):
            continue
        totals[column] = max(values, default=0) if column.startswith("max_") else sum(values)
    return {"columns": columns, "rows": len(rows), "totals": totals}


def stdout_totals(text: str) -> dict[str, int] | None:
    """The last stdout line as ``key=integer`` totals, or ``None`` when it is not one."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    totals: dict[str, int] = {}
    for token in lines[-1].split():
        match = _TOTAL_RE.fullmatch(token)
        if match is None:
            return None
        totals[match.group(1)] = int(match.group(2))
    return totals or None


def relisted_errors(value: object) -> str | None:
    """Why a capsule's relisted figure is not a flat mapping of finite numbers."""
    if not isinstance(value, Mapping) or not value:
        return "relisted must be a non-empty mapping of name to number"
    for name, number in value.items():
        if not isinstance(name, str) or not name:
            return f"relisted names a figure that is not a string: {name!r}"
        if number is None:
            continue
        if isinstance(number, bool) or not isinstance(number, int | float):
            return f"relisted {name} is not a number: {number!r}"
        if not math.isfinite(number):
            return f"relisted {name} is not finite: {number!r}"
    return None


def _document(raw: str | Mapping[str, object], what: str) -> dict[str, Any]:
    try:
        document = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError) as exc:
        raise FaultError(f"{what} is not a JSON object: {exc}") from None
    if not isinstance(document, dict):
        raise FaultError(f"{what} is not a JSON object")
    return document


def _require_canonical(raw: str | Mapping[str, object], expected: object, what: str) -> None:
    """A string handed across a process boundary must be the canonical rendering."""
    if isinstance(raw, str) and raw != canonical_json(expected):
        raise FaultError(f"{what} document is not canonical JSON")


def documents_in(case_inputs: str) -> tuple[FaultSchedule | None, Sidecar | None]:
    """The two treatments a stored ``case_inputs`` document carries, parsed.

    The worker flags are rendered from here rather than from columns of their
    own: ``case_inputs`` is stored byte-exactly and the hash is over it, so it
    is the one place these values cannot disagree with the identity.
    """
    document = _document(case_inputs, "case_inputs")
    raw_schedule = document.get("fault_schedule")
    raw_sidecar = document.get("sidecar")
    return (
        None if raw_schedule is None else parse_schedule(raw_schedule),
        None if raw_sidecar is None else parse_sidecar(raw_sidecar),
    )


def worker_flags(case_inputs: str) -> Sequence[tuple[str, str]]:
    """The ``--fault-schedule`` and ``--sidecar`` pairs a worker is handed, if any."""
    schedule, sidecar = documents_in(case_inputs)
    pairs: list[tuple[str, str]] = []
    if schedule is not None:
        pairs.append(("--fault-schedule", canonical_json(schedule.as_dict())))
    if sidecar is not None:
        pairs.append(("--sidecar", canonical_json(sidecar.as_dict())))
    return tuple(pairs)
