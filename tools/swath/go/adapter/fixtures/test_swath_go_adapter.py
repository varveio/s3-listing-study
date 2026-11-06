"""Offline QA for the Swath (Go port) adapter: argv compilation, build facts, normalizer.

Run with ``uv run pytest tools/swath/go``. Nothing here contacts a network or
runs the subject. The normalizer cases read ``format2-dataset/``, a committed
format-2 dataset the Go binary built from the pinned revision wrote over a
synthetic local S3 store; ``docs/running.md`` § Adapter fixture says exactly
how. It is adapter test input, not a run record: it shows the normalizer reads
what the binary writes, never how the tool behaves against S3.
"""

from __future__ import annotations

import hashlib
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

from benchmark.runtime.build_selection import (
    adapter_bundle_sha256,
    load_staged_selection,
)
from benchmark.runtime.command_adapter import (
    Ceiling,
    CommandAdapterError,
    CommandRequest,
    LoadedCommandAdapter,
    load_command_adapter,
)
from benchmark.runtime.contract import parse_line

HERE = Path(__file__).resolve().parent
ADAPTER = HERE.parent
CAPSULE = ADAPTER.parent
REPO = CAPSULE.parents[2]
BUILD = CAPSULE / "build"
FIXTURE = HERE / "format2-dataset"

FIXTURE_REQUEST = {
    "mode": "recursive-parquet-sorted",
    "bucket": "fixture-bucket",
    "region": "us-east-1",
    "prefix": "a/",
    "sink_dir": "format2-dataset",
    "endpoint_url": "http://127.0.0.1:5055",
    "config": {"concurrency": 4, "part_size": "30", "part_interval": "0s", "max_duration": "60s"},
}
"""The request whose compiled argv wrote the fixture; its run report records that argv."""

FIXTURE_KEYS = [
    "a/b/c/four",
    "a/b/three.bin",
    "a/empty",
    "a/one.txt",
    "a/plus+sign",
    "a/space key.txt",
    "a/two.txt",
    "a/unicode-\u00e9\u00e8.txt",
    "a/z-last",
]


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


def test_unsigned_whole_bucket_argv_at_the_declared_default(adapter: LoadedCommandAdapter) -> None:
    assert adapter.compile(_request()) == (
        "/opt/swath-go/swath",
        "list",
        "s3://bucket",
        "-o",
        "/sink/listing",
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
                "seed": True,
                "request_payer": True,
                "part_interval": "1m30s",
                "max_duration": "2h",
                "part_size": "64MiB",
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
        "--seed=true",
        "--request-payer=true",
        "--part-interval",
        "1m30s",
        "--max-duration",
        "2h",
        "--part-size",
        "64MiB",
    )
    assert "--anonymous" not in argv


def test_resume_argv_reuses_the_dataset_and_report_paths(adapter: LoadedCommandAdapter) -> None:
    config = {"concurrency": 8, "limiter": False, "max_duration": "2h"}
    listed = adapter.compile(_request(config=config))
    resumed = adapter.compile(_request("resume-parquet-sorted", config=config))
    assert resumed == (
        "/opt/swath-go/swath",
        "resume",
        "/sink/listing",
        "--report",
        "/sink/swath-go-report.json",
    )
    assert listed[listed.index("-o") + 1] == resumed[2]
    assert listed[listed.index("--report") + 1] == resumed[4]


def test_modes_declare_one_sorted_dataset_and_a_ceiling(adapter: LoadedCommandAdapter) -> None:
    assert set(adapter.modes) == {"recursive-parquet-sorted", "resume-parquet-sorted"}
    for manifest in adapter.modes.values():
        assert manifest.product == "parquet-sorted"
        assert manifest.product_channel == "dataset"
        assert dict(manifest.artifacts) == {"listing": "listing"}
        assert manifest.product_artifact == "listing"
        assert manifest.fields == ("key", "size", "etag", "mtime", "storage_class")
        axis = manifest.axes["concurrency"]
        assert isinstance(axis, Ceiling) and axis.value == 64
        assert axis.provenance == "source@63a4256"
        assert "heap_percent" not in manifest.axes
    assert adapter.modes["resume-parquet-sorted"].purpose_ceiling == "diagnostic"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"concurrency": 0}, "concurrency"),
        ({"concurrency": True}, "concurrency"),
        ({"limiter": "false"}, "limiter"),
        ({"seed": 1}, "seed"),
        ({"part_interval": "30"}, "part_interval"),
        ({"part_interval": "-1s"}, "part_interval"),
        ({"max_duration": "two hours"}, "max_duration"),
        ({"part_size": "0"}, "part_size"),
        ({"part_size": "12XB"}, "part_size"),
        ({"part_size": 64}, "part_size"),
    ],
)
def test_malformed_values_are_refused(
    adapter: LoadedCommandAdapter, config: dict[str, object], message: str
) -> None:
    with pytest.raises(CommandAdapterError, match=message):
        adapter.compile(_request(config=config))


def test_undeclared_key_and_missing_sink_are_refused(adapter: LoadedCommandAdapter) -> None:
    with pytest.raises(CommandAdapterError):
        adapter.compile(_request(config={"workers": 8}))
    for mode in adapter.modes:
        with pytest.raises(CommandAdapterError, match="sink directory"):
            adapter.compile(_request(mode, sink_dir=""))


