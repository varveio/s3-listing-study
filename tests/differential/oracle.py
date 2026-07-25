"""Oracle inputs and the preflight that decides whether a verdict is possible.

The replay is a differential: an implementation under test is judged against the
bytes the *committed* verdicts were produced from. Those bytes are the oracle. If
any of them is absent or does not hash to the value the committed receipts cite,
the harness cannot judge anything at all — that is ORACLE_UNAVAILABLE (42), which
is neither a pass nor a skip.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .corpus import Payload

# Exit-code contract (cleanup plan §7). Every gate in this campaign speaks it.
EXIT_GREEN = 0
EXIT_MISMATCH = 1
EXIT_ORACLE_UNAVAILABLE = 42

# The reference manifest the 85 committed run.meta bind themselves to.
MANIFEST_NAME = "noaa-normals-pds.2026-07-17.tsv.gz"
MANIFEST_SHA256 = "c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb"

# The registry the committed runs saw. docs/smoke-bucket.md has moved on since,
# and the verifier refuses to judge a run against a registry it never saw, so the
# replay pins --registry to this fixture. Recovered from the pre-squash repo
# at 32951ff6:docs/smoke-bucket.md.
REGISTRY_SHA256 = "254c8cfedd06b1b8671c5bbabc753bfe45462124821eacf44bd27b43c67bbced"
REGISTRY_FIXTURE = "tests/fixtures/registry-254c8cfe.md"

DEFAULT_DATA_DIR = "~/s3-list-study-data"

# Interpreter dependencies of the reference side. `tools/aws-cli/adapter/
# normalize.sh:64-82` (mode `s3api-v2-yamlstream`) shells out to bare `python3`
# and imports these; whichever `python3` is first on PATH is the one that runs,
# so the probe has to be a subprocess, not an `import` here.
ADAPTER_MODULES = ("duckdb", "yaml")


class OracleUnavailable(Exception):
    """The oracle inputs are missing or do not match their pinned digests."""


@dataclass(frozen=True)
class Oracle:
    repo: Path
    data_dir: Path
    manifests: Path
    receipts: Path
    registry: Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def data_dir() -> Path:
    """Resolve the external data directory. Never hardcoded to one machine."""
    raw = os.environ.get("S3_STUDY_DATA") or DEFAULT_DATA_DIR
    return Path(raw).expanduser()


def preflight(repo: Path) -> Oracle:
    """Verify every oracle input, or raise OracleUnavailable.

    Acceptance is only ever a sha256 match against the binding that already
    existed in the committed receipts. Bytes may come from anywhere; a broken
    source cannot fake a preimage. Reject on mismatch — never accept-and-explain.
    """
    _check_adapter_modules()

    root = data_dir()
    if not root.is_dir():
        raise OracleUnavailable(
            f"oracle data directory not found: {root} "
            "(set $S3_STUDY_DATA to the directory holding manifests/ and receipts/)"
        )

    manifests = root / "manifests"
    receipts = root / "receipts"
    for sub in (manifests, receipts):
        if not sub.is_dir():
            raise OracleUnavailable(f"oracle data directory has no {sub.name}/: {sub}")

    manifest = manifests / MANIFEST_NAME
    if not manifest.is_file():
        raise OracleUnavailable(f"reference manifest not found: {manifest}")
    got = sha256_file(manifest)
    if got != MANIFEST_SHA256:
        raise OracleUnavailable(
            f"reference manifest digest mismatch: {manifest} is {got}, "
            f"receipts cite {MANIFEST_SHA256}"
        )

    registry = repo / REGISTRY_FIXTURE
    if not registry.is_file():
        raise OracleUnavailable(f"registry fixture not found: {registry}")
    got = sha256_file(registry)
    if got != REGISTRY_SHA256:
        raise OracleUnavailable(
            f"registry fixture digest mismatch: {registry} is {got}, "
            f"receipts cite {REGISTRY_SHA256}"
        )

    return Oracle(
        repo=repo,
        data_dir=root,
        manifests=manifests,
        receipts=receipts,
        registry=registry,
    )


def _check_adapter_modules() -> None:
    """Require every module the reference adapters import, or raise OracleUnavailable.

    Without this the replay of a `python3`-dependent adapter mode fails for want
    of an import and the gate reports exit 1 — "the implementation under test is
    wrong" — about an interpreter the implementation does not control. A missing
    dependency of the *oracle side* is ORACLE_UNAVAILABLE, exactly like a missing
    payload: the harness declined to form an opinion, it did not form a bad one.
    """
    for module in ADAPTER_MODULES:
        probe = subprocess.run(
            ["python3", "-c", f"import {module}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0:
            detail = probe.stderr.strip().splitlines()[-1] if probe.stderr.strip() else ""
            raise OracleUnavailable(
                f"the reference adapters import {module!r} under bare `python3`, and this "
                f"`python3` cannot: {detail} (it is a declared project dependency — install "
                f"it, or run the gate under an interpreter that has it)"
            )


def resolve_payload(oracle: Oracle, path: str) -> Path:
    """Resolve a `run.meta` payload path the way the link farm makes it resolve.

    The farm is three symlinks — `manifests` and `receipts` into the data dir,
    `tools` into the repo — so resolution here has to agree with it exactly, or
    the preflight would vouch for a file the verifier never reads.
    """
    parts = PurePosixPath(path).parts
    roots = {
        "manifests": oracle.manifests,
        "receipts": oracle.receipts,
        "tools": oracle.repo / "tools",
    }
    if parts and parts[0] in roots:
        return roots[parts[0]].joinpath(*parts[1:])
    return oracle.repo / path


def check_payloads(oracle: Oracle, payloads: Iterable[Payload]) -> int:
    """Verify every raw listing the corpus names, or raise OracleUnavailable.

    Without this, a partial restore of the data directory passes preflight, the
    replays fail for want of their inputs, and the gate reports exit 1 — "your
    port is wrong" — when the truth is that the oracle is incomplete. That
    inversion is precisely what the 42 code exists to prevent, so a missing or
    off-digest payload is ORACLE_UNAVAILABLE, never a mismatch.
    """
    seen: dict[str, str] = {}
    for payload in payloads:
        if seen.get(payload.path) == payload.sha256:
            continue
        resolved = resolve_payload(oracle, payload.path)
        if not resolved.is_file():
            raise OracleUnavailable(
                f"raw payload named by the corpus is missing: {payload.path} "
                f"(resolved to {resolved})"
            )
        if not payload.sha256:
            raise OracleUnavailable(f"run.meta records no sha256 for payload {payload.path}")
        got = sha256_file(resolved)
        if got != payload.sha256:
            raise OracleUnavailable(
                f"raw payload digest mismatch: {resolved} is {got}, "
                f"the committed receipt cites {payload.sha256}"
            )
        seen[payload.path] = payload.sha256
    return len(seen)
