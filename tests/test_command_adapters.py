"""Exhaustive argv snapshots for the Python-only capsule command adapters."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from s3_listing_study import __version__
from s3_listing_study.common import build_selection
from s3_listing_study.common.build_selection import (
    BuildSelectionError,
    adapter_bundle_sha256,
    build_derived_image_main,
    derived_image_tag,
    load_registered_selection,
    load_selection,
)
from s3_listing_study.common.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    load_command_adapter,
)

REPO = Path(__file__).resolve().parents[1]
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
    return REPO / "tools" / tool / "adapter" / "command.py"


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
def test_every_mode_matches_the_frozen_shell_argv_contract(tool: str) -> None:
    """The pre-deletion audit covered 120 cases; this matrix varies every live dimension."""
    module = load_module(tool)
    adapter = load_command_adapter(adapter_path(tool))
    assert set(module.MODES) == EXPECTED_MODES[tool]
    for mode in module.MODES:
        for prefix in PREFIXES:
            for region in REGIONS:
                request = CommandRequest(mode, BUCKET, region, prefix, sink_dir=SINK)
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
                    expected_subject_argv = (*FIXED_PREFIXES[tool], *expected)
                    actual = adapter.compile(request)
                    assert actual == expected_subject_argv
                    assert tuple(item.encode() for item in actual) == tuple(
                        item.encode() for item in expected_subject_argv
                    )


def test_minio_strips_exactly_one_accidental_leading_slash() -> None:
    adapter = load_command_adapter(adapter_path("minio-mc"))
    request = CommandRequest("recursive", BUCKET, REGION, "//nested/")
    assert adapter.build(request)[-1] == f"s3/{BUCKET}//nested/"


@pytest.mark.parametrize("value", [0, 9, -1, True, 1.5])
def test_s4cmd_rejects_invalid_concurrency_override(value: object) -> None:
    adapter = load_command_adapter(adapter_path("s4cmd"))
    with pytest.raises(CommandAdapterError):
        adapter.compile(CommandRequest("recursive", BUCKET, REGION, concurrency=value))  # type: ignore[arg-type]


def test_s4cmd_accepts_every_registered_concurrency_in_the_full_matrix() -> None:
    adapter = load_command_adapter(adapter_path("s4cmd"))
    for mode in EXPECTED_MODES["s4cmd"]:
        for prefix in PREFIXES:
            for region in REGIONS:
                for concurrency in range(1, 9):
                    request = CommandRequest(
                        mode,
                        BUCKET,
                        region,
                        prefix,
                        concurrency=concurrency,
                    )
                    argv = adapter.compile(request)
                    index = argv.index("-c")
                    assert argv[index + 1] == str(concurrency)
                    assert argv[0] == "/usr/local/bin/s4cmd"
                    assert argv[-1] == f"s3://{BUCKET}/{prefix}"


@pytest.mark.parametrize("tool", tuple(tool for tool in TOOLS if tool != "s4cmd"))
def test_other_adapters_reject_explicit_logical_concurrency(tool: str) -> None:
    adapter = load_command_adapter(adapter_path(tool))
    mode = next(iter(EXPECTED_MODES[tool]))
    with pytest.raises(CommandAdapterError, match="does not support logical concurrency"):
        adapter.compile(CommandRequest(mode, BUCKET, REGION, concurrency=1))


def test_command_adapter_cli_has_help_and_emits_json() -> None:
    path = adapter_path("aws-cli")
    help_result = subprocess.run(
        [sys.executable, str(path), "--help"], capture_output=True, text=True
    )
    assert help_result.returncode == 0
    assert "usage:" in help_result.stdout
    result = subprocess.run(
        [sys.executable, str(path), "s3api-v2-json", BUCKET, REGION],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert tuple(json.loads(result.stdout)) == (
        *FIXED_PREFIXES["aws-cli"],
        *_aws("s3api-v2-json", ""),
    )


def test_shell_command_compilers_are_gone() -> None:
    retired_name = "run" + ".sh"
    assert not list((REPO / "tools").glob(f"*/adapter/{retired_name}"))


def test_bundled_driver_refuses_tool_mix_and_match() -> None:
    with pytest.raises(CommandAdapterError, match="not requested tool"):
        load_command_adapter(adapter_path("aws-cli"), expected_tool="rclone")


def test_registered_build_selection_binds_tool_and_subject_digest() -> None:
    path = REPO / "tools/aws-cli/build/image.json"
    subject = (
        "amazon/aws-cli@sha256:406ca32d31e640a56e8d52921b40528cc64bfa59ec9cb4ee1456db6746cb7292"
    )
    selected = load_selection(path, expected_tool="aws-cli", subject_image=subject)
    assert selected.executable == ("/usr/local/bin/aws",)
    assert selected.command == "adapter/command.py"
    assert selected.normalizer == "adapter/normalize.py"
    assert selected.adapter_bundle_sha256 == adapter_bundle_sha256(REPO / "tools/aws-cli/adapter")
    assert load_registered_selection(REPO, "aws-cli") == selected
    with pytest.raises(BuildSelectionError, match="expected capsule location"):
        load_selection(path, expected_tool="rclone", subject_image=subject)
    with pytest.raises(BuildSelectionError, match="subject image"):
        load_selection(path, expected_tool="aws-cli", subject_image="wrong@sha256:0")


def _registered_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    root = tmp_path / "repo"
    adapter = root / "tools" / "aws-cli" / "adapter"
    build = root / "tools" / "aws-cli" / "build"
    adapter.mkdir(parents=True)
    build.mkdir()
    shutil.copyfile(adapter_path("aws-cli"), adapter / "command.py")
    shutil.copyfile(REPO / "tools/aws-cli/adapter/normalize.py", adapter / "normalize.py")
    metadata: dict[str, object] = {
        "tool": "aws-cli",
        "subject_image": (
            "amazon/aws-cli@sha256:406ca32d31e640a56e8d52921b40528cc64bfa59ec9cb4ee1456db6746cb7292"
        ),
        "subject_version": "2.36.1",
        "python_libc": "gnu",
        "subject_workdir": "/aws",
        "executable": ["/usr/local/bin/aws"],
        "command": "adapter/command.py",
        "normalizer": "adapter/normalize.py",
        "adapter_bundle_sha256": adapter_bundle_sha256(adapter),
    }
    (build / "image.json").write_text(json.dumps(metadata))
    return root, build / "image.json", metadata


@pytest.mark.parametrize("slug", ["", "../aws-cli", "AWS-CLI", "aws_cli", "aws--cli"])
def test_registered_selection_rejects_invalid_or_escaping_slugs(tmp_path: Path, slug: str) -> None:
    with pytest.raises(BuildSelectionError, match="invalid tool slug"):
        load_registered_selection(tmp_path, slug)


def test_registered_selection_rejects_metadata_symlink_escape(tmp_path: Path) -> None:
    root, metadata_path, _metadata = _registered_fixture(tmp_path)
    outside = tmp_path / "outside.json"
    metadata_path.replace(outside)
    metadata_path.symlink_to(outside)
    with pytest.raises(BuildSelectionError, match="escapes"):
        load_registered_selection(root, "aws-cli")


def test_selection_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    _root, metadata_path, metadata = _registered_fixture(tmp_path)
    payload = json.dumps(metadata)
    metadata_path.write_text(payload[:-1] + ',"tool":"aws-cli"}')
    with pytest.raises(BuildSelectionError, match="duplicate JSON key"):
        load_selection(
            metadata_path,
            expected_tool="aws-cli",
            subject_image=str(metadata["subject_image"]),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("tool", "rclone", "selected tool"),
        ("subject_image", "amazon/aws-cli:latest", "pinned"),
        ("subject_image", "amazon/aws-cli@sha256:ABC", "pinned"),
        ("subject_image", "../aws-cli@sha256:" + "0" * 64, "pinned"),
        ("subject_version", "", "subject_version"),
        ("subject_version", "-2.36.1", "subject_version"),
        ("subject_version", "2.36.1 ", "subject_version"),
        ("subject_version", "2.36.1/beta", "subject_version"),
        ("subject_version", 2, "subject_version"),
        ("python_libc", "glibc", "python_libc must be one of"),
        ("python_libc", "", "python_libc must be one of"),
        ("subject_workdir", "/aws/", "canonical absolute"),
        ("executable", ["aws"], "canonical absolute"),
        ("executable", ["/usr/bin/java", ""], "argv token"),
        ("command", "../adapter/command.py", "fixed registered"),
        ("normalizer", "adapter/../normalize.py", "fixed registered"),
        ("adapter_bundle_sha256", "0" * 63, "64 lowercase"),
        ("adapter_bundle_sha256", "A" * 64, "64 lowercase"),
    ],
)
def test_selection_rejects_invalid_metadata_values(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    _root, metadata_path, metadata = _registered_fixture(tmp_path)
    metadata[field] = value
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(BuildSelectionError, match=message):
        load_selection(
            metadata_path,
            expected_tool="aws-cli",
            subject_image=str(metadata["subject_image"]),
        )


def test_selection_rejects_adapter_bundle_and_tool_executable_mix(tmp_path: Path) -> None:
    _root, metadata_path, metadata = _registered_fixture(tmp_path)
    command = metadata_path.parents[1] / "adapter" / "command.py"
    command.write_text(command.read_text().replace('TOOL = "aws-cli"', 'TOOL = "rclone"'))
    metadata["adapter_bundle_sha256"] = adapter_bundle_sha256(command.parent)
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(BuildSelectionError, match="not requested tool"):
        load_selection(
            metadata_path,
            expected_tool="aws-cli",
            subject_image=str(metadata["subject_image"]),
        )

    command.write_text(command.read_text().replace('TOOL = "rclone"', 'TOOL = "aws-cli"'))
    metadata["adapter_bundle_sha256"] = adapter_bundle_sha256(command.parent)
    metadata["executable"] = ["/wrong/aws"]
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(BuildSelectionError, match="registered executable"):
        load_selection(
            metadata_path,
            expected_tool="aws-cli",
            subject_image=str(metadata["subject_image"]),
        )


def test_selection_rejects_missing_or_changed_adapter_bytes(tmp_path: Path) -> None:
    _root, metadata_path, metadata = _registered_fixture(tmp_path)
    normalizer = metadata_path.parents[1] / "adapter" / "normalize.py"
    normalizer.write_text(normalizer.read_text() + "\n# changed\n")
    with pytest.raises(BuildSelectionError, match="bundle digest"):
        load_selection(
            metadata_path,
            expected_tool="aws-cli",
            subject_image=str(metadata["subject_image"]),
        )
    normalizer.unlink()
    with pytest.raises(BuildSelectionError, match="escapes"):
        load_selection(
            metadata_path,
            expected_tool="aws-cli",
            subject_image=str(metadata["subject_image"]),
        )


def test_slug_only_builder_registers_exact_named_contexts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, ...]] = []

    class Completed:
        returncode = 0

    def fake_run(command: tuple[str, ...], *, check: bool) -> Completed:
        assert check is False
        calls.append(command)
        return Completed()

    # The real provisioner downloads a quarter-gigabyte interpreter archive; the
    # offline suite must never reach the network, so the tree is faked here and
    # the bound context asserted against it.
    interpreter = tmp_path / "python"
    interpreter.mkdir()

    def fake_ensure_runtime(architecture: str, libc: str, **kwargs: object) -> Path:
        assert libc == "gnu"
        return interpreter

    monkeypatch.chdir(REPO)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(build_selection, "ensure_runtime", fake_ensure_runtime)
    assert build_derived_image_main(["--tool", "aws-cli", "--tag", "study:test"]) == 0
    assert len(calls) == 1
    command = calls[0]
    assert "--build-arg" not in command
    assert command.count("--build-context") == 4
    assert f"python={interpreter}" in command
    assert any(item.startswith("subject=docker-image://amazon/aws-cli@sha256:") for item in command)
    assert f"adapter={REPO / 'tools/aws-cli/adapter'}" in command
    assert f"selection={REPO / 'tools/aws-cli/build'}" in command
    assert command[-3:] == ("--tag", "study:test", str(REPO))

    # Omitting --tag must not fall back to a bare, upstream-looking name.
    calls.clear()
    assert build_derived_image_main(["--tool", "aws-cli"]) == 0
    assert calls[0][-3:] == (
        "--tag",
        f"s3-listing-study/aws-cli:2.36.1-h{__version__}-406ca32d31e6",
        str(REPO),
    )


def test_derived_image_tag_states_both_versions_and_the_subject_digest() -> None:
    """The name a reader sees must not be mistakable for the upstream image."""
    selection = load_registered_selection(REPO, "swath")
    tag = derived_image_tag(selection)
    assert tag == f"s3-listing-study/swath:0.2.2-h{__version__}-e03f7be9c025"
    # A Docker reference: one repository component, one legal tag component.
    repository, _, reference = tag.partition(":")
    assert re.fullmatch(
        r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+", repository
    )
    assert re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}", reference)
