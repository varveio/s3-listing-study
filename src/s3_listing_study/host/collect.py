"""Post-attempt collection: row counts, and optionally Parquet, from a finalized attempt.

Runs entirely after an attempt directory is already complete — after the
derived image's container has already exited — so its own runtime never
touches ``result.json``'s ``elapsed_ns``; nothing here runs inside a timed
window. Reuses each tool's own ``adapter/normalize.py`` as an unmodified
subprocess, through its documented CLI contract (raw bytes on stdin,
contract-v2 TSV on stdout, or ``--dataset DIR`` for a mode whose sink is a
directory) — this module holds no per-tool parsing logic of its own.

Writes a companion ``collected.json`` next to the attempt directory's own
``result.json``; never rewrites ``result.json`` itself, which the attempt
engine already documents as written atomically and last.
"""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


class CollectError(RuntimeError):
    """A finalized attempt's output could not be normalized or converted."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _normalize(tool: str, mode: str, prefix: str, stdin_data: bytes, dataset: Path | None) -> bytes:
    normalize_path = _repo_root() / "tools" / tool / "adapter" / "normalize.py"
    if not normalize_path.is_file():
        raise CollectError(f"no normalizer for tool: {tool}")
    argv = [sys.executable, str(normalize_path), mode, prefix]
    if dataset is not None:
        argv += ["--dataset", str(dataset)]
        stdin_data = b""
    completed = subprocess.run(argv, input=stdin_data, capture_output=True, check=False)
    if completed.returncode != 0:
        raise CollectError(
            f"normalize.py exited {completed.returncode} for {tool}/{mode}: "
            f"{completed.stderr.decode('utf-8', 'replace')}"
        )
    return completed.stdout


def _write_parquet(tsv: bytes, parquet_path: Path) -> None:
    """Convert contract-v2 TSV to Parquet with one generic query — no per-tool logic.

    Assumes the normalized TSV is valid UTF-8, which holds for every ASCII and
    ordinary-Unicode key; a key containing raw non-UTF-8 bytes (rare, no
    committed corpus case yet exercises one) would fail this read_csv rather
    than being carried through byte-for-byte the way the BLOB-typed DuckDB
    adapter path can for a native binary sink. Known limitation, not silently
    mishandled: this raises CollectError instead of writing a wrong file.
    """
    from s3_listing_study.host.contract import FIELD_NAMES
    from s3_listing_study.host.duckdb_adapter import connect, staged

    try:
        tsv.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CollectError(
            "normalized output contains non-UTF-8 bytes; Parquet conversion via "
            "read_csv cannot carry it byte-for-byte (known limitation)"
        ) from exc

    # DuckDB's COPY ... TO grammar does not accept a bound parameter for the
    # destination path, so both paths are escaped and embedded as literals
    # rather than bound with `?` — both are internally-controlled temp/attempt
    # paths, never untrusted input.
    columns = ", ".join(f"'{name}': 'VARCHAR'" for name in FIELD_NAMES)
    with staged(tsv) as path:
        connection = connect()
        escaped_source = path.replace("'", "''")
        escaped_dest = str(parquet_path).replace("'", "''")
        connection.execute(
            f"COPY (SELECT * FROM read_csv('{escaped_source}', delim='\t', header=false, "
            f"columns={{{columns}}})) TO '{escaped_dest}' (FORMAT PARQUET)"
        )


def _collect(attempt_dir: Path, tool: str, *, convert_parquet: bool) -> dict[str, Any]:
    result = json.loads((attempt_dir / "result.json").read_text())
    if result["outcome"]["status"] != "completed":
        return {"schema_version": 1, "row_count": None, "reason": "attempt did not complete"}

    mode = result["target"]["mode"]
    prefix = result["target"].get("prefix") or ""
    native_records = result.get("native_output") or []
    if native_records:
        stdin_data = b""
        dataset = attempt_dir / "native"
    else:
        stdin_data = gzip.decompress((attempt_dir / "stdout.raw.gz").read_bytes())
        dataset = None

    tsv = _normalize(tool, mode, prefix, stdin_data, dataset)
    row_count = tsv.count(b"\n")

    collected: dict[str, Any] = {"schema_version": 1, "row_count": row_count}

    if convert_parquet:
        parquet_path = attempt_dir / "normalized.parquet"
        _write_parquet(tsv, parquet_path)
        collected["parquet_path"] = parquet_path.name

    return collected


def collect_attempt_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="s3-listing-study collect-attempt")
    parser.add_argument("--attempt-dir", required=True, type=Path)
    parser.add_argument("--tool", required=True)
    parser.add_argument(
        "--convert-parquet",
        action="store_true",
        help="also write normalized.parquet (skipped by default — the expensive part)",
    )
    args = parser.parse_args(argv)

    if not (args.attempt_dir / "result.json").is_file():
        print(
            f"collect-attempt: not a finalized attempt directory: {args.attempt_dir}",
            file=sys.stderr,
        )
        return 2

    try:
        collected = _collect(args.attempt_dir, args.tool, convert_parquet=args.convert_parquet)
    except CollectError as exc:
        print(f"collect-attempt: {exc}", file=sys.stderr)
        return 2

    (args.attempt_dir / "collected.json").write_text(json.dumps(collected, indent=2) + "\n")
    print(f"collect-attempt: {json.dumps(collected)}", file=sys.stderr)
    return 0
