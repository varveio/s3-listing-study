"""Tests for s3_listing_study.host.collect: row counts and Parquet conversion."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import duckdb

from s3_listing_study.host.collect import CollectError, _collect

REPO = Path(__file__).resolve().parents[1]


def _write_attempt(
    tmp_path: Path,
    *,
    stdout: bytes,
    mode: str = "s3api-v2-text",
    prefix: str = "",
    status: str = "completed",
) -> Path:
    attempt_dir = tmp_path / "attempt-1"
    attempt_dir.mkdir()
    (attempt_dir / "stdout.raw.gz").write_bytes(gzip.compress(stdout))
    result = {
        "outcome": {"status": status},
        "target": {"mode": mode, "prefix": prefix},
    }
    (attempt_dir / "result.json").write_text(json.dumps(result))
    return attempt_dir


def test_row_count_matches_lines_of_contract_v2_output(tmp_path: Path) -> None:
    stdout = (
        b"normals-hourly/a.csv\t100\tabc\t2026-07-22T12:54:13Z\tSTANDARD\n"
        b"normals-hourly/b.csv\t200\tdef\t2026-07-22T12:55:13Z\tSTANDARD\n"
    )
    attempt_dir = _write_attempt(tmp_path, stdout=stdout)
    collected = _collect(attempt_dir, "aws-cli", convert_parquet=False)
    assert collected["row_count"] == 2
    assert "parquet_path" not in collected


def test_convert_parquet_writes_a_readable_five_column_file(tmp_path: Path) -> None:
    stdout = b"normals-hourly/a.csv\t100\tabc\t2026-07-22T12:54:13Z\tSTANDARD\n"
    attempt_dir = _write_attempt(tmp_path, stdout=stdout)
    collected = _collect(attempt_dir, "aws-cli", convert_parquet=True)
    parquet_path = attempt_dir / collected["parquet_path"]
    assert parquet_path.is_file()
    rows = duckdb.sql(f"SELECT * FROM read_parquet('{parquet_path}')").fetchall()
    assert rows == [("normals-hourly/a.csv", "100", "abc", "2026-07-22T12:54:13Z", "STANDARD")]


def test_incomplete_attempt_is_recorded_not_normalized(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path, stdout=b"", status="failed")
    collected = _collect(attempt_dir, "aws-cli", convert_parquet=False)
    assert collected["row_count"] is None
    assert collected["reason"] == "attempt did not complete"


def test_unknown_tool_raises_collect_error(tmp_path: Path) -> None:
    attempt_dir = _write_attempt(tmp_path, stdout=b"")
    try:
        _collect(attempt_dir, "not-a-real-tool", convert_parquet=False)
    except CollectError as exc:
        assert "no normalizer" in str(exc)
    else:
        raise AssertionError("expected CollectError")


def test_non_utf8_normalized_output_refuses_parquet_conversion_rather_than_corrupt(
    tmp_path: Path,
) -> None:
    from s3_listing_study.host.collect import _write_parquet

    try:
        _write_parquet(b"\xff\xfe not valid utf-8\n", tmp_path / "out.parquet")
    except CollectError as exc:
        assert "non-UTF-8" in str(exc)
    else:
        raise AssertionError("expected CollectError")
