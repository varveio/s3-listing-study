"""Conformance and equivalence for the ported ``normalize.py`` adapters.

Two questions, kept apart on purpose:

* **Conformance** — does the adapter's output satisfy contract v2 at all? Answered
  offline from synthetic rows, so it holds on a checkout with no data directory
  and states the bar a future port is held to.
* **Equivalence** — does it produce the *same bytes* as the ``normalize.sh`` it
  replaces, over every committed payload? Answered by
  :mod:`tests.adapters.equivalence`. Payloads that resolve are always compared;
  payloads that do not are ORACLE_UNAVAILABLE — reported as a failure, never a
  skip, because a skip here is indistinguishable from a pass and that is exactly
  how a checkout with no ``$S3_STUDY_DATA`` came back green.
"""

from __future__ import annotations

import functools
import io
import subprocess
from pathlib import Path

import pytest

from s3_listing_study.contract import FIELD_COUNT, MTIME_RE, ContractViolation, read_records
from s3_listing_study.duckdb_adapter import emit_result
from tests.adapters.equivalence import (
    ToolReport,
    compare_tool,
    corpus_shortfall,
    discover_cases,
    load_adapter,
    repo_root,
    unexercised_modes,
)

PORTED = ("aws-cli", "rclone")

REPO = repo_root()

