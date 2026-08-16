"""Command-adapter and build-fact boundary tests."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from benchmark.runtime.build_selection import (
    BuildSelectionError,
    adapter_bundle_sha256,
    load_registered_selection,
)
from benchmark.runtime.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    load_command_adapter,
)

ROOT = Path(__file__).parents[1]
TOOLS = (
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
BUCKET = "bucket-x"
REGION = "region-y"
SINK = "/sink"
REGIONS = (REGION, "eu-west-3")
PREFIXES = ("", "p x/雪/")
Q_CONTENTS = "Contents[].[Key,Size,ETag,LastModified,StorageClass]"
Q_VERSIONS = "Versions[].[Key,Size,ETag,LastModified,StorageClass]"


def adapter_path(tool: str) -> Path:
    return ROOT / "tools" / tool / "adapter" / "command.py"


def load_module(tool: str) -> ModuleType:
    path = adapter_path(tool)
    spec = importlib.util.spec_from_file_location(f"_command_test_{tool.replace('-', '_')}", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _aws(mode: str, prefix: str) -> tuple[str, ...]:
    if mode == "s3api-v2-remainder":
        return (
            "s3api",
            "list-objects-v2",
            "--bucket",
            BUCKET,
            "--region",
            REGION,
            "--no-sign-request",
            "--delimiter",
            "/",
            "--query",
            Q_CONTENTS,
            "--output",
            "text",
        )
    if mode.startswith("s3-ls-"):
        recursive = ("--recursive",) if mode == "s3-ls-recursive" else ()
        return (
            "s3",
            "ls",
            f"s3://{BUCKET}/{prefix}",
            *recursive,
            "--region",
            REGION,
            "--no-sign-request",
        )
    operation, query, output = {
        "s3api-v2-text": ("list-objects-v2", Q_CONTENTS, "text"),
        "s3api-v2-json": ("list-objects-v2", None, "json"),
        "s3api-v2-yamlstream": ("list-objects-v2", None, "yaml-stream"),
        "s3api-v1-text": ("list-objects", Q_CONTENTS, "text"),
        "s3api-versions-text": ("list-object-versions", Q_VERSIONS, "text"),
        "s3api-v2-delimiter": ("list-objects-v2", None, "json"),
    }[mode]
    argv = ["s3api", operation, "--bucket", BUCKET, "--region", REGION, "--no-sign-request"]
    if mode == "s3api-v2-delimiter":
        argv.extend(("--delimiter", "/"))
    if prefix:
        argv.extend(("--prefix", prefix))
    if query:
        argv.extend(("--query", query))
    return *argv, "--output", output


def _minio(mode: str, prefix: str) -> tuple[str, ...]:
    target = f"s3/{BUCKET}/{prefix}"
    return {
        "recursive": ("ls", "--recursive", target),
        "recursive-json": ("--json", "ls", "--recursive", target),
        "shallow": ("ls", target),
        "shallow-json": ("--json", "ls", target),
        "versions-json": ("--json", "ls", "--versions", "--recursive", target),
        "find": ("find", target),
        "find-json": ("--json", "find", target),
    }[mode]


def _ps3(mode: str, prefix: str) -> tuple[str, ...]:
    if prefix:
        raise CommandAdapterError("pS3 has no prefix")
    operation = {
        "list": "list-objects-v2",
        "list-versions": "list-object-versions",
        "head": "head-objects",
    }[mode]
    return operation, "--bucket", BUCKET, "--region", REGION


def _rclone(mode: str, prefix: str) -> tuple[str, ...]:
    backend = f"s3,provider=AWS,region={REGION}" + (",list_version=1" if mode == "listv1" else "")
    remote = f":{backend}:{BUCKET}" + (f"/{prefix}" if prefix else "")
    standard = ("--files-only", "--use-server-modtime", "--no-mimetype")
    return {
        "recursive-fastlist": ("lsjson", "--fast-list", *standard, "-R", remote),
        "recursive-hierarchical": ("lsjson", *standard, "--checkers", "4", "-R", remote),
        "recursive-walk": (
            "lsjson",
            *standard,
            "--disable",
            "ListR",
            "--checkers",
            "4",
            "-R",
            remote,
        ),
        "delimiter-shallow": ("lsjson", "--use-server-modtime", "--no-mimetype", remote),
        "listv1": ("lsjson", "--fast-list", *standard, "-R", remote),
        "lsf": (
            "lsf",
            "--fast-list",
            "--files-only",
            "--format",
            "ps",
            "--separator",
            ";",
            "-R",
            remote,
        ),
        "debug": ("lsjson", "--fast-list", *standard, "-R", "-vv", "--dump", "headers", remote),
        "walk-debug": (
            "lsjson",
            *standard,
            "--disable",
            "ListR",
            "--checkers",
            "4",
            "-R",
            "-vv",
            "--dump",
            "headers",
            remote,
        ),
    }[mode]


def _s3_fast_list(mode: str, prefix: str) -> tuple[str, ...]:
    assert mode == "list"
    scoped = ("--prefix", prefix) if prefix else ()
    return (
        "--no-sign-request",
        "--output-parquet-file",
        "/dev/stdout",
        "--output-ks-file",
        "/dev/null",
        *scoped,
        "list",
        "--region",
        REGION,
        "--bucket",
        BUCKET,
    )


def _s3kor(mode: str, prefix: str) -> tuple[str, ...]:
    uri = f"s3://{BUCKET}" + (f"/{prefix}" if prefix else "")
    versions = ("--all-versions",) if mode == "list-versions" else ()
    return "ls", *versions, "--region", REGION, uri


def _s3p(mode: str, prefix: str) -> tuple[str, ...]:
    head = {
        "ls": ("ls",),
        "ls-long": ("ls", "--long"),
        "ls-raw": ("ls", "--raw"),
        "summarize": ("summarize",),
    }[mode]
    scoped = ("--prefix", prefix) if prefix else ()
    return *head, "--bucket", BUCKET, "--region", REGION, "--list-concurrency", "8", *scoped


def _s4cmd(mode: str, prefix: str) -> tuple[str, ...]:
    url = f"s3://{BUCKET}/{prefix}"
    return {
        "recursive": ("ls", "-r", "-c", "4", url),
        "shallow": ("ls", "-c", "4", url),
        "show-directory": ("ls", "-d", "-c", "4", url),
        "du": ("du", "-r", "-c", "4", url),
    }[mode]


def _s5cmd(mode: str, prefix: str) -> tuple[str, ...]:
    target, recursive = f"s3://{BUCKET}/{prefix}", f"s3://{BUCKET}/{prefix}*"
    return {
        "recursive": ("--no-sign-request", "ls", "-e", "-s", recursive),
        "delimiter": ("--no-sign-request", "ls", "-e", "-s", target),
        "rootkeys": ("--no-sign-request", "ls", "-e", "-s", target),
        "json": ("--json", "--no-sign-request", "ls", recursive),
        "listv1": ("--no-sign-request", "--use-list-objects-v1", "ls", "-e", "-s", recursive),
        "allversions": ("--no-sign-request", "ls", "--all-versions", "-e", "-s", recursive),
        "fullpath": ("--no-sign-request", "ls", "--show-fullpath", recursive),
    }[mode]


def _s7cmd(mode: str, prefix: str) -> tuple[str, ...]:
    target = f"s3://{BUCKET}/{prefix}"
    obs = ("-vv", "--disable-color-tracing")
    parallel = ("--max-parallel-listings", "16")
    anon = ("--target-no-sign-request", "--target-region", REGION)
    fields = ("--tsv", "--show-storage-class", "--show-etag")
    return {
        "recursive-tsv": ("ls", "-r", *obs, *fields, *parallel, *anon, target),
        "recursive-tsv-nosort": ("ls", "-r", *obs, "--no-sort", *fields, *parallel, *anon, target),
        "recursive-aligned": ("ls", "-r", *obs, *parallel, *anon, target),
        "recursive-json": ("ls", "-r", *obs, "--json", *parallel, *anon, target),
        "recursive-one": ("ls", "-r", *obs, "-1", *parallel, *anon, target),
        "all-versions": ("ls", "-r", *obs, "--all-versions", *fields, *parallel, *anon, target),
        "max-depth": ("ls", "-r", *obs, "--max-depth", "1", *fields, *parallel, *anon, target),
        "shallow-tsv": ("ls", *obs, *fields, *anon, target),
        "bucket-list": ("ls", *obs, *anon),
    }[mode]


def _swath(mode: str, prefix: str) -> tuple[str, ...]:
    uri = f"s3://{BUCKET}" + (f"/{prefix}" if prefix else "")
    head = (
        "-v",
        "--color",
        "never",
        "list",
        uri,
        "--region",
        REGION,
        "--no-sign-request",
        "--concurrency",
        "8",
    )
    common = (*head, "--checkpoint", "none")
    dataset = f"{SINK}/listing"
    return {
        "recursive-tsv": (*common, "--format", "tsv"),
        "recursive-jsonl": (*common, "--format", "jsonl"),
        "recursive-table": (*common, "--format", "table"),
        "seed-none": (*common, "--format", "tsv", "--tune", "seed.mode=none"),
        "recursive-parquet": (*common, "--format", "parquet", "-o", dataset),
        "recursive-parquet-sorted": (
            *head,
            "--checkpoint",
            "auto",
            "--format",
            "parquet",
            "-o",
            dataset,
            "--sort",
            "--tune",
            "sort.ignore-disk-check=on",
        ),
    }[mode]


EXPECTED: dict[str, Callable[[str, str], tuple[str, ...]]] = {
    "aws-cli": _aws,
    "minio-mc": _minio,
    "ps3": _ps3,
    "rclone": _rclone,
    "s3-fast-list": _s3_fast_list,
    "s3kor": _s3kor,
    "s3p": _s3p,
    "s4cmd": _s4cmd,
    "s5cmd": _s5cmd,
    "s7cmd": _s7cmd,
    "swath": _swath,
}
FIXED_PREFIXES = {
    "aws-cli": ("/usr/local/bin/aws",),
    "minio-mc": ("/usr/bin/mc",),
    "ps3": ("/usr/local/bin/pS3",),
    "rclone": ("/usr/local/bin/rclone",),
    "s3-fast-list": ("/usr/bin/s3-fast-list",),
    "s3kor": ("/usr/local/bin/s3kor",),
    "s3p": ("/usr/local/bin/s3p",),
    "s4cmd": ("/usr/local/bin/s4cmd",),
    "s5cmd": ("/s5cmd",),
    "s7cmd": ("/usr/local/bin/s7cmd",),
    "swath": ("/opt/java/openjdk/bin/java", "-jar", "/opt/swath/swath.jar"),
}
EXPECTED_MODES = {
    "aws-cli": {
        "s3api-v2-text",
        "s3api-v2-json",
        "s3api-v2-yamlstream",
        "s3api-v1-text",
        "s3api-versions-text",
        "s3api-v2-delimiter",
        "s3api-v2-remainder",
        "s3-ls-recursive",
        "s3-ls-delimiter",
    },
    "minio-mc": {
        "recursive",
        "recursive-json",
        "shallow",
        "shallow-json",
        "versions-json",
        "find",
        "find-json",
    },
    "ps3": {"list", "list-versions", "head"},
    "rclone": {
        "recursive-fastlist",
        "recursive-hierarchical",
        "recursive-walk",
        "delimiter-shallow",
        "listv1",
        "lsf",
        "debug",
        "walk-debug",
    },
    "s3-fast-list": {"list"},
    "s3kor": {"list", "list-versions"},
    "s3p": {"ls", "ls-long", "ls-raw", "summarize"},
    "s4cmd": {"recursive", "shallow", "show-directory", "du"},
    "s5cmd": {"recursive", "delimiter", "rootkeys", "json", "listv1", "allversions", "fullpath"},
    "s7cmd": {
        "recursive-tsv",
        "recursive-tsv-nosort",
        "recursive-aligned",
        "recursive-json",
        "recursive-one",
        "all-versions",
        "max-depth",
        "shallow-tsv",
        "bucket-list",
    },
    "swath": {
        "recursive-tsv",
        "recursive-jsonl",
        "recursive-table",
        "seed-none",
        "recursive-parquet",
        "recursive-parquet-sorted",
    },
}


@pytest.mark.parametrize("tool", TOOLS)
def test_registered_adapter_and_build_facts_agree(tool: str) -> None:
    selected = load_registered_selection(ROOT, tool)
    adapter = load_command_adapter(ROOT / "tools" / tool / "adapter/command.py", expected_tool=tool)
    assert adapter.fixed_command_prefix == selected.executable
    assert selected.adapter_bundle_sha256 == adapter_bundle_sha256(
        ROOT / "tools" / tool / "adapter"
    )


@pytest.mark.parametrize("tool", TOOLS)
def test_every_mode_matches_the_frozen_subject_argv_contract(tool: str) -> None:
    """Vary every live mode, prefix, and region against independent expected argv."""
    module = load_module(tool)
    adapter = load_command_adapter(adapter_path(tool))
    assert set(module.MODES) == EXPECTED_MODES[tool]
    for mode in EXPECTED_MODES[tool]:
        for prefix in PREFIXES:
            for region in REGIONS:
                request = CommandRequest(
                    mode,
                    BUCKET,
                    region,
                    prefix,
                    tool=tool,
                    sink_dir=SINK,
                )
                try:
                    expected = EXPECTED[tool](mode, prefix)
                except CommandAdapterError:
                    with pytest.raises(CommandAdapterError):
                        adapter.compile(request)
                else:
                    expected = tuple(
                        region
                        if item == REGION
                        else item.replace(f"region={REGION}", f"region={region}")
                        for item in expected
                    )
                    assert adapter.compile(request) == (*FIXED_PREFIXES[tool], *expected)


@pytest.mark.parametrize("value", [0, 9, -1, True, 1.5, "4"])
def test_s4cmd_rejects_invalid_concurrency_override(value: object) -> None:
    adapter = load_command_adapter(adapter_path("s4cmd"))
    with pytest.raises(CommandAdapterError):
        adapter.compile(
            CommandRequest(
                "recursive",
                BUCKET,
                REGION,
                tool="s4cmd",
                concurrency=value,  # type: ignore[arg-type]
            )
        )


def test_s4cmd_accepts_every_registered_concurrency_in_the_full_matrix() -> None:
    adapter = load_command_adapter(adapter_path("s4cmd"))
    for mode in EXPECTED_MODES["s4cmd"]:
        for prefix in PREFIXES:
            for region in REGIONS:
                for concurrency in range(1, 9):
                    argv = adapter.compile(
                        CommandRequest(
                            mode,
                            BUCKET,
                            region,
                            prefix,
                            tool="s4cmd",
                            concurrency=concurrency,
                        )
                    )
                    index = argv.index("-c")
                    assert argv[index + 1] == str(concurrency)
                    assert argv[0] == "/usr/local/bin/s4cmd"
                    assert argv[-1] == f"s3://{BUCKET}/{prefix}"


@pytest.mark.parametrize("tool", tuple(tool for tool in TOOLS if tool != "s4cmd"))
def test_other_adapters_reject_explicit_logical_concurrency(tool: str) -> None:
    adapter = load_command_adapter(adapter_path(tool))
    mode = sorted(EXPECTED_MODES[tool])[0]
    with pytest.raises(CommandAdapterError, match="does not support logical concurrency"):
        adapter.compile(CommandRequest(mode, BUCKET, REGION, tool=tool, concurrency=1))


def test_commands_compile_exact_subject_argv() -> None:
    aws = load_command_adapter(ROOT / "tools/aws-cli/adapter/command.py")
    argv = aws.compile(CommandRequest("s3api-v2-json", "bucket", "us-east-1", tool="aws-cli"))
    assert argv[:2] == ("/usr/local/bin/aws", "s3api")
    assert "--no-sign-request" in argv

    swath = load_command_adapter(ROOT / "tools/swath/adapter/command.py")
    argv = swath.compile(CommandRequest("recursive-tsv", "bucket", "us-east-1", tool="swath"))
    assert argv[:3] == ("/opt/java/openjdk/bin/java", "-jar", "/opt/swath/swath.jar")


@pytest.mark.parametrize("tool", TOOLS)
def test_capsule_metadata_has_exact_current_contract_and_no_legacy_parent_identity(
    tool: str,
) -> None:
    document = json.loads((ROOT / "tools" / tool / "build/image.json").read_text())
    assert set(document) == {
        "tool",
        "tool_version",
        "tool_build_sha256",
        "tool_artifact",
        "subject_workdir",
        "executable",
        "command",
        "normalizer",
        "adapter_bundle_sha256",
    }
    assert set(document["tool_artifact"]) == {"kind", "locator", "sha256"}
    assert document["tool"] == tool
    assert "shared_base_source_sha256" not in document
    assert "tool_parent_image" not in document


@pytest.mark.parametrize("slug", ["", "../aws-cli", "AWS-CLI", "aws_cli", "aws--cli"])
def test_registered_selection_rejects_invalid_slugs(slug: str) -> None:
    with pytest.raises(BuildSelectionError, match="invalid tool slug"):
        load_registered_selection(ROOT, slug)
