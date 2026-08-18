"""Typed command-adapter boundary owned by the benchmark runtime.

The Python contract in ``benchmark/docs/capsule-contract.md``: what a capsule's
``adapter/command.py`` declares, and what the loader refuses. A capsule imports
its declaration vocabulary from here, so the harness side and the eleven capsule
sides cannot drift apart into two spellings of the same fact.
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType, ModuleType
from typing import Protocol, cast

from benchmark.runtime.contract import FIELD_NAMES


class CommandAdapterError(ValueError):
    """A friendly command request cannot be represented by the subject CLI."""


HEAP_PERCENT = 75
"""The share of the visible memory ceiling a managed runtime may take as heap.

A methodology decision under the comparable-setup-effort rule, so it is stated
once here rather than in eleven capsules that would each drift to their own
number. The *translation* — which variable, which syntax — stays the capsule's:
swath renders it into ``-XX:MaxRAMPercentage``, s3p into a V8 flag, and the
other nine have no heap to size. A capsule that declares the ``heap_percent``
axis must declare it ``Fixed(HEAP_PERCENT)``; the loader refuses any other
number, which is what keeps this one decision one decision.
"""

PRODUCTS = ("text", "parquet", "parquet-sorted")
"""The shared output vocabulary. ``text`` means the same thing for every tool,
which is what lets a report group a text stratum and keep Parquet out of it."""

PURPOSES = ("diagnostic", "canary", "preparation", "measurement")
"""The attempt purposes of ``model.md``, weakest claim first.

The order is what ``purpose_ceiling`` is read against: a plan may demote a mode
below its ceiling, never promote one above it.
"""

RESERVED_AXES = ("concurrency", "heap_percent", "segments")
"""Config key names reserved for axes a comparison is read along.

A capsule with such a knob calls it by the reserved name and declares it, rather
than pinning a number inside ``build_command`` where no report can see it and no
plan can sweep it. The *name* is the study's; the meaning stays the capsule's --
``-c``, ``--checkers`` and ``--list-concurrency`` govern different things and no
declaration makes them one.

``segments`` is how many pieces a prepared artifact divides the work into --
s3-fast-list's hinted path cuts the keyspace into N ranges. It is an axis of the
*consuming* measurement even though only the preparation's argv carries it: the
artifact's shape is what the run listed under, so it belongs in the identity.
"""

RESERVED_CONFIG_KEYS = ("mode", *RESERVED_AXES)
"""Reserved key names in the config blob. ``mode`` is reserved and never an axis:
it is a plan row field that resolution folds into the blob."""

_PROVENANCE_RE = re.compile(r"source@\S+")
_LITERAL_PROVENANCE = ("help", "unverified")


AxisValue = int | str
"""What an axis may carry. A bool is refused: a flag is not a swept value."""


def _check_provenance(provenance: str) -> None:
    """Where a recorded number came from -- required of every axis that records one."""
    if provenance not in _LITERAL_PROVENANCE and not _PROVENANCE_RE.fullmatch(provenance):
        raise CommandAdapterError(
            f"axis provenance must be one of {_LITERAL_PROVENANCE} or 'source@<rev>': "
            f"{provenance!r}"
        )


def _check_axis_value(value: object) -> AxisValue:
    if isinstance(value, bool) or not isinstance(value, int | str) or value == "":
        raise CommandAdapterError(f"axis value must be a non-empty int or str: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class Fixed:
    """Real, effective, and not settable — ps3's 256. A plan setting it is refused."""

    value: AxisValue

    def __post_init__(self) -> None:
        _check_axis_value(self.value)


@dataclass(frozen=True, slots=True)
class Default:
    """Settable; this is what the subject runs at unsilenced — s4cmd's ``-c``.

    The provenance is not decoration. A capsule that misses an upstream version
    bump writes a confident lie into a hashed blob, and a recorded-but-wrong
    value is worse than an absent one because it claims knowledge.
    """

    value: AxisValue
    provenance: str
    """``help``, ``source@<rev>``, or ``unverified`` -- see the contract page."""

    def __post_init__(self) -> None:
        _check_axis_value(self.value)
        _check_provenance(self.provenance)


@dataclass(frozen=True, slots=True)
class Ceiling:
    """Settable; the subject's own limit when unsilenced, and an upper bound.

    Exactly :class:`Default` plus one semantic: the recorded number is a bound
    whose effective width is lower and data-dependent -- swath's AIMD starting at
    ``min(4, N)``, s5cmd's ``min(numworkers, shards)``. So it carries the same
    provenance, for the same reason.

    The value is the *subject's* number, never the study's: what a campaign asks
    for is plan content, which is what makes a detune reviewable in the plan
    rather than buried in a capsule. What was *achieved* is a fact about the run
    and belongs in evidence, never in ``config``.
    """

    value: AxisValue
    provenance: str
    """``help``, ``source@<rev>``, or ``unverified`` -- see the contract page."""

    def __post_init__(self) -> None:
        _check_axis_value(self.value)
        _check_provenance(self.provenance)


