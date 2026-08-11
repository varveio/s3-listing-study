"""Worker-owned post-measurement, bounded native row counting."""

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
from s3_listing_study.worker.runtime_identity import interpreter_identity


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


def _load_adapter(path: Path) -> ModuleType:
    if not path.is_file():
        raise SummaryError(f"adapter path is not a file: {path}")
    _install_compatibility_aliases()
    name = f"_s3_study_summary_adapter_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SummaryError(f"could not load adapter: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "count_rows", None)):
        raise SummaryError(f"adapter exports no callable count_rows(): {path}")
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
    adapter_path: Path,
    mode: str,
    prefix: str,
    stdout_path: Path,
    native_root: Path | None,
) -> tuple[int, str]:
    module = _load_adapter(adapter_path)
    count_rows: Any = module.count_rows
    with existing_input_path(str(stdout_path)), mapped_input(str(stdout_path)) as data:
        row_count = count_rows(data, mode, prefix, str(native_root) if native_root else "")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
        raise SummaryError("count_rows() must return a nonnegative integer")
    return row_count, _duckdb_version()


def summarize(
    *,
    outcome_status: str,
    adapter_bundle_sha256: str,
    adapter_path: Path | None,
    mode: str | None,
    prefix: str | None,
    stdout_path: Path,
    native_root: Path | None,
) -> dict[str, object]:
    """Return the typed count-only summary embedded in ``result.json``.

    Tool failures and partial runs are deliberately not counted: their raw
    output remains evidence, but its row count is not the target's completed
    logical object count. A counting failure is separate from the tool outcome
    and is returned as ``status=error`` so the engine can retain all artifacts
    and use its distinct post-attempt exit policy.
    """
    base: dict[str, object] = {
        "schema_version": 2,
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
    if adapter_path is None:
        base["reason"] = "adapter_not_configured"
        return base
    if mode is None:
        base["status"] = "error"
        base["error"] = {"code": "missing_mode", "type": "SummaryError"}
        return base
    try:
        row_count, version = _count(
            adapter_path=adapter_path,
            mode=mode,
            prefix=prefix or "",
            stdout_path=stdout_path,
            native_root=native_root,
        )
    except Exception as exc:  # adapter errors must remain data, not erase evidence
        base["status"] = "error"
        # Exception strings from a parser can quote arbitrary raw listing
        # fragments. Keep the small routinely-read result free of raw keys;
        # the retained raw artifact and Batch diagnostics remain available for
        # a targeted investigation.
        base["error"] = {"code": "row_count_failed", "type": type(exc).__name__}
        return base
    base.update(status="counted", row_count=row_count, duckdb_version=version)
    return base
