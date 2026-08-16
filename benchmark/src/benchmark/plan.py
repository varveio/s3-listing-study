"""Read one bucket's benchmark plan: ``benchmark/plans/buckets/<bucket>.yaml``.

A plan says what to run against one bucket and on what box. It is deliberately
*not* the smoke registry: ``data/registry.toml`` holds bucket facts for the
verification lineage — region, key count, reference-manifest binding — and
nothing in the attempt path reads it. A benchmark bucket needs a plan, not a
registration, so a plan is self-contained.

**A plan is intent; a campaign is an execution.** One campaign runs many plans
with its image set frozen, which is why a plan carries no campaign ID and no
image digest. Receipts group under the campaign that produced them.

**A tool that names no mode runs once**, at the mode ``benchmark/plans/tools.yaml``
records for it, on the plan's own allocation. That is what most tools want and
it says nothing a plan needs to restate, so the name alone declares it — and a
tool that only re-allocates keeps the default mode rather than having to write
its cases out to say so.

**A plan has two shapes: a layer and a row.** A *row* — one entry in a tool's
``cases`` — states what one case **is**: ``mode``, the stratum, the allocation.
A *layer* — ``defaults``, or a tool's own body — states what every case under it
inherits: the stratum and allocation again, plus the schedule, and never
``mode``. Both draw on one flat vocabulary, so a tool body is ``defaults`` plus
``cases``.

A row may carry only keys the ID and the fingerprint can *both* see, which is
what keeps ``timeout_s`` out of one: it is in the fingerprint but not the ID, so
two rows differing only there would render one ID and two fingerprints — two
non-comparable runs filed into one case directory.

**Cases are an ordered union.** Each entry is either one literal row or an
explicit product generator with an optional atomic zip factor. Generators
expand to ordinary rows before inheritance. Rows may be ragged: a row states
what differs and inherits the rest. IDs are still derived from the *union* of
the keys a tool's expanded rows state, so a row that omitted one renders the
value it inherited and one tool's IDs keep one shape.

**The box and the process are different questions.** ``vcpus``/``memory_gb``
buy a machine; ``container_memory_gb`` is a cgroup ceiling on top of it, via
``docker run --memory``, and is the only figure here a running program is known
to feel — Batch documents its per-task ``memoryMib`` as a scheduling input and
says nothing about enforcing it.
Sweeping the ceiling therefore holds the machine, its cores and its neighbours
still, and reaches sizes no machine type sells. A managed runtime is additionally
told what share of that it may use as heap, because a JVM and V8 both default to
a fraction of what they can see; that share lives with the heap policies in
``benchmark/plans/tools.yaml`` rather than in a plan, since it configures two tools out of
eleven and every plan would otherwise restate a figure most cases ignore.

**The derived ID is a path, not an identity.** Adding a key to one row changes
every ID that tool generates, so an ID cannot be what says "these attempts are the
same case". :attr:`Case.fingerprint` is — a digest over the resolved case, which
survives ID scheme changes and, more importantly, refuses the reverse mistake:
editing a row's value while the derived ID happens to land the same would
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

# Bumped only when a file written for an older reader would be misread by this
# one. Unknown versions are refused rather than best-effort parsed. One number
# for the whole `benchmark/plans/` set, since the reader loads all three files together.
#
# 2: `matrix` became `cases`, a list of rows; the allocation stopped nesting
# under `resources`. Product/zip later extended `cases` into an ordered union,
# without a bump because an older v2 reader fails closed on the unknown
# `product` key rather than misreading the generator as a row. A v1 plan is
# refused rather than reinterpreted.
SPEC_VERSION = 2

# Versioned separately from the spec: the fingerprint function is part of the
# on-disk contract the append guard will enforce, so changing how it is computed
# is a migration and must be visible as one.
#
# Nothing reads a fingerprint yet — `resolve-plan` emits one and the attempt
# store that would refuse a mismatched append is not written. Until it is, this
# number is a promise rather than a check.
FINGERPRINT_VERSION = 1

TOP_LEVEL = ("spec_version", "bucket", "region", "defaults", "tools", "exclude")

# What a box is, in the terms a plan states it: shape, not product name. The
# machine type is resolved from the pair through benchmark/plans/instances.yaml, so a plan
# never names a provider's catalogue and a new generation is one edit there.
BOX_FIELDS = ("vcpus", "memory_gb")

# What the process gets, which is not the same question. A real cgroup ceiling
# via `docker run --memory`, so it is the only figure here a running program is
# known to feel; Batch documents its per-task memoryMib as a scheduling input
# and says nothing about enforcing it. Absent means unconstrained — the
# container sees the whole box. Unlike a heap share, this means something to
# every tool.
PROCESS_FIELDS = ("container_memory_gb",)

# Required once resolved: a case that did not say how much memory it wanted
# cannot be compared against one that did. The container ceiling is the
# exception, because "no ceiling" is a real and different answer.
REQUIRED_RESOURCE_FIELDS = BOX_FIELDS

RESOURCE_FIELDS = (*BOX_FIELDS, *PROCESS_FIELDS)

# Scheduling, not allocation. Settable on a layer, never in a row — varying reps
# does not make a different case, and a timeout is in the fingerprint but not the
# ID, so two rows differing only there would file non-comparable runs into one
# directory.
SCHEDULE_FIELDS = ("reps", "timeout_s")

# Whether the request is signed, not whether the bucket is private: every target
# is public and four of the eleven tools have no unsigned path, so a mixed roster
# would compare listing against listing-plus-signing-1,000-requests. A stratum is
# an identity too — an authenticated case runs as the service account that may
# read the credential (infra/.../aws-credentials.tf).
AUTH_CHOICES = ("anonymous", "authenticated")

# What a row may state: what one case *is*. `mode` first so it leads every
# derived ID; the rest follow in this order, so an ID is a function of the key
# set and not of the order someone typed them in.
ROW_FIELDS = ("mode", "auth", *RESOURCE_FIELDS)

# What a layer may state: what every case under it inherits. No `mode` — eleven
# tools have eleven mode vocabularies, so nothing above a row has one to state.
# `auth` is here as well as in a row: a campaign usually runs one stratum, and
# saying so once beats repeating it on every line.
LAYER_FIELDS = ("auth", *RESOURCE_FIELDS, *SCHEDULE_FIELDS)

TOOL_FIELDS = ("cases", *LAYER_FIELDS)

# Anchored with ``\Z`` and applied with ``fullmatch``: ``$`` also matches before
# a trailing newline, and a case ID is used as a directory name.
CASE_ID_RE = re.compile(r"\A[a-z0-9][a-z0-9._-]{0,80}\Z")
TOOL_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,40}\Z")


class PlanError(Exception):
    """A plan is unreadable, malformed, or does not carry what was asked."""


@dataclass(frozen=True)
class Resources:
    """The box a case asks for, and what the process on it may use."""

    vcpus: int
    memory_gb: int
    # Resolved from the pair above, never stated by a plan. Carried because it is
    # what Batch is actually told, so a receipt can cite the box rather than the
    # request that implied it.
    machine_type: str
    # None means no ceiling: the container sees the whole box.
    container_memory_gb: int | None

    @property
    def visible_memory_gb(self) -> int:
        """What a program running here can actually use."""
        return self.container_memory_gb or self.memory_gb

    @property
    def memory_mib(self) -> int:
        return self.memory_gb * 1024

    @property
    def cpu_milli(self) -> int:
        return self.vcpus * 1000

    @property
    def docker_options(self) -> tuple[str, ...]:
        """`docker run` flags, for Batch's container `options` and the local path.

        ``--memory-swap`` is pinned to ``--memory`` deliberately: left alone,
        Docker permits swap up to twice the limit, so a run that "fits in 2 GB"
        might have fitted in 2 GB of RAM plus 2 GB of disk.
        """
        if self.container_memory_gb is None:
            return ()
        return (
            f"--memory={self.container_memory_gb}g",
            f"--memory-swap={self.container_memory_gb}g",
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            **{field: getattr(self, field) for field in RESOURCE_FIELDS},
            "machine_type": self.machine_type,
        }


@dataclass(frozen=True)
class Case:
    """One resolved row of a tool's ``cases`` — the unit a campaign submits."""

    tool: str
    case_id: str
    mode: str
    auth: str
    resources: Resources
    reps: int
    timeout_s: int
    # The values this case was rendered into an ID from, in ID order: the union
    # of the keys the tool's rows state, so a row that omitted one carries the
    # value it inherited. Kept so a reader can group by a key without re-parsing
    # the ID. ``None`` is the container ceiling nobody set.
    axes: tuple[tuple[str, str | int | None], ...]
    # What the runtime must be told about its own memory, if it is the kind that
    # needs telling. Empty for a tool with no managed heap.
    env: tuple[tuple[str, str], ...]
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
    def load(
        cls,
        path: Path,
        *,
        default_modes: Mapping[str, str] | None = None,
        instances: Mapping[tuple[int, int], str] | None = None,
        heap: HeapConfig | None = None,
    ) -> Plan:
        """Read a plan; tables default to files under ``benchmark/plans/``."""
        return _load(path, default_modes, instances, heap)

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
    """The declarative plan directory inside the benchmark boundary.

    The package lives at ``benchmark/src/benchmark/``; the plans it reads sit
    beside that source root at ``benchmark/plans/``, so this climbs out of
    ``src/`` rather than looking inside the package.
    """
    return Path(__file__).resolve().parents[2] / "plans"


