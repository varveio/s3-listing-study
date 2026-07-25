"""Differential the shell adapter against the Python adapter, per tool.

The port replaces eleven ``tools/*/adapter/normalize.sh`` with ``normalize.py``.
The acceptance bar is the same one the verdict replay uses: **byte-identical
stdout**, over every raw payload the repo has committed a ``run.meta`` for. An
adapter that produces different bytes produces a different ``verify.md``, so
nothing weaker is worth running.

Cases are discovered from ``run.meta``, never from directory layout — the
``run.meta`` is what the verifier itself reads the mode and the prefix out of
(``harness/verify-listing.sh:565``, ``:785``), so a case here is invoked exactly
as the verifier would invoke it, by exec'ing the adapter rather than importing
it. That includes the capability probes, whose mode no adapter implements: both
sides must *reject* them, and identically, or a future port has quietly widened
the mode set.

Payload paths are spelled the way ``run.meta`` spells them (``receipts/…`` for
the external data directory, ``tools/…`` for an in-repo one) and are resolved
through :func:`tests.differential.oracle.resolve_payload`, so this harness and
the replay oracle agree on which bytes a case names — and each one is checked
against the sha256 its own ``run.meta`` binds it to before either adapter reads
it, so "byte-identical over N payloads" names the bytes the receipts recorded
and not whatever is on this disk.

A payload that cannot be used — absent, or off its digest — is never a skip and
never a pass. It is ORACLE_UNAVAILABLE, the same 0/1/42 contract tier 1 speaks
(``tests/differential/README.md``): the cases that CAN be resolved are still
compared and still fail on a mismatch, and the ones that cannot are reported
separately, because a checkout without ``$S3_STUDY_DATA`` still holds five
in-repo payloads whose judgement is real.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from tests.differential.corpus import read_meta, select_stream
from tests.differential.oracle import Oracle, data_dir, resolve_payload, sha256_file

# The corpus this gate is pinned to, per tool. Discovery alone cannot police it:
# if a receipt stops being discovered — a restructure moves it, a merge drops it,
# a glob stops matching — the denominator shrinks with the numerator and the run
# reports byte-identical over whatever survived. `tests/differential/__main__.py`
# pins EXPECTED_SINGLES for exactly this reason; this harness would otherwise
# have inherited the bug that pin was added to fix.
#
# Changing a number here is a DECISION, not a formality: it asserts that the set
# of committed payloads for that tool genuinely changed, and it belongs in the
# same commit that adds or removes the receipt, with the reason in the message.
# Never adjust one to make a red gate go green.
EXPECTED_PAYLOADS = {"aws-cli": 15, "rclone": 10}


class PayloadUnavailable(Exception):
    """A payload a discovered case names cannot be used, so that case cannot be judged.

    Either it is not on disk, or its bytes do not match the sha256 its own
    ``run.meta`` records. Both are oracle failures, not adapter findings.
    """


@dataclass(frozen=True)
class AdapterCase:
    """One ``(mode, prefix, payload)`` triple, read back out of a committed ``run.meta``."""

    tool: str
    mode: str
    prefix: str
    payload: str  # exactly as run.meta spells it
    sha256: str  # the binding the committed receipt already carried
    receipt: str  # repo-relative, for reporting

    @property
    def name(self) -> str:
        return f"{self.mode}:{self.receipt}"


@dataclass(frozen=True)
class Comparison:
    """The verdict for one case: the two sides' exit status and stdout."""

    case: AdapterCase
    shell_status: int
    python_status: int
    shell_stdout: bytes
    python_stdout: bytes
    python_stderr: bytes

    @property
    def identical(self) -> bool:
        return self.shell_status == self.python_status and self.shell_stdout == self.python_stdout

    def detail(self) -> str:
        if self.shell_status != self.python_status:
            return f"exit {self.shell_status} (sh) vs {self.python_status} (py)"
        shell, python = self.shell_stdout, self.python_stdout
        for offset, (a, b) in enumerate(zip(shell, python, strict=False)):
            if a != b:
                return (
                    f"stdout differs at byte {offset}: "
                    f"{shell[offset : offset + 60]!r} (sh) vs {python[offset : offset + 60]!r} (py)"
                )
        return f"stdout length differs: {len(shell)} (sh) vs {len(python)} (py)"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ToolReport:
    """Every case for one tool: the ones that were judged, and the ones that could not be.

    Kept apart on purpose. ``comparisons`` is a verdict about the adapters;
    ``unavailable`` is a verdict about the oracle, and folding the second into
    the first — as a skip, or as an exception that abandons the tool at its first
    missing payload — is how a run with no data directory comes back green.
    """

    tool: str
    comparisons: list[Comparison] = field(default_factory=list)
    unavailable: list[tuple[AdapterCase, str]] = field(default_factory=list)

    @property
    def differing(self) -> list[Comparison]:
        return [c for c in self.comparisons if not c.identical]