# One synthetic input per ported mode, in the tool's own raw output shape, plus
# the prefix a scoped run would have passed and the keys the adapter must
# reconstruct from it. The keys are the assertion that matters: a mode that
# prints path-relative names has to rebuild the full bucket key, and that
# reconstruction is the one piece of adapter logic no field check would catch.
FIXTURES: dict[tuple[str, str], tuple[bytes, str, list[bytes]]] = {
    ("aws-cli", "s3api-v2-text"): (
        b'a/b.csv\t12\t"deadbeef"\t2026-03-16T14:41:50+00:00\tSTANDARD\n'
        b'c.csv\t0\t"cafe"\t2026-03-16T14:41:51+0000\tGLACIER\n',
        "",
        [b"a/b.csv", b"c.csv"],
    ),
    ("aws-cli", "s3api-v1-text"): (
        b'a/b.csv\t12\t"deadbeef"\t2026-03-16T14:41:50+00:00\tSTANDARD\n',
        "",
        [b"a/b.csv"],
    ),
    ("aws-cli", "s3api-versions-text"): (
        b'a/b.csv\t12\t"deadbeef"\t2026-03-16T14:41:50+00:00\tSTANDARD\n',
        "",
        [b"a/b.csv"],
    ),
    ("aws-cli", "s3api-v2-remainder"): (
        b'index.html\t36822\t"0d68"\t2025-11-24T20:33:44+00:00\tSTANDARD\n',
        "",
        [b"index.html"],
    ),
    ("aws-cli", "s3api-v2-json"): (
        b'{"Contents":[{"Key":"a/b.csv","LastModified":"2026-03-16T14:41:50+00:00",'
        b'"ETag":"\\"deadbeef\\"","Size":12,"StorageClass":"STANDARD"},'
        b'{"Key":"c.csv","LastModified":"2026-03-16T14:41:51+00:00",'
        b'"ETag":"\\"cafe\\"","Size":0}]}',
        "",
        [b"a/b.csv", b"c.csv"],
    ),
    ("aws-cli", "s3api-v2-delimiter"): (
        b'{"CommonPrefixes":[{"Prefix":"a/"}],'
        b'"Contents":[{"Key":"index.html","LastModified":"2025-11-24T20:33:44+00:00",'
        b'"ETag":"\\"0d68\\"","Size":36822,"StorageClass":"STANDARD"}]}',
        "",
        [b"a/", b"index.html"],
    ),
    ("aws-cli", "s3api-v2-yamlstream"): (
        b"- Contents:\n"
        b"  - ETag: '\"deadbeef\"'\n"
        b"    Key: a/b.csv\n"
        b"    LastModified: '2026-03-16T14:41:50+00:00'\n"
        b"    Size: 12\n"
        b"    StorageClass: STANDARD\n",
        "",
        [b"a/b.csv"],
    ),
    ("aws-cli", "s3-ls-recursive"): (
        b"2025-11-24 20:33:44      36822 index.html\n2026-03-16 14:05:58       4196 a/b.csv\n",
        "",
        [b"index.html", b"a/b.csv"],
    ),
    # A prefix that does not end in '/': only its directory portion may be
    # prepended, or key `foobar.txt` under prefix `foo` becomes `foofoobar.txt`.
    ("aws-cli", "s3-ls-delimiter"): (
        b"                           PRE 1981-2010/\n2025-11-24 20:33:44      36822 foobar.txt\n",
        "normals-hourly/foo",
        [b"normals-hourly/1981-2010/", b"normals-hourly/foobar.txt"],
    ),
    ("rclone", "recursive-fastlist"): (
        b'[\n{"Path":"access/A.csv","Size":18410,'
        b'"ModTime":"2026-03-16T15:01:21.000000000Z","IsDir":false,"Tier":"STANDARD"}\n]\n',
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("rclone", "recursive-hierarchical"): (
        b'[\n{"Path":"access/A.csv","Size":18410,'
        b'"ModTime":"2026-03-16T15:01:21.000000000Z","IsDir":false,"Tier":"STANDARD"}\n]\n',
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("rclone", "recursive-walk"): (
        b'[\n{"Path":"access/A.csv","Size":18410,'
        b'"ModTime":"2026-03-16T15:01:21.000000000Z","IsDir":false,"Tier":"STANDARD"}\n]\n',
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("rclone", "listv1"): (
        b'[\n{"Path":"access/A.csv","Size":18410,'
        b'"ModTime":"2026-03-16T15:01:21.000000000Z","IsDir":false,"Tier":"STANDARD"}\n]\n',
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("rclone", "delimiter-shallow"): (
        b'[\n{"Path":"index.html","Size":36822,'
        b'"ModTime":"2025-11-24T20:33:44.000000000Z","IsDir":false,"Tier":"STANDARD"},\n'
        b'{"Path":"normals-daily","Size":0,'
        b'"ModTime":"2000-01-01T00:00:00.000000000Z","IsDir":true}\n]\n',
        "",
        [b"index.html", b"normals-daily/"],
    ),
    ("rclone", "lsf"): (
        b"1981-2010/access/A.csv;3184153\n",
        "normals-hourly/",
        [b"normals-hourly/1981-2010/access/A.csv"],
    ),
}


@functools.cache
def report_for(tool: str) -> ToolReport:
    """The equivalence run for one tool, computed once: it re-reads ~90 MB of payload."""
    return compare_tool(REPO, tool)


def adapter_path(tool: str) -> Path:
    return REPO / "tools" / tool / "adapter" / "normalize.py"


def run(tool: str, mode: str, prefix: str, payload: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(adapter_path(tool)), mode, prefix],
        input=payload,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("tool", PORTED)
def test_adapter_is_an_executable_the_verifier_can_invoke(tool: str) -> None:
    """``verify-listing.sh:529`` refuses a ``--normalize`` that is not executable."""
    path = adapter_path(tool)
    assert path.is_file()
    assert path.stat().st_mode & 0o111
    assert path.read_bytes().startswith(b"#!")


@pytest.mark.parametrize("tool", PORTED)
def test_every_declared_mode_has_a_fixture(tool: str) -> None:
    declared = set(load_adapter(REPO, tool).MODES)
    covered = {adapter_mode for adapter_tool, adapter_mode in FIXTURES if adapter_tool == tool}
    assert declared == covered


@pytest.mark.parametrize(("tool", "mode"), sorted(FIXTURES))
def test_output_conforms_to_contract_v2(tool: str, mode: str) -> None:
    payload, prefix, expected_keys = FIXTURES[(tool, mode)]
    done = run(tool, mode, prefix, payload)
    assert done.returncode == 0, done.stderr

    lines = done.stdout.split(b"\n")
    assert lines.pop() == b"", "every record ends in the record separator"
    assert lines, "the fixture produces at least one record"
    for line in lines:
        assert len(line.split(b"\t")) == FIELD_COUNT

    records = list(read_records(io.BytesIO(done.stdout)))
    assert [record.key for record in records] == expected_keys
    for record in records:
        if record.etag is not None:
            assert '"' not in record.etag
        if record.mtime is not None:
            assert MTIME_RE.fullmatch(record.mtime)
            assert record.mtime.endswith("Z")
        if record.size is not None:
            assert record.size.isdigit()


@pytest.mark.parametrize(("tool", "mode"), sorted(FIXTURES))
@pytest.mark.parametrize("payload", [b"", b"\n"], ids=["empty", "newline-only"])
def test_an_empty_listing_normalises_to_nothing(tool: str, mode: str, payload: bytes) -> None:
    """Zero objects is a 0-row PASS, not an adapter crash.

    A `--scope prefix` verify over a prefix holding no objects, and a fan-out
    whose shards cover every key (an empty remainder), both hand the adapter an
    empty payload. Every `normalize.sh` answers with exit 0 and no output; the
    ported adapter must too, or a clean 0-row PASS becomes an ERROR about the
    tool. The four s3api text modes regressed here once, on DuckDB's CSV sniffer
    reading zero columns where five were declared.
    """
    prefix = FIXTURES[(tool, mode)][1]
    done = run(tool, mode, prefix, payload)
    assert done.returncode == 0, done.stderr
    assert done.stdout == b""


def test_a_short_text_row_is_refused_rather_than_padded() -> None:
    """The deliberate deviation from ``normalize.sh`` — see the adapter's docstring.

    A 4-field row only comes from a TRUNCATED payload. The shell adapter prints
    ``$5`` of it as the empty string, emitting a record whose storage_class is
    neither a value nor the ``-`` that means "unexposed"; this adapter refuses
    the payload, which the verifier reports as ERROR — "no verdict was formed",
    which is the truth about a truncated listing.
    """
    payload = b'a/b.csv\t12\t"deadbeef"\t2026-03-16T14:41:50+00:00\n'
    done = run("aws-cli", "s3api-v2-text", "", payload)
    assert done.returncode != 0
    assert done.stdout == b""
    assert b"Expected Number of Columns: 5" in done.stderr


class OneBatch:
    """A ResultSet stand-in: one batch of rows, then exhaustion."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.batches = [rows]

    def fetchmany(self, size: int = 0) -> list[tuple[object, ...]]:
        return self.batches.pop() if self.batches else []


def test_a_non_varchar_column_is_a_contract_violation_not_a_type_error() -> None:
    """`tools/**` is outside mypy, so a query that lost its CAST fails only at runtime.

    It has to fail as a ``ContractViolation`` — the emit boundary's own error
    class, which the verifier reads as an adapter-contract violation — and not as
    a ``TypeError`` raised out of a regex several frames deeper.
    """
    out = io.BytesIO()
    with pytest.raises(ContractViolation):
        emit_result(out, OneBatch([("a/b.csv", 12, None, "2026-03-16T14:41:50Z", None)]))
    assert out.getvalue() == b""


def test_row_order_survives_a_payload_big_enough_to_parallelise() -> None:
    """Row order IS the adapter's output, and the verifier compares bytes.

    ``duckdb_adapter.connect`` pins ``preserve_insertion_order``; without an
    assertion over a payload large enough for DuckDB to split across threads,
    nothing offline would notice it being unset or its default changing. The
    threshold is real: with the setting off, this reader returns these rows in
    file order below ~20 MB and out of it above, so a payload smaller than this
    one would pass either way. The keys descend, so a re-ordering scan cannot
    accidentally reproduce them.
    """
    rows = 400_000
    keys = [f"k/{index:06d}.csv" for index in reversed(range(rows))]
    payload = b"".join(
        f'{key}\t{i}\t"deadbeef"\t2026-03-16T14:41:50+00:00\tSTANDARD\n'.encode()
        for i, key in enumerate(keys)
    )
    done = run("aws-cli", "s3api-v2-text", "", payload)
    assert done.returncode == 0, done.stderr
    emitted = [line.split(b"\t", 1)[0] for line in done.stdout.splitlines()]
    assert emitted == [key.encode() for key in keys]


@pytest.mark.parametrize("tool", PORTED)
def test_unknown_mode_is_refused(tool: str) -> None:
    done = run(tool, "no-such-mode", "", b"")
    assert done.returncode != 0
    assert done.stdout == b""
    assert b"unknown mode" in done.stderr


def test_rclone_refuses_a_key_the_framing_cannot_carry() -> None:
    """The fidelity bug ``normalize.sh:10-17`` documents, made structural.

    ``jq -r … @tsv`` C-escaped a TAB in a key and emitted a record whose key was
    not the key the bucket holds. Reading the JSON with DuckDB and handing the
    key to the contract's own validation cannot do that: the record is refused
    instead.
    """
    payload = (
        b'[\n{"Path":"a\\tb.csv","Size":1,'
        b'"ModTime":"2026-03-16T15:01:21.000000000Z","IsDir":false,"Tier":"STANDARD"}\n]\n'
    )
    done = run("rclone", "recursive-fastlist", "", payload)
    assert done.returncode != 0
    assert b"ContractViolation" in done.stderr
    assert done.stdout == b""


@pytest.mark.parametrize("tool", PORTED)
def test_the_committed_payload_corpus_is_the_pinned_one(tool: str) -> None:
    """Discovery alone cannot police the denominator — see ``EXPECTED_PAYLOADS``."""
    assert corpus_shortfall(REPO, tool) == ""


@pytest.mark.oracle
@pytest.mark.parametrize("tool", PORTED)
def test_shell_and_python_adapters_are_byte_identical(tool: str) -> None:
    """The acceptance bar for the port: identical bytes on every payload that resolves."""
    assert discover_cases(REPO, tool), f"no committed payload discovered for {tool}"
    report = report_for(tool)
    assert report.comparisons, f"no payload for {tool} could be read; nothing was compared"
    assert not report.differing, "\n".join(
        f"{comparison.case.name}: {comparison.detail()}" for comparison in report.differing
    )


@pytest.mark.oracle
@pytest.mark.parametrize("tool", PORTED)
def test_every_committed_payload_was_actually_judged(tool: str) -> None:
    """A payload that cannot be read is ORACLE_UNAVAILABLE, and that is not a pass.

    Skipping here made a missing data directory look like success while the
    comparison above judged nothing at all — the false green this pair exists to
    prevent. Failing is the honest report: the bar was not met because it was not
    measured. ``python3 -m tests.adapters`` says the same thing as exit 42.
    """
    report = report_for(tool)
    assert not report.unavailable, "ORACLE_UNAVAILABLE: " + "\n".join(
        reason for _, reason in report.unavailable
    )


@pytest.mark.parametrize("tool", PORTED)
def test_no_declared_mode_is_left_without_a_committed_payload(tool: str) -> None:
    """Pins today's coverage: every mode these two adapters implement has a payload.

    If this fails, the equivalence run above stopped judging a mode, and the port
    of that mode rests on the fixture alone.
    """
    assert unexercised_modes(REPO, tool) == set()