def buckets_dir() -> Path:
    """``benchmark/plans/buckets`` at the repo root."""
    return bench_dir() / "buckets"


def default_path(bucket: str) -> Path:
    return buckets_dir() / f"{bucket}.yaml"


@dataclass(frozen=True)
class HeapPolicy:
    """How one runtime is told how much of its memory it may use as heap.

    Only a managed runtime needs this. A Go or Rust tool takes what it takes;
    a JVM and V8 both default to a *fraction* of what they can see, so leaving
    it alone would make the runtime's own heuristic the independent variable
    rather than the memory we set.
    """

    env: str
    # ``{percent}`` and ``{mib}`` are the two shapes a runtime accepts: the JVM
    # reads its cgroup ceiling itself and wants a proportion, V8 does not and
    # wants an absolute size.
    value: str

    def render(self, *, percent: int, visible_memory_gb: int) -> tuple[str, str]:
        return self.env, self.value.format(
            percent=percent, mib=visible_memory_gb * 1024 * percent // 100
        )


@dataclass(frozen=True)
class HeapConfig:
    """The share a managed runtime may use, and how each one is told.

    Not part of a plan: nine of the eleven tools have no heap to size, so a
    per-bucket setting would be a knob most cases ignore and every plan restates.
    """

    percent: int
    policies: Mapping[str, HeapPolicy]

    def env_for(self, tool: str, *, visible_memory_gb: int) -> tuple[tuple[str, str], ...]:
        policy = self.policies.get(tool)
        if policy is None:
            return ()
        return (policy.render(percent=self.percent, visible_memory_gb=visible_memory_gb),)


