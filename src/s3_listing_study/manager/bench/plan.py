"""Read one bucket's benchmark plan: ``bench/buckets/<bucket>.yaml``.

A plan says what to run against one bucket and on what box. It is deliberately
*not* the smoke registry: ``data/registry.toml`` holds bucket facts for the
verification lineage — region, key count, reference-manifest binding — and
nothing in the attempt path reads it. A benchmark bucket needs a plan, not a
registration, so a plan is self-contained.

**A plan is intent; a campaign is an execution.** One campaign runs many plans
with its image set frozen, which is why a plan carries no campaign ID and no
image digest. Receipts group under the campaign that produced them.

**Cases are generated, not hand-written.** Each tool declares a ``matrix`` whose
cross-product is the set of cases, so the four swath cases behind "2 GB vs 4 GB,
sorted vs unsorted" are two lines rather than four hand-copied blocks that can
disagree. Case IDs are derived from the axis values for the same reason: a
hand-typed ID is a hand-typed opportunity to file one case's attempt under
another case's name.

**The derived ID is a path, not an identity.** Adding an axis later changes
every ID a tool generates, so an ID cannot be what says "these attempts are the
same case". :attr:`Case.fingerprint` is — a digest over the resolved case, which
survives ID scheme changes and, more importantly, refuses the reverse mistake:
editing a matrix value while the derived ID happens to land the same would
otherwise append non-comparable runs into one case directory. ``reps`` is
excluded from it because how many times we ran something is not part of what we
ran; ``timeout_s`` is included because it can truncate a run and therefore
change the result.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Bumped only when a plan written for an older reader would be misread by this
# one. Unknown versions are refused rather than best-effort parsed.
SPEC_VERSION = 1

# Versioned separately from the spec: the fingerprint function is part of the
# on-disk contract the append guard enforces, so changing how it is computed is
# a migration and must be visible as one.
FINGERPRINT_VERSION = 1

TOP_LEVEL = ("spec_version", "bucket", "region", "defaults", "tools", "exclude")

# What a box is. Every field is required once resolved: a case that did not say
# how much memory it wanted cannot be compared against one that did.
RESOURCE_FIELDS = ("machine_type", "memory_mib", "cpu_milli")

# Scheduling, not allocation. Settable at plan and tool level, never an axis —
# varying reps does not make a different case, and a timeout swept as an axis is
# a symptom of not knowing how long the run takes.
SCHEDULE_FIELDS = ("reps", "timeout_s")

# ``mode`` first so it leads every derived ID; the rest are sorted, so the ID is
# a function of the axis *set* and not of the order someone typed them in.
AXIS_FIELDS = ("mode", *RESOURCE_FIELDS)

TOOL_FIELDS = ("matrix", "resources", *SCHEDULE_FIELDS)

# Anchored with ``\Z`` and applied with ``fullmatch``: ``$`` also matches before
# a trailing newline, and a case ID is used as a directory name.
CASE_ID_RE = re.compile(r"\A[a-z0-9][a-z0-9._-]{0,80}\Z")
TOOL_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,40}\Z")


class PlanError(Exception):
    """A plan is unreadable, malformed, or does not carry what was asked."""


@dataclass(frozen=True)
class Resources:
    """The box a case asks for. What it actually got is recorded by the worker."""

    machine_type: str
    memory_mib: int
    cpu_milli: int

    def as_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in RESOURCE_FIELDS}


@dataclass(frozen=True)
class Case:
    """One resolved cell of a tool's matrix — the unit a campaign submits."""

    tool: str
    case_id: str
    mode: str
    resources: Resources
    reps: int
    timeout_s: int
    # The axis values that generated this case, in ID order. Kept so a reader
    # can group by an axis without re-parsing the ID it was rendered into.
    axes: tuple[tuple[str, str | int], ...]
    fingerprint: str


@dataclass(frozen=True)
class Exclusion:
    """A tool deliberately not run against this bucket, and why."""

    tool: str
    reason: str


