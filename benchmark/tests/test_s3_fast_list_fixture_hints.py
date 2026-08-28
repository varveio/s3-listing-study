from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from benchmark import replay_fixture

ROOT = Path(__file__).parents[2]
GENERATOR = ROOT / "tools/s3-fast-list/adapter/fixture_hints.py"


def _write_fixture(path: Path, rows: list[tuple[bytes, str]]) -> None:
    path.parent.mkdir(parents=True)
    with duckdb.connect() as connection:
        connection.execute("CREATE TABLE fixture_rows (key BLOB, row_type VARCHAR)")
        connection.executemany("INSERT INTO fixture_rows VALUES (?, ?)", rows)
        connection.execute(
            "COPY (SELECT * FROM fixture_rows ORDER BY key) TO ? (FORMAT PARQUET)", [str(path)]
        )


def _run(
    fixture: Path, output: Path, segments: int, *, prefix: str = ""
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(GENERATOR),
        "--fixture",
        str(fixture),
        "--segments",
        str(segments),
        "--output",
        str(output),
    ]
    if prefix:
        command.extend(("--prefix", prefix))
    return subprocess.run(command, text=True, capture_output=True, check=False)


def test_committed_replay_fixture_exercises_the_upstream_greedy_split(tmp_path: Path) -> None:
    source = ROOT / "benchmark/fixtures/replay-canary"
    fixture = tmp_path / "fixture"
    replay_fixture.generate_parquet(source / "generate.sql", fixture / "part-00000.parquet")
    output = tmp_path / "hints.input"

    done = _run(fixture, output, 16)

    assert done.returncode == 0, done.stderr
    expected = "".join(f"group-{index:02d}\n" for index in range(0, 16, 2))
    assert output.read_text() == expected
    assert json.loads(done.stdout) == {
        "cut_points": 8,
        "object_rows": 2048,
        "output": str(output),
        "prefix_groups": 16,
        "ranges": 9,
        "requested_segments": 16,
        "sha256": "4aec93ee42183c2f971ad2be228c46abbee02d46d74b0c21357938285b3e3039",
    }


def test_generator_reads_only_visible_objects_under_the_requested_prefix(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    rows = [
        *((f"scope/a/{index}".encode(), "OBJECT") for index in range(2)),
        *((f"scope/b/{index}".encode(), "OBJECT") for index in range(2)),
        *((f"scope/c/{index}".encode(), "OBJECT") for index in range(3)),
        *((f"scope/d/{index}".encode(), "OBJECT") for index in range(2)),
        (b"scope/ignored/common-prefix", "COMMON_PREFIX"),
        (b"outside/object", "OBJECT"),
    ]
    _write_fixture(fixture / "part-00000.parquet", rows)
    output = tmp_path / "hints.input"

    done = _run(fixture, output, 3, prefix="scope/")

    assert done.returncode == 0, done.stderr
    assert output.read_bytes() == b"scope/a\nscope/c\n"


@pytest.mark.parametrize(
    ("rows", "segments", "failure"),
    (
        (
            [(b"same/key", "OBJECT"), (b"same/key", "OBJECT")],
            1,
            "duplicate OBJECT key",
        ),
        (
            [
                (b"a", "OBJECT"),
                (b"a/1", "OBJECT"),
                (b"a/2", "OBJECT"),
                (b"b/1", "OBJECT"),
                (b"b/2", "OBJECT"),
                (b"c/1", "OBJECT"),
                (b"c/2", "OBJECT"),
            ],
            2,
            "equal an object key",
        ),
        (
            [
                (b"a/1", "OBJECT"),
                (b"a/2", "OBJECT"),
                (b"a/3", "OBJECT"),
                (b"a/4", "OBJECT"),
                (b"b/1", "OBJECT"),
                (b"c/1", "OBJECT"),
            ],
            3,
            "empty first cut point",
        ),
    ),
)
def test_generator_refuses_fixture_shapes_that_would_corrupt_evidence(
    tmp_path: Path, rows: list[tuple[bytes, str]], segments: int, failure: str
) -> None:
    fixture = tmp_path / "fixture"
    _write_fixture(fixture / "part-00000.parquet", rows)
    output = tmp_path / "hints.input"

    done = _run(fixture, output, segments)

    assert done.returncode == 1
    assert failure in done.stderr
    assert not output.exists()


def test_generator_refuses_to_replace_an_existing_artifact(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    _write_fixture(
        fixture / "part-00000.parquet",
        [
            (b"a/1", "OBJECT"),
            (b"a/2", "OBJECT"),
            (b"b/1", "OBJECT"),
            (b"b/2", "OBJECT"),
        ],
    )
    output = tmp_path / "hints.input"
    output.write_bytes(b"sealed\n")

    done = _run(fixture, output, 2)

    assert done.returncode == 1
    assert "refusing to overwrite" in done.stderr
    assert output.read_bytes() == b"sealed\n"
