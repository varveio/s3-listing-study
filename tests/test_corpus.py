"""Tests for reading an invocation back out of committed artifacts.

Everything here is offline: it exercises the parsers against synthetic artifacts,
so it runs in CI without `$S3_STUDY_DATA`. The oracle is the safety mechanism for
the whole port, and an untested oracle vouches for nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.differential.corpus import (
    EMPTY_SHA256,
    CorpusError,
    parse_scope,
    parse_union_shards,
    read_meta,
    select_stream,
)

NONEMPTY_SHA256 = "f7978ea26f104aa6776e95df6b3dc21c8006d5f78d7d0787d62c608683f4d1d7"


def _verify_md(tmp_path: Path, scope: str) -> Path:
    path = tmp_path / "verify.md"
    path.write_text(f"| Field | Value |\n| --- | --- |\n| Scope | `{scope}` |\n| Result | PASS |\n")
    return path


def test_read_meta_first_key_wins(tmp_path: Path) -> None:
    # awk -F= in the verifier takes the first occurrence; the parser must agree.
    meta = tmp_path / "run.meta"
    meta.write_text("tool=s5cmd\ntool=impostor\nprefix=\nnot a pair\n")
    assert read_meta(meta) == {"tool": "s5cmd", "prefix": ""}


def test_parse_scope_full(tmp_path: Path) -> None:
    assert parse_scope(_verify_md(tmp_path, "full")) == ["--scope", "full"]


def test_parse_scope_prefix(tmp_path: Path) -> None:
    assert parse_scope(_verify_md(tmp_path, "prefix=normals-hourly/")) == [
        "--scope",
        "prefix",
        "--scope-prefix",
        "normals-hourly/",
    ]


def test_parse_scope_delimiter_with_prefix(tmp_path: Path) -> None:
    verify = _verify_md(tmp_path, "delimiter=/ prefix=normals-hourly/")
    assert parse_scope(verify) == [
        "--scope",
        "delimiter",
        "--scope-delimiter",
        "/",
        "--scope-prefix",
        "normals-hourly/",
    ]


def test_parse_scope_delimiter_without_prefix(tmp_path: Path) -> None:
    # `<none>` is the verifier's own spelling for "no prefix", not a prefix.
    verify = _verify_md(tmp_path, "delimiter=/ prefix=<none>")
    assert parse_scope(verify) == ["--scope", "delimiter", "--scope-delimiter", "/"]


def test_parse_scope_rejects_unknown(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="unrecognized Scope"):
        parse_scope(_verify_md(tmp_path, "everything"))


def test_parse_scope_rejects_missing_row(tmp_path: Path) -> None:
    path = tmp_path / "verify.md"
    path.write_text("| Result | PASS |\n")
    with pytest.raises(CorpusError, match="no Scope row"):
        parse_scope(path)


def test_select_stream_prefers_the_stream_that_carried_content(tmp_path: Path) -> None:
    meta = {
        "stdout_path": "receipts/tool/out.txt",
        "stdout_sha256": EMPTY_SHA256,
        "stderr_path": "tools/t/receipts/r/stderr.txt",
        "stderr_sha256": NONEMPTY_SHA256,
    }
    assert select_stream(meta, tmp_path) == ("stderr", "tools/t/receipts/r/stderr.txt")


def test_select_stream_stdout_wins_ties(tmp_path: Path) -> None:
    meta = {
        "stdout_path": "receipts/tool/out.txt",
        "stdout_sha256": NONEMPTY_SHA256,
        "stderr_path": "tools/t/receipts/r/stderr.txt",
        "stderr_sha256": NONEMPTY_SHA256,
    }
    assert select_stream(meta, tmp_path) == ("stdout", "receipts/tool/out.txt")


def test_select_stream_falls_back_when_both_are_empty(tmp_path: Path) -> None:
    meta = {
        "stdout_path": "receipts/tool/out.txt",
        "stdout_sha256": EMPTY_SHA256,
        "stderr_path": "tools/t/receipts/r/stderr.txt",
        "stderr_sha256": EMPTY_SHA256,
    }
    assert select_stream(meta, tmp_path) == ("stdout", "receipts/tool/out.txt")


def test_select_stream_is_decided_by_the_record_not_the_filesystem(tmp_path: Path) -> None:
    # A stray file on disk must not change the choice: the payload named by the
    # record is the one the committed verdict was produced from.
    (tmp_path / "stderr.txt").write_text("noise on disk\n")
    meta = {
        "stdout_path": "receipts/tool/out.txt",
        "stdout_sha256": NONEMPTY_SHA256,
        "stderr_path": "tools/t/receipts/r/stderr.txt",
        "stderr_sha256": EMPTY_SHA256,
    }
    assert select_stream(meta, tmp_path) == ("stdout", "receipts/tool/out.txt")


def test_select_stream_rejects_a_record_with_no_payload(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="records no stdout/stderr payload"):
        select_stream({"tool": "s5cmd"}, tmp_path)


def _union_md(tmp_path: Path, rows: str) -> Path:
    path = tmp_path / "union-verify.md"
    path.write_text(
        "# Union\n\n- Generated (UTC): 2026-07-17T00:00:00Z\n\n"
        "## Shards\n\n| # | Prefix | Receipt |\n| --- | --- | --- |\n"
        f"{rows}\n## Counts\n\n| Rows | 1 |\n"
    )
    return path


def test_parse_union_shards_keeps_table_order_and_remainder(tmp_path: Path) -> None:
    union = _union_md(
        tmp_path,
        "| 0 | a/ | `tools/aws-cli/receipts/smoke/a` |\n"
        "| 1 | b/ | `tools/aws-cli/receipts/smoke/b` |\n"
        "| 2 | <remainder> | `tools/aws-cli/receipts/smoke/rest` |\n"
        # The shard the layout heuristic drops: outside the fanout directory.
        "| 3 | c/ | `tools/aws-cli/receipts/s3api-v2-text-hourly` |\n",
    )
    shards, remainder = parse_union_shards(union)
    assert shards == [
        "tools/aws-cli/receipts/smoke/a",
        "tools/aws-cli/receipts/smoke/b",
        "tools/aws-cli/receipts/smoke/rest",
        "tools/aws-cli/receipts/s3api-v2-text-hourly",
    ]
    assert remainder == "tools/aws-cli/receipts/smoke/rest"


def test_parse_union_shards_without_remainder(tmp_path: Path) -> None:
    union = _union_md(tmp_path, "| 0 | a/ | `tools/aws-cli/receipts/smoke/a` |\n")
    assert parse_union_shards(union) == (["tools/aws-cli/receipts/smoke/a"], None)


def test_parse_union_shards_rejects_out_of_order_table(tmp_path: Path) -> None:
    union = _union_md(
        tmp_path,
        "| 0 | a/ | `tools/aws-cli/receipts/smoke/a` |\n"
        "| 2 | b/ | `tools/aws-cli/receipts/smoke/b` |\n",
    )
    with pytest.raises(CorpusError, match="not index-ordered"):
        parse_union_shards(union)


def test_parse_union_shards_rejects_two_remainders(tmp_path: Path) -> None:
    union = _union_md(
        tmp_path,
        "| 0 | <remainder> | `tools/aws-cli/receipts/smoke/a` |\n"
        "| 1 | <remainder> | `tools/aws-cli/receipts/smoke/b` |\n",
    )
    with pytest.raises(CorpusError, match="two remainders"):
        parse_union_shards(union)


def test_parse_union_shards_rejects_a_report_with_no_table(tmp_path: Path) -> None:
    path = tmp_path / "union-verify.md"
    path.write_text("# Union\n\n## Counts\n\n| Rows | 1 |\n")
    with pytest.raises(CorpusError, match="no shard table"):
        parse_union_shards(path)


def test_parse_union_shards_rejects_an_empty_table(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="lists no shards"):
        parse_union_shards(_union_md(tmp_path, ""))