@dataclass(frozen=True)
class Plan:
    """One bucket's plan, plus the bytes it was read from."""

    path: Path
    digest: str
    bucket: str
    region: str
    cases: tuple[Case, ...]
    exclusions: tuple[Exclusion, ...]

    @classmethod
    def load(cls, path: Path) -> Plan:
        return _load(path)

    def tools(self) -> list[str]:
        """Every tool with at least one case, in plan order."""
        seen: dict[str, None] = {}
        for case in self.cases:
            seen.setdefault(case.tool, None)
        return list(seen)

    def declared(self) -> set[str]:
        """Every tool the plan has an opinion about — run or excluded."""
        return {case.tool for case in self.cases} | {e.tool for e in self.exclusions}

    def cases_for(self, tool: str) -> tuple[Case, ...]:
        return tuple(case for case in self.cases if case.tool == tool)


def buckets_dir() -> Path:
    """``bench/buckets`` at the repo root."""
    return Path(__file__).resolve().parents[4] / "bench" / "buckets"


def default_path(bucket: str) -> Path:
    return buckets_dir() / f"{bucket}.yaml"


def _load(path: Path) -> Plan:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PlanError(f"plan not readable: {path} ({exc.strerror})") from None
    try:
        doc = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise PlanError(f"plan is not valid YAML: {path}: {exc}") from None
    if not isinstance(doc, dict):
        raise PlanError(f"plan {path} is not a mapping")

    _reject_unknown(doc, TOP_LEVEL, "plan", path)
    _require_version(doc, path)

    bucket = _string(doc, "bucket", "plan", path)
    # The filename is the bucket's name, so a plan that disagrees with its own
    # path would be found by one name and cite another.
    if path.suffix in (".yaml", ".yml") and path.stem != bucket:
        raise PlanError(f"plan {path} declares bucket {bucket!r} but is named {path.stem!r}")

    defaults = _table(doc, "defaults", "defaults", path)
    _reject_unknown(defaults, ("resources", *SCHEDULE_FIELDS), "[defaults]", path)
    base_resources = _resources(defaults, "defaults", path, complete=True)
    base_schedule = _schedule(defaults, "defaults", path, complete=True)

    plan = Plan(
        path=path,
        digest=hashlib.sha256(raw).hexdigest(),
        bucket=bucket,
        region=_string(doc, "region", "plan", path),
        cases=_cases(
            doc, bucket, _string(doc, "region", "plan", path), base_resources, base_schedule, path
        ),
        exclusions=_exclusions(doc, path),
    )
    _reject_overlap(plan, path)
    return plan


def _require_version(doc: dict[str, Any], path: Path) -> None:
    version = doc.get("spec_version")
    if version != SPEC_VERSION:
        raise PlanError(
            f"plan {path} has spec_version {version!r}, this reader supports {SPEC_VERSION}"
        )


def _reject_unknown(
    table: Mapping[str, Any], allowed: Sequence[str], where: str, path: Path
) -> None:
    unknown = sorted(set(table) - set(allowed))
    if unknown:
        raise PlanError(
            f"{where} in {path} carries unknown key(s) "
            f"{', '.join(repr(k) for k in unknown)} ({'|'.join(allowed)})"
        )


def _table(doc: Mapping[str, Any], key: str, where: str, path: Path) -> dict[str, Any]:
    value = doc.get(key)
    if value is None:
        raise PlanError(f"plan {path} has no '{where}' mapping")
    if not isinstance(value, dict):
        raise PlanError(f"'{where}' in {path} is not a mapping")
    return value


def _string(table: Mapping[str, Any], key: str, where: str, path: Path) -> str:
    value = table.get(key)
    if value is None:
        raise PlanError(f"'{where}' in {path} has no '{key}'")
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"'{where}' '{key}' in {path} is not a non-empty string")
    return value


def _positive_int(value: object, key: str, where: str, path: Path) -> int:
    # ``isinstance(True, int)`` is True, and YAML turns a bare ``yes`` into a
    # bool: without this a typo becomes memory_mib=1.
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PlanError(f"'{where}' '{key}' in {path} is not a positive integer: {value!r}")
    return value


def _resources(
    table: Mapping[str, Any], where: str, path: Path, *, complete: bool
) -> dict[str, Any]:
    """The resource keys ``table`` states. ``complete`` demands all of them."""
    raw = table.get("resources")
    if raw is None:
        if complete:
            raise PlanError(f"'{where}' in {path} has no 'resources'")
        return {}
    if not isinstance(raw, dict):
        raise PlanError(f"'{where}' 'resources' in {path} is not a mapping")
    _reject_unknown(raw, RESOURCE_FIELDS, f"'{where}' resources", path)
    if complete:
        missing = sorted(set(RESOURCE_FIELDS) - set(raw))
        if missing:
            raise PlanError(
                f"'{where}' resources in {path} is missing {', '.join(missing)} "
                "(defaults must be complete so every case resolves)"
            )
    resolved: dict[str, Any] = {}
    for field in RESOURCE_FIELDS:
        if field not in raw:
            continue
        resolved[field] = (
            _string(raw, field, f"{where} resources", path)
            if field == "machine_type"
            else _positive_int(raw[field], field, f"{where} resources", path)
        )
    return resolved