@dataclass(frozen=True, slots=True)
class Stated:
    """Settable, and the capsule carries no value at all: the plan must state it.

    s3-fast-list's ``segments`` — upstream records no default the capsule could
    cite, and a preparation that quietly chose one would freeze the whole sweep
    at it. Distinct from :class:`Default` with an ``unverified`` provenance,
    which still asserts *a* number; ``Stated`` asserts there is none to record,
    so ``effective_config`` refuses a plan that leaves it silent rather than
    folding anything in.
    """


@dataclass(frozen=True, slots=True)
class Inert:
    """The flag is accepted and has no effect *on this mode* — rclone's
    ``--checkers`` under flat ``ListR``.

    Statically inert only. A knob whose effect depends on the target — s4cmd's
    ``-c`` on a flat prefix — is not inert: namespace shape is an analysis
    covariate, not a declaration.
    """


Axis = Fixed | Default | Ceiling | Stated | Inert

SettableAxis = Default | Ceiling | Stated
"""The axis states a plan may set a value on. ``Fixed`` refuses one, and
``Inert`` would accept a value that changes nothing."""


@dataclass(frozen=True, slots=True)
class Executable:
    """One of the subject's executables, as the exact argv tokens that invoke it.

    Named because a mode says which one it runs: s3-fast-list's hinted listing
    runs ``ks-tool`` as its inline setup and the lister as its subject, and an
    index into a tuple would say that unreadably.
    """

    name: str
    argv: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise CommandAdapterError(f"executable name must be a non-empty string: {self.name!r}")
        if (
            not isinstance(self.argv, tuple)
            or not self.argv
            or not all(isinstance(token, str) and token for token in self.argv)
        ):
            raise CommandAdapterError(f"executable {self.name} needs non-empty argv tokens")
        if not self.argv[0].startswith("/"):
            # The attempt engine replaces the environment wholesale with its own
            # allowlist, so a bare name would not resolve against its PATH.
            raise CommandAdapterError(
                f"executable {self.name} must start from an absolute in-image path: "
                f"{self.argv[0]!r}"
            )


