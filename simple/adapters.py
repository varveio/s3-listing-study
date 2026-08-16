"""Bridge the production candidate to real command and normalizer capsules.

The candidate owns no tool-specific argv or output knowledge. It loads each
capsule's command adapter, bounded ``count_rows``, and file/dataset-backed
normalizer CLI through the stable common package.
Every argv compiled here comes from the SAME tools/<tool>/adapter/command.py
the real worker loads (src/s3_listing_study/common/command_adapter.py's
load_command_adapter), and every normalization runs the SAME
tools/<tool>/adapter/normalize.py CLI the real study's own verifier calls
through (src/s3_listing_study/manager/normalizer_cli.py's normalizer_main).

A capsule's own emit boundary already refuses an unframeable key and already
emits canonical mtime (YYYY-MM-DDTHH:MM:SSZ) -- round 2's framing-safety and
mtime-canonicalization machinery is not reproduced here. "The capsule CLI
already did it" is the whole answer; this module checks only the normalize
subprocess's own exit code, plus one free structural sanity pass
(_belt_check) that costs no extra I/O since the bytes are already in hand.

Two adapter-root conventions apply, because the worker and the manager sit in
different places relative to a checkout:
  - measure.py uses the bundled root /opt/simple/tools and selects
    <root>/<tool>/adapter after validating the tool against image metadata.
  - verify.py/campaign.py (manager; full checkout, any of the 11 tools) call
    adapter_dir_for(tool, adapter_root), default adapter_root="tools",
    mirroring worker/cli.py's local-checkout fallback
    (Path.cwd() / "tools" / tool / "adapter").

Dataset-sink modes (for example Swath's Parquet modes) use the file-backed
``--dataset`` normalizer path. ``normalize_attempt`` remains only a small-fixture
compatibility helper for stream-shaped inputs.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import types
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

from s3_listing_study.common.command_adapter import CommandRequest, load_command_adapter

DEFAULT_ADAPTER_ROOT = "/opt/simple/tools"


class AdapterError(Exception):
    """A capsule's command or normalize step could not be bridged."""


def adapter_dir_for(tool: str, adapter_root: str) -> Path:
    """``<adapter_root>/<tool>/adapter`` -- the manager-side checkout convention."""
    return Path(adapter_root) / tool / "adapter"


def compile_command(
    adapter_dir: Path | str,
    tool: str,
    *,
    mode: str,
    bucket: str,
    region: str,
    prefix: str = "",
    auth: str = "anonymous",
    sink_dir: str = "",
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Load ``<adapter_dir>/command.py`` and compile this case's exact subject argv.

    Returns ``(argv, functional_env)``: functional_env is the capsule's own
    non-secret, tool-specific environment (LoadedCommandAdapter.functional_env),
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
            auth=auth,
            sink_dir=sink_dir,
        )
        return adapter.compile(request), adapter.functional_env
    except Exception as exc:
        raise AdapterError(f"{tool}: could not compile command: {exc}") from exc


def _belt_check(tsv: bytes) -> None:
    """Free structural sanity pass -- the capsule's emit boundary is authoritative.

    Not a framing check (that already happened inside the capsule); this only
    catches a normalizer that silently emitted a short/ragged row, which
    would otherwise surface many fields later as a confusing DuckDB load
    error or, worse, a load that succeeds with columns shifted.
    """
    for number, line in enumerate(tsv.split(b"\n"), start=1):
        if line and line.count(b"\t") != 4:
            raise AdapterError(f"normalize.py line {number} does not have 5 tab-separated fields")


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
) -> None:
    """Normalize a file or native dataset without materializing it in memory."""
    if (input_path is None) == (dataset is None):
        raise AdapterError("normalization requires exactly one of input_path or dataset")
    with output_path.open("wb") as output:
        result = subprocess.run(
            _normalizer_command(adapter_dir, mode, prefix, input_path=input_path, dataset=dataset),
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


def normalize_attempt(
    adapter_dir: Path | str, tool: str, mode: str, prefix: str, native: bytes
) -> bytes:
    """Compatibility helper for small, stream-shaped fixtures."""
    normalize_path = Path(adapter_dir) / "normalize.py"
    result = subprocess.run(
        [sys.executable, str(normalize_path), mode, prefix], input=native, capture_output=True
    )
    if result.returncode != 0:
        raise AdapterError(
            f"{tool} normalize.py ({mode}) exited {result.returncode}: "
            f"{result.stderr.decode(errors='replace')[:500]}"
        )
    _belt_check(result.stdout)
    return result.stdout


def _install_compatibility_aliases() -> None:
    from s3_listing_study.common import contract, duckdb_adapter, normalizer_cli

    package_name = "s3_listing_study.manager"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = []  # type: ignore[attr-defined]
        sys.modules[package_name] = package
    for name, module in {
        f"{package_name}.contract": contract,
        f"{package_name}.duckdb_adapter": duckdb_adapter,
        f"{package_name}.normalizer_cli": normalizer_cli,
    }.items():
        sys.modules.setdefault(name, module)


def _load_normalizer(path: Path) -> ModuleType:
    _install_compatibility_aliases()
    spec = importlib.util.spec_from_file_location(f"_simple_normalizer_{uuid.uuid4().hex}", path)
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
        from s3_listing_study.common.duckdb_adapter import existing_input_path
        from s3_listing_study.common.normalizer_cli import mapped_input

        with existing_input_path(str(input_path)), mapped_input(str(input_path)) as data:
            value = counter(data, mode, prefix, str(native_root))
    except Exception as exc:
        raise AdapterError(f"{tool}: count_rows failed: {exc}") from exc
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AdapterError(f"{tool}: count_rows returned a nonnegative-integer violation")
    return value
