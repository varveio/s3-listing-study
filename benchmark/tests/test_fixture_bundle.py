"""``fixture_bundle``'s s5cmd shard union must actually be disjoint.

Fanout renders every shard as an unslashed ``s3://bucket/{shard}*`` glob, so a
shard that is a string prefix of another (``v1``/``v10``,
``index.html``/``index.html.bak``) would double-list every key under the
shorter one -- silently contradicting the "complete, disjoint" union the
manifest and README claim.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from benchmark.fixture_bundle import (
    FixtureBundleError,
    _generate_s5cmd_shards,
    _physical_order_validation,
)


def _write_fixture(data_dir: Path, keys: tuple[str, ...]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    _write_part(data_dir / "part-00000.parquet", keys)


def _write_part(path: Path, keys: tuple[str, ...]) -> None:
    values = ", ".join(f"('{key}')" for key in keys)
    with duckdb.connect() as connection:
        connection.execute(
            f"""
            COPY (
                SELECT encode(k) AS key, 'OBJECT' AS row_type
                FROM (VALUES {values}) AS t(k)
            ) TO '{path}' (FORMAT PARQUET)
            """
        )


def test_colliding_top_level_shards_are_refused(tmp_path: Path) -> None:
    """``v1`` prefixes ``v10``: a fanout `s3://bucket/v1*` glob would also match
    every key under ``v10/``, so the two shards are not disjoint.
    """
    data_dir = tmp_path / "data"
    _write_fixture(data_dir, ("v1/a.dat", "v10/b.dat", "v2/c.dat"))

    with pytest.raises(FixtureBundleError, match="not prefix-free"):
        _generate_s5cmd_shards(data_dir, tmp_path / "s5cmd-shards.input")


def test_colliding_dotted_top_level_shards_are_refused(tmp_path: Path) -> None:
    """The same collision as it would actually occur: an object and its
    ``.bak`` sibling sharing a bucket root.
    """
    data_dir = tmp_path / "data"
    _write_fixture(data_dir, ("index.html", "index.html.bak/x"))

    with pytest.raises(FixtureBundleError, match="not prefix-free"):
        _generate_s5cmd_shards(data_dir, tmp_path / "s5cmd-shards.input")


def test_disjoint_top_level_shards_are_accepted(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_fixture(data_dir, ("alpha/a.dat", "beta/b.dat", "gamma/c.dat"))
    output = tmp_path / "s5cmd-shards.input"

    summary = _generate_s5cmd_shards(data_dir, output)

    assert summary["shards"] == 3
    assert output.read_text() == "alpha\nbeta\ngamma\n"


def test_physical_order_scan_records_part_boundaries(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_part(data_dir / "part-00000.parquet", ("a", "b"))
    _write_part(data_dir / "part-00001.parquet", ("c", "d"))

    summary = _physical_order_validation(data_dir)

    assert summary["rows"] == 4
    assert summary["descending_adjacent_pairs"] == 0
    assert summary["adjacent_duplicate_pairs"] == 0
    assert summary["parts"] == [
        {
            "name": "part-00000.parquet",
            "rows": 2,
            "first_key": "a",
            "last_key": "b",
            "descending_adjacent_pairs": 0,
            "adjacent_duplicate_pairs": 0,
        },
        {
            "name": "part-00001.parquet",
            "rows": 2,
            "first_key": "c",
            "last_key": "d",
            "descending_adjacent_pairs": 0,
            "adjacent_duplicate_pairs": 0,
        },
    ]


@pytest.mark.parametrize(
    ("first", "second", "message"),
    (
        (("a", "c", "b"), ("d",), "descending adjacent"),
        (("a", "b"), ("aa", "c"), "descending adjacent"),
        (("a", "b"), ("b", "c"), "adjacent duplicate"),
    ),
)
def test_physical_order_scan_refuses_unsorted_or_duplicate_fixture(
    tmp_path: Path, first: tuple[str, ...], second: tuple[str, ...], message: str
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_part(data_dir / "part-00000.parquet", first)
    _write_part(data_dir / "part-00001.parquet", second)

    with pytest.raises(FixtureBundleError, match=message):
        _physical_order_validation(data_dir)


def test_dataset_uri_replaces_the_capture(tmp_path: Path) -> None:
    from benchmark.fixture_bundle import parse_args

    args = parse_args(
        [
            "--bucket",
            "real-changesets",
            "--region",
            "us-west-2",
            "--output",
            str(tmp_path / "bundle"),
            "--dataset-uri",
            "gs://results/scale-study/real-changesets/swath.abc.s1/native/listing",
            "--replay-image",
            "ghcr.io/varveio/swath-replay@sha256:" + "0" * 64,
            "--cpuset",
            "0-3",
        ]
    )
    assert args.swath_image is None
    assert args.dataset_uri.endswith("/native/listing")

    with pytest.raises(SystemExit):
        parse_args(
            [
                "--bucket",
                "real-changesets",
                "--region",
                "us-west-2",
                "--output",
                str(tmp_path / "other"),
                "--replay-image",
                "ghcr.io/varveio/swath-replay@sha256:" + "0" * 64,
                "--cpuset",
                "0-3",
            ]
        )


def test_string_annotated_keys_are_read_as_bytes(tmp_path: Path) -> None:
    """Swath 0.3.1 writes the key column as a UTF-8 string; the bundle must treat it as bytes."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "part-00000.parquet"
    with duckdb.connect() as connection:
        connection.execute(
            f"""
            COPY (
                SELECT k AS key, 'OBJECT' AS row_type
                FROM (VALUES ('a/1'), ('a/2'), ('b/1')) AS t(k)
            ) TO '{path}' (FORMAT PARQUET)
            """
        )
    order = _physical_order_validation(data_dir)
    assert order["rows"] == 3 and order["descending_adjacent_pairs"] == 0
    shards_path = tmp_path / "shards.input"
    shards = _generate_s5cmd_shards(data_dir, shards_path)
    assert shards["shards"] == 2 and shards_path.read_text() == "a\nb\n"
    from benchmark.fixture_bundle import _fixture_analysis

    analysis = _fixture_analysis(data_dir)
    assert analysis["rows"] == 3 and analysis["key_annotation"] == "VARCHAR"
