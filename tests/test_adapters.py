"""Conformance and coverage for the ``normalize.py`` adapters.

* **Conformance** — does the adapter's output satisfy contract v2 at all? Answered
  offline from synthetic rows, so it holds on a checkout with no data directory
  and states the bar a future port is held to.
* **Coverage** — which committed payloads exist per tool, and which declared modes
  no payload reaches. Answered by :mod:`tests.adapters.equivalence`.

The bytes these adapters produce over the committed payloads are judged by the
differential replay, which re-issues all 67 committed verdicts through them and
requires ``verify.md`` back unchanged. The shell adapters that used to provide a
second opinion are gone; the committed artifacts are the oracle now.
"""

from __future__ import annotations

import functools
import io
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from s3_listing_study.contract import FIELD_COUNT, MTIME_RE, ContractViolation, read_records
from s3_listing_study.duckdb_adapter import emit_result
from tests.adapters.equivalence import (
    corpus_shortfall,
    load_adapter,
    repo_root,
    unexercised_modes,
)

PORTED = (
    "aws-cli",
    "minio-mc",
    "ps3",
    "rclone",
    "s3-fast-list",
    "s3kor",
    "s3p",
    "s4cmd",
    "s5cmd",
    "s7cmd",
    "swath",
)

# Modes an adapter declares that NO committed payload reaches. Pinned per tool
# rather than asserted empty, because for four tools it is not empty and saying so
# is the point: `unexercised_modes` names them, and the equivalence run says
# nothing about them however green it is. `ls-long`, `du` and `list-versions` were
# never smoked; `shallow` and `show-directory` are s4cmd modes whose only campaign
# was blocked at auth. A mode LEAVING this map means coverage arrived; a mode
# joining it means the equivalence run quietly stopped judging that mode, and the
# port of it now rests on its FIXTURES entry alone.
UNEXERCISED = {
    "ps3": {"list-versions"},
    "s3p": {"ls-long"},
    "s4cmd": {"du", "shallow", "show-directory"},
    # v0.1.0 receipts were retired with that subject; v0.2.0 currently has
    # observations only, so no mode has a replayable committed payload yet.
    "swath": {"recursive-tsv", "recursive-jsonl", "recursive-table", "seed-none"},
}

# A mode whose input is a BINARY stream, where a lone newline is not an empty
# listing but a corrupt payload. Only a 0-byte stream means "zero objects" for
# s3-fast-list, and the adapter refuses the newline — see
# `test_a_newline_only_parquet_stream_is_refused`, which pins that half.
BINARY_MODES = {("s3-fast-list", "list")}

REPO = repo_root()

