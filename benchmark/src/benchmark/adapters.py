"""Load capsule command and normalizer declarations for the benchmark harness.

The benchmark owns no tool-specific argv or output knowledge. It loads each
capsule's command adapter, bounded ``count_rows``, and file/dataset-backed
normalizer through :mod:`benchmark.runtime`.

A capsule's own emit boundary already refuses an unframeable key and emits
canonical mtime (YYYY-MM-DDTHH:MM:SSZ). This module checks the normalize
subprocess's own exit code and structurally validates its file-backed output.

Two adapter-root conventions apply because execution and controller code sit in
different places relative to a checkout:
  - measure.py uses the bundled root /opt/benchmark/tools and selects
    <root>/<tool>/adapter after validating the tool against image metadata.
  - verify.py/campaign.py (full checkout, any of the 11 tools) call
    adapter_dir_for(tool, adapter_root), default adapter_root="tools",
    the repository's ``tools/<tool>/adapter`` path.

Dataset-sink modes (for example Swath's Parquet modes) use the file-backed
``--dataset`` normalizer path.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

from benchmark.contract import canonical_json
from benchmark.runtime.command_adapter import (
    HEAP_PERCENT,
    CommandRequest,
    LoadedCommandAdapter,
    Mode,
    load_command_adapter,
)

DEFAULT_ADAPTER_ROOT = "/opt/benchmark/tools"


class AdapterError(Exception):
    """A capsule's command or normalize step could not be bridged."""


def adapter_dir_for(tool: str, adapter_root: str) -> Path:
    """Return ``<adapter_root>/<tool>/adapter`` for controller-side use."""
    return Path(adapter_root) / tool / "adapter"


def load_adapter(adapter_dir: Path | str, tool: str) -> LoadedCommandAdapter:
    """One bundled capsule's loaded ``command.py``, with its refusals as AdapterError.

    Reached where a caller needs the declaration itself rather than one compiled
    argv -- an inline setup exec's mode, its config, its validator -- so the
    capsule stays the one answer to what it declares.
    """
    try:
        return load_command_adapter(Path(adapter_dir) / "command.py", expected_tool=tool)
    except Exception as exc:
        raise AdapterError(f"{tool}: could not load command adapter: {exc}") from exc


def mode_manifest(adapter_dir: Path | str, tool: str, mode: str) -> Mode:
    """The capsule's manifest for one mode: what it produces and which fields.

    Resolved from the capsule rather than read off a column, because product and
    fields are a pure function of ``(tool, mode)`` at the revision the row
    already pins -- a stored copy would be a second answer that can disagree.
    """
    try:
        return load_adapter(adapter_dir, tool).modes[mode]
    except Exception as exc:
        raise AdapterError(f"{tool}: no mode manifest for {mode!r}: {exc}") from exc


def compile_command(
    adapter_dir: Path | str,
    tool: str,
    *,
    mode: str,
    bucket: str,
    region: str,
    prefix: str = "",
    signed: bool = False,
    config: Mapping[str, object] | None = None,
    sink_dir: str = "",
    artifact_path: str = "",
    visible_memory_gb: float | None = None,
    heap_percent: int = HEAP_PERCENT,
    endpoint_url: str = "",
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Load ``<adapter_dir>/command.py`` and compile this case's exact subject argv.

    Returns ``(argv, env)``: env is the capsule's request-derived environment
    (``LoadedCommandAdapter.build_env(request)``, which defaults to its static
    ``FUNCTIONAL_ENV`` for a capsule that declares nothing request-derived),
    which the caller merges into the subject's env alongside its own base env.
    """
    try:
        adapter = load_command_adapter(Path(adapter_dir) / "command.py", expected_tool=tool)
        request = CommandRequest(
            mode=mode,
            bucket=bucket,
            region=region,
            prefix=prefix,
            tool=tool,
            signed=signed,
            config=config or {},
            sink_dir=sink_dir,
            artifact_path=artifact_path,
            visible_memory_gb=visible_memory_gb,
            heap_percent=heap_percent,
            endpoint_url=endpoint_url,
        )
        return adapter.compile(request), adapter.build_env(request)
    except Exception as exc:
        raise AdapterError(f"{tool}: could not compile command: {exc}") from exc


def _belt_check_path(path: Path) -> None:
    with path.open("rb") as source:
        for number, line in enumerate(source, start=1):
            if line.rstrip(b"\r\n").count(b"\t") != 4:
                raise AdapterError(
                    f"normalize.py line {number} does not have 5 tab-separated fields"
                )


def _normalizer_command(
    adapter_dir: Path | str,
    mode: str,
    prefix: str,
    config: Mapping[str, object],
    *,
    input_path: Path | None = None,
    dataset: Path | None = None,
) -> list[str]:
    normalize_path = Path(adapter_dir) / "normalize.py"
    command = [sys.executable, str(normalize_path), mode, prefix]
    if input_path is not None:
        command.extend(("--input", str(input_path)))
    if dataset is not None:
        command.extend(("--dataset", str(dataset)))
    # Always present, never left to the CLI's own default: the same blob
    # command.py compiled argv from, so a capsule whose output shape depends
    # on a config key can parse its own output.
    command.extend(("--config", canonical_json(dict(config))))
    return command


def normalize_to_path(
    adapter_dir: Path | str,
    tool: str,
    mode: str,
    prefix: str,
    output_path: Path,
    *,
    input_path: Path | None = None,
    dataset: Path | None = None,
    config: Mapping[str, object] | None = None,
) -> None:
    """Normalize a file or native dataset without materializing it in memory."""
    if (input_path is None) == (dataset is None):
        raise AdapterError("normalization requires exactly one of input_path or dataset")
    with output_path.open("wb") as output:
        result = subprocess.run(
            _normalizer_command(
                adapter_dir, mode, prefix, config or {}, input_path=input_path, dataset=dataset
            ),
            stdout=output,
            stderr=subprocess.PIPE,
            check=False,
        )
    if result.returncode != 0:
        raise AdapterError(
            f"{tool} normalize.py ({mode}) exited {result.returncode}: "
            f"{result.stderr.decode(errors='replace')[:500]}"
        )
    _belt_check_path(output_path)


def _load_normalizer(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_benchmark_normalizer_{uuid.uuid4().hex}", path)
    if spec is None or spec.loader is None:
        raise AdapterError(f"could not load normalizer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def count_rows(
    adapter_dir: Path | str,
    tool: str,
    mode: str,
    prefix: str,
    input_path: Path,
    native_root: Path,
) -> int:
    """Call the capsule's bounded, mode-aware ``count_rows`` implementation."""
    try:
        module = _load_normalizer(Path(adapter_dir) / "normalize.py")
        counter: Any = module.count_rows
        from benchmark.runtime.duckdb_adapter import existing_input_path
        from benchmark.runtime.normalizer_cli import mapped_input

        with existing_input_path(str(input_path)), mapped_input(str(input_path)) as data:
            value = counter(data, mode, prefix, str(native_root))
    except Exception as exc:
        raise AdapterError(f"{tool}: count_rows failed: {exc}") from exc
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AdapterError(f"{tool}: count_rows returned a nonnegative-integer violation")
    return value