@dataclass(frozen=True, slots=True)
class Mode:
    """What one mode of a capsule produces, and what it runs it at.

    None of this runs the tool — ``build_command`` owns argv entirely and the
    harness never interprets a mode. The manifest exists for what happens
    afterwards: deciding what a result may be compared with, and recording what
    it actually ran at. It is per mode rather than per capsule because the truth
    differs within one tool — rclone's ``--checkers`` is live on its walk mode
    and inert on its flat one.
    """

    product: str
    """One of :data:`PRODUCTS`. Translates across tools, which is what makes two
    results comparable at all."""

    fields: tuple[str, ...]
    """The contract columns this mode populates, canonically ordered.

    A mode emitting key-only must not be ranked against one emitting four
    columns, or a tool wins by emitting less.
    """

    axes: Mapping[str, Axis] = field(default_factory=lambda: MappingProxyType({}))
    """Per reserved name: what the knob is on this mode. An axis name identifies
    the axis and explicitly not its semantics."""

    artifacts: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    """Logical name to the filename this mode publishes into its sink.

    Not :attr:`product` under another name: ``product`` is the *format*
    vocabulary — ``text``, ``parquet`` — shared across tools so a report can keep
    a Parquet number out of a text stratum. This says which files land, so a
    consumer can ask for one of them by name. A mode publishing a listing beside
    a key distribution has one ``product`` and two artifacts, and the harness has
    no way to tell them apart from the sink alone: two files in a manifest is not
    a question a count can answer.
    """

    product_artifact: str = ""
    """Which of :attr:`artifacts` carries this mode's *measured* output.

    Empty means this mode's product does not travel as a declared file yet — it
    still streams through stdout — which is every mode today. Set, it must name
    a key of ``artifacts``; the loader refuses anything else, because a product
    pointing at a file the mode does not publish is a promise nothing keeps.
    """

    purpose_ceiling: str = "measurement"
    """The most a plan may claim this mode is."""

    executable: str = ""
    """Which declared executable this mode runs; empty names the capsule's first."""

    inline: str = ""
    """Another mode of this capsule the engine runs untimed, in this container,
    immediately before the timed subject.

    The alternative to a chain link, for a step whose entire product is consumed
    by the very next exec on the same machine: s3-fast-list's ``ks-tool split``
    turns the staged key distribution into hints in under a second, and making
    that its own attempt buys a slot, a job, a VM and an identity for a file the
    measurement is the only reader of. A chain link is what a *shared* artifact
    is for — one preparation serving a sweep — and the split serves exactly one
    consumer. Its wall clock is recorded as setup evidence and never merged into
    the measurement.
    """

    def __post_init__(self) -> None:
        if self.product not in PRODUCTS:
            raise CommandAdapterError(f"mode product must be one of {PRODUCTS}: {self.product!r}")
        if not isinstance(self.fields, tuple) or not self.fields:
            raise CommandAdapterError("mode fields must be a non-empty tuple of contract columns")
        unknown = sorted(set(self.fields) - set(FIELD_NAMES))
        if unknown:
            raise CommandAdapterError(f"mode fields name no contract column: {', '.join(unknown)}")
        if len(set(self.fields)) != len(self.fields):
            raise CommandAdapterError(f"mode fields repeat a contract column: {self.fields}")
        if self.purpose_ceiling not in PURPOSES:
            raise CommandAdapterError(
                f"purpose_ceiling must be one of {PURPOSES}: {self.purpose_ceiling!r}"
            )
        if not isinstance(self.executable, str):
            raise CommandAdapterError("mode executable must name a declared executable")
        if not isinstance(self.inline, str):
            raise CommandAdapterError("mode inline must name a declared mode of this capsule")
        axes = dict(self.axes)
        unreserved = sorted(set(axes) - set(RESERVED_AXES))
        if unreserved:
            raise CommandAdapterError(
                f"axis name(s) not reserved by the study: {', '.join(unreserved)}; "
                f"reserved names are {RESERVED_AXES}"
            )
        for name, axis in axes.items():
            if not isinstance(axis, Fixed | Default | Ceiling | Stated | Inert):
                raise CommandAdapterError(f"axis {name} is not an axis state: {axis!r}")
        heap = axes.get("heap_percent")
        if heap is not None and heap != Fixed(HEAP_PERCENT):
            raise CommandAdapterError(
                f"heap_percent is the harness's methodology share and must be declared "
                f"Fixed({HEAP_PERCENT}), not {heap!r}"
            )
        artifacts = self._checked_artifacts()
        if self.product_artifact and self.product_artifact not in artifacts:
            raise CommandAdapterError(
                f"mode product_artifact {self.product_artifact!r} is not one of the artifacts "
                f"this mode publishes: {sorted(artifacts) or 'none'}"
            )
        # Canonical column order, so two modes populating the same columns
        # declare the same tuple whatever order their authors wrote it in.
        object.__setattr__(self, "fields", tuple(n for n in FIELD_NAMES if n in set(self.fields)))
        object.__setattr__(self, "axes", MappingProxyType(axes))
        object.__setattr__(self, "artifacts", MappingProxyType(artifacts))

    def _checked_artifacts(self) -> dict[str, str]:
        """The declared sink filenames, held to what a manifest key can be.

        A manifest keys every published file by its path relative to the sink
        root, so a nested name is legal and an absolute or escaping one names
        bytes no attempt record accounts for.
        """
        if not isinstance(self.artifacts, Mapping):
            raise CommandAdapterError("mode artifacts must map a logical name to a filename")
        artifacts: dict[str, str] = {}
        for name, filename in self.artifacts.items():
            if not isinstance(name, str) or not name:
                raise CommandAdapterError(f"artifact name must be a non-empty string: {name!r}")
            if not isinstance(filename, str) or not filename:
                raise CommandAdapterError(f"artifact {name} must name a file in the sink")
            if filename.startswith("/") or ".." in PurePosixPath(filename).parts:
                raise CommandAdapterError(
                    f"artifact {name} must be a path under the sink, not {filename!r}"
                )
            artifacts[name] = filename
        if len(set(artifacts.values())) != len(artifacts):
            # Two names over one file makes the reverse lookup a guess, and the
            # digest would look right whichever name asked for it.
            raise CommandAdapterError(f"mode artifacts name one file twice: {self.artifacts}")
        return artifacts

    def permits_purpose(self, purpose: str) -> bool:
        """Whether a plan may claim this mode ran for ``purpose``."""
        if purpose not in PURPOSES:
            raise CommandAdapterError(f"unknown purpose: {purpose!r}")
        return PURPOSES.index(purpose) <= PURPOSES.index(self.purpose_ceiling)


@dataclass(frozen=True, slots=True)
class Requirement:
    """One link of a declared chain: a mode of this capsule, and what is taken from it.

    A capsule writes the pair — ``("list", "keyspace")`` — and never the bare
    mode. Naming only the mode leaves the harness to pick a file out of the
    producer's sink, and the only rule available to it is *"there is exactly one,
    take it"*, which holds by coincidence and stops holding the moment a producer
    publishes its listing beside its key distribution. The failure is silent: a
    131 MB listing staged where a hints file belongs, under a digest that checks
    out.
    """

    mode: str
    artifact: str
    """The logical name — a key of the producing mode's ``artifacts``, not a filename."""