def load_heap_config(path: Path) -> HeapConfig:
    """Read the ``heap`` table of ``benchmark/plans/tools.yaml``."""
    doc = _tool_defaults_document(path)
    heap = doc.get("heap")
    if heap is None:
        return HeapConfig(percent=100, policies={})
    if not isinstance(heap, dict):
        raise PlanError(f"'heap' in {path} is not a mapping")
    _reject_unknown(heap, ("percent", "tools"), "'heap'", path)

    percent = _positive_int(heap.get("percent"), "percent", "heap", path)
    if percent > 100:
        raise PlanError(f"'heap' percent in {path} is {percent}, which is over 100")

    table = heap.get("tools")
    if table is None:
        return HeapConfig(percent=percent, policies={})
    if not isinstance(table, dict):
        raise PlanError(f"'heap.tools' in {path} is not a mapping")
    policies: dict[str, HeapPolicy] = {}
    for tool, entry in table.items():
        where = f"heap.tools.{tool}"
        if not isinstance(entry, dict):
            raise PlanError(f"'{where}' in {path} is not a mapping")
        _reject_unknown(entry, ("env", "value"), f"'{where}'", path)
        value = _string(entry, "value", where, path)
        unknown = set(re.findall(r"\{(\w+)\}", value)) - {"percent", "mib"}
        if unknown:
            raise PlanError(
                f"'{where}' value in {path} uses unknown placeholder(s) "
                f"{', '.join(sorted(unknown))} (percent|mib)"
            )
        policies[tool] = HeapPolicy(env=_string(entry, "env", where, path), value=value)
    return HeapConfig(percent=percent, policies=policies)


