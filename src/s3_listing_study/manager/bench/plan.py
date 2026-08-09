"""Read one bucket's benchmark plan: ``bench/buckets/<bucket>.yaml``.

A plan says what to run against one bucket and on what box. It is deliberately
*not* the smoke registry: ``data/registry.toml`` holds bucket facts for the
verification lineage — region, key count, reference-manifest binding — and
nothing in the attempt path reads it. A benchmark bucket needs a plan, not a
registration, so a plan is self-contained.

**A plan is intent; a campaign is an execution.** One campaign runs many plans
with its image set frozen, which is why a plan carries no campaign ID and no
image digest. Receipts group under the campaign that produced them.

**A tool with an empty body runs once**, at the mode ``bench/tools.yaml``
records for it, on the plan's own allocation. That is what most tools want and
it says nothing a plan needs to restate, so the name alone declares it.

**Cases are generated, not hand-written.** A tool wanting more declares a
``matrix`` whose
cross-product is the set of cases, so "2 GB vs 4 GB, sorted vs unsorted" is two
lines rather than four hand-copied blocks that can disagree. Case IDs are
derived from the axis values for the same reason: a hand-typed ID is a
hand-typed opportunity to file one case's attempt under another case's name.

A tool may state several blocks and take their union, because one cross-product
forces every mode to take every value of every axis — wrong as soon as one mode
needs an allocation its siblings do not. A block carries its own ``resources``
override for exactly that, and every block must declare the same axis names so
one tool's IDs keep their shape.

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
    def load(cls, path: Path, *, default_modes: Mapping[str, str] | None = None) -> Plan:
        """Read a plan. ``default_modes`` defaults to ``bench/tools.yaml``."""
        return _load(path, default_modes)

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


def bench_dir() -> Path:
    """``bench`` at the repo root."""
    return Path(__file__).resolve().parents[4] / "bench"


def buckets_dir() -> Path:
    """``bench/buckets`` at the repo root."""
    return bench_dir() / "buckets"


def default_path(bucket: str) -> Path:
    return buckets_dir() / f"{bucket}.yaml"


def load_default_modes(path: Path) -> dict[str, str]:
    """Read ``bench/tools.yaml`` — the mode a sampled tool runs."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PlanError(f"tool defaults not readable: {path} ({exc.strerror})") from None
    try:
        doc = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise PlanError(f"tool defaults are not valid YAML: {path}: {exc}") from None
    if not isinstance(doc, dict):
        raise PlanError(f"tool defaults {path} is not a mapping")
    _reject_unknown(doc, ("spec_version", "default_modes"), "tool defaults", path)
    if doc.get("spec_version") != SPEC_VERSION:
        raise PlanError(
            f"tool defaults {path} has spec_version {doc.get('spec_version')!r}, "
            f"this reader supports {SPEC_VERSION}"
        )
    table = _table(doc, "default_modes", "default_modes", path)
    if not table:
        raise PlanError(f"tool defaults {path} names no tools")
    return {tool: _string(table, tool, "default_modes", path) for tool in table}


