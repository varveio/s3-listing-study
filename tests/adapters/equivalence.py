"""Discover the committed payload corpus each ``normalize.py`` adapter answers for.

The shell adapters this module was built to differential against are gone, and
with them the byte-for-byte comparison that proved the port. What survives is the
part that does not need two implementations: WHICH committed payloads exist, per
tool, and which of an adapter's declared modes no payload reaches.

Cases are discovered from ``run.meta``, never from directory layout — the
``run.meta`` is what the verifier itself reads the mode and the prefix out of —
so a case here names exactly what the verifier would normalise. That includes the
capability probes, whose mode no adapter implements.

A retired differential predecessor re-issued committed verdicts through the
ported adapters. That historical gate supplied the reason for the invariant
preserved here: the discovered corpus needs an independent pinned denominator,
or a shrinking payload set can look like a clean sweep. Current adapter byte
conformance is exercised with synthetic fixtures; this module covers historical
payload discovery and mode reachability only.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

# SHA-256 of zero bytes, recorded by run.meta for an empty captured stream.
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def read_meta(path: Path) -> dict[str, str]:
    """Parse historical ``run.meta`` with first occurrence winning."""
    meta: dict[str, str] = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key not in meta:
            meta[key] = value
    return meta


def select_stream(meta: dict[str, str], receipt: Path) -> tuple[str, str]:
    """Choose the first non-empty historical stream; stdout wins non-empty ties.

    Candidate order is deliberate. Most listings belong on stdout, while some
    historical adapters emitted their payload on stderr. ``EMPTY_SHA256`` lets
    the recorded digest distinguish an empty file from an absent stream without
    reading the payload during discovery.
    """
    candidates = [
        (stream, meta.get(f"{stream}_path", ""), meta.get(f"{stream}_sha256", ""))
        for stream in ("stdout", "stderr")
        if meta.get(f"{stream}_path") and meta.get(f"{stream}_sha256")
    ]
    for stream, path, digest in candidates:
        if digest != EMPTY_SHA256:
            return stream, path
    if candidates:
        return candidates[0][0], candidates[0][1]
    raise ValueError(f"run.meta in {receipt} records no stdout/stderr payload")


# The corpus this gate is pinned to, per tool. Discovery alone cannot police it:
# if a receipt stops being discovered — a restructure moves it, a merge drops it,
# a glob stops matching — the denominator shrinks with the numerator and the run
# reports success over whatever survived. The retired differential predecessor
# independently pinned its case count for exactly this reason; this gate keeps
# that mutation-resistant denominator without depending on the deleted suite.
#
# Changing a number here is a DECISION, not a formality: it asserts that the set
# of committed payloads for that tool genuinely changed, and it belongs in the
# same commit that adds or removes the receipt, with the reason in the message.
# Never adjust one to make a red gate go green.
EXPECTED_PAYLOADS = {
    "aws-cli": 15,
    "minio-mc": 10,
    "ps3": 1,
    "rclone": 10,
    "s3-fast-list": 4,
    "s3kor": 2,
    "s3p": 3,
    "s4cmd": 1,
    "s5cmd": 14,
    "s7cmd": 13,
    # The v0.1.0 receipt corpus was retired with that subject. The v0.2.0 files
    # are explicitly observations, not harness payloads suitable for replay.
    "swath": 0,
}


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


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


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
