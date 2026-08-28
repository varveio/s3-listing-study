"""Conformance and coverage for the ``normalize.py`` adapters.

* **Conformance** — does the adapter's output satisfy contract v2 at all? Answered
  offline from synthetic rows, so it holds on a checkout with no data directory
  and states the bar a future port is held to.
* **Coverage** — which committed payloads exist per tool, and which declared modes
  no payload reaches. Answered by :mod:`tests.adapters.equivalence`.

Current assurance has two independent parts: synthetic fixtures exercise every
declared mode against contract v2, while ``EXPECTED_PAYLOADS`` and
``unexercised_modes`` pin the historical corpus denominator and its reachability.
A retired predecessor additionally replayed committed verdicts byte for byte;
that is historical port evidence, not a current test path.
"""

from __future__ import annotations

import functools
import io
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark.runtime import duckdb_adapter
from benchmark.runtime.contract import FIELD_COUNT, MTIME_RE, ContractViolation, Record, parse_line
from benchmark.runtime.duckdb_adapter import emit_result, existing_input_path
from benchmark.runtime.normalizer_cli import mapped_input
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


def read_records(stream: io.BytesIO) -> Iterator[Record]:
    for line_number, raw in enumerate(stream, start=1):
        yield parse_line(raw.removesuffix(b"\n"), line_number=line_number)


# Modes an adapter declares that NO committed payload reaches. Pinned per tool
# rather than asserted empty, because for four tools it is not empty and saying so
# is the point: `unexercised_modes` names them, and the equivalence run says
# nothing about them however green it is. `ls-long`, `du` and `list-versions` were
# never smoked; `shallow` and `show-directory` are s4cmd modes whose only campaign
# was blocked at auth. A mode LEAVING this map means coverage arrived; a mode
# joining it means the equivalence run quietly stopped judging that mode, and the
# port of it now rests on its FIXTURES entry alone.
# Modes whose output is a DIRECTORY dataset, not a stream. They take no stdin,
# so the stdin-payload fixture harness cannot express them; they are exercised
# through a published native/ directory instead.
DATASET_MODES = {
    ("swath", "recursive-parquet"),
    ("swath", "recursive-parquet-sorted"),
    ("swath", "recursive-tsv-dataset"),
    ("swath", "recursive-tsv-zstd"),
}

UNEXERCISED = {
    "ps3": {"list-versions"},
    # The hinted mode's first live run (c-2026-08-17-large, noaa-rtma-pds)
    # postdates the committed corpus; no committed payload reaches it yet.
    "s3-fast-list": {"list-hinted", "list-hinted-fixture"},
    "s3p": {"ls-long"},
    # These modes were added from the IDC directory-marker diagnosis and have
    # parser fixtures below, but no committed raw campaign product yet.
    "rclone": {"recursive-walk-with-dirs"},
    "s4cmd": {"du", "shallow", "show-directory"},
    "s5cmd": {"recursive-with-dirs", "fanout-with-dirs"},
    # Streaming one-line output is new campaign coverage and has no committed
    # raw product in the capsule's immutable groundwork corpus yet.
    "s7cmd": {"recursive-one-nosort"},
    # v0.1.0 receipts were retired with that subject; v0.2.0 currently has
    # observations only, so no mode has a replayable committed payload yet.
    "swath": {
        "recursive-tsv",
        "recursive-jsonl",
        "recursive-table",
        "seed-none",
        "recursive-parquet",
        "recursive-parquet-sorted",
        "recursive-tsv-dataset",
        "recursive-tsv-zstd",
    },
}