def shared_axis_values(manifest: Mode, consumer: Mapping[str, object]) -> dict[str, object]:
    """The consumer's value for every axis ``manifest`` declares settable itself.

    One rule for both ways a producing mode inherits from the row it serves — a
    chain link the resolver expands, and an inline setup exec the worker runs —
    because two copies of it are two chances to disagree about what a sweep
    varied. An axis the producer does not declare never flows into it, and a
    value on an ``Inert`` one would split shared preparations over a knob that
    does nothing.
    """
    return {
        name: consumer[name]
        for name, axis in manifest.axes.items()
        if name in consumer and isinstance(axis, SettableAxis)
    }


@dataclass(frozen=True, slots=True)
class CommandRequest:
    """Tool-neutral inputs from which a capsule compiles exact subject argv."""

    mode: str
    bucket: str
    region: str
    prefix: str = ""
    tool: str = ""
    operation: str = "list"
    signed: bool = False
    """Whether the subject should sign its requests.

    Derived by the harness from the case's auth role -- null role, unsigned --
    and passed as a boolean because signing is an argv decision several
    capsules make (``--no-sign-request`` and friends), while *which* identity
    signs is the harness's business and never the subject's.
    """

    config: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    """The capsule's own knobs, forwarded verbatim and never read by the harness.

    A capsule declares the keys it accepts in ``CONFIG_KEYS``; anything else is
    refused before ``build_command`` runs, so a misspelled knob is an error
    rather than a sweep whose cells are all identical.
    """

    sink_dir: str = ""
    """Container-local directory a mode with a native file sink may write into.

    Absent for every mode that writes its listing to stdout, which is most of
    them. A mode whose tool refuses to stream — Swath's sorted Parquet requires
    a directory dataset — builds its destination path under this directory, and
    the attempt engine collects, scans, and publishes whatever lands there. An
    adapter never chooses its own path: a container-local path the engine does
    not know about is output the attempt record cannot account for.
    """

    artifact_path: str = ""
    """Container-local path where the harness staged the artifact this case consumes.

    The symmetric thing to :attr:`sink_dir`, for the modes that read an
    artifact a preparation produced — hints, a key distribution, a commands
    file. Empty for the many modes that consume nothing. Identity never sees
    this path: the case hashes the artifact's content digest, and the engine
    stages the bytes wherever it likes. A consuming capsule refuses an empty
    path rather than inventing one, for the same reason ``sink_dir`` works
    that way — a path the engine does not know about is input the attempt
    record cannot account for.
    """

    visible_memory_gb: float | None = None
    """The memory ceiling the subject can see: the container's cgroup limit, or
    the whole box where there is none.

    ``None`` where the harness has resolved no ceiling — a managed-runtime
    capsule refuses rather than sizing a heap from nothing.
    """

    heap_percent: int = HEAP_PERCENT
    """The share of :attr:`visible_memory_gb` a managed runtime may take as heap.

    Carried on the request rather than read from the module constant so a
    capsule renders the number it was actually given, which is the number that
    reached its ``config`` and its identity.
    """


class CommandBuilder(Protocol):
    """The importable function every capsule's ``command.py`` exports."""

    def __call__(self, request: CommandRequest) -> tuple[str, ...]: ...


class EnvBuilder(Protocol):
    """A capsule's optional request-derived environment — heap flags, so far."""

    def __call__(self, request: CommandRequest) -> dict[str, str]: ...


class ArtifactValidator(Protocol):
    """A capsule's optional structural check on an artifact one of its modes produced.

    A digest proves an artifact is unchanged, not that it is usable: s3-fast-list's
    ``ks-tool split`` can emit an empty cut point that digests cleanly and turns
    the hinted run into a full-range serial scan. Raising refuses the producer,
    before anything consumes what it made.

    Declared per producing mode in ``VALIDATE_ARTIFACT``, because a capsule with
    two producers has two different structures to hold them to, and a validator
    handed the wrong file would either pass it or refuse it for the wrong reason.
    """

    def __call__(self, path: Path) -> None: ...