def test_fixture_was_written_by_the_compiled_argv(adapter: LoadedCommandAdapter) -> None:
    report = json.loads((FIXTURE / "swath-go-report.json").read_text())
    argv = adapter.compile(CommandRequest(**FIXTURE_REQUEST))
    assert report["invocation"]["argv"] == list(argv[1:])
    assert report["outcome"]["class"] == "completed"
    assert report["outcome"]["exit_code"] == 0


# --- build facts ------------------------------------------------------------------------------


def _image() -> dict[str, Any]:
    document: dict[str, Any] = json.loads((BUILD / "image.json").read_text())
    return document


def test_image_metadata_loads_and_matches_the_adapter() -> None:
    selection = load_staged_selection(BUILD / "image.json", ADAPTER, expected_tool="swath-go")
    assert selection.executable == ("/opt/swath-go/swath",)
    assert selection.adapter_bundle_sha256 == adapter_bundle_sha256(ADAPTER)


def test_tool_build_digest_covers_the_recipe_and_artifact() -> None:
    """The registered-selection digest, computed for this nested location.

    ``load_registered_selection`` assumes ``tools/<tool>/build``; the recipe below
    is its formula verbatim, rooted where this capsule actually is.
    """
    image = _image()
    artifact = image["tool_artifact"]
    digest = hashlib.sha256(
        b"s3-listing-study-tool-build-input-v1\0"
        + artifact["kind"].encode()
        + b"\0"
        + artifact["locator"].encode()
        + b"\0"
        + artifact["sha256"].encode()
        + b"\0"
    )
    for path in sorted(p for p in BUILD.rglob("*") if p.is_file() and p.name != "image.json"):
        payload = path.read_bytes()
        digest.update(
            path.relative_to(REPO).as_posix().encode() + b"\0" + len(payload).to_bytes(8, "big")
        )
        digest.update(payload)
    assert digest.hexdigest() == image["tool_build_sha256"]


def test_dockerfile_pins_the_recorded_archive_and_a_digest_pinned_builder() -> None:
    image = _image()
    dockerfile = (BUILD / "Dockerfile").read_text()
    artifact = image["tool_artifact"]
    assert artifact["kind"] == "source-archive"
    assert f"--checksum=sha256:{artifact['sha256']}" in dockerfile
    assert artifact["locator"] in dockerfile
    revision = re.search(r"/archive/([0-9a-f]{40})\.tar\.gz$", artifact["locator"])
    assert revision is not None
    assert image["tool_version"].endswith(revision.group(1)[:7])
    assert re.search(r"^FROM golang:[0-9.]+-bookworm@sha256:[0-9a-f]{64} AS ", dockerfile, re.M)
    assert "CGO_ENABLED=0" in dockerfile
    assert "-trimpath -ldflags='-s -w'" in dockerfile
    assert "COPY --from=swath-go-build /out/swath /opt/swath-go/swath" in dockerfile


# --- normalizer -------------------------------------------------------------------------------


def _normalize(normalizer: ModuleType, dataset: Path, prefix: str = "a/") -> tuple[int, bytes]:
    out = io.BytesIO()
    code = normalizer.normalize(out, b"", "recursive-parquet-sorted", prefix, str(dataset))
    return code, out.getvalue()


@pytest.mark.parametrize("dataset", [FIXTURE, FIXTURE / "listing"])
def test_fixture_normalizes_in_manifest_order(normalizer: ModuleType, dataset: Path) -> None:
    code, payload = _normalize(normalizer, dataset)
    assert code == 0
    records = [parse_line(line) for line in payload.splitlines()]
    assert [record.key.decode("utf-8") for record in records] == FIXTURE_KEYS
    keys = [record.key for record in records]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    first = records[0]
    assert (first.size, first.storage_class) == ("22", "STANDARD")
    assert first.etag == "ee0945e1d9c818058680b98955391717"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", first.mtime or "")
    assert records[1].storage_class == "STANDARD_IA"


def test_both_modes_count_the_manifest_rows(normalizer: ModuleType) -> None:
    manifest = json.loads((FIXTURE / "listing" / "manifest.json").read_text())
    assert len(manifest["parts"]) > 1
    expected = sum(part["rows"] for part in manifest["parts"])
    for mode in sorted(normalizer.MODES):
        assert normalizer.count_rows(b"", mode, "a/", str(FIXTURE)) == expected == 9


def test_cli_reads_the_dataset(
    normalizer: ModuleType, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    assert normalizer.main(["recursive-parquet-sorted", "a/", "--dataset", str(FIXTURE)]) == 0
    assert len(capsysbinary.readouterr().out.splitlines()) == 9


@pytest.fixture
def scratch(tmp_path: Path) -> Iterator[Path]:
    copy = tmp_path / "sink"
    shutil.copytree(FIXTURE, copy)
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

    parts = [dict(part) for part in good]
    parts[0]["rows"] += 1
    _rewrite_manifest(scratch, parts=parts)
    with pytest.raises(ValueError, match="manifest says"):
        normalizer.count_rows(b"", "recursive-parquet-sorted", "a/", str(scratch))


def test_unknown_mode_is_refused(normalizer: ModuleType) -> None:
    assert normalizer.normalize(io.BytesIO(), b"", "recursive-tsv", "a/", str(FIXTURE)) == 2
    with pytest.raises(ValueError, match="unknown mode"):
        normalizer.count_rows(b"", "recursive-tsv", "a/", str(FIXTURE))