# A mode whose input is a BINARY stream, where a lone newline is not an empty
# listing but a corrupt payload. Only a 0-byte stream means "zero objects" for
# s3-fast-list, and the adapter refuses the newline — see
# `test_a_newline_only_parquet_stream_is_refused`, which pins that half.
BINARY_MODES = {
    ("s3-fast-list", "list"),
    ("s3-fast-list", "list-hinted"),
    ("s3-fast-list", "list-hinted-fixture"),
}

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
    ("rclone", "recursive-walk-with-dirs"): (
        b'[\n{"Path":"access/A.csv","Size":18410,'
        b'"ModTime":"2026-03-16T15:01:21.000000000Z","IsDir":false,"Tier":"STANDARD"},\n'
        b'{"Path":"markers/empty","Size":0,'
        b'"ModTime":"2026-03-16T15:01:22.000000000Z","IsDir":true}\n]\n',
        "normals-hourly/",
        [b"normals-hourly/access/A.csv", b"normals-hourly/markers/empty/"],
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
    ("s5cmd", "recursive-with-dirs"): (
        b"                                       DIR  markers/empty/\n"
        b"2026/03/16 14:05:58 STANDARD ff41               4196  1981-2010/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/markers/empty/", b"normals-hourly/1981-2010/access/A.csv"],
    ),
    ("s5cmd", "fanout-with-dirs"): (
        b"                                       DIR  markers/empty/\n"
        b"2026/03/16 14:05:58 STANDARD ff41               4196  1981-2010/access/A.csv\n",
        "normals-hourly/",
        [b"normals-hourly/markers/empty/", b"normals-hourly/1981-2010/access/A.csv"],
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
    ("s7cmd", "recursive-one-nosort"): (
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
# The hinted mode emits the identical parquet through the identical writer —
# the hints only shape how the keyspace was walked — so one payload serves both.
FIXTURES[("s3-fast-list", "list-hinted")] = FIXTURES[("s3-fast-list", "list")]
FIXTURES[("s3-fast-list", "list-hinted-fixture")] = FIXTURES[("s3-fast-list", "list")]


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
def test_normalizer_has_standard_help(tool: str) -> None:
    done = subprocess.run([str(adapter_path(tool)), "--help"], capture_output=True, check=False)
    assert done.returncode == 0
    assert b"usage:" in done.stdout


@pytest.mark.parametrize("tool", PORTED)
def test_every_declared_mode_has_a_fixture(tool: str) -> None:
    declared = set(load_adapter(REPO, tool).MODES)
    declared -= {mode for fixture_tool, mode in DATASET_MODES if fixture_tool == tool}
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


@pytest.mark.parametrize(("tool", "mode"), sorted(FIXTURES))
def test_count_rows_matches_explicit_normalization_without_constructing_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tool: str, mode: str
) -> None:
    payload, prefix, _expected_keys = FIXTURES[(tool, mode)]
    normalized = run(tool, mode, prefix, payload)
    assert normalized.returncode == 0, normalized.stderr
    expected = len(normalized.stdout.splitlines())

    adapter = load_adapter(REPO, tool)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("count-only path crossed the contract emit boundary")

    monkeypatch.setattr(duckdb_adapter, "Record", forbidden)
    for emit_name in ("emit_result", "emit"):
        if hasattr(adapter, emit_name):
            monkeypatch.setattr(adapter, emit_name, forbidden)
    assert adapter.count_rows(payload, mode, prefix=prefix) == expected

    raw = tmp_path / "stdout.raw"
    raw.write_bytes(payload)
    with existing_input_path(str(raw)), mapped_input(str(raw)) as file_data:
        assert adapter.count_rows(file_data, mode, prefix=prefix) == expected


def test_large_line_count_uses_the_binary_chunked_reader() -> None:
    rows = 250_000
    adapter = load_adapter(REPO, "s3kor")
    assert adapter.count_rows(b"object\n" * rows, "list") == rows


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"", [b""]),
        (b"\n", [b"", b""]),
        (b"a", [b"a"]),
        (b"a\n", [b"a", b""]),
        (b"a\n\n", [b"a", b"", b""]),
        (b"a\x00b\nc", [b"a\x00b", b"c"]),
    ],
)
def test_binary_line_iterator_preserves_split_framing_across_chunks(
    payload: bytes, expected: list[bytes]
) -> None:
    assert list(duckdb_adapter.iter_lf_lines(payload, chunk_size=2)) == expected
    assert list(duckdb_adapter.iter_lf_lines(io.BytesIO(payload), chunk_size=2)) == expected


@pytest.mark.parametrize(
    ("tool", "mode", "payload"),
    [
        ("s3kor", "list", b"a\x00b\n"),
        ("s3p", "ls", b"a\x00b\n"),
        ("rclone", "lsf", b"a\x00b;1\n"),
        ("s7cmd", "recursive-one", b"a\x00b\n"),
        ("s7cmd", "recursive-one-nosort", b"a\x00b\n"),
    ],
)
def test_binary_line_counts_match_normalization_for_nul_keys(
    tool: str, mode: str, payload: bytes
) -> None:
    normalized = run(tool, mode, "", payload)
    assert normalized.returncode == 0, normalized.stderr
    adapter = load_adapter(REPO, tool)
    assert adapter.count_rows(payload, mode) == len(normalized.stdout.splitlines()) == 1


