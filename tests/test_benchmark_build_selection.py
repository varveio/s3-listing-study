from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from benchmark.runtime.build_selection import (
    BuildSelectionError,
    adapter_bundle_sha256,
    load_registered_selection,
    load_selection,
)

ROOT = Path(__file__).parents[1]


def registered_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    root = tmp_path / "repo"
    adapter = root / "tools" / "aws-cli" / "adapter"
    build = root / "tools" / "aws-cli" / "build"
    adapter.mkdir(parents=True)
    build.mkdir()
    shutil.copyfile(ROOT / "tools/aws-cli/adapter/command.py", adapter / "command.py")
    shutil.copyfile(ROOT / "tools/aws-cli/adapter/normalize.py", adapter / "normalize.py")
    metadata: dict[str, object] = {
        "tool": "aws-cli",
        "tool_version": "2.36.1",
        "tool_build_sha256": "1" * 64,
        "tool_artifact": {
            "kind": "release-archive",
            "locator": "https://example.test/aws.zip",
            "sha256": "2" * 64,
        },
        "subject_workdir": "/aws",
        "executable": ["/usr/local/bin/aws"],
        "command": "adapter/command.py",
        "normalizer": "adapter/normalize.py",
        "adapter_bundle_sha256": adapter_bundle_sha256(adapter),
    }
    metadata_path = build / "image.json"
    metadata_path.write_text(json.dumps(metadata))
    return root, metadata_path, metadata


def test_unmutated_registered_fixture_loads_successfully(tmp_path: Path) -> None:
    _, metadata_path, metadata = registered_fixture(tmp_path)
    selected = load_selection(metadata_path, expected_tool="aws-cli")
    assert selected.tool == metadata["tool"]
    assert selected.executable == ("/usr/local/bin/aws",)
    assert selected.adapter_bundle_sha256 == metadata["adapter_bundle_sha256"]


def test_registered_selection_rejects_metadata_symlink_escape(tmp_path: Path) -> None:
    root, metadata_path, _ = registered_fixture(tmp_path)
    outside = tmp_path / "outside.json"
    metadata_path.replace(outside)
    metadata_path.symlink_to(outside)
    with pytest.raises(BuildSelectionError, match="escapes"):
        load_registered_selection(root, "aws-cli")


def test_selection_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    _, metadata_path, metadata = registered_fixture(tmp_path)
    payload = json.dumps(metadata)
    metadata_path.write_text(payload[:-1] + ',"tool":"aws-cli"}')
    with pytest.raises(BuildSelectionError, match="duplicate JSON key"):
        load_selection(metadata_path, expected_tool="aws-cli")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("tool", "rclone", "selected tool"),
        ("tool_version", "", "tool_version"),
        ("tool_version", "-2.36.1", "tool_version"),
        ("tool_version", "2.36.1 ", "tool_version"),
        ("tool_build_sha256", "A" * 64, "64 lowercase"),
        ("tool_build_sha256", "1" * 63, "64 lowercase"),
        ("tool_artifact", {}, "exactly"),
        (
            "tool_artifact",
            {"kind": "image", "locator": "x", "sha256": "2" * 64},
            "unsupported",
        ),
        (
            "tool_artifact",
            {"kind": "release-archive", "locator": "has space", "sha256": "2" * 64},
            "locator",
        ),
        (
            "tool_artifact",
            {"kind": "release-archive", "locator": "x", "sha256": "A" * 64},
            "64 lowercase",
        ),
        ("subject_workdir", "/aws/", "canonical absolute"),
        ("subject_workdir", "aws", "canonical absolute"),
        ("executable", [], "non-empty path array"),
        ("executable", ["aws"], "canonical absolute"),
        ("executable", ["/usr/bin/java", ""], "non-empty strings"),
        ("executable", ["/usr/bin/java", 1], "non-empty strings"),
        ("command", "../adapter/command.py", "fixed registered"),
        ("normalizer", "adapter/../normalize.py", "fixed registered"),
        ("adapter_bundle_sha256", "0" * 63, "64 lowercase"),
        ("adapter_bundle_sha256", "A" * 64, "64 lowercase"),
    ],
)
def test_selection_rejects_invalid_metadata_values(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    _, metadata_path, metadata = registered_fixture(tmp_path)
    metadata[field] = value
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(BuildSelectionError, match=message):
        load_selection(metadata_path, expected_tool="aws-cli")


@pytest.mark.parametrize("missing", ["tool", "tool_artifact", "adapter_bundle_sha256"])
def test_selection_rejects_missing_required_metadata_field(tmp_path: Path, missing: str) -> None:
    _, metadata_path, metadata = registered_fixture(tmp_path)
    del metadata[missing]
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(BuildSelectionError, match="unexpected field set"):
        load_selection(metadata_path, expected_tool="aws-cli")


def test_selection_rejects_adapter_bundle_digest_mismatch(tmp_path: Path) -> None:
    _, metadata_path, _ = registered_fixture(tmp_path)
    normalizer = metadata_path.parents[1] / "adapter" / "normalize.py"
    normalizer.write_text(normalizer.read_text() + "\n# changed\n")
    with pytest.raises(BuildSelectionError, match="bundle digest"):
        load_selection(metadata_path, expected_tool="aws-cli")


def test_selection_rejects_adapter_component_symlink_escape(tmp_path: Path) -> None:
    _, metadata_path, _ = registered_fixture(tmp_path)
    normalizer = metadata_path.parents[1] / "adapter" / "normalize.py"
    outside = tmp_path / "outside.py"
    normalizer.replace(outside)
    normalizer.symlink_to(outside)
    with pytest.raises(BuildSelectionError, match="escapes"):
        load_selection(metadata_path, expected_tool="aws-cli")


def test_selection_rejects_command_adapter_tool_or_executable_mismatch(tmp_path: Path) -> None:
    _, metadata_path, metadata = registered_fixture(tmp_path)
    command = metadata_path.parents[1] / "adapter" / "command.py"
    command.write_text(command.read_text().replace('TOOL = "aws-cli"', 'TOOL = "rclone"'))
    metadata["adapter_bundle_sha256"] = adapter_bundle_sha256(command.parent)
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(BuildSelectionError, match="not requested tool"):
        load_selection(metadata_path, expected_tool="aws-cli")

    command.write_text(command.read_text().replace('TOOL = "rclone"', 'TOOL = "aws-cli"'))
    metadata["adapter_bundle_sha256"] = adapter_bundle_sha256(command.parent)
    metadata["executable"] = ["/wrong/aws"]
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(BuildSelectionError, match="registered executable"):
        load_selection(metadata_path, expected_tool="aws-cli")
