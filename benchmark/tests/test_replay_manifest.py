from __future__ import annotations

import gzip
from pathlib import Path

import duckdb
import pytest

from benchmark.contract import sha256_of
from benchmark.replay_manifest import ManifestError, build_manifest, fixture_digest
from benchmark.runtime.contract import read_records


def write_part(path: Path, rows: list[tuple[bytes, int, str, str, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    try:
        connection.execute(
            "CREATE TABLE fixture (key BLOB, size BIGINT, last_modified TIMESTAMPTZ, "
            "etag VARCHAR, storage_class VARCHAR, row_type VARCHAR)"
        )
        connection.executemany("INSERT INTO fixture VALUES (?, ?, ?, ?, ?, ?)", rows)
        connection.execute("COPY fixture TO ? (FORMAT PARQUET)", [str(path)])
    finally:
        connection.close()


def object_row(key: bytes, size: int = 1) -> tuple[bytes, int, str, str, str, str]:
    return (key, size, "2026-01-02T03:04:05.987654Z", "etag", "STANDARD", "OBJECT")


def test_manifest_bytes_are_deterministic_and_preserve_raw_keys(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    first = fixture / "part-000.parquet"
    second = fixture / "part-001.parquet"
    write_part(
        first,
        [
            object_row(b"a"),
            object_row(b"raw-\xff"),
            (*object_row(b"rollup")[:-1], "COMMON_PREFIX"),
        ],
    )
    write_part(second, [object_row(b"z", 9)])

    one = tmp_path / "one.tsv.gz"
    two = tmp_path / "two.tsv.gz"
    expected_fixture = fixture_digest((first.resolve(), second.resolve()))
    descriptor_one = build_manifest([fixture], one, supplied_fixture_sha256=expected_fixture)
    descriptor_two = build_manifest([first, second], two)

    assert one.read_bytes() == two.read_bytes()
    assert descriptor_one["manifest_sha256"] == descriptor_two["manifest_sha256"]
    assert descriptor_one["manifest_sha256"] == sha256_of(one)
    assert descriptor_one["row_count"] == 3
    with gzip.open(one, "rb") as stream:
        records = list(read_records(stream))
    assert [record.key for record in records] == [b"a", b"raw-\xff", b"z"]
    assert records[0].mtime == "2026-01-02T03:04:05Z"


@pytest.mark.parametrize(
    "keys",
    [
        pytest.param([b"a", b"a"], id="duplicate"),
        pytest.param([b"z", b"a"], id="out-of-order"),
    ],
)
def test_manifest_refuses_duplicate_or_out_of_order_keys(tmp_path: Path, keys: list[bytes]) -> None:
    part = tmp_path / "part.parquet"
    write_part(part, [object_row(key) for key in keys])
    with pytest.raises(ManifestError, match=r"duplicate|out-of-order"):
        build_manifest([part], tmp_path / "manifest.tsv.gz")


def test_manifest_refuses_the_wrong_fixture_digest(tmp_path: Path) -> None:
    part = tmp_path / "part.parquet"
    write_part(part, [object_row(b"a")])
    with pytest.raises(ManifestError, match="fixture sha256 mismatch"):
        build_manifest([part], tmp_path / "manifest.tsv.gz", supplied_fixture_sha256="0" * 64)
    assert not (tmp_path / "manifest.tsv.gz").exists()


def test_manifest_refuses_unframeable_keys_and_output_overwrite(tmp_path: Path) -> None:
    part = tmp_path / "part.parquet"
    write_part(part, [object_row(b"bad\tkey")])
    with pytest.raises(ManifestError, match="contract v2"):
        build_manifest([part], tmp_path / "manifest.tsv.gz")

    output = tmp_path / "existing.tsv.gz"
    output.write_bytes(b"owned")
    with pytest.raises(ManifestError, match="already exists"):
        build_manifest([part], output)
    assert output.read_bytes() == b"owned"