def test_line_count_predicates_preserve_leading_tab_before_field_split() -> None:
    payload = b"\ta b c\n"
    normalized = run("s3p", "ls-long", "", payload)
    assert normalized.returncode == 0, normalized.stderr
    assert len(normalized.stdout.splitlines()) == 1
    assert load_adapter(REPO, "s3p").count_rows(payload, "ls-long") == 1

    # s5cmd's SQL similarly trims spaces, not tabs, before deciding whether the
    # first field is DIR. Count the selected native relation directly because
    # this deliberately malformed short row has no five-field record to emit.
    s5cmd = load_adapter(REPO, "s5cmd")
    with duckdb_adapter.staged(b"\tDIR prefix/\n") as path:
        selected = duckdb_adapter.count_query(
            duckdb_adapter.connect(), s5cmd.QUERIES["recursive"], {"path": path, "pfx": ""}
        )
    assert selected == 1
    assert s5cmd.count_rows(b"\tDIR prefix/\n", "recursive") == selected


class TinyReader(io.BytesIO):
    def read(self, size: int | None = -1) -> bytes:
        return super().read(3 if size is None or size < 0 else min(size, 3))


def test_aws_json_counts_selected_arrays_across_tokens_and_chunk_boundaries() -> None:
    payload = (
        b'{"Noise":"\\"Contents\\":[{},] and [,] and CommonPrefixes",'
        b'"Contents":[{"Key":"a,[]\\""},{"nested":[1,2,{"x":"CommonPrefixes"}]}],'
        b'"CommonPrefixes":[{"Prefix":"a/"},{"Prefix":"b/"}]}'
    )
    adapter = load_adapter(REPO, "aws-cli")
    assert adapter.count_rows(TinyReader(payload), "s3api-v2-json") == 2
    assert adapter.count_rows(TinyReader(payload), "s3api-v2-delimiter") == 4


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"{}", 0),
        (b'{"Contents":null}', 0),
        (b'{"Contents":[]}', 0),
        (b'{"Other":[],"CommonPrefixes":null}', 0),
    ],
)
def test_aws_json_count_accepts_empty_null_and_missing_arrays(
    payload: bytes, expected: int
) -> None:
    adapter = load_adapter(REPO, "aws-cli")
    assert adapter.count_rows(payload, "s3api-v2-delimiter") == expected


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b'{"Contents":[}',
        b'{"Contents":[{}] trailing}',
        b'{"Contents":"not-an-array"}',
        b'{"Contents":[],"Contents":[]}',
        b'{"Contents":[1]}',
        b'{"Contents":[{"Key":"unterminated}]}',
        b'{"Contents":[{"Key","a"}]}',
    ],
)
def test_aws_json_count_rejects_malformed_or_wrong_shaped_payloads(payload: bytes) -> None:
    adapter = load_adapter(REPO, "aws-cli")
    with pytest.raises(ValueError):
        adapter.count_rows(payload, "s3api-v2-json")


def test_aws_json_count_uses_the_required_c_backend() -> None:
    from ijson.backends import yajl2_c

    assert yajl2_c.backend == "yajl2_c"


def test_aws_yaml_count_tracks_only_contents_items_across_pages_and_documents() -> None:
    payload = (
        b"---\n"
        b"- Contents:\n"
        b"  - Key: one\n"
        b"    Note: '  - not an item'\n"
        b"  - Key: two\n"
        b"  OtherList:\n"
        b"    - ignored\n"
        b"- Other:\n"
        b"  - Key: ignored\n"
        b"- Contents: []\n"
        b"---\n"
        b"Contents:\n"
        b"- Key: three\n"
        b"  Note: Contents: [not structural]\n"
        b"Other: value\n"
    )
    adapter = load_adapter(REPO, "aws-cli")
    assert adapter.count_rows(TinyReader(payload), "s3api-v2-yamlstream") == 3


@pytest.mark.parametrize(
    "payload",
    [
        b"Contents: scalar\n",
        b"- Contents:\n  - scalar\n",
        b"Contents:\nnot-a-mapping-field\n",
    ],
)
def test_aws_yaml_count_rejects_unrecognized_contents_shapes(payload: bytes) -> None:
    with pytest.raises(ValueError, match="malformed yaml-stream"):
        load_adapter(REPO, "aws-cli").count_rows(payload, "s3api-v2-yamlstream")