def oracle_for(repo: Path) -> Oracle:
    """The payload-resolution view of the oracle, without its manifest preflight.

    Only :func:`~tests.differential.oracle.resolve_payload` is wanted here, plus
    the per-payload digests :func:`compare_case` checks. Running the full
    preflight would re-hash a 500 MB manifest to answer a question this harness
    does not ask: no case here reads the manifest.
    """
    root = data_dir()
    return Oracle(
        repo=repo,
        data_dir=root,
        manifests=root / "manifests",
        receipts=root / "receipts",
        registry=repo / "tests/fixtures/registry-254c8cfe.md",
    )


def discover_cases(repo: Path, tool: str) -> list[AdapterCase]:
    """Every committed payload for ``tool``, as the verifier would normalise it."""
    cases = []
    for meta_path in sorted(repo.glob(f"tools/{tool}/receipts/**/run.meta")):
        meta = read_meta(meta_path)
        if meta.get("tool") != tool:
            continue
        mode = meta.get("mode", "")
        if not mode:
            continue
        stream, payload = select_stream(meta, meta_path.parent)
        cases.append(
            AdapterCase(
                tool=tool,
                mode=mode,
                prefix=meta.get("prefix", ""),
                payload=payload,
                sha256=meta.get(f"{stream}_sha256", ""),
                receipt=str(meta_path.parent.relative_to(repo)),
            )
        )
    return cases


def run_adapter(adapter: Path, case: AdapterCase, payload: Path) -> tuple[int, bytes, bytes]:
    with payload.open("rb") as stdin:
        done = subprocess.run(
            [str(adapter), case.mode, case.prefix],
            stdin=stdin,
            capture_output=True,
            check=False,
        )
    return done.returncode, done.stdout, done.stderr


def compare_case(repo: Path, oracle: Oracle, case: AdapterCase) -> Comparison:
    payload = resolve_payload(oracle, case.payload)
    if not payload.is_file():
        raise PayloadUnavailable(f"{case.payload} is not on disk (resolved to {payload})")
    if not case.sha256:
        raise PayloadUnavailable(f"{case.receipt}/run.meta records no sha256 for {case.payload}")
    got = sha256_file(payload)
    if got != case.sha256:
        raise PayloadUnavailable(
            f"{case.payload} is not the payload the receipt bound: "
            f"run.meta says {case.sha256}, {payload} hashes {got}"
        )
    adapters = repo / "tools" / case.tool / "adapter"
    shell_status, shell_stdout, _ = run_adapter(adapters / "normalize.sh", case, payload)
    python_status, python_stdout, python_stderr = run_adapter(
        adapters / "normalize.py", case, payload
    )
    return Comparison(
        case=case,
        shell_status=shell_status,
        python_status=python_status,
        shell_stdout=shell_stdout,
        python_stdout=python_stdout,
        python_stderr=python_stderr,
    )


def compare_tool(repo: Path, tool: str) -> ToolReport:
    """Compare every case for ``tool`` that can be resolved; report the rest as unavailable.

    One unusable payload never abandons the tool: the in-repo payloads need no
    data directory, and discarding their judgement because an external one is
    absent is how this gate would report a clean sweep of nothing.
    """
    oracle = oracle_for(repo)
    report = ToolReport(tool=tool)
    for case in discover_cases(repo, tool):
        try:
            report.comparisons.append(compare_case(repo, oracle, case))
        except PayloadUnavailable as exc:
            report.unavailable.append((case, str(exc)))
    return report


def corpus_shortfall(repo: Path, tool: str) -> str:
    """The pinned-count complaint for ``tool``, or ``""`` if the corpus is the pinned one."""
    expected = EXPECTED_PAYLOADS.get(tool)
    if expected is None:
        return f"{tool}: no pinned payload count in EXPECTED_PAYLOADS"
    found = len(discover_cases(repo, tool))
    if found != expected:
        return (
            f"{tool}: expected {expected} committed payload(s), discovered {found} — "
            "the corpus is not the one this gate is pinned to, so byte-identical over "
            "what survives proves nothing; if the change is deliberate, update "
            "EXPECTED_PAYLOADS in tests/adapters/equivalence.py in the same commit"
        )
    return ""


def payloads_per_mode(cases: list[AdapterCase]) -> Counter[str]:
    """How many committed payloads exercise each mode.

    A mode with **zero** committed payloads is untested by construction, and this
    count is the only thing that says so. It cannot be derived from the case list
    alone — the adapter's own mode set is the other half — so callers pair it
    with :func:`unexercised_modes`.
    """
    return Counter(case.mode for case in cases)


def load_adapter(repo: Path, tool: str) -> ModuleType:
    """Import a tool's ``normalize.py`` by path — ``tools/`` is not an importable package."""
    path = repo / "tools" / tool / "adapter" / "normalize.py"
    name = f"_adapter_{tool.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - a broken checkout
        raise ImportError(f"cannot load adapter: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def unexercised_modes(repo: Path, tool: str) -> set[str]:
    """Modes the adapter declares but no committed payload reaches.

    These are untested by construction: the equivalence run says nothing about
    them, however green it is.
    """
    declared: set[str] = set(load_adapter(repo, tool).MODES)
    return declared - set(payloads_per_mode(discover_cases(repo, tool)))