def load_instances(path: Path) -> dict[tuple[int, int], str]:
    """Read ``benchmark/plans/instances.yaml`` — which box a (vcpus, memory_gb) pair is."""
    _, doc = _read_yaml_mapping(path, "instance catalogue")
    _reject_unknown(doc, ("spec_version", "instances"), "instance catalogue", path)
    _require_spec_version(doc, "instance catalogue", path)
    entries = doc.get("instances")
    if not isinstance(entries, list) or not entries:
        raise PlanError(f"instance catalogue {path} lists no instances")
    catalogue: dict[tuple[int, int], str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PlanError(f"instance catalogue {path} has a non-mapping entry: {entry!r}")
        _reject_unknown(entry, ("vcpus", "memory_gb", "machine_type"), "instance", path)
        shape = (
            _positive_int(entry.get("vcpus"), "vcpus", "instance", path),
            _positive_int(entry.get("memory_gb"), "memory_gb", "instance", path),
        )
        # A shape listed twice would resolve to whichever came last, so two
        # campaigns could name the same box and get different machines.
        if shape in catalogue:
            raise PlanError(
                f"instance catalogue {path} lists {shape[0]} vCPU / {shape[1]} GB twice"
            )
        catalogue[shape] = _string(entry, "machine_type", "instance", path)
    return catalogue


def _tool_defaults_document(path: Path) -> dict[str, Any]:
    """Validate ``benchmark/plans/tools.yaml`` as a whole, whichever table a caller wants.

    Both readers of this file go through here so neither can be the one that
    skips the version check. ``load_heap_config`` runs on every plan while
    ``load_default_modes`` runs only when some tool wants its default mode, so
    validating in the callers left the common path unguarded: a tools.yaml
    written for a future reader was accepted, and its heap table applied, as
    long as the plan being resolved happened to name every mode itself.
    """
    _, doc = _read_yaml_mapping(path, "tool defaults")
    _reject_unknown(doc, ("spec_version", "default_modes", "heap"), "tool defaults", path)
    _require_spec_version(doc, "tool defaults", path)
    return doc


def load_default_modes(path: Path) -> dict[str, str]:
    """Read ``benchmark/plans/tools.yaml`` — the mode an empty tool runs."""
    doc = _tool_defaults_document(path)
    table = _table(doc, "default_modes", "default_modes", path)
    if not table:
        raise PlanError(f"tool defaults {path} names no tools")
    return {tool: _string(table, tool, "default_modes", path) for tool in table}


def _read_yaml_mapping(path: Path, what: str) -> tuple[bytes, dict[str, Any]]:
    """The bytes and the mapping they parse to — bytes so a caller can cite them."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PlanError(f"{what} not readable: {path} ({exc.strerror})") from None
    try:
        doc = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise PlanError(f"{what} is not valid YAML: {path}: {exc}") from None
    if not isinstance(doc, dict):
        raise PlanError(f"{what} {path} is not a mapping")
    return raw, doc


def _require_spec_version(doc: Mapping[str, Any], what: str, path: Path) -> None:
    if doc.get("spec_version") != SPEC_VERSION:
        raise PlanError(
            f"{what} {path} has spec_version {doc.get('spec_version')!r}, "
            f"this reader supports {SPEC_VERSION}"
        )


def _load(
    path: Path,
    default_modes: Mapping[str, str] | None,
    instances: Mapping[tuple[int, int], str] | None,
    heap: HeapConfig | None,
) -> Plan:
    raw, doc = _read_yaml_mapping(path, "plan")

    _reject_unknown(doc, TOP_LEVEL, "plan", path)
    _require_spec_version(doc, "plan", path)

    bucket = _string(doc, "bucket", "plan", path)
    # The filename is the bucket's name, so a plan that disagrees with its own
    # path would be found by one name and cite another.
    if path.suffix in (".yaml", ".yml") and path.stem != bucket:
        raise PlanError(f"plan {path} declares bucket {bucket!r} but is named {path.stem!r}")

    # The plan-level sweep this schema does not have: one default row and a list
    # of them coincide at one entry and diverge silently at the second.
    if isinstance(doc.get("defaults"), list):
        raise PlanError(
            f"'defaults' in {path} is a list — defaults is one row of inherited "
            "values, not a sweep; give each tool the cases it should run"
        )
    defaults = _table(doc, "defaults", "defaults", path)
    _reject_mode(defaults, "[defaults]", path)
    _reject_unknown(defaults, LAYER_FIELDS, "[defaults]", path)
    base_resources = _resources(defaults, "defaults", path, complete=True)
    base_schedule = _schedule(defaults, "defaults", path, complete=True)
    base_auth = _auth(defaults, "defaults", path, complete=True)

    region = _string(doc, "region", "plan", path)
    # Resolved next to the plan rather than passed down from the caller, so a
    # plan read from anywhere still means the same thing.
    modes = default_modes if default_modes is not None else _sibling_default_modes(doc, path)
    catalogue = instances if instances is not None else load_instances(_sibling(path, "instances"))
    heap_config = heap if heap is not None else load_heap_config(_sibling(path, "tools"))

    plan = Plan(
        path=path,
        digest=hashlib.sha256(raw).hexdigest(),
        bucket=bucket,
        region=region,
        cases=_cases(
            doc,
            {**base_resources, **base_auth},
            base_schedule,
            modes,
            _Context(
                bucket=bucket,
                region=region,
                instances=catalogue,
                heap=heap_config,
                path=path,
            ),
        ),
        exclusions=_exclusions(doc, path),
    )
    _reject_overlap(plan, path)
    return plan


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
    """The resource keys ``table`` states, flat. ``complete`` demands all of them.

    Not nested under ``resources``, so a layer and a row draw on one vocabulary
    and the cascade stays a per-key merge over scalars.
    """
    if complete:
        missing = sorted(set(REQUIRED_RESOURCE_FIELDS) - set(table))
        if missing:
            raise PlanError(
                f"'{where}' in {path} is missing {', '.join(missing)} "
                "(defaults must be complete so every case resolves)"
            )
    return {
        field: _positive_int(table[field], field, where, path)
        for field in RESOURCE_FIELDS
        if field in table
    }


def _reject_mode(table: Mapping[str, Any], where: str, path: Path) -> None:
    """``mode`` belongs to a row, and only to a row.

    Said apart from the unknown-key list, which would read as "no such thing"
    when the answer is "one level down".
    """
    if "mode" in table:
        raise PlanError(
            f"{where} in {path} states a mode — a mode belongs to a case row, not "
            "to what rows inherit; state it per row, or record the tool's usual "
            "mode in benchmark/plans/tools.yaml"
        )


def _auth(table: Mapping[str, Any], where: str, path: Path, *, complete: bool) -> dict[str, str]:
    """The stratum ``table`` states. Required of ``defaults``, so a plan written
    before the field existed is refused rather than taking one nobody chose."""
    value = table.get("auth")
    if value is None:
        if complete:
            raise PlanError(
                f"'{where}' in {path} has no 'auth' ({'|'.join(AUTH_CHOICES)}) — a case that "
                "did not say whether it signed cannot be compared with one that did"
            )
        return {}
    if value not in AUTH_CHOICES:
        raise PlanError(f"'{where}' 'auth' in {path} is not {'|'.join(AUTH_CHOICES)}: {value!r}")
    return {"auth": value}


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


def _wants_a_default_row(body: object) -> bool:
    """A tool that stated no cases — ``aws-cli:``, or one that only re-allocates.

    It gets one empty row, which inherits everything including the mode. A body
    that is neither absent nor a mapping falls through to the tool reader, which
    says what is wrong with it, rather than being replaced with a default case.
    """
    return body is None or (isinstance(body, dict) and "cases" not in body)


def _needs_default_modes(body: object) -> bool:
    """Whether resolving this tool will have to consult ``benchmark/plans/tools.yaml``.

    True for a tool that stated no cases, and for one whose rows do not all name
    a mode — a row may omit it and take the tool's usual one.
    """
    if _wants_a_default_row(body):
        return True
    rows = body["cases"] if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            return True
        if "product" not in row:
            if "mode" not in row:
                return True
            continue
        product = row.get("product")
        if not isinstance(product, dict):
            return True
        if "mode" in product:
            continue
        zipped = product.get("zip")
        if not isinstance(zipped, list) or not zipped:
            return True
        if not all(isinstance(choice, dict) and "mode" in choice for choice in zipped):
            return True
    return False


def _sibling(path: Path, name: str) -> Path:
    """A shared table one level above the plan's own directory.

    A draft reviewed from outside the tree falls back to the repository's own
    ``benchmark/plans/``, since ``resolve-plan --path`` is for reading a plan before it is
    moved into place and would otherwise fail on a table its author never wrote.
    Neither present keeps the message pointing at where the plan expected one.
    """
    beside = path.resolve().parents[1] / f"{name}.yaml"
    if beside.exists():
        return beside
    fallback = bench_dir() / f"{name}.yaml"
    return fallback if fallback.exists() else beside


def _sibling_default_modes(doc: Mapping[str, Any], path: Path) -> Mapping[str, str]:
    """``benchmark/plans/tools.yaml`` beside the plan's directory — read only if needed."""
    tools = doc.get("tools")
    if not isinstance(tools, dict) or not any(_needs_default_modes(b) for b in tools.values()):
        return {}
    return load_default_modes(_sibling(path, "tools"))


@dataclass(frozen=True)
class _Context:
    """What every case needs to know but no case states: the target and the boxes."""

    bucket: str
    region: str
    instances: Mapping[tuple[int, int], str]
    heap: HeapConfig
    path: Path


def _cases(
    doc: Mapping[str, Any],
    base_resources: Mapping[str, Any],
    base_schedule: Mapping[str, int],
    default_modes: Mapping[str, str],
    context: _Context,
) -> tuple[Case, ...]:
    path = context.path
    declared = _table(doc, "tools", "tools", path)
    if not declared:
        raise PlanError(f"plan {path} runs no tools")
    tools: dict[str, Any] = {}
    for tool, body in declared.items():
        if not TOOL_RE.fullmatch(tool):
            raise PlanError(f"plan {path} has a malformed tool name: {tool!r}")
        # One empty row: everything inherited, including the mode. Anything the
        # tool *did* say (a ceiling, a timeout) survives beside it.
        stated = body if isinstance(body, dict) else {}
        tools[tool] = {**stated, "cases": [{}]} if _wants_a_default_row(body) else body
    cases: list[Case] = []
    for tool in tools:
        cases.extend(
            _tool_cases(tool, tools[tool], base_resources, base_schedule, default_modes, context)
        )
    return tuple(cases)


def _tool_cases(
    tool: str,
    body: Any,
    base_resources: Mapping[str, Any],
    base_schedule: Mapping[str, int],
    default_modes: Mapping[str, str],
    context: _Context,
) -> list[Case]:
    path = context.path
    where = f"tools.{tool}"
    table = _table({tool: body}, tool, where, path)
    _reject_mode(table, f"'{where}'", path)
    _reject_unknown(table, TOOL_FIELDS, f"'{where}'", path)

    # Cascade is shallow and per-key, over a flat table of scalars: there is no
    # nesting for a deep-merge surprise to hide in.
    settings = {
        **base_resources,
        **_resources(table, where, path, complete=False),
        **_auth(table, where, path, complete=False),
    }
    schedule = {**base_schedule, **_schedule(table, where, path, complete=False)}

    rows = _case_rows(table, where, path)
    # The union of the keys the rows state, not each row's own: otherwise a row
    # omitting a ceiling its sibling stated would give one tool IDs of two
    # shapes. `mode` is always in it, or a bare tool would render an empty ID.
    rendered = tuple(f for f in ROW_FIELDS if f == "mode" or any(f in row for row in rows))

    cases: list[Case] = []
    for row in rows:
        resolved = {**settings, **row, "mode": _row_mode(row, tool, where, default_modes, path)}
        chosen = tuple((field, resolved.get(field)) for field in rendered)
        cases.append(_case(tool, resolved, chosen, schedule, context))
    return cases


def _row_mode(
    row: Mapping[str, Any], tool: str, where: str, default_modes: Mapping[str, str], path: Path
) -> str:
    """The mode a row states, or the one ``benchmark/plans/tools.yaml`` records for the tool.

    A row may omit it: that is how ``aws-cli:`` declares itself, and how a sweep
    over allocation alone stays one line per case.
    """
    stated = row.get("mode")
    if isinstance(stated, str):
        return stated
    mode = default_modes.get(tool)
    if mode is None:
        known = "|".join(sorted(default_modes)) or "none"
        raise PlanError(
            f"'{where}' in {path} states no mode, and {tool} has no default in "
            f"benchmark/plans/tools.yaml ({known}) — name the mode in the row, or record "
            "the tool's usual one there"
        )
    return mode


def _case_rows(table: Mapping[str, Any], where: str, path: Path) -> list[dict[str, Any]]:
    """Expand one tool's ordered union into rows that inherit unstated keys."""
    raw = table.get("cases")
    if raw is None:
        raise PlanError(f"'{where}' in {path} has no 'cases'")
    if not isinstance(raw, list) or not raw:
        raise PlanError(f"'{where}.cases' in {path} is not a non-empty list")

    rows: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        label = f"{where}.cases[{index}]"
        if not isinstance(entry, dict):
            raise PlanError(f"'{label}' in {path} is not a mapping")
        if "product" in entry:
            rows.extend(_product_rows(entry, label, path))
            continue
        rows.append(_literal_row(entry, label, path))
    return rows


def _literal_row(entry: Mapping[str, Any], label: str, path: Path) -> dict[str, Any]:
    """Validate one literal row; generators feed their expanded rows here too."""
    # Named before the unknown-key list, which would read as "no such thing"
    # when the answer is "one level up".
    scheduling = sorted(set(entry) & set(SCHEDULE_FIELDS))
    if scheduling:
        raise PlanError(
            f"'{label}' in {path} states {', '.join(scheduling)} — that is "
            "scheduling, not what a case is; set it on the tool or in defaults"
        )
    _reject_unknown(entry, ROW_FIELDS, f"'{label}'", path)
    return {key: _row_field_value(key, value, label, path) for key, value in entry.items()}


def _row_field_value(key: str, value: Any, label: str, path: Path) -> str | int:
    """Validate one scalar row-field value wherever its author stated it."""
    if key == "mode":
        if not isinstance(value, str) or not value.strip():
            raise PlanError(f"'{label}' 'mode' in {path} is not a non-empty string")
        return value
    if key == "auth":
        return _auth({key: value}, label, path, complete=False)[key]
    return _positive_int(value, key, label, path)


def _product_rows(entry: Mapping[str, Any], label: str, path: Path) -> list[dict[str, Any]]:
    """Expand an explicit product whose optional zip choices stay indivisible."""
    scheduling = sorted(set(entry) & set(SCHEDULE_FIELDS))
    if scheduling:
        raise PlanError(
            f"'{label}' in {path} states {', '.join(scheduling)} — that is "
            "scheduling, not what a case is; set it on the tool or in defaults"
        )
    if set(entry) != {"product"}:
        extras = sorted(set(entry) - {"product"})
        raise PlanError(
            f"'{label}' in {path} is a product generator with extra key(s) "
            f"{', '.join(repr(key) for key in extras)}; a generator entry contains only 'product'"
        )

    raw_product = entry["product"]
    if not isinstance(raw_product, dict):
        raise PlanError(f"'{label}.product' in {path} is not a mapping")
    scheduling = sorted(set(raw_product) & set(SCHEDULE_FIELDS))
    if scheduling:
        raise PlanError(
            f"'{label}.product' in {path} states {', '.join(scheduling)} — that is "
            "scheduling, not what a case is; set it on the tool or in defaults"
        )
    _reject_unknown(raw_product, (*ROW_FIELDS, "zip"), f"'{label}.product'", path)
    if not raw_product:
        raise PlanError(f"'{label}.product' in {path} is empty")

    independent: dict[str, list[str | int]] = {}
    for field in ROW_FIELDS:
        if field not in raw_product:
            continue
        values = raw_product[field]
        if not isinstance(values, list):
            raise PlanError(f"'{label}.product.{field}' in {path} is not a list")
        if not values:
            raise PlanError(f"'{label}.product.{field}' in {path} is empty")
        independent[field] = [
            _row_field_value(field, value, f"{label}.product.{field}[{index}]", path)
            for index, value in enumerate(values)
        ]

    zipped = _zip_choices(raw_product.get("zip"), label, path) if "zip" in raw_product else []
    zipped_fields = set(zipped[0]) if zipped else set()
    overlap = [field for field in ROW_FIELDS if field in independent and field in zipped_fields]
    if overlap:
        raise PlanError(
            f"'{label}.product' in {path} states {', '.join(overlap)} both as an "
            "independent axis and inside zip"
        )
    if not independent and not zipped:
        raise PlanError(f"'{label}.product' in {path} has no axes or zip choices")

    # Correlated choices are the outermost factor. Independent axes follow in
    # canonical row-field order, with the rightmost advancing fastest. The
    # result therefore never depends on YAML mapping order.
    factors: list[list[dict[str, str | int]]] = []
    if zipped:
        factors.append(zipped)
    factors.extend([{field: value} for value in values] for field, values in independent.items())

    rows: list[dict[str, Any]] = []
    for choices in itertools.product(*factors):
        row: dict[str, Any] = {}
        for choice in choices:
            row.update(choice)
        rows.append(_literal_row(row, f"{label}.product expansion", path))
    return rows


def _zip_choices(value: Any, label: str, path: Path) -> list[dict[str, str | int]]:
    """Validate atomic correlated rows used as one product factor."""
    where = f"{label}.product.zip"
    if not isinstance(value, list):
        raise PlanError(f"'{where}' in {path} is not a list")
    if not value:
        raise PlanError(f"'{where}' in {path} is empty")

    choices: list[dict[str, str | int]] = []
    expected_fields: set[str] | None = None
    seen: set[tuple[tuple[str, str | int], ...]] = set()
    for index, raw_choice in enumerate(value):
        choice_label = f"{where}[{index}]"
        if not isinstance(raw_choice, dict):
            raise PlanError(f"'{choice_label}' in {path} is not a mapping")
        scheduling = sorted(set(raw_choice) & set(SCHEDULE_FIELDS))
        if scheduling:
            raise PlanError(
                f"'{choice_label}' in {path} states {', '.join(scheduling)} — that is "
                "scheduling, not what a case is; set it on the tool or in defaults"
            )
        _reject_unknown(raw_choice, ROW_FIELDS, f"'{choice_label}'", path)
        fields = set(raw_choice)
        if len(fields) < 2:
            raise PlanError(f"'{choice_label}' in {path} must state at least two row fields")
        if expected_fields is None:
            expected_fields = fields
        elif fields != expected_fields:
            raise PlanError(
                f"'{choice_label}' in {path} has fields that differ from the first zip choice"
            )
        choice = {
            field: _row_field_value(field, raw_choice[field], choice_label, path)
            for field in ROW_FIELDS
            if field in raw_choice
        }
        identity = tuple(choice.items())
        if identity in seen:
            raise PlanError(f"'{where}' in {path} contains the same choice twice")
        seen.add(identity)
        choices.append(choice)
    return choices


def _case(
    tool: str,
    resolved: Mapping[str, Any],
    chosen: tuple[tuple[str, str | int | None], ...],
    schedule: Mapping[str, int],
    context: _Context,
) -> Case:
    path = context.path
    mode = str(resolved["mode"])
    auth = str(resolved["auth"])
    resources = resolved
    shape = (int(resources["vcpus"]), int(resources["memory_gb"]))
    machine_type = context.instances.get(shape)
    if machine_type is None:
        offered = ", ".join(f"{v}x{m}" for v, m in sorted(context.instances)) or "none"
        raise PlanError(
            f"'tools.{tool}' in {path} asks for {shape[0]} vCPU / {shape[1]} GB, which "
            f"benchmark/plans/instances.yaml does not offer ({offered}) — add the shape or ask "
            "for one that exists"
        )

    ceiling = resources.get("container_memory_gb")
    container_memory_gb = None if ceiling is None else int(ceiling)
    # A ceiling above the box is not a bigger container, it is a plan that will
    # be silently ignored by the one thing that enforces it.
    if container_memory_gb is not None and container_memory_gb > shape[1]:
        raise PlanError(
            f"'tools.{tool}' in {path} caps the container at {container_memory_gb} GB "
            f"on a {shape[1]} GB box — a ceiling above the box constrains nothing"
        )

    resolved_resources = Resources(
        vcpus=shape[0],
        memory_gb=shape[1],
        machine_type=machine_type,
        container_memory_gb=container_memory_gb,
    )
    env = context.heap.env_for(tool, visible_memory_gb=resolved_resources.visible_memory_gb)

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
        auth=auth,
        resources=resolved_resources,
        reps=schedule["reps"],
        timeout_s=schedule["timeout_s"],
        axes=chosen,
        env=env,
        fingerprint=fingerprint(
            bucket=context.bucket,
            region=context.region,
            tool=tool,
            mode=mode,
            auth=auth,
            resources=resolved_resources,
            timeout_s=schedule["timeout_s"],
            env=env,
        ),
    )


def derive_case_id(chosen: Iterable[tuple[str, str | int | None]]) -> str:
    """``recursive-parquet.container_memory_gb-2`` — the mode, then each key.

    Every key any of the tool's rows states appears, even one only a single row
    varies: dropping it would make the ID mean "whatever the default was at the
    time". ``none`` is the ceiling nobody set — a real answer, not an absent key.
    """
    segments: list[str] = []
    for field, value in chosen:
        rendered = "none" if value is None else str(value)
        segments.append(rendered if field == "mode" else f"{field}-{rendered}")
    return ".".join(segments)


def fingerprint(
    *,
    bucket: str,
    region: str,
    tool: str,
    mode: str,
    auth: str,
    resources: Resources,
    timeout_s: int,
    env: Sequence[tuple[str, str]] = (),
) -> str:
    """A digest over the resolved case — what makes two attempts comparable."""
    payload = {
        "fingerprint_version": FINGERPRINT_VERSION,
        "bucket": bucket,
        "region": region,
        "tool": tool,
        "mode": mode,
        "auth": auth,
        "resources": resources.as_dict(),
        "timeout_s": timeout_s,
        "env": [list(pair) for pair in env],
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