def _schedule(
    table: Mapping[str, Any], where: str, path: Path, *, complete: bool
) -> dict[str, int]:
    resolved: dict[str, int] = {}
    for field in SCHEDULE_FIELDS:
        if field not in table:
            if complete:
                raise PlanError(f"'{where}' in {path} has no '{field}'")
            continue
        resolved[field] = _positive_int(table[field], field, where, path)
    return resolved


def _cases(
    doc: Mapping[str, Any],
    bucket: str,
    region: str,
    base_resources: Mapping[str, Any],
    base_schedule: Mapping[str, int],
    path: Path,
) -> tuple[Case, ...]:
    tools = _table(doc, "tools", "tools", path)
    if not tools:
        raise PlanError(f"plan {path} runs no tools")
    cases: list[Case] = []
    for tool in tools:
        if not TOOL_RE.fullmatch(tool):
            raise PlanError(f"plan {path} has a malformed tool name: {tool!r}")
        cases.extend(_tool_cases(tool, tools, bucket, region, base_resources, base_schedule, path))
    return tuple(cases)


def _tool_cases(
    tool: str,
    tools: Mapping[str, Any],
    bucket: str,
    region: str,
    base_resources: Mapping[str, Any],
    base_schedule: Mapping[str, int],
    path: Path,
) -> list[Case]:
    where = f"tools.{tool}"
    table = _table(tools, tool, where, path)
    _reject_unknown(table, TOOL_FIELDS, f"'{where}'", path)

    # Cascade is shallow and per-key, over a flat table of scalars: there is no
    # nesting for a deep-merge surprise to hide in.
    resources = {**base_resources, **_resources(table, where, path, complete=False)}
    schedule = {**base_schedule, **_schedule(table, where, path, complete=False)}

    axes = _matrix(table, where, path)
    ordered = [axis for axis in AXIS_FIELDS if axis in axes]
    cases: list[Case] = []
    for combination in itertools.product(*(axes[axis] for axis in ordered)):
        chosen = tuple(zip(ordered, combination, strict=True))
        case_resources = {**resources, **{k: v for k, v in chosen if k in RESOURCE_FIELDS}}
        mode = str(dict(chosen)["mode"])
        cases.append(_case(tool, mode, chosen, case_resources, schedule, bucket, region, path))
    return cases


def _matrix(table: Mapping[str, Any], where: str, path: Path) -> dict[str, list[str | int]]:
    matrix = _table(table, "matrix", f"{where}.matrix", path)
    _reject_unknown(matrix, AXIS_FIELDS, f"'{where}.matrix'", path)
    if "mode" not in matrix:
        raise PlanError(f"'{where}.matrix' in {path} has no 'mode' axis")
    axes: dict[str, list[str | int]] = {}
    for axis, values in matrix.items():
        if not isinstance(values, list) or not values:
            raise PlanError(f"'{where}.matrix' '{axis}' in {path} is not a non-empty list")
        checked: list[str | int] = []
        for value in values:
            if axis == "mode" or axis == "machine_type":
                if not isinstance(value, str) or not value.strip():
                    raise PlanError(
                        f"'{where}.matrix' '{axis}' in {path} has a non-string value: {value!r}"
                    )
                checked.append(value)
            else:
                checked.append(_positive_int(value, axis, f"{where}.matrix", path))
        # A repeated value would silently generate two identical cases whose IDs
        # collide, so the second would append into the first's directory.
        if len(set(checked)) != len(checked):
            raise PlanError(f"'{where}.matrix' '{axis}' in {path} repeats a value")
        axes[axis] = checked
    return axes