def test_aws_large_buffered_formats_keep_counting_out_of_the_python_parser_slow_path() -> None:
    adapter = load_adapter(REPO, "aws-cli")
    json_row = (
        b'{"Key":"path/name","Size":123,"ETag":"\\"abc\\"",'
        b'"LastModified":"2026-01-01T00:00:00Z","StorageClass":"STANDARD"}'
    )
    json_payload = b'{"Contents":[' + b",".join([json_row] * 10_000) + b"]}"
    started = time.perf_counter()
    assert adapter.count_rows(json_payload, "s3api-v2-json") == 10_000
    assert time.perf_counter() - started < 1.0

    yaml_row = (
        b"  - ETag: '\"abc\"'\n"
        b"    Key: path/name\n"
        b"    LastModified: '2026-01-01T00:00:00+00:00'\n"
        b"    Size: 123\n"
        b"    StorageClass: STANDARD\n"
    )
    yaml_payload = b"- Contents:\n" + yaml_row * 10_000
    started = time.perf_counter()
    assert adapter.count_rows(yaml_payload, "s3api-v2-yamlstream") == 10_000
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize("tool", PORTED)
def test_normalizer_can_read_an_existing_raw_path_without_stdin(tool: str, tmp_path: Path) -> None:
    """The worker path uses the same adapters without staging listing bytes."""
    mode = next(mode for fixture_tool, mode in FIXTURES if fixture_tool == tool)
    payload, prefix, _keys = FIXTURES[(tool, mode)]
    raw = tmp_path / "stdout.raw"
    raw.write_bytes(payload)
    by_stdin = run(tool, mode, prefix, payload)
    by_path = subprocess.run(
        [str(adapter_path(tool)), mode, prefix, "--input", str(raw)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    assert by_path.returncode == by_stdin.returncode, by_path.stderr
    assert by_path.stdout == by_stdin.stdout


@pytest.mark.parametrize("tool", PORTED)
def test_the_config_blob_reaches_the_normalizer(tool: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The same blob ``command.py`` compiled argv from reaches ``normalize.py`` too.

    Unused by every adapter today, but genuinely threaded through the CLI
    boundary rather than defaulted away, so a capsule whose output shape
    depends on a config key can parse its own output later.
    """
    adapter = load_adapter(REPO, tool)
    mode = next(mode for fixture_tool, mode in FIXTURES if fixture_tool == tool)
    captured: list[object] = []

    def capture(*_args: object, config: object = None, **_kwargs: object) -> int:
        captured.append(config)
        return 0

    monkeypatch.setattr(adapter, "normalize", capture)
    monkeypatch.setattr(adapter.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"")))
    monkeypatch.setattr(adapter.sys, "stdout", SimpleNamespace(buffer=io.BytesIO()))
    assert adapter.main([mode, "", "--config", '{"mode": "x", "concurrency": 4}']) == 0
    assert captured == [{"mode": "x", "concurrency": 4}]


@pytest.mark.parametrize(
    ("tool", "mode"),
    sorted(set(FIXTURES) - DATASET_MODES),
)
def test_empty_existing_raw_path_matches_empty_stdin(tool: str, mode: str, tmp_path: Path) -> None:
    """File-backed input preserves bytes-mode empty-listing/error semantics."""
    prefix = FIXTURES[(tool, mode)][1]
    raw = tmp_path / "stdout.raw"
    raw.write_bytes(b"")
    by_stdin = run(tool, mode, prefix, b"")
    by_path = subprocess.run(
        [str(adapter_path(tool)), mode, prefix, "--input", str(raw)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    assert by_path.returncode == by_stdin.returncode, by_path.stderr
    assert by_path.stdout == by_stdin.stdout
    assert by_path.stderr == by_stdin.stderr


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


@pytest.mark.parametrize(
    "mode",
    sorted(load_adapter(REPO, "swath").MODES - {mode for tool, mode in DATASET_MODES}),
)
def test_swath_treats_a_closed_downstream_as_success(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    adapter = load_adapter(REPO, "swath")

    def broken_pipe(*_args: object, **_kwargs: object) -> int:
        raise BrokenPipeError

    monkeypatch.setattr(adapter, "normalize", broken_pipe)
    monkeypatch.setattr(adapter.sys, "stdin", SimpleNamespace(buffer=io.BytesIO()))
    monkeypatch.setattr(adapter.sys, "stdout", SimpleNamespace(buffer=io.BytesIO()))
    assert adapter.main([mode]) == 0


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


def test_a_swath_dataset_without_its_success_marker_is_refused(tmp_path: Path) -> None:
    """A killed swath run leaves valid-but-short parts and no ``_SUCCESS``.

    Normalizing those parts would produce a clean short listing, which the
    verifier would read as the tool having missed keys rather than as a run that
    never finished.
    """
    dataset = tmp_path / "listing"
    (dataset / "data").mkdir(parents=True)
    (dataset / "data" / "part-w0-00000.parquet").write_bytes(b"PAR1")
    done = subprocess.run(
        [str(adapter_path("swath")), "recursive-parquet", "--dataset", str(dataset)],
        capture_output=True,
        check=False,
    )
    assert done.returncode == 1
    assert done.stdout == b""
    assert b"_SUCCESS" in done.stderr


@pytest.mark.parametrize("mode", ["recursive-parquet", "recursive-parquet-sorted"])
def test_swath_dataset_count_and_normalize_accept_native_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    import duckdb

    native = tmp_path / "native"
    dataset = native / "listing"
    (dataset / "data").mkdir(parents=True)
    part = dataset / "data" / "part-w0-00000.parquet"
    duckdb.connect().execute(
        """COPY (
               SELECT 'object' AS "key", 1::UBIGINT AS "size", 'etag' AS "etag",
                      TIMESTAMPTZ '2026-03-16 14:41:50+00' AS "last_modified",
                      'STANDARD' AS "storage_class", 'OBJECT' AS "row_type"
               UNION ALL
               SELECT 'prefix/', NULL, NULL, NULL, NULL, 'COMMON_PREFIX'
           ) TO $part (FORMAT parquet)""",
        {"part": str(part)},
    )
    (dataset / "_SUCCESS").touch()

    by_root = subprocess.run(
        [str(adapter_path("swath")), mode, "--dataset", str(dataset)],
        capture_output=True,
        check=False,
    )
    by_parent = subprocess.run(
        [str(adapter_path("swath")), mode, "--dataset", str(native)],
        capture_output=True,
        check=False,
    )
    assert by_root.returncode == 0, by_root.stderr
    assert by_parent.returncode == 0, by_parent.stderr
    assert by_parent.stdout == by_root.stdout
    assert len(by_parent.stdout.splitlines()) == 1

    adapter = load_adapter(REPO, "swath")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dataset count selected or emitted contract records")

    monkeypatch.setattr(duckdb_adapter, "Record", forbidden)
    monkeypatch.setattr(adapter, "emit_result", forbidden)
    assert adapter.count_rows(b"", mode, native_root=str(native)) == 1


def test_swath_tsv_dataset_count_and_normalize_stream_parts(tmp_path: Path) -> None:
    import zstandard

    native = tmp_path / "native"
    dataset = native / "listing"
    (dataset / "data").mkdir(parents=True)
    header = b"key\tsize\tlast_modified\tetag\tstorage_class\trow_type\n"
    (dataset / "data/part-w0-00000.tsv").write_bytes(
        header + b"a\t1\t2026-03-16T14:41:50Z\te1\tSTANDARD\tOBJECT\n"
    )
    compressed = (
        header
        + b"prefix/\t\t\t\t\tCOMMON_PREFIX\n"
        + b"b\t2\t2026-03-16T14:41:51Z\te2\tSTANDARD\tOBJECT\n"
    )
    (dataset / "data/part-w1-00000.tsv.zst").write_bytes(
        zstandard.ZstdCompressor().compress(compressed)
    )
    (dataset / "_SUCCESS").touch()

    done = subprocess.run(
        [
            str(adapter_path("swath")),
            "recursive-tsv-zstd",
            "--dataset",
            str(native),
        ],
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert [line.split(b"\t", 1)[0] for line in done.stdout.splitlines()] == [b"a", b"b"]
    adapter = load_adapter(REPO, "swath")
    assert adapter.count_rows(b"", "recursive-tsv-zstd", native_root=str(native)) == 2

    (dataset / "data/part-w2-00000.tsv.zst").write_bytes(
        zstandard.ZstdCompressor().compress(
            header + b"ambiguous\\x0akey\t3\t2026-03-16T14:41:52Z\te3\tSTANDARD\tOBJECT\n"
        )
    )
    with pytest.raises(ContractViolation, match="ambiguous"):
        adapter.count_rows(b"", "recursive-tsv-zstd", native_root=str(native))


def test_count_rows_preserves_malformed_and_row_filter_semantics() -> None:
    import duckdb

    aws = load_adapter(REPO, "aws-cli")
    with pytest.raises(duckdb.Error, match="Expected Number of Columns: 5"):
        aws.count_rows(
            b'a/b.csv\t12\t"deadbeef"\t2026-03-16T14:41:50+00:00\n',
            "s3api-v2-text",
        )

    swath = load_adapter(REPO, "swath")
    payload = (
        b"key\tsize\tlast_modified\tetag\tstorage_class\trow_type\n"
        b"object\t1\t2026-03-16T14:41:50Z\te\tSTANDARD\tOBJECT\n"
        b"prefix/\t\t\t\t\tCOMMON_PREFIX\n"
    )
    assert swath.count_rows(payload, "recursive-tsv") == 1
    with pytest.raises(ContractViolation, match="ambiguous swath"):
        swath.count_rows(
            b"literal\\x09key\t1\t2026-03-16T14:41:50Z\te\tSTANDARD\tOBJECT\n",
            "recursive-tsv",
        )


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


def test_aws_yaml_string_timestamp_drops_fractional_seconds() -> None:
    payload = (
        b"Contents:\n"
        b"- Key: a.txt\n"
        b"  Size: 1\n"
        b"  ETag: deadbeef\n"
        b"  LastModified: '2026-03-16T14:41:50.123456+00:00'\n"
        b"  StorageClass: STANDARD\n"
    )
    done = run("aws-cli", "s3api-v2-yamlstream", "", payload)
    assert done.returncode == 0, done.stderr
    assert done.stdout == b"a.txt\t1\tdeadbeef\t2026-03-16T14:41:50Z\tSTANDARD\n"


def test_s7cmd_aligned_reconstructs_object_and_prefix_keys_with_spaces() -> None:
    payload = (
        f"{'2026-03-16T14:41:50Z':<25}  {12:>14}    dir/object  key.txt\n"
        f"{'':25}  {'PRE':>14}    dir/common  prefix/\n"
    ).encode()
    done = run("s7cmd", "recursive-aligned", "", payload)
    assert done.returncode == 0, done.stderr
    assert [line.split(b"\t", 1)[0] for line in done.stdout.splitlines()] == [
        b"  dir/object  key.txt",
        b"  dir/common  prefix/",
    ]


def test_s7cmd_aligned_refuses_a_tab_in_a_key_instead_of_rewriting_it() -> None:
    payload = f"{'2026-03-16T14:41:50Z':<25}  {12:>14}  \tdir/object.txt\n".encode()
    done = run("s7cmd", "recursive-aligned", "", payload)
    assert done.returncode != 0
    assert b"cannot carry" in done.stderr


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


@pytest.mark.parametrize(
    ("mode", "rows"),
    [
        ("list", b"first-key\nUsing custom endpoint [literal-key] on region [still-a-key]\n"),
        (
            "list-versions",
            b"null first-key\nnull Using custom endpoint [literal-key] on region [still-a-key]\n",
        ),
    ],
)
def test_s3kor_discards_only_the_leading_custom_endpoint_notice(mode: str, rows: bytes) -> None:
    notice = b"Using custom endpoint [http://127.0.0.1:19090] on region [us-east-1]\n"
    adapter = load_adapter(REPO, "s3kor")
    payload = notice + rows
    done = run("s3kor", mode, "", payload)
    assert done.returncode == 0, done.stderr
    assert [record.key for record in read_records(io.BytesIO(done.stdout))] == [
        b"first-key",
        b"Using custom endpoint [literal-key] on region [still-a-key]",
    ]
    assert adapter.count_rows(payload, mode) == 2


@pytest.mark.parametrize("tool", PORTED)
def test_the_modes_no_committed_payload_reaches_are_the_known_ones(tool: str) -> None:
    """Pins today's coverage, per tool — see ``UNEXERCISED``.

    Some declared modes have no replayable committed payload, including every
    current Swath mode because that capsule has observations only. The synthetic
    fixture matrix still checks their adapter behavior, while the pinned corpus
    gate explicitly records that historical payload coverage says nothing about
    them. If this fails, either coverage arrived or the corpus stopped reaching
    a mode it used to.
    """
    assert unexercised_modes(REPO, tool) == UNEXERCISED.get(tool, set())
