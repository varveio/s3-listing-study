"""Offline QA for the Swath (Go port) adapter: argv compilation, build facts, normalizer.

Run with ``uv run pytest tools/swath-go``. Nothing here contacts a network or
runs the subject. The normalizer cases read three committed products the Go
binary built from the pinned revision wrote over a synthetic local S3 store
(``format2-dataset/``, ``tsv-file/``, ``tsv-zstd-file/``); ``docs/running.md``
§ Adapter fixtures says exactly how. They are adapter test input, not run
records: they show the normalizer reads what the binary writes, never how the
tool behaves against S3.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import zstandard

from benchmark.runtime.build_selection import load_registered_selection
from benchmark.runtime.command_adapter import (
    Ceiling,
    CommandAdapterError,
    CommandRequest,
    LoadedCommandAdapter,
    load_command_adapter,
)
from benchmark.runtime.contract import ContractViolation, parse_line

HERE = Path(__file__).resolve().parent
ADAPTER = HERE.parent
CAPSULE = ADAPTER.parent
REPO = CAPSULE.parents[1]
BUILD = CAPSULE / "build"
DATASET = HERE / "format2-dataset"
TSV = HERE / "tsv-file" / "listing.tsv"
TSV_ZSTD = HERE / "tsv-zstd-file" / "listing.tsv.zst"
PIN = "7b05605444766de4e7a736db0bc410409af47429"

FIXTURE_BASE = {
    "bucket": "fixture-bucket",
    "region": "us-east-1",
    "prefix": "a/",
    "endpoint_url": "http://127.0.0.1:5055",
}
FIXTURE_REQUESTS = {
    "format2-dataset": {
        "mode": "recursive-parquet-sorted",
        "config": {
            "concurrency": 4,
            "part_size": "30",
            "part_interval": "0s",
            "max_duration": "60s",
        },
    },
    "tsv-file": {"mode": "recursive-tsv", "config": {"concurrency": 4, "max_duration": "60s"}},
    "tsv-zstd-file": {
        "mode": "recursive-tsv-zstd",
        "config": {"concurrency": 4, "max_duration": "60s"},
    },
}
"""The requests whose compiled argv wrote the fixtures; each run report records its argv."""

FIXTURE_KEYS = [
    "a/b/c/four",
    "a/b/three.bin",
    "a/back\\slash.txt",
    "a/empty",
    "a/one.txt",
    "a/plus+sign",
    "a/space key.txt",
    "a/two.txt",
    "a/unicode-\u00e9\u00e8.txt",
    "a/z-last",
]

HEADER = (
    b"Key\tLastModified\tETag\tChecksumAlgorithm\tChecksumType\tSize\tStorageClass"
    b"\tOwner.DisplayName\tOwner.ID\n"
)


def _module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def adapter() -> LoadedCommandAdapter:
    return load_command_adapter(ADAPTER / "command.py", expected_tool="swath-go")


@pytest.fixture(scope="module")
def normalizer() -> ModuleType:
    return _module("swath_go_normalize", ADAPTER / "normalize.py")


def _request(mode: str = "recursive-parquet-sorted", **overrides: Any) -> CommandRequest:
    fields: dict[str, Any] = {
        "mode": mode,
        "bucket": "bucket",
        "region": "us-west-2",
        "sink_dir": "/sink",
    }
    fields.update(overrides)
    return CommandRequest(**fields)


# --- argv -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "output"),
    [
        ("recursive-parquet-sorted", "/sink/listing"),
        ("recursive-tsv", "/sink/listing.tsv"),
        ("recursive-tsv-zstd", "/sink/listing.tsv.zst"),
    ],
)
def test_unsigned_whole_bucket_argv_at_the_declared_default(
    adapter: LoadedCommandAdapter, mode: str, output: str
) -> None:
    assert adapter.compile(_request(mode)) == (
        "/opt/swath-go/swath",
        "list",
        "s3://bucket",
        "-o",
        output,
        "--report",
        "/sink/swath-go-report.json",
        "--region",
        "us-west-2",
        "--anonymous",
        "--workers",
        "64",
    )


def test_signed_prefix_endpoint_and_every_knob(adapter: LoadedCommandAdapter) -> None:
    argv = adapter.compile(
        _request(
            prefix="logs/2026/",
            signed=True,
            sink_dir="/sink/",
            endpoint_url="http://127.0.0.1:19090",
            config={
                "concurrency": 256,
                "limiter": False,
                "seed": False,
                "row_budget": "256MiB",
                "spill_max": "0",
                "max_duration": "1h59m30s",
                "part_size": "64mb",
                "part_interval": "1m30s",
            },
        )
    )
    assert argv == (
        "/opt/swath-go/swath",
        "list",
        "s3://bucket/logs/2026/",
        "-o",
        "/sink/listing",
        "--report",
        "/sink/swath-go-report.json",
        "--region",
        "us-west-2",
        "--workers",
        "256",
        "--endpoint",
        "http://127.0.0.1:19090",
        "--limiter=false",
        "--seed=false",
        "--row-budget",
        "256MiB",
        "--spill-max",
        "0",
        "--max-duration",
        "1h59m30s",
        "--part-size",
        "64mb",
        "--part-interval",
        "1m30s",
    )
    assert "--anonymous" not in argv


def test_structure_runs_the_diagnostic_list(adapter: LoadedCommandAdapter) -> None:
    argv = adapter.compile(_request("recursive-tsv", config={"structure": "off"}))
    assert argv[1:4] == ("diag", "list", "s3://bucket")
    assert "--structure=off" in argv
    assert adapter.compile(_request())[1:3] == ("list", "s3://bucket")


def test_resume_restates_what_a_resume_may_change(adapter: LoadedCommandAdapter) -> None:
    config = {
        "concurrency": 8,
        "limiter": False,
        "row_budget": "256MiB",
        "spill_max": "10GiB",
        "max_duration": "2h",
        "part_interval": "1m",
    }
    listed = adapter.compile(_request(config=config))
    resumed = adapter.compile(_request("resume-parquet-sorted", config=config))
    assert resumed == (
        "/opt/swath-go/swath",
        "resume",
        "/sink/listing",
        "--report",
        "/sink/swath-go-report.json",
        "--workers",
        "8",
        "--anonymous",
        "--limiter=false",
        "--row-budget",
        "256MiB",
        "--spill-max",
        "10GiB",
        "--max-duration",
        "2h",
        "--part-interval",
        "1m",
    )
    assert listed[listed.index("-o") + 1] == resumed[2]
    assert listed[listed.index("--report") + 1] == resumed[4]
    signed = adapter.compile(_request("resume-parquet-sorted", signed=True))
    assert signed[-1] == "--anonymous=false"


@pytest.mark.parametrize(
    "config", [{"seed": False}, {"structure": "off"}, {"part_size": "64MiB"}]
)
def test_resume_refuses_what_the_journal_freezes(
    adapter: LoadedCommandAdapter, config: dict[str, object]
) -> None:
    with pytest.raises(CommandAdapterError, match="frozen in the journal"):
        adapter.compile(_request("resume-parquet-sorted", config=config))


def test_modes_declare_their_products_channels_and_a_ceiling(
    adapter: LoadedCommandAdapter,
) -> None:
    expected = {
        "recursive-parquet-sorted": ("parquet-sorted", "dataset", "listing", True),
        "resume-parquet-sorted": ("parquet-sorted", "dataset", "listing", True),
        "recursive-tsv": ("text", "file", "listing.tsv", True),
        "recursive-tsv-zstd": ("text", "file", "listing.tsv.zst", False),
    }
    assert set(adapter.modes) == set(expected)
    for mode, (product, channel, name, compressed) in expected.items():
        manifest = adapter.modes[mode]
        assert (manifest.product, manifest.product_channel) == (product, channel)
        assert dict(manifest.artifacts) == {"listing": name}
        assert manifest.product_artifact == "listing"
        assert manifest.fields == ("key", "size", "etag", "mtime", "storage_class")
        if channel == "file":
            assert manifest.compresses_product is compressed
        axis = manifest.axes["concurrency"]
        assert isinstance(axis, Ceiling) and axis.value == 64
        assert axis.provenance == f"source@{PIN[:7]}"
        assert "heap_percent" not in manifest.axes
    assert adapter.modes["resume-parquet-sorted"].purpose_ceiling == "diagnostic"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"concurrency": 0}, "concurrency"),
        ({"concurrency": True}, "concurrency"),
        ({"limiter": "false"}, "limiter"),
        ({"seed": 1}, "seed"),
        ({"structure": "true"}, "structure"),
        ({"part_interval": "30"}, "part_interval"),
        ({"part_interval": "-1s"}, "part_interval"),
        ({"max_duration": "two hours"}, "max_duration"),
        ({"part_size": "0"}, "part_size"),
        ({"part_size": "12XB"}, "part_size"),
        ({"part_size": 64}, "part_size"),
        ({"row_budget": "0mib"}, "row_budget"),
        ({"spill_max": "-1"}, "spill_max"),
    ],
)
def test_malformed_values_are_refused(
    adapter: LoadedCommandAdapter, config: dict[str, object], message: str
) -> None:
    for mode in ("recursive-parquet-sorted", "resume-parquet-sorted"):
        with pytest.raises(CommandAdapterError, match=message):
            adapter.compile(_request(mode, config=config))


@pytest.mark.parametrize("key", ["part_size", "part_interval"])
def test_text_modes_refuse_part_controls(adapter: LoadedCommandAdapter, key: str) -> None:
    value = "64MiB" if key == "part_size" else "30s"
    for mode in ("recursive-tsv", "recursive-tsv-zstd"):
        with pytest.raises(CommandAdapterError, match="one text file"):
            adapter.compile(_request(mode, config={key: value}))


def test_undeclared_key_and_missing_sink_are_refused(adapter: LoadedCommandAdapter) -> None:
    with pytest.raises(CommandAdapterError):
        adapter.compile(_request(config={"workers": 8}))
    for mode in adapter.modes:
        with pytest.raises(CommandAdapterError, match="sink directory"):
            adapter.compile(_request(mode, sink_dir=""))


@pytest.mark.parametrize("sink", sorted(FIXTURE_REQUESTS))
def test_fixture_was_written_by_the_compiled_argv(adapter: LoadedCommandAdapter, sink: str) -> None:
    report = json.loads((HERE / sink / "swath-go-report.json").read_text())
    request = CommandRequest(**FIXTURE_BASE, sink_dir=sink, **FIXTURE_REQUESTS[sink])
    argv = adapter.compile(request)
    assert report["invocation"]["argv"] == list(argv[1:])
    assert report["outcome"]["class"] == "completed"
    assert report["outcome"]["exit_code"] == 0
    assert report["build"]["revision"] == PIN


# --- build facts ------------------------------------------------------------------------------


def _image() -> dict[str, Any]:
    document: dict[str, Any] = json.loads((BUILD / "image.json").read_text())
    return document


def test_image_metadata_is_the_registered_selection() -> None:
    selection = load_registered_selection(REPO, "swath-go")
    assert selection.executable == ("/opt/swath-go/swath",)
    assert selection.subject_workdir == "/opt/swath-go"


def test_dockerfiles_pin_the_recorded_archive_and_stamp_its_revision() -> None:
    image = _image()
    artifact = image["tool_artifact"]
    assert artifact["kind"] == "source-archive"
    assert artifact["locator"] == f"https://github.com/varveio/swath/archive/{PIN}.tar.gz"
    assert image["tool_version"] == f"provisional-{PIN[:7]}"
    stamp = f'-ldflags="-s -w -X github.com/varveio/swath/go/internal/listing.revision={PIN}"'
    toolbox = (REPO / "benchmark/build/Dockerfile").read_text()
    stage = toolbox[toolbox.index("FROM golang:") : toolbox.index("AS toolbox")]
    for recipe in ((BUILD / "Dockerfile").read_text(), stage):
        assert f"--checksum=sha256:{artifact['sha256']}" in recipe
        assert artifact["locator"] in recipe
        assert re.search(r"FROM golang:1\.26\.0-bookworm@sha256:[0-9a-f]{64} AS ", recipe)
        assert "GOTOOLCHAIN=local GOFLAGS=-mod=readonly CGO_ENABLED=0" in recipe
        assert "go build -trimpath" in recipe
        assert stamp in recipe
    assert "COPY --from=swath_go_build /out/swath /opt/swath-go/swath" in toolbox


# --- the sorted dataset -----------------------------------------------------------------------


def _normalize(normalizer: ModuleType, dataset: Path, prefix: str = "a/") -> tuple[int, bytes]:
    out = io.BytesIO()
    code = normalizer.normalize(out, b"", "recursive-parquet-sorted", prefix, str(dataset))
    return code, out.getvalue()


def _records(payload: bytes) -> list[Any]:
    return [parse_line(line) for line in payload.splitlines()]


@pytest.mark.parametrize("dataset", [DATASET, DATASET / "listing"])
def test_dataset_normalizes_in_manifest_order(normalizer: ModuleType, dataset: Path) -> None:
    code, payload = _normalize(normalizer, dataset)
    assert code == 0
    records = _records(payload)
    assert [record.key.decode("utf-8") for record in records] == FIXTURE_KEYS
    keys = [record.key for record in records]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    first = records[0]
    assert (first.size, first.storage_class) == ("22", "STANDARD")
    assert first.etag == "5ea828cb42c7a041e9b6fe17ed4ac5ce"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", first.mtime or "")
    assert records[1].storage_class == "STANDARD_IA"


def test_both_dataset_modes_count_the_manifest_rows(normalizer: ModuleType) -> None:
    manifest = json.loads((DATASET / "listing" / "manifest.json").read_text())
    assert len(manifest["parts"]) > 1
    expected = sum(part["rows"] for part in manifest["parts"])
    for mode in sorted(normalizer.DATASET_MODES):
        assert normalizer.count_rows(b"", mode, "a/", str(DATASET)) == expected == 10


def test_cli_reads_the_dataset(
    normalizer: ModuleType, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    assert normalizer.main(["recursive-parquet-sorted", "a/", "--dataset", str(DATASET)]) == 0
    assert len(capsysbinary.readouterr().out.splitlines()) == 10


@pytest.fixture
def scratch(tmp_path: Path) -> Iterator[Path]:
    copy = tmp_path / "sink"
    shutil.copytree(DATASET, copy)
    yield copy / "listing"


def _rewrite_manifest(root: Path, **changes: Any) -> dict[str, Any]:
    path = root / "manifest.json"
    manifest: dict[str, Any] = json.loads(path.read_text())
    manifest.update(changes)
    path.write_text(json.dumps(manifest))
    return manifest


def test_uncommitted_dataset_is_refused(normalizer: ModuleType, scratch: Path) -> None:
    (scratch / "manifest.json").unlink()
    assert _normalize(normalizer, scratch) == (1, b"")
    with pytest.raises(ValueError, match="did not commit"):
        normalizer.count_rows(b"", "recursive-parquet-sorted", "a/", str(scratch))


def test_format_one_manifest_is_refused(normalizer: ModuleType, scratch: Path) -> None:
    (scratch / "manifest.json").write_text(
        json.dumps({"version": "1", "fileFormat": "parquet", "files": [], "schemaId": "x"})
    )
    with pytest.raises(ValueError, match="format-1"):
        normalizer.committed_parts(str(scratch), "a/")


@pytest.mark.parametrize(
    "coverage",
    [
        {"complete": False, "stop_reason": "max_keys", "skipped_entries": 0, "filters": []},
        {"complete": True, "stop_reason": "completed", "skipped_entries": 2, "filters": []},
        {"complete": True, "stop_reason": "completed", "skipped_entries": 0, "filters": ["x"]},
        {"complete": True, "stop_reason": "completed", "skipped_entries": 0},
    ],
)
def test_incomplete_coverage_is_refused(
    normalizer: ModuleType, scratch: Path, coverage: dict[str, object]
) -> None:
    _rewrite_manifest(scratch, coverage=coverage)
    assert _normalize(normalizer, scratch)[0] == 1


def test_scope_must_be_the_case_scope(normalizer: ModuleType, scratch: Path) -> None:
    assert _normalize(normalizer, scratch, prefix="")[0] == 1
    assert _normalize(normalizer, scratch, prefix="b/")[0] == 1
    _rewrite_manifest(scratch, scope={"kind": "whole"})
    assert _normalize(normalizer, scratch, prefix="")[0] == 0


def test_part_path_size_and_row_mismatches_are_refused(
    normalizer: ModuleType, scratch: Path
) -> None:
    manifest = json.loads((scratch / "manifest.json").read_text())
    good = [dict(part) for part in manifest["parts"]]

    for bad in ({"path": "../escape.parquet"}, {"path": "/abs.parquet"}, {"bytes": 1}):
        parts = [dict(part) for part in good]
        parts[0].update(bad)
        _rewrite_manifest(scratch, parts=parts)
        assert _normalize(normalizer, scratch)[0] == 1



def test_a_manifest_row_count_the_parts_do_not_hold_is_refused_by_both_entry_points(
    normalizer: ModuleType, scratch: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = json.loads((scratch / "manifest.json").read_text())
    parts = [dict(part) for part in manifest["parts"]]
    parts[0]["rows"] += 1
    _rewrite_manifest(scratch, parts=parts)
    assert sum(part["rows"] for part in parts) == 11
    assert _normalize(normalizer, scratch) == (1, b"")
    assert "manifest says 11" in capsys.readouterr().err
    with pytest.raises(ValueError, match="manifest says"):
        normalizer.count_rows(b"", "recursive-parquet-sorted", "a/", str(scratch))


# --- the TSV file -----------------------------------------------------------------------------


def _tsv(normalizer: ModuleType, payload: bytes, mode: str = "recursive-tsv") -> bytes:
    out = io.BytesIO()
    assert normalizer.normalize(out, payload, mode, "a/") == 0
    return out.getvalue()


@pytest.mark.parametrize(
    ("mode", "path"), [("recursive-tsv", TSV), ("recursive-tsv-zstd", TSV_ZSTD)]
)
def test_tsv_fixtures_normalize_and_count(normalizer: ModuleType, mode: str, path: Path) -> None:
    payload = path.read_bytes()
    records = _records(_tsv(normalizer, payload, mode))
    assert [record.key.decode("utf-8") for record in records] == FIXTURE_KEYS
    assert normalizer.count_rows(payload, mode, "a/") == 10


def test_every_product_of_the_fixture_bucket_agrees(normalizer: ModuleType) -> None:
    """Cross-mode agreement: the same listing through the dataset and both TSV files.

    Not a completeness check (every arm can agree and be wrong the same way); it
    catches a normalizer that reads one product differently from the others.
    """
    dataset = _normalize(normalizer, DATASET)[1]
    assert _tsv(normalizer, TSV.read_bytes()) == dataset
    assert _tsv(normalizer, TSV_ZSTD.read_bytes(), "recursive-tsv-zstd") == dataset


def test_cli_reads_the_tsv_file_by_path(
    normalizer: ModuleType, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    assert normalizer.main(["recursive-tsv-zstd", "a/", "--input", str(TSV_ZSTD)]) == 0
    assert len(capsysbinary.readouterr().out.splitlines()) == 10


def test_tsv_cuts_the_fraction_and_unquotes_the_etag(normalizer: ModuleType) -> None:
    row = b'a/x\t2026-03-16T14:41:50.999000+00:00\t"abc-2"\tCRC32\tFULL_OBJECT\t5\tGLACIER\t\t\n'
    (record,) = _records(_tsv(normalizer, HEADER + row))
    assert (record.mtime, record.etag, record.size, record.storage_class) == (
        "2026-03-16T14:41:50Z",
        "abc-2",
        "5",
        "GLACIER",
    )


def test_tsv_unescapes_keys_to_their_own_bytes(normalizer: ModuleType) -> None:
    row = b'a/\\\\\\u0001\\b\\f\\u007f\t2026-01-01T00:00:00+00:00\t"e"\t\t\t1\tSTANDARD\t\t\n'
    (record,) = _records(_tsv(normalizer, HEADER + row))
    assert record.key == b"a/\\\x01\b\f\x7f"
    assert normalizer.count_rows(HEADER + row, "recursive-tsv") == 1


@pytest.mark.parametrize("escape", [b"\\t", b"\\n", b"\\r", b"\\x41", b"\\u0009", b"\\"])
def test_tsv_refuses_an_unframeable_or_unknown_escape(
    normalizer: ModuleType, escape: bytes
) -> None:
    payload = HEADER + b"a/" + escape + b'\t2026-01-01T00:00:00+00:00\t"e"\t\t\t1\tSTANDARD\t\t\n'
    with pytest.raises(ContractViolation):
        _tsv(normalizer, payload)
    with pytest.raises(ContractViolation):
        normalizer.count_rows(payload, "recursive-tsv")


@pytest.mark.parametrize(
    "payload",
    [
        b"key\tsize\tlast_modified\tetag\tstorage_class\trow_type\n",
        HEADER + b'a/x\t2026-01-01T00:00:00+00:00\t"e"\t\t\t1\tSTANDARD\t\n',
        HEADER + b'a/x\t2026-01-01T00:00:00Z\t"e"\t\t\t1\tSTANDARD\t\t\n',
        HEADER + b"a/x\t2026-01-01T00:00:00+00:00\te\t\t\t1\tSTANDARD\t\t\n",
        HEADER + b'a/x\t2026-01-01T00:00:00+00:00\t"e"\t\t\t1\tSTANDARD\t\t',
        HEADER + b'\n\na/x\t2026-01-01T00:00:00+00:00\t"e"\t\t\t1\tSTANDARD\t\t\n',
    ],
    ids=["java-header", "eight-fields", "zulu-mtime", "bare-etag", "no-final-lf", "blank-line"],
)
def test_tsv_refuses_what_the_port_does_not_write(normalizer: ModuleType, payload: bytes) -> None:
    with pytest.raises(ContractViolation):
        _tsv(normalizer, payload)


ROW = b'a/x\t2026-01-01T00:00:00+00:00\t"e"\t\t\t1\tSTANDARD\t\t\n'


@pytest.mark.parametrize(
    "payload",
    [
        HEADER + b"\n" + ROW,
        HEADER + ROW + b"\n" + ROW,
        HEADER + ROW + b"\n",
        HEADER + ROW + ROW[:-1],
    ],
    ids=["blank-after-header", "blank-between-rows", "blank-at-end", "unterminated-last-row"],
)
@pytest.mark.parametrize("mode", ["recursive-tsv", "recursive-tsv-zstd"])
def test_both_entry_points_refuse_the_same_framing(
    normalizer: ModuleType, payload: bytes, mode: str
) -> None:
    if mode == "recursive-tsv-zstd":
        payload = zstandard.ZstdCompressor().compress(payload)
    with pytest.raises(ContractViolation):
        normalizer.normalize(io.BytesIO(), payload, mode, "a/")
    with pytest.raises(ContractViolation):
        normalizer.count_rows(payload, mode, "a/")


def test_tsv_count_refuses_a_foreign_header_and_a_short_row(normalizer: ModuleType) -> None:
    with pytest.raises(ContractViolation):
        normalizer.count_rows(b"key\tsize\n", "recursive-tsv")
    short = HEADER + b'a/x\t2026-01-01T00:00:00+00:00\t"e"\t\t\t1\tSTANDARD\t\n'
    with pytest.raises(ValueError):
        normalizer.count_rows(short, "recursive-tsv")


def test_a_header_only_file_is_an_empty_listing(normalizer: ModuleType) -> None:
    assert _tsv(normalizer, HEADER) == b""
    assert normalizer.count_rows(HEADER, "recursive-tsv") == 0
    packed = zstandard.ZstdCompressor().compress(HEADER)
    assert normalizer.count_rows(packed, "recursive-tsv-zstd") == 0


def test_unknown_mode_is_refused(normalizer: ModuleType) -> None:
    assert normalizer.normalize(io.BytesIO(), b"", "recursive-jsonl", "a/", str(DATASET)) == 2
    with pytest.raises(ValueError, match="unknown mode"):
        normalizer.count_rows(b"", "recursive-jsonl", "a/", str(DATASET))
