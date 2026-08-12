"""The tool registry: builds TOOLS from an explicit list of per-tool modules.

This package replaces round 1/2's single tools.py, mirroring the real
repo's tools/<tool>/adapter/ capsule boundary: per-tool adapter knowledge
(the argv shape, the native-output normalizer, the `# approximate` markers)
is a different concern from the runner/worker suite around it, with a
different owner and change cadence per tool. The original single-file
layout was the right *content*, just the wrong *unit* -- this is a
structure-only move, no behavior change.

Each tools/<name>.py exposes:
    TOOL           -- the TOOLS registry key, e.g. "aws-cli"
    argv(bucket, prefix[, attempt_dir]) -- the listing command
    normalize_sql(native_path)          -- DuckDB SELECT to contract columns
    NATIVE_FILE    -- suffix hint for the decompressed stdout copy (optional)
    HEAP_ENV / HEAP_TEMPLATE            -- heap-sizing env var (optional)

No importlib discovery magic: the module list below is a plain, greppable
import list, exactly as adding an adapter to the real repo means adding a
line somewhere real, not registering into a scanned directory.
"""

from __future__ import annotations

from . import (
    aws_cli,
    minio_mc,
    ps3,
    rclone,
    s3_fast_list,
    s3kor,
    s3p,
    s4cmd,
    s5cmd,
    s7cmd,
    swath,
)

_MODULES = (aws_cli, minio_mc, ps3, rclone, s3_fast_list, s3kor, s3p, s4cmd, s5cmd, s7cmd, swath)


def _entry(argv, normalize_sql, *, native_file=None, native=None, heap_env=None, heap_template=None) -> dict:
    entry = {"argv": argv, "normalize_sql": normalize_sql}
    if native_file is not None:
        entry["native_file"] = native_file
    if native is not None:
        entry["native"] = native
    if heap_env is not None:
        entry["heap_env"] = heap_env
        entry["heap_template"] = heap_template
    return entry


TOOLS: dict[str, dict] = {
    module.TOOL: _entry(
        module.argv, module.normalize_sql,
        native_file=getattr(module, "NATIVE_FILE", None),
        heap_env=getattr(module, "HEAP_ENV", None),
        heap_template=getattr(module, "HEAP_TEMPLATE", None),
    )
    for module in _MODULES
}
# swath's Parquet-sink mode is a second entry off the same module (see
# tools/swath.py's docstring), not a twelfth tool.
TOOLS[swath.PARQUET_TOOL] = _entry(swath.parquet_argv, swath.parquet_normalize_sql, native=swath.PARQUET_NATIVE)


UNFRAMEABLE_CHARS = ("\t", "\n", "\r")


class FramingViolation(Exception):
    """A key contains TAB/LF/CR, which a one-record-per-line TAB-delimited
    TSV cannot carry. Raised to refuse producing a corrupt row, never to
    silently drop or escape it -- see common/contract.py's UNFRAMEABLE_BYTES
    for the real (bytes-based, non-UTF-8-safe) answer this sketch does not
    reproduce; this sketch works in DuckDB VARCHAR (UTF-8 text) throughout,
    so a non-UTF-8 key is out of scope here too (see README.md).
    """


def assert_framing_safe(con, select_sql: str) -> None:
    """Run once, immediately before COPYing `select_sql` to a contract TSV --
    the one shared check manifest.py and verify.py's normalize step both call,
    so a corrupt row (a key containing the delimiter or a line break) is
    refused at the point of TSV production instead of being risked per-tool.
    """
    condition = " OR ".join(f"key LIKE '%' || chr({ord(c)}) || '%'" for c in UNFRAMEABLE_CHARS)
    bad = con.execute(f"SELECT count(*) FROM ({select_sql}) WHERE {condition}").fetchone()[0]
    if bad:
        raise FramingViolation(f"{bad} row(s) have a key containing TAB/LF/CR; cannot frame as TSV")


HEAP_PERCENT = 75


def heap_env_for(tool: str, container_memory_mib: int | None) -> tuple[str, str] | None:
    """The one (name, value) environment pair `tool` needs sized to its
    container ceiling, or None for the tools with no heap to size.

    Driven by each tool module's own HEAP_ENV/HEAP_TEMPLATE attributes
    (currently swath, s3p) rather than a central table, now that a tool's
    heap policy lives with the rest of that tool's knowledge. Real
    bench/tools.yaml substitutes both {percent} (JVM) and {mib} (V8) per
    case; a tool only ever uses one of the two placeholders in its template.
    """
    spec = TOOLS.get(tool, {})
    env, template = spec.get("heap_env"), spec.get("heap_template")
    if env is None or container_memory_mib is None:
        return None
    return env, template.format(percent=HEAP_PERCENT, mib=int(container_memory_mib * HEAP_PERCENT / 100))