@dataclass(frozen=True, slots=True)
class LoadedCommandAdapter:
    """A capsule's complete-argv builder and its recorded subject prefix.

    The config keys this capsule accepts are the union of ``CONFIG_KEYS``, every
    axis name any of its modes declares, and ``mode``, computed once at
    construction as :attr:`accepted_config_keys`. The union is what lets the
    merged blob round-trip: :meth:`effective_config` writes a ``Fixed`` axis into
    it — the heap share, which no plan may ever set and no capsule therefore
    lists in ``CONFIG_KEYS`` — and :meth:`compile` must not then refuse the
    loader's own output. An axis is declared exactly once, in the mode manifest.
    What a *plan* may set is unchanged and enforced in
    :meth:`effective_config`, which still refuses a plan setting a ``Fixed``
    axis or passing ``mode`` as a config key.
    """

    build: CommandBuilder
    fixed_command_prefix: tuple[str, ...]
    config_keys: frozenset[str]
    supports_unsigned: bool
    """Whether this subject can list without a credential.

    The interesting half of the question: signing is the default credential
    chain and every subject here can do it, while four have no unsigned request
    path at all. A capsule states this because it is a fact about the tool, and
    a stratum it cannot issue is refused rather than silently ignored — which is
    what let a signing case run unsigned while every receipt recorded it signed.
    """

    supports_signed: bool
    """Almost always true, and false only where the capsule's own mechanism cannot
    carry a credential — mc resolves one from a static alias, not per request.

    A capsule omits ``SUPPORTS_SIGNED`` and the loader defaults it to true."""

    tool: str
    functional_env: dict[str, str]
    """Non-secret, tool-specific environment the subject structurally needs.

    Empty for most tools. Exists for a subject like mc, which has no
    ``--no-sign-request`` flag and instead resolves an anonymous alias from an
    endpoint URL it must be told — configuration, not a credential. Reviewed
    as non-secret at capsule-authoring time, same as ``DECLARED_FUNCTIONAL_ENV``
    in the attempt engine; the engine still refuses any key that collides with
    a reserved or credential name.
    """

    modes: Mapping[str, Mode]
    """One manifest per mode; the capsule's whole mode vocabulary."""

    executables: tuple[Executable, ...] = ()
    """The subject's executables; the first is the one ``build/image.json`` registers."""

    requires: Mapping[str, tuple[Requirement, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    """Per mode, the ordered chain of this capsule's own modes that must run first,
    each naming the artifact taken from it."""

    build_env: EnvBuilder = field(default_factory=lambda: _static_env({}))
    """The request-derived environment; defaults to returning ``FUNCTIONAL_ENV``."""

    validate_artifact: Mapping[str, ArtifactValidator] = field(
        default_factory=lambda: MappingProxyType({})
    )
    """Per producing mode, the structural check its artifact must pass. A mode
    with no entry produces bytes nothing structural can be said about."""

    accepted_config_keys: frozenset[str] = frozenset()
    """Derived, never declared: see the class docstring. Any value passed in is
    replaced with the union, so it cannot drift from the manifests."""

    chain_artifacts: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    """Derived from ``REQUIRES``: per producing mode, the logical artifact the
    chain takes from it. Any value passed in is replaced."""

    def __post_init__(self) -> None:
        axes = {name for manifest in self.modes.values() for name in manifest.axes}
        object.__setattr__(self, "accepted_config_keys", self.config_keys | axes | {"mode"})
        object.__setattr__(self, "chain_artifacts", MappingProxyType(self._chain_artifacts()))

    def _chain_artifacts(self) -> dict[str, str]:
        """Invert ``requires``: which artifact each producing mode is consumed for.

        A producer serving two consumers that want *different* artifacts of it is
        refused here rather than resolved per consumer, because the harness
        records one ``artifact_sha256`` against the producing attempt and two
        answers to that one question is how evidence comes to disagree with
        itself. No capsule declares that shape; when one wants to, the column has
        to grow a name first.
        """
        artifacts: dict[str, str] = {}
        for consumer, chain in self.requires.items():
            for step in chain:
                taken = artifacts.setdefault(step.mode, step.artifact)
                if taken != step.artifact:
                    raise CommandAdapterError(
                        f"{self.tool}: mode {step.mode!r} is consumed for {taken!r} and "
                        f"{consumer!r} wants {step.artifact!r} from it"
                    )
        return artifacts

    def consumed_artifact(self, mode: str) -> tuple[str, str]:
        """The `(logical name, sink filename)` the declared chain takes from ``mode``.

        The question a settled preparation is read with: its manifest may carry
        more than one file, and only the declaration says which of them the chain
        was for.
        """
        manifest = self.modes.get(mode)
        if manifest is None:
            raise CommandAdapterError(f"{self.tool} has no mode {mode!r}")
        artifact = self.chain_artifacts.get(mode)
        if artifact is None:
            raise CommandAdapterError(
                f"{self.tool}: no mode of this capsule consumes what {mode!r} publishes"
            )
        return artifact, manifest.artifacts[artifact]

    def compile(self, request: CommandRequest) -> tuple[str, ...]:
        """Return the complete subject argv the attempt engine will execute."""
        self._refuse_undeclared(request.config)
        if request.signed and not self.supports_signed:
            raise CommandAdapterError(f"{self.tool} cannot sign its requests")
        if not request.signed and not self.supports_unsigned:
            raise CommandAdapterError(f"{self.tool} has no unsigned request path")
        return self.build(request)

    def effective_config(self, mode: str, config: Mapping[str, object]) -> dict[str, object]:
        """The config blob identity hashes: the plan's keys plus what the capsule declares.

        A plan stating no concurrency gets the capsule's declared value written
        in, not an absent key, because absent means *this tool has no such knob*
        while a value means *this is what it ran at*. ``mode`` is folded in here:
        a plan keeps it as a named row field, and resolution puts it in the blob.
        """
        if mode not in self.modes:
            raise CommandAdapterError(f"{self.tool} has no mode {mode!r}")
        if "mode" in config:
            raise CommandAdapterError(
                f"{self.tool}: mode is a plan row field folded in by resolution, not a config key"
            )
        self._refuse_undeclared(config)
        merged: dict[str, object] = {"mode": mode, **config}
        for name, axis in self.modes[mode].axes.items():
            if name in config:
                if isinstance(axis, Fixed):
                    raise CommandAdapterError(
                        f"{self.tool} mode {mode!r} fixes {name}; a plan may not set it"
                    )
                continue
            if isinstance(axis, Stated):
                raise CommandAdapterError(
                    f"{self.tool} mode {mode!r} declares {name} with no value of its own; "
                    f"the plan must state it"
                )
            if isinstance(axis, Fixed | Default | Ceiling):
                merged[name] = axis.value
        return {key: merged[key] for key in sorted(merged)}

    def _refuse_undeclared(self, config: Mapping[str, object]) -> None:
        undeclared = sorted(set(config) - self.accepted_config_keys)
        if undeclared:
            raise CommandAdapterError(
                f"{self.tool} does not accept config key(s): {', '.join(undeclared)}"
            )


def _static_env(functional_env: Mapping[str, str]) -> EnvBuilder:
    """The default ``build_env``: the capsule's static environment, whatever the request."""

    def build_env(request: CommandRequest) -> dict[str, str]:
        return dict(functional_env)

    return build_env


def _load_module(path: Path) -> ModuleType:
    name = f"_s3_listing_command_{path.parent.parent.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CommandAdapterError(f"cannot load command adapter: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_executables(module: ModuleType, path: Path) -> tuple[Executable, ...]:
    """The subject's declared executables; the first is the one the image registers."""
    raw = getattr(module, "EXECUTABLES", None)
    if not isinstance(raw, tuple) or not raw or not all(isinstance(e, Executable) for e in raw):
        raise CommandAdapterError(f"{path} EXECUTABLES must be a non-empty tuple of Executable")
    executables = cast(tuple[Executable, ...], raw)
    names = [executable.name for executable in executables]
    if len(set(names)) != len(names):
        raise CommandAdapterError(f"{path} declares two executables under one name")
    return executables


def _load_modes(
    module: ModuleType, path: Path, executables: tuple[Executable, ...]
) -> Mapping[str, Mode]:
    raw = getattr(module, "MODES", None)
    if not isinstance(raw, Mapping) or not raw:
        raise CommandAdapterError(f"{path} MODES must be a non-empty mapping of mode to Mode")
    modes: dict[str, Mode] = {}
    names = {executable.name for executable in executables}
    for mode, manifest in raw.items():
        if not isinstance(mode, str) or not mode:
            raise CommandAdapterError(f"{path} names a mode that is not a non-empty string")
        if not isinstance(manifest, Mode):
            raise CommandAdapterError(f"{path} mode {mode!r} does not carry a Mode manifest")
        if manifest.executable and manifest.executable not in names:
            raise CommandAdapterError(
                f"{path} mode {mode!r} runs undeclared executable {manifest.executable!r}"
            )
        modes[mode] = manifest
    return MappingProxyType(modes)


def _load_requires(
    module: ModuleType, path: Path, modes: Mapping[str, Mode]
) -> Mapping[str, tuple[Requirement, ...]]:
    raw = getattr(module, "REQUIRES", {})
    if not isinstance(raw, Mapping):
        raise CommandAdapterError(f"{path} REQUIRES must map a mode to its ordered prerequisites")
    requires: dict[str, tuple[Requirement, ...]] = {}
    for mode, chain in raw.items():
        if mode not in modes:
            raise CommandAdapterError(f"{path} REQUIRES names unknown mode {mode!r}")
        if not isinstance(chain, tuple) or not chain:
            raise CommandAdapterError(f"{path} REQUIRES[{mode!r}] must be a non-empty tuple")
        steps = tuple(_requirement(step, mode, path, modes) for step in chain)
        if len({step.mode for step in steps}) != len(steps):
            raise CommandAdapterError(f"{path} REQUIRES[{mode!r}] repeats a prerequisite")
        requires[mode] = steps
    for mode in requires:
        _refuse_requires_cycle(requires, mode, path)
    return MappingProxyType(requires)


def _requirement(step: object, mode: str, path: Path, modes: Mapping[str, Mode]) -> Requirement:
    """One `(mode, artifact)` pair of a chain, held to what the producer declares.

    The bare mode is refused rather than read as *"whatever that mode's sole
    artifact is"*: a capsule that later publishes a second file would silently
    change what every consumer binds.
    """
    if not isinstance(step, tuple) or len(step) != 2 or not all(isinstance(v, str) for v in step):
        raise CommandAdapterError(
            f"{path} REQUIRES[{mode!r}] must name each prerequisite as (mode, artifact): {step!r}"
        )
    prerequisite, artifact = cast(tuple[str, str], step)
    manifest = modes.get(prerequisite)
    if manifest is None:
        # A dependency on something another tool produced is a different
        # problem: that artifact is an input the study supplies.
        raise CommandAdapterError(
            f"{path} REQUIRES[{mode!r}] names mode {prerequisite!r}, which this capsule "
            f"does not have"
        )
    if not manifest.artifacts:
        raise CommandAdapterError(
            f"{path} REQUIRES[{mode!r}] names {prerequisite!r}, which declares no artifact "
            f"for anything to consume"
        )
    if artifact not in manifest.artifacts:
        raise CommandAdapterError(
            f"{path} REQUIRES[{mode!r}] wants {artifact!r} from {prerequisite!r}, which "
            f"publishes {sorted(manifest.artifacts)}"
        )
    return Requirement(prerequisite, artifact)


def _refuse_requires_cycle(
    requires: Mapping[str, tuple[Requirement, ...]], mode: str, path: Path
) -> None:
    """Walk one mode's chain transitively: a cycle is a shape no reviewer can read offline."""

    def walk(current: str, chain: tuple[str, ...]) -> None:
        if current in chain:
            raise CommandAdapterError(
                f"{path} REQUIRES makes {mode!r} depend on itself through {current!r}"
            )
        for step in requires.get(current, ()):
            walk(step.mode, (*chain, current))

    walk(mode, ())


def _load_build_env(
    module: ModuleType, path: Path, functional_env: Mapping[str, str]
) -> EnvBuilder:
    raw = getattr(module, "build_env", None)
    if raw is None:
        return _static_env(functional_env)
    if not callable(raw) or not _accepts_one_argument(raw):
        raise CommandAdapterError(f"{path} build_env must be callable as build_env(request)")
    return cast(EnvBuilder, raw)


def _load_validate_artifact(
    module: ModuleType, path: Path, modes: Mapping[str, Mode]
) -> Mapping[str, ArtifactValidator]:
    raw = getattr(module, "VALIDATE_ARTIFACT", None)
    if raw is None:
        return MappingProxyType({})
    if not isinstance(raw, Mapping) or not raw:
        raise CommandAdapterError(
            f"{path} VALIDATE_ARTIFACT must map a producing mode to its validator"
        )
    validators: dict[str, ArtifactValidator] = {}
    for mode, validator in raw.items():
        if mode not in modes:
            raise CommandAdapterError(f"{path} VALIDATE_ARTIFACT names unknown mode {mode!r}")
        if not callable(validator) or not _accepts_one_argument(validator):
            raise CommandAdapterError(
                f"{path} VALIDATE_ARTIFACT[{mode!r}] must be callable as validator(path)"
            )
        validators[mode] = cast(ArtifactValidator, validator)
    return MappingProxyType(validators)


def _check_inline(
    modes: Mapping[str, Mode], requires: Mapping[str, tuple[Requirement, ...]], path: Path
) -> None:
    """Hold an inline setup exec to one flat, offline-readable step.

    An inline mode that itself declared an inline or a chain would put a graph
    back inside one attempt, where no reviewer can see it and no slot records
    what it owes — the whole reason the chain is declared statically.
    """
    for mode, manifest in modes.items():
        inline = manifest.inline
        if not inline:
            continue
        if inline == mode:
            raise CommandAdapterError(f"{path} mode {mode!r} names itself as its inline setup")
        setup = modes.get(inline)
        if setup is None:
            raise CommandAdapterError(f"{path} mode {mode!r} names unknown inline mode {inline!r}")
        if setup.purpose_ceiling != "preparation":
            raise CommandAdapterError(
                f"{path} mode {mode!r} runs {inline!r} as setup, whose capsule caps it at "
                f"{setup.purpose_ceiling!r} — an inline exec produces, it does not measure"
            )
        if setup.inline or inline in requires:
            raise CommandAdapterError(
                f"{path} inline mode {inline!r} needs a step of its own; an inline setup exec "
                f"is one command, not a chain"
            )


def _accepts_one_argument(value: Callable[..., object]) -> bool:
    try:
        inspect.signature(value).bind(object())
    except (TypeError, ValueError):
        return False
    return True


def _check_registered_executable(path: Path, primary: Executable) -> None:
    """Cross-check the primary executable against the capsule's ``build/image.json``.

    That file registers the exact argv the image was built around, so a capsule
    that renames a binary without rebuilding fails here rather than in a job.
    Skipped only where there is no registered image at all — a staged or
    fixture capsule; ``image.json`` records one executable, so any further
    declared executable rides on the same build receipt.
    """
    registered = path.parents[1] / "build" / "image.json"
    if not registered.is_file():
        return
    try:
        document = json.loads(registered.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CommandAdapterError(f"cannot read {registered}: {exc}") from exc
    executable = document.get("executable") if isinstance(document, dict) else None
    if not isinstance(executable, list) or tuple(executable) != primary.argv:
        raise CommandAdapterError(
            f"{path} declares executable {primary.name!r} that is not the registered executable "
            f"in {registered}"
        )


def load_command_adapter(
    path: Path,
    *,
    expected_tool: str | None = None,
) -> LoadedCommandAdapter:
    """Load and validate one capsule-owned ``adapter/command.py`` module."""
    module = _load_module(path)
    build = getattr(module, "build_command", None)
    tool = getattr(module, "TOOL", None)
    config_keys: frozenset[str] = getattr(module, "CONFIG_KEYS", frozenset())
    supports_unsigned = getattr(module, "SUPPORTS_UNSIGNED", None)
    supports_signed = getattr(module, "SUPPORTS_SIGNED", True)
    functional_env = getattr(module, "FUNCTIONAL_ENV", {})
    if not callable(build):
        raise CommandAdapterError(f"{path} does not export callable build_command")
    if not isinstance(tool, str) or not tool:
        raise CommandAdapterError(f"{path} does not export non-empty TOOL")
    if not isinstance(functional_env, dict) or not all(
        isinstance(key, str) and key and isinstance(value, str) and "\x00" not in key + value
        for key, value in functional_env.items()
    ):
        raise CommandAdapterError(f"{path} exports invalid FUNCTIONAL_ENV; expected dict[str, str]")
    if expected_tool is not None and tool != expected_tool:
        raise CommandAdapterError(
            f"bundled driver is for {tool}, not requested tool {expected_tool}"
        )
    if not isinstance(config_keys, frozenset) or not all(
        isinstance(key, str) and key for key in config_keys
    ):
        raise CommandAdapterError(f"{path} exports invalid CONFIG_KEYS; expected frozenset[str]")
    if not isinstance(supports_unsigned, bool):
        raise CommandAdapterError(f"{path} does not export bool SUPPORTS_UNSIGNED")
    if not isinstance(supports_signed, bool):
        raise CommandAdapterError(f"{path} exports a non-bool SUPPORTS_SIGNED")
    if not supports_unsigned and not supports_signed:
        raise CommandAdapterError(f"{path} declares a subject that can issue no request at all")

    executables = _load_executables(module, path)
    modes = _load_modes(module, path, executables)
    requires = _load_requires(module, path, modes)
    _check_inline(modes, requires, path)
    _check_registered_executable(path, executables[0])

    return LoadedCommandAdapter(
        cast(CommandBuilder, build),
        executables[0].argv,
        config_keys,
        supports_unsigned,
        supports_signed,
        tool,
        cast(dict[str, str], functional_env),
        modes,
        executables,
        requires,
        _load_build_env(module, path, functional_env),
        _load_validate_artifact(module, path, modes),
    )


def build_parser(prog: str) -> argparse.ArgumentParser:
    """Build the common inspection CLI used by every command adapter."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Compile friendly listing parameters into a JSON argv array.",
        allow_abbrev=False,
    )
    parser.add_argument("mode")
    parser.add_argument("bucket")
    parser.add_argument("region")
    parser.add_argument("prefix", nargs="?", default="")
    parser.add_argument("--signed", action="store_true")
    parser.add_argument("--config", default="{}", metavar="JSON")
    return parser


def command_adapter_main(
    build: Callable[[CommandRequest], tuple[str, ...]],
    *,
    prog: str,
    argv: Sequence[str] | None = None,
) -> int:
    """Parse one request and print adapter argv as JSON for human inspection."""
    args = build_parser(prog).parse_args(argv)
    try:
        config = json.loads(args.config)
        if not isinstance(config, dict):
            raise CommandAdapterError("--config must be a JSON object")
        command = build(
            CommandRequest(
                args.mode,
                args.bucket,
                args.region,
                args.prefix,
                signed=args.signed,
                config=config,
            )
        )
    except json.JSONDecodeError as exc:
        print(f"{prog}: --config is not valid JSON: {exc}", file=sys.stderr)
        return 2
    except CommandAdapterError as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return 2
    json.dump(command, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0
