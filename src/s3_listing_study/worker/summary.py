"""Worker-owned post-measurement normalization and bounded row counting."""

from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

from s3_listing_study.common.duckdb_adapter import existing_input_path
from s3_listing_study.common.duckdb_runtime import VERSION as DUCKDB_VERSION
from s3_listing_study.common.normalizer_cli import mapped_input
from s3_listing_study.common.python_runtime import interpreter_identity


class SummaryError(RuntimeError):
    """The worker could not form a row count from an otherwise retained attempt."""


def _install_compatibility_aliases() -> None:
    """Expose historical capsule import names without shipping manager code."""
    from s3_listing_study.common import contract, duckdb_adapter, normalizer_cli

    package_name = "s3_listing_study.manager"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = []
        sys.modules[package_name] = package
    aliases = {
        f"{package_name}.contract": contract,
        f"{package_name}.duckdb_adapter": duckdb_adapter,
        f"{package_name}.normalizer_cli": normalizer_cli,
    }
    for name, module in aliases.items():
        sys.modules.setdefault(name, module)


class _LineCounter:
    """A write-only binary sink that keeps only a row count."""

    def __init__(self) -> None:
        self.row_count = 0
        self.bytes_written = 0
        self.last_byte: int | None = None

    def writable(self) -> bool:
        return True

    def write(self, data: bytes) -> int:
        if not isinstance(data, bytes):
            raise TypeError("normalizer output must be bytes")
        self.row_count += data.count(b"\n")
        self.bytes_written += len(data)
        if data:
            self.last_byte = data[-1]
        return len(data)

    def finish(self) -> int:
        if self.bytes_written and self.last_byte != ord("\n"):
            raise SummaryError("normalizer emitted an unterminated contract row")
        return self.row_count


def _load_normalizer(path: Path) -> ModuleType:
    if not path.is_file():
        raise SummaryError(f"normalizer path is not a file: {path}")
    _install_compatibility_aliases()
    name = f"_s3_study_normalizer_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SummaryError(f"could not load normalizer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "normalize", None)):
        raise SummaryError(f"normalizer exports no callable normalize(): {path}")
    return module


def _duckdb_version() -> str:
    import duckdb

    version = str(duckdb.__version__)
    if version != DUCKDB_VERSION:
        raise SummaryError(f"DuckDB version is {version}, expected locked {DUCKDB_VERSION}")
    return version


def _observed_duckdb_version() -> str | None:
    try:
        import duckdb
    except ImportError:
        return None
    return str(duckdb.__version__)


def _count(
    *,
    normalizer_path: Path,
    mode: str,
    prefix: str,
    stdout_path: Path,
    dataset_path: Path | None,
) -> tuple[int, str]:
    module = _load_normalizer(normalizer_path)
    normalize: Any = module.normalize
    counter = _LineCounter()
    if dataset_path is not None:
        returncode = normalize(counter, b"", mode, prefix, str(dataset_path))
    else:
        with existing_input_path(str(stdout_path)), mapped_input(str(stdout_path)) as data:
            returncode = normalize(counter, data, mode, prefix)
    if returncode != 0:
        raise SummaryError(f"normalizer returned {returncode}")
    return counter.finish(), _duckdb_version()


def summarize(
    *,
    outcome_status: str,
    adapter_bundle_sha256: str,
    normalizer_path: Path | None,
    mode: str | None,
    prefix: str | None,
    stdout_path: Path,
    dataset_path: Path | None,
) -> dict[str, object]:
    """Return the typed summary embedded in ``result.json``.

    Tool failures and partial runs are deliberately not counted: their raw
    output remains evidence, but its line count is not the target's row count.
    A normalizer failure is separate from the tool outcome and is returned as
    ``status=error`` so the engine can retain all artifacts and use its distinct
    post-attempt exit policy.
    """
    base: dict[str, object] = {
        "schema_version": 1,
        "status": "skipped",
        "row_count": None,
        "reason": None,
        "error": None,
        "adapter_bundle_sha256": adapter_bundle_sha256,
        "duckdb_version": _observed_duckdb_version(),
        "interpreter": interpreter_identity(),
    }
    if outcome_status != "completed":
        base["reason"] = f"tool_outcome_{outcome_status}"
        return base
    if normalizer_path is None:
        base["reason"] = "normalizer_not_configured"
        return base
    if mode is None:
        base["status"] = "error"
        base["error"] = {"code": "missing_mode", "type": "SummaryError"}
        return base
    try:
        row_count, version = _count(
            normalizer_path=normalizer_path,
            mode=mode,
            prefix=prefix or "",
            stdout_path=stdout_path,
            dataset_path=dataset_path,
        )
    except Exception as exc:  # adapter errors must remain data, not erase evidence
        base["status"] = "error"
        # Exception strings from a parser can quote arbitrary raw listing
        # fragments. Keep the small routinely-read result free of raw keys;
        # the retained raw artifact and Batch diagnostics remain available for
        # a targeted investigation.
        base["error"] = {"code": "normalizer_failed", "type": type(exc).__name__}
        return base
    base.update(status="counted", row_count=row_count, duckdb_version=version)
    return base
