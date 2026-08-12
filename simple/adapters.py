"""The bridge to the real repo's tool capsules: command_adapter + normalizer CLI.

This is the seam simple/ binds to instead of duplicating (see README.md's
architecture section). Round 2 had simple/tools/, a from-scratch guess at
each tool's argv shape and native-output columns; that guessing is gone.
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

Two different adapter-directory conventions apply, because the worker and
the manager sit in different places relative to a checkout:
  - measure.py (worker; one derived image bundles exactly one tool) passes
    --adapter-dir pointing directly at a directory holding
    command.py/normalize.py -- default DEFAULT_ADAPTER_DIR, mirroring
    worker/driver.py's BUNDLED_ADAPTER. This sketch's image never actually
    stages one (see README.md); the default documents real-pipeline intent.
  - verify.py/campaign.py (manager; full checkout, any of the 11 tools) call
    adapter_dir_for(tool, adapter_root), default adapter_root="tools",
    mirroring worker/cli.py's local-checkout fallback
    (Path.cwd() / "tools" / tool / "adapter").

Dataset-sink modes (a mode whose tool refuses to stream, e.g. swath's
Parquet modes) are out of scope for normalize_attempt: it only runs the
stdin path. compile_command still passes sink_dir through unconditionally,
since a capsule that does not need it simply ignores it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from s3_listing_study.common.command_adapter import CommandRequest, load_command_adapter

DEFAULT_ADAPTER_DIR = "/opt/s3-listing-study/tool"


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
            mode=mode, bucket=bucket, region=region, prefix=prefix, tool=tool,
            auth=auth, sink_dir=sink_dir,
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


def normalize_attempt(adapter_dir: Path | str, tool: str, mode: str, prefix: str, native: bytes) -> bytes:
    """Run ``<adapter_dir>/normalize.py <mode> [prefix]``: native bytes on
    stdin, contract v2 TSV on stdout (normalizer_cli.normalizer_main).
    """
    normalize_path = Path(adapter_dir) / "normalize.py"
    result = subprocess.run(
        ["python3", str(normalize_path), mode, prefix],
        input=native, capture_output=True,
    )
    if result.returncode != 0:
        raise AdapterError(
            f"{tool} normalize.py ({mode}) exited {result.returncode}: "
            f"{result.stderr.decode(errors='replace')[:500]}"
        )
    _belt_check(result.stdout)
    return result.stdout