# s3kor's `list` capability probe, whose selected stream is the panic it exited
# with — the payload the emit boundary refuses.
S3KOR_LIST_PROBE = "tools/s3kor/receipts/smoke/_capability/list/stderr.txt"

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
    # mc prints keys RELATIVE to the listed target, so a scoped run rebuilds them;
    # a folder row is a common prefix and exposes nothing but its key.
    ("minio-mc", "recursive-json"): (
        b'{"status":"success","type":"file","lastModified":"2026-03-16T14:41:50Z",'
        b'"size":3184153,"key":"access/A.csv","etag":"8f60","storageClass":"STANDARD"}\n',
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("minio-mc", "shallow-json"): (
        b'{"status":"success","type":"file","lastModified":"2025-11-24T20:33:44Z",'
        b'"size":36822,"key":"index.html","etag":"0d68","storageClass":"STANDARD"}\n'
        b'{"status":"success","type":"folder","lastModified":"2026-07-17T12:27:45.460988405Z",'
        b'"size":0,"key":"normals-daily/","etag":""}\n',
        "",
        [b"index.html", b"normals-daily/"],
    ),
    ("minio-mc", "versions-json"): (
        b'{"status":"success","type":"file","lastModified":"2026-03-16T14:41:50Z",'
        b'"size":3184153,"key":"access/A.csv","etag":"8f60","versionId":"null",'
        b'"storageClass":"STANDARD"}\n',
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    # `mc find` prints the ALIAS-prefixed absolute path, so the first two segments
    # come off and the scope prefix is NOT prepended.
    ("minio-mc", "find-json"): (
        b'{"status":"success","type":"","lastModified":"2026-03-16T14:41:50Z",'
        b'"size":3184153,"key":"s3/noaa-normals-pds/normals-hourly/access/A.csv","etag":""}\n',
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("minio-mc", "find"): (
        b"s3/noaa-normals-pds/normals-hourly/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("minio-mc", "recursive"): (
        b"[2026-03-16 14:41:50 UTC] 3.0MiB STANDARD access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("minio-mc", "shallow"): (
        b"[2025-11-24 20:33:44 UTC]  36KiB STANDARD index.html\n"
        b"[2026-07-17 12:27:44 UTC]     0B normals-daily/\n",
        "",
        [b"index.html", b"normals-daily/"],
    ),
    # s5cmd's text `ls` prints paths RELATIVE to the query prefix; `--json` and
    # `--show-fullpath` print the absolute s3:// URL instead.
    ("s5cmd", "recursive"): (
        b"2026/03/16 14:05:58 STANDARD ff41               4196  1981-2010/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/1981-2010/access/A.csv"],
    ),
    ("s5cmd", "listv1"): (
        b"2026/03/16 14:05:58 STANDARD ff41               4196  1981-2010/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/1981-2010/access/A.csv"],
    ),
    ("s5cmd", "rootkeys"): (
        b"                                       DIR  normals-daily/\n"
        b"2025/11/24 20:33:44 STANDARD 0d68              36822  index.html\n",
        "",
        [b"index.html"],
    ),
    ("s5cmd", "allversions"): (
        b"2026/03/16 14:05:58 STANDARD ff41               4196  1981-2010/access/A.csv null\n",
        "normals-hourly/",
        [b"normals-hourly/1981-2010/access/A.csv"],
    ),
    ("s5cmd", "delimiter"): (
        b"                                       DIR  normals-daily/\n"
        b"2025/11/24 20:33:44 STANDARD 0d68              36822  index.html\n",
        "",
        [b"normals-daily/", b"index.html"],
    ),
    ("s5cmd", "fullpath"): (
        b"s3://noaa-normals-pds/normals-hourly/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("s5cmd", "json"): (
        b'{"key":"s3://noaa-normals-pds/index.html","etag":"0d68",'
        b'"last_modified":"2025-11-24T20:33:44Z","type":"file","size":36822,'
        b'"storage_class":"STANDARD"}\n',
        "",
        [b"index.html"],
    ),
    # s7cmd's TSV column order is DATE SIZE STORAGE_CLASS ETAG [VERSION_ID] KEY;
    # the key is always the last field, and a PRE row carries only that.
    ("s7cmd", "recursive-tsv"): (
        b"2026-03-16T14:41:50Z\t3184153\tSTANDARD\t8f60\tnormals-hourly/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("s7cmd", "recursive-tsv-nosort"): (
        b"2026-03-16T14:41:50Z\t3184153\tSTANDARD\t8f60\tnormals-hourly/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("s7cmd", "all-versions"): (
        b"2026-03-16T14:41:50Z\t3184153\tSTANDARD\t8f60\tnull\tnormals-hourly/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("s7cmd", "max-depth"): (
        b"2025-11-24T20:33:44Z\t36822\tSTANDARD\t0d68\tindex.html\n\tPRE\t\t\tnormals-daily/\n",
        "",
        [b"index.html", b"normals-daily/"],
    ),
    ("s7cmd", "shallow-tsv"): (
        b"\tPRE\t\t\tnormals-hourly/1981-2010/\n",
        "normals-hourly/",
        [b"normals-hourly/1981-2010/"],
    ),
    ("s7cmd", "recursive-aligned"): (
        b"2026-03-16T14:41:50Z              3184153  normals-hourly/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("s7cmd", "recursive-json"): (
        b'{"ETag":"\\"8f60\\"","Key":"normals-hourly/access/A.csv",'
        b'"LastModified":"2026-03-16T14:41:50Z","Size":3184153,"StorageClass":"STANDARD"}\n'
        b'{"Prefix":"normals-hourly/1981-2010/"}\n',
        "normals-hourly/",
        [b"normals-hourly/access/A.csv", b"normals-hourly/1981-2010/"],
    ),
    ("s7cmd", "recursive-one"): (
        b"normals-hourly/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    # swath emits FULL keys, so no fixture here reconstructs one. The TSV sink
    # writes a header line, which the row_type filter drops.
    ("swath", "recursive-tsv"): (
        b"key\tsize\tlast_modified\tetag\tstorage_class\trow_type\n"
        b"normals-hourly/access/A.csv\t3184153\t2026-03-16T14:41:50Z\t8f60\tSTANDARD\tOBJECT\n"
        b"normals-hourly/1981-2010/\t\t\t\t\tCOMMON_PREFIX\n",
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("swath", "seed-none"): (
        b"key\tsize\tlast_modified\tetag\tstorage_class\trow_type\n"
        b"index.html\t36822\t2025-11-24T20:33:44Z\t0d68\tSTANDARD\tOBJECT\n",
        "",
        [b"index.html"],
    ),
    ("swath", "recursive-jsonl"): (
        b'{"key":"normals-hourly/access/A.csv","size":3184153,'
        b'"last_modified":"2026-03-16T14:41:50Z","etag":"8f60",'
        b'"storage_class":"STANDARD","row_type":"OBJECT"}\n',
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    # AlignedFormatter: size right-justified in columns [1,14], the instant from
    # column 17, the key from column 43.
    ("swath", "recursive-table"): (
        b"       3184153  2026-03-16T14:41:50Z      normals-hourly/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    # s3p prints FULL keys in every mode. `summarize` is an aggregate report with
    # no per-object records, so it normalises to nothing.
    ("s3p", "ls"): (
        b"normals-hourly/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("s3p", "ls-long"): (
        b"2026-03-16 14:41:50 3.0MB normals-hourly/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("s3p", "ls-raw"): (
        b'{"Key":"normals-hourly/access/A.csv","LastModified":"2026-03-16T14:41:50.000Z",'
        b'"ETag":"\\"8f60\\"","Size":3184153,"StorageClass":"STANDARD"}\n',
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("s3p", "summarize"): (b"Total objects: 1\nTotal size: 3184153\n", "normals-hourly/", []),
    # s4cmd prints an aligned "<mtime> <size> <name>" whose name is the absolute
    # s3:// URL. mtime is minute-precision, so it is unexposed; a DIR row is a
    # common prefix and carries no size either. `du` emits an aggregate.
    ("s4cmd", "recursive"): (
        b"2026-03-16 14:41       3184153  s3://noaa-normals-pds/normals-hourly/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("s4cmd", "shallow"): (
        b"                           DIR  s3://noaa-normals-pds/normals-hourly/1981-2010/\n"
        b"2026-03-16 14:41       3184153  s3://noaa-normals-pds/normals-hourly/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/1981-2010/", b"normals-hourly/A.csv"],
    ),
    ("s4cmd", "show-directory"): (
        b"                           DIR  s3://noaa-normals-pds/normals-hourly/\n",
        "normals-hourly/",
        [b"normals-hourly/"],
    ),
    ("s4cmd", "du"): (b"3184153  s3://noaa-normals-pds/normals-hourly/\n", "normals-hourly/", []),
    # pS3 prints "Object: <LastModified> \t <size> \t <key>", the timestamp being a
    # Go time.Time rendered with %v.
    ("ps3", "list"): (
        b"Object: 2026-03-16 14:41:50 +0000 UTC \t   3184153 \t normals-hourly/access/A.csv\n",
        "",
        [b"normals-hourly/access/A.csv"],
    ),
    ("ps3", "list-versions"): (
        b"Object: 2026-03-16 14:41:50 +0000 UTC \t   3184153 \t normals-hourly/access/A.csv\n",
        "",
        [b"normals-hourly/access/A.csv"],
    ),
    # s3kor prints the whole key per line; `list-versions` puts a version id and
    # one space in front of it.
    ("s3kor", "list"): (
        b"normals-hourly/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
    ("s3kor", "list-versions"): (
        b"null normals-hourly/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/access/A.csv"],
    ),
}


@functools.cache
def parquet_payload() -> bytes:
    """s3-fast-list's Arrow schema as a one-row parquet file — a synthetic fixture.

    The only fixture that cannot be a byte literal: this tool emits parquet, so
    "one object listed" has to be written by a parquet writer. The schema is the
    tool's own (``s3-fast-list/src/utils.rs``), including the ``DiffFlag`` column
    the adapter ignores, and ``LastModified`` is Unix epoch SECONDS.
    """
    import duckdb

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "list.parquet"
        duckdb.connect().execute(
            f"""COPY (SELECT 'normals-hourly/access/A.csv' AS "Key",
                             3184153::UBIGINT AS "Size",
                             1773671510::UBIGINT AS "LastModified",
                             '8f60' AS "ETag",
                             0::UTINYINT AS "DiffFlag")
                TO '{path}' (FORMAT parquet)"""
        )
        return path.read_bytes()


FIXTURES[("s3-fast-list", "list")] = (
    parquet_payload(),
    "normals-hourly/",
    [b"normals-hourly/access/A.csv"],
)


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
    """The verifier refuses a ``--normalize`` that is not executable (`verify/cli.py`)."""
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
    if not expected_keys:
        # A listing-REQUEST mode with no per-object output — s3p's `summarize`
        # and s4cmd's `du` both emit an aggregate report. Nothing to verify
        # against the manifest is the mode's contract, not a shortfall.
        assert done.stdout == b""
        return

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


def test_swath_table_drops_non_object_rows() -> None:
    payload = (
        f"{'PRE':>14}  {'':24}  normals-hourly/prefix/\n"
        f"{'-':>14}  {'':24}  normals-hourly/deleted\n"
        f"{3184153:>14}  {'2026-03-16T14:41:50Z':24}  normals-hourly/object\n"
    ).encode()
    done = run("swath", "recursive-table", "normals-hourly/", payload)
    assert done.returncode == 0, done.stderr
    assert [record.key for record in read_records(io.BytesIO(done.stdout))] == [
        b"normals-hourly/object"
    ]


@pytest.mark.parametrize("mode", ["recursive-tsv", "recursive-table"])
def test_swath_text_modes_refuse_ambiguous_control_escapes(mode: str) -> None:
    if mode == "recursive-tsv":
        payload = b"literal\\x09key\t1\t2026-03-16T14:41:50Z\tetag\tSTANDARD\tOBJECT\n"
    else:
        payload = f"{1:>14}  {'2026-03-16T14:41:50Z':24}  literal\\x09key\n".encode()
    done = run("swath", mode, "", payload)
    assert done.returncode != 0
    assert b"ambiguous swath" in done.stderr


@pytest.mark.parametrize("mode", sorted(load_adapter(REPO, "swath").MODES))
def test_swath_treats_a_closed_downstream_as_success(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    adapter = load_adapter(REPO, "swath")

    def broken_pipe(*_args: object) -> int:
        raise BrokenPipeError

    monkeypatch.setattr(adapter, "normalize", broken_pipe)
    monkeypatch.setattr(adapter.sys, "stdin", SimpleNamespace(buffer=io.BytesIO()))
    monkeypatch.setattr(adapter.sys, "stdout", SimpleNamespace(buffer=io.BytesIO()))
    assert adapter.main(["normalize.py", mode]) == 0


# Every (mode, empty payload) pair, minus the one combination that is not an
# empty listing at all — excluded from the parametrization rather than skipped
# inside it, because a skipped case reads as "not run" in exactly the place this
# file refuses to let anything read that way.
EMPTY_LISTING_CASES = [
    pytest.param(tool, mode, payload, id=f"{tool}-{mode}-{name}")
    for tool, mode in sorted(FIXTURES)
    for payload, name in ((b"", "empty"), (b"\n", "newline-only"))
    if not (payload and (tool, mode) in BINARY_MODES)
]


@pytest.mark.parametrize(("tool", "mode", "payload"), EMPTY_LISTING_CASES)
def test_an_empty_listing_normalises_to_nothing(tool: str, mode: str, payload: bytes) -> None:
    """Zero objects is a 0-row PASS, not an adapter crash.

    A `--scope prefix` verify over a prefix holding no objects, and a fan-out
    whose shards cover every key (an empty remainder), both hand the adapter an
    empty payload. The adapter must answer with exit 0 and no output, or a clean
    0-row PASS becomes an ERROR about the tool. The four s3api text modes
    regressed here once, on DuckDB's CSV sniffer reading zero columns where five
    were declared.
    """
    prefix = FIXTURES[(tool, mode)][1]
    done = run(tool, mode, prefix, payload)
    assert done.returncode == 0, done.stderr
    assert done.stdout == b""


def test_a_newline_only_parquet_stream_is_refused() -> None:
    """The other half of the newline-only rule, for the one BINARY input format.

    s3-fast-list emits parquet, so only a 0-BYTE stream is "this run listed
    nothing" — a run that listed zero objects still writes a valid 0-row parquet
    file. A lone newline is a corrupt payload, refused at exit 1 rather than
    reported as an empty listing that was never observed.
    """
    done = run("s3-fast-list", "list", "", b"\n")
    assert done.returncode == 1
    assert done.stdout == b""
    assert b"not readable parquet" in done.stderr


def test_a_short_text_row_is_refused_rather_than_padded() -> None:
    """The deliberate deviation from the shell adapter — see the adapter's docstring.

    A 4-field row only comes from a TRUNCATED payload. The shell adapter printed
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
    """The fidelity bug the shell adapter documented, made structural.

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


def test_s3kor_list_refuses_a_panic_whose_frames_are_tab_indented() -> None:
    """The one payload the adapter refuses rather than normalises.

    s3kor's only `list` receipt is a capability probe that panicked, so the stream
    the corpus selects is a Go stack trace whose frames are TAB-indented. `list`
    reads a whole line as a key, so emitting those frames would produce records
    whose KEY contains a TAB — 6 fields where the framing declares 5, and a field
    split downstream reads the surplus as an extra column. The adapter refuses at
    the emit boundary instead, which the verifier reports as ERROR: no verdict was
    formed, which is the truth about a stack trace. In-repo bytes, so this holds
    with no data directory.
    """
    payload = (REPO / S3KOR_LIST_PROBE).read_bytes()
    done = run("s3kor", "list", "", payload)
    assert done.returncode == 1
    assert done.stdout == b""
    assert b"ContractViolation" in done.stderr
    assert rb"key contains b'\t'" in done.stderr


@pytest.mark.parametrize("tool", PORTED)
def test_the_modes_no_committed_payload_reaches_are_the_known_ones(tool: str) -> None:
    """Pins today's coverage, per tool — see ``UNEXERCISED``.

    Seven adapters have a payload for every mode they declare. Four modes across
    three tools have none, and for those the differential replay says nothing
    however green it is: their port rests on the fixture alone. If this fails,
    either coverage arrived or the corpus stopped reaching a mode it used to.
    """
    assert unexercised_modes(REPO, tool) == UNEXERCISED.get(tool, set())