def _case(
    tool: str,
    mode: str,
    chosen: tuple[tuple[str, str | int], ...],
    resources: Mapping[str, Any],
    schedule: Mapping[str, int],
    bucket: str,
    region: str,
    path: Path,
) -> Case:
    resolved = Resources(
        machine_type=str(resources["machine_type"]),
        memory_mib=int(resources["memory_mib"]),
        cpu_milli=int(resources["cpu_milli"]),
    )
    case_id = derive_case_id(chosen)
    if not CASE_ID_RE.fullmatch(case_id):
        raise PlanError(
            f"'tools.{tool}' in {path} generates the unusable case id {case_id!r} "
            "(axis values must be lowercase, digits, '.', '_' or '-')"
        )
    return Case(
        tool=tool,
        case_id=case_id,
        mode=mode,
        resources=resolved,
        reps=schedule["reps"],
        timeout_s=schedule["timeout_s"],
        axes=chosen,
        fingerprint=fingerprint(
            bucket=bucket,
            region=region,
            tool=tool,
            mode=mode,
            resources=resolved,
            timeout_s=schedule["timeout_s"],
        ),
    )


def derive_case_id(chosen: Iterable[tuple[str, str | int]]) -> str:
    """``recursive-parquet.memory_mib-2048`` — the mode, then each varied axis.

    Every axis the matrix declares appears, including one that happens to hold a
    single value: dropping it would make the ID mean "whatever the default was
    at the time", which nothing later can recover.
    """
    segments: list[str] = []
    for axis, value in chosen:
        segments.append(str(value) if axis == "mode" else f"{axis}-{value}")
    return ".".join(segments)


def fingerprint(
    *,
    bucket: str,
    region: str,
    tool: str,
    mode: str,
    resources: Resources,
    timeout_s: int,
) -> str:
    """A digest over the resolved case — what makes two attempts comparable."""
    payload = {
        "fingerprint_version": FINGERPRINT_VERSION,
        "bucket": bucket,
        "region": region,
        "tool": tool,
        "mode": mode,
        "resources": resources.as_dict(),
        "timeout_s": timeout_s,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _exclusions(doc: Mapping[str, Any], path: Path) -> tuple[Exclusion, ...]:
    raw = doc.get("exclude")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PlanError(f"'exclude' in {path} is not a list")
    exclusions: list[Exclusion] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise PlanError(f"'exclude' in {path} has a non-mapping entry: {entry!r}")
        _reject_unknown(entry, ("tool", "reason"), "'exclude' entry", path)
        # A reason is required because the alternative is a roster that shrinks
        # over time with nobody able to say why any given tool left it.
        exclusions.append(
            Exclusion(
                tool=_string(entry, "tool", "exclude", path),
                reason=_string(entry, "reason", "exclude", path),
            )
        )
    return tuple(exclusions)


def _reject_overlap(plan: Plan, path: Path) -> None:
    both = sorted({case.tool for case in plan.cases} & {e.tool for e in plan.exclusions})
    if both:
        raise PlanError(f"plan {path} both runs and excludes {', '.join(both)}")
    seen: set[tuple[str, str]] = set()
    for case in plan.cases:
        key = (case.tool, case.case_id)
        if key in seen:
            raise PlanError(f"plan {path} generates {case.tool} case {case.case_id!r} twice")
        seen.add(key)


def check_roster(plan: Plan, registered: Collection[str]) -> None:
    """Every registered tool must be run or excluded — silence is not a decision.

    Kept out of :func:`Plan.load` because it needs the repository's tool set,
    and a plan should be readable and testable without one.
    """
    undeclared = sorted(set(registered) - plan.declared())
    if undeclared:
        raise PlanError(
            f"plan {plan.path} does not mention {', '.join(undeclared)} — "
            "list each under 'tools' or 'exclude' with a reason"
        )
    unknown = sorted(plan.declared() - set(registered))
    if unknown:
        raise PlanError(
            f"plan {plan.path} mentions unregistered tool(s) {', '.join(unknown)} "
            "(no build/image.json)"
        )


def check_modes(plan: Plan, modes_by_tool: Mapping[str, Collection[str]]) -> None:
    """Refuse a mode the tool's adapter does not implement, before submitting."""
    for case in plan.cases:
        known = modes_by_tool.get(case.tool)
        if known is None:
            raise PlanError(f"plan {plan.path} names {case.tool}, whose modes are unknown")
        if case.mode not in known:
            raise PlanError(
                f"plan {plan.path}: {case.tool} has no mode {case.mode!r} "
                f"({'|'.join(sorted(known))})"
            )