def _load(path: Path, default_modes: Mapping[str, str] | None) -> Plan:
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

    region = _string(doc, "region", "plan", path)
    # Resolved next to the plan rather than passed down from the caller, so a
    # plan read from anywhere still means the same thing.
    modes = default_modes if default_modes is not None else _sibling_default_modes(doc, path)

    plan = Plan(
        path=path,
        digest=hashlib.sha256(raw).hexdigest(),
        bucket=bucket,
        region=region,
        cases=_cases(doc, bucket, region, base_resources, base_schedule, modes, path),
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


def _says_nothing(body: object) -> bool:
    """``aws-cli:`` with no body — the tool adds nothing to the plan's defaults."""
    return body is None or (isinstance(body, dict) and not body)


def _sibling_default_modes(doc: Mapping[str, Any], path: Path) -> Mapping[str, str]:
    """``bench/tools.yaml`` beside the plan's directory — read only if needed."""
    tools = doc.get("tools")
    if not isinstance(tools, dict) or not any(_says_nothing(body) for body in tools.values()):
        return {}
    return load_default_modes(path.resolve().parents[1] / "tools.yaml")


def _default_body(tool: str, default_modes: Mapping[str, str], path: Path) -> dict[str, Any]:
    """What an empty tool means: one case, at the mode recorded for that tool.

    A tool that runs once, at its usual mode, on the plan's own allocation says
    nothing a plan needs to spell out — so writing the name and stopping is the
    whole declaration.
    """
    mode = default_modes.get(tool)
    if mode is None:
        known = "|".join(sorted(default_modes)) or "none"
        raise PlanError(
            f"'tools.{tool}' in {path} is empty, but {tool} has no default mode "
            f"in bench/tools.yaml ({known}) — give it a matrix or record its mode"
        )
    return {"matrix": {"mode": [mode]}}


def _cases(
    doc: Mapping[str, Any],
    bucket: str,
    region: str,
    base_resources: Mapping[str, Any],
    base_schedule: Mapping[str, int],
    default_modes: Mapping[str, str],
    path: Path,
) -> tuple[Case, ...]:
    declared = _table(doc, "tools", "tools", path)
    if not declared:
        raise PlanError(f"plan {path} runs no tools")
    tools: dict[str, Any] = {}
    for tool, body in declared.items():
        if not TOOL_RE.fullmatch(tool):
            raise PlanError(f"plan {path} has a malformed tool name: {tool!r}")
        tools[tool] = _default_body(tool, default_modes, path) if _says_nothing(body) else body
    cases: list[Case] = []
    for tool in tools:
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

    cases: list[Case] = []
    for block in _matrix_blocks(table, where, path):
        # Block resources sit between the tool and the axes: a block is how one
        # group of modes says it needs a different box from its siblings.
        block_resources = {**resources, **block.resources}
        ordered = [axis for axis in AXIS_FIELDS if axis in block.axes]
        for combination in itertools.product(*(block.axes[axis] for axis in ordered)):
            chosen = tuple(zip(ordered, combination, strict=True))
            case_resources = {
                **block_resources,
                **{k: v for k, v in chosen if k in RESOURCE_FIELDS},
            }
            mode = str(dict(chosen)["mode"])
            cases.append(_case(tool, mode, chosen, case_resources, schedule, bucket, region, path))
    return cases


@dataclass(frozen=True)
class _Block:
    """One cross-product, plus the allocation it overrides for its own cases."""

    axes: dict[str, list[str | int]]
    resources: dict[str, Any]


def _matrix_blocks(table: Mapping[str, Any], where: str, path: Path) -> list[_Block]:
    """One block, or several when modes do not all want the same sweep.

    A single cross-product forces every mode to take every value of every axis.
    That is wrong as soon as one mode needs a different allocation from its
    siblings — sorting spills to disk where a streaming write does not — so a
    tool may state several blocks and take their union.

    Every block must declare the same axis *names*, so that one tool's case IDs
    stay the same shape and remain comparable. The values are what differ.
    """
    raw = table.get("matrix")
    if raw is None:
        raise PlanError(f"'{where}' in {path} has no 'matrix'")
    entries = raw if isinstance(raw, list) else [raw]
    if not entries:
        raise PlanError(f"'{where}.matrix' in {path} is an empty list")

    blocks: list[_Block] = []
    for index, entry in enumerate(entries):
        label = f"{where}.matrix" if not isinstance(raw, list) else f"{where}.matrix[{index}]"
        if not isinstance(entry, dict):
            raise PlanError(f"'{label}' in {path} is not a mapping")
        _reject_unknown(entry, (*AXIS_FIELDS, "resources"), f"'{label}'", path)
        blocks.append(
            _Block(
                axes=_axes(entry, label, path),
                resources=_resources(entry, label, path, complete=False),
            )
        )

    names = {frozenset(block.axes) for block in blocks}
    if len(names) > 1:
        shapes = " vs ".join(sorted("+".join(sorted(n)) for n in names))
        raise PlanError(
            f"'{where}.matrix' in {path} mixes axis sets ({shapes}) — every block must "
            "declare the same axes so the tool's case ids stay comparable"
        )
    return blocks


def _axes(entry: Mapping[str, Any], where: str, path: Path) -> dict[str, list[str | int]]:
    if "mode" not in entry:
        raise PlanError(f"'{where}' in {path} has no 'mode' axis")
    axes: dict[str, list[str | int]] = {}
    for axis, values in entry.items():
        if axis == "resources":
            continue
        if not isinstance(values, list) or not values:
            raise PlanError(f"'{where}' '{axis}' in {path} is not a non-empty list")
        checked: list[str | int] = []
        for value in values:
            if axis in ("mode", "machine_type"):
                if not isinstance(value, str) or not value.strip():
                    raise PlanError(
                        f"'{where}' '{axis}' in {path} has a non-string value: {value!r}"
                    )
                checked.append(value)
            else:
                checked.append(_positive_int(value, axis, where, path))
        # A repeated value would silently generate two identical cases whose IDs
        # collide, so the second would append into the first's directory.
        if len(set(checked)) != len(checked):
            raise PlanError(f"'{where}' '{axis}' in {path} repeats a value")
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
