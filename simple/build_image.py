"""Build the single simplified worker image from all immutable tool parents."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from contract import TOOLBOX_TOOLS

from s3_listing_study.common.build_selection import (
    BuildSelection,
    BuildSelectionError,
    load_registered_selection,
)

PINNED_IMAGE_RE = re.compile(r"\A[^\s@]+@sha256:[0-9a-f]{64}\Z")
REVISION_RE = re.compile(r"\A[0-9a-f]{40}\Z")
TOOL_BUILD_LABEL = "io.varve.s3-listing-study.tool-build-sha256"
SHARED_BASE_LABEL = "io.varve.s3-listing-study.shared-base-source-sha256"
TOOLBOX_LABEL = "io.varve.s3-listing-study.toolbox-manifest-sha256"
PARENT_ARGUMENTS = {
    "aws-cli": "AWS_CLI_PARENT",
    "minio-mc": "MINIO_MC_PARENT",
    "ps3": "PS3_PARENT",
    "rclone": "RCLONE_PARENT",
    "s3-fast-list": "S3_FAST_LIST_PARENT",
    "s3kor": "S3KOR_PARENT",
    "s3p": "S3P_PARENT",
    "s4cmd": "S4CMD_PARENT",
    "s5cmd": "S5CMD_PARENT",
    "s7cmd": "S7CMD_PARENT",
    "swath": "SWATH_PARENT",
}


class BuildError(RuntimeError):
    """The requested image cannot be bound to the checked-in capsules."""


def command_output(argv: list[str], *, cwd: Path) -> str:
    try:
        return subprocess.run(
            argv, cwd=cwd, check=True, text=True, stdout=subprocess.PIPE
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BuildError(f"command failed: {' '.join(argv)}: {exc}") from exc


def assert_clean_revision(root: Path, revision: str) -> None:
    if REVISION_RE.fullmatch(revision) is None:
        raise BuildError("harness revision must be a full lowercase commit ID")
    if command_output(["git", "rev-parse", "HEAD"], cwd=root) != revision:
        raise BuildError("harness revision does not equal the checked-out commit")
    if command_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root):
        raise BuildError("refusing to attest a worker image from a dirty checkout")


def registered_selections(root: Path) -> dict[str, BuildSelection]:
    tools = sorted(path.parent.parent.name for path in (root / "tools").glob("*/build/image.json"))
    if set(PARENT_ARGUMENTS) != TOOLBOX_TOOLS:
        raise BuildError("Docker parent arguments do not match the toolbox contract")
    if set(tools) != TOOLBOX_TOOLS:
        missing = sorted(TOOLBOX_TOOLS - set(tools))
        extra = sorted(set(tools) - TOOLBOX_TOOLS)
        raise BuildError(f"registered toolbox roster changed (missing={missing}, extra={extra})")
    return {tool: load_registered_selection(root, tool) for tool in tools}


def load_build_inputs(path: Path, required_tools: set[str]) -> tuple[str, dict[str, str]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot read tool parents JSON: {exc}") from exc
    fields = {"schema_version", "shared_base_image", "parents"}
    if not isinstance(document, dict) or set(document) != fields:
        raise BuildError(f"build inputs must contain exactly {sorted(fields)}")
    parents = document["parents"]
    if document["schema_version"] != 1 or not isinstance(parents, dict):
        raise BuildError("build inputs schema_version must be 1 and parents must be an object")
    shared_base_image = document["shared_base_image"]
    if (
        not isinstance(shared_base_image, str)
        or PINNED_IMAGE_RE.fullmatch(shared_base_image) is None
    ):
        raise BuildError("shared_base_image must be pinned by an immutable sha256 digest")
    if set(parents) != required_tools:
        missing = sorted(required_tools - set(parents))
        extra = sorted(set(parents) - required_tools)
        raise BuildError(f"tool parents roster mismatch (missing={missing}, extra={extra})")
    result: dict[str, str] = {}
    for tool in sorted(required_tools):
        parent = parents[tool]
        if not isinstance(parent, str) or PINNED_IMAGE_RE.fullmatch(parent) is None:
            raise BuildError(f"{tool}: parent must be pinned by an immutable sha256 digest")
        result[tool] = parent
    return shared_base_image, result


def validate_parent_config(selection: BuildSelection, document: Any) -> None:
    if not isinstance(document, list) or len(document) != 1 or not isinstance(document[0], dict):
        raise BuildError("docker inspect returned an unexpected parent image document")
    config = document[0].get("Config")
    if not isinstance(config, dict):
        raise BuildError("parent image has no inspectable Config")
    if document[0].get("Architecture") != "amd64" or document[0].get("Os") != "linux":
        raise BuildError("parent image must be the registered linux/amd64 execution platform")
    labels = config.get("Labels")
    actual_build = labels.get(TOOL_BUILD_LABEL) if isinstance(labels, dict) else None
    actual_base = labels.get(SHARED_BASE_LABEL) if isinstance(labels, dict) else None
    if actual_build != selection.tool_build_sha256:
        raise BuildError("parent tool-build label does not match the registered capsule")
    if actual_base != selection.shared_base_source_sha256:
        raise BuildError("parent shared-base label does not match the registered capsule")
    if config.get("WorkingDir") != selection.subject_workdir:
        raise BuildError("parent working directory does not match the registered capsule")


def validate_shared_base_config(source_sha256: str, document: Any) -> None:
    if not isinstance(document, list) or len(document) != 1 or not isinstance(document[0], dict):
        raise BuildError("docker inspect returned an unexpected shared base document")
    config = document[0].get("Config")
    if not isinstance(config, dict):
        raise BuildError("shared base has no inspectable Config")
    labels = config.get("Labels")
    if document[0].get("Architecture") != "amd64" or document[0].get("Os") != "linux":
        raise BuildError("shared base must be linux/amd64")
    if not isinstance(labels, dict) or labels.get(SHARED_BASE_LABEL) != source_sha256:
        raise BuildError("shared base source label does not match the registered capsules")
    if config.get("User") != "10001:10001" or config.get("WorkingDir") != "/home/s3study":
        raise BuildError("shared base runtime identity does not match the study contract")


def validate_built_config(document: Any, manifest_sha256: str, revision: str) -> None:
    if not isinstance(document, list) or len(document) != 1 or not isinstance(document[0], dict):
        raise BuildError("docker inspect returned an unexpected built image document")
    if document[0].get("Architecture") != "amd64" or document[0].get("Os") != "linux":
        raise BuildError("built image is not linux/amd64")
    config = document[0].get("Config")
    if not isinstance(config, dict):
        raise BuildError("built image has no inspectable Config")
    if config.get("User") != "10001:10001":
        raise BuildError("built image does not run as uid/gid 10001")
    if config.get("WorkingDir") != "/home/s3study":
        raise BuildError("built image has an unexpected worker working directory")
    if config.get("Entrypoint") != ["/usr/bin/python3", "/opt/simple/measure.py"]:
        raise BuildError("built image has an unexpected worker entrypoint")
    labels = config.get("Labels")
    if not isinstance(labels, dict) or labels.get(TOOLBOX_LABEL) != manifest_sha256:
        raise BuildError("built image aggregate toolbox label is missing or incorrect")
    if labels.get(TOOL_BUILD_LABEL) != "":
        raise BuildError("built image retains a misleading single-tool build label")
    if labels.get("io.varve.s3-listing-study.harness-revision") != revision:
        raise BuildError("built image harness revision label is missing or incorrect")


def toolbox_manifest(
    selections: Mapping[str, BuildSelection], parents: Mapping[str, str], shared_base_image: str
) -> tuple[dict[str, object], str]:
    base_hashes = {selection.shared_base_source_sha256 for selection in selections.values()}
    if len(base_hashes) != 1:
        raise BuildError("registered tool parents do not share one base source identity")
    tools: dict[str, object] = {}
    for tool, selection in sorted(selections.items()):
        tools[tool] = {
            "tool_parent_image": parents[tool],
            "tool_version": selection.tool_version,
            "tool_build_sha256": selection.tool_build_sha256,
            "subject_workdir": selection.subject_workdir,
            "executable": list(selection.executable),
        }
    manifest: dict[str, object] = {
        "schema_version": 1,
        "shared_base_uri": shared_base_image,
        "shared_base_digest": shared_base_image.rsplit("@", 1)[1],
        "shared_base_source_sha256": base_hashes.pop(),
        "tools": tools,
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return manifest, hashlib.sha256(canonical).hexdigest()


def final_image_metadata(
    manifest: Mapping[str, object],
    selections: Mapping[str, BuildSelection],
    manifest_sha256: str,
    revision: str,
) -> dict[str, object]:
    manifest_tools = manifest["tools"]
    if not isinstance(manifest_tools, dict):
        raise BuildError("toolbox manifest tools are malformed")
    image_tools = {
        tool: {
            **value,
            "adapter_bundle_sha256": selections[tool].adapter_bundle_sha256,
        }
        for tool, value in manifest_tools.items()
        if isinstance(tool, str) and isinstance(value, dict)
    }
    if set(image_tools) != set(selections):
        raise BuildError("toolbox manifest tools do not match registered selections")
    return {
        **manifest,
        "schema_version": 2,
        "toolbox_manifest_sha256": manifest_sha256,
        "harness_revision": revision,
        "tools": image_tools,
    }


def build_image(root: Path, inputs_path: Path, revision: str, tag: str) -> str:
    if not tag or any(character.isspace() for character in tag):
        raise BuildError("output tag must be one non-empty token")
    assert_clean_revision(root, revision)
    selections = registered_selections(root)
    shared_base_image, parents = load_build_inputs(inputs_path, set(selections))
    shared_source_sha256 = next(iter(selections.values())).shared_base_source_sha256
    command_output(["docker", "pull", shared_base_image], cwd=root)
    shared_inspect = command_output(["docker", "image", "inspect", shared_base_image], cwd=root)
    try:
        validate_shared_base_config(shared_source_sha256, json.loads(shared_inspect))
    except json.JSONDecodeError as exc:
        raise BuildError(f"shared base inspect returned invalid JSON: {exc}") from None
    for tool in sorted(selections):
        parent = parents[tool]
        command_output(["docker", "pull", parent], cwd=root)
        inspect = command_output(["docker", "image", "inspect", parent], cwd=root)
        try:
            document = json.loads(inspect)
        except json.JSONDecodeError as exc:
            raise BuildError(f"{tool}: docker inspect returned invalid JSON: {exc}") from None
        try:
            validate_parent_config(selections[tool], document)
        except BuildError as exc:
            raise BuildError(f"{tool}: {exc}") from exc

    manifest, manifest_sha256 = toolbox_manifest(selections, parents, shared_base_image)
    image_metadata = final_image_metadata(manifest, selections, manifest_sha256, revision)
    metadata_b64 = base64.b64encode(
        json.dumps(image_metadata, sort_keys=True, separators=(",", ":")).encode()
    ).decode("ascii")
    command = [
        "docker",
        "build",
        "--pull=false",
        "--platform=linux/amd64",
        "-f",
        "simple/Dockerfile",
        ".",
        "--build-arg",
        f"SHARED_BASE_PARENT={shared_base_image}",
    ]
    for tool, argument in PARENT_ARGUMENTS.items():
        command.extend(("--build-arg", f"{argument}={parents[tool]}"))
    command.extend(
        (
            "--build-arg",
            f"IMAGE_METADATA_B64={metadata_b64}",
            "--build-arg",
            f"TOOLBOX_MANIFEST_SHA256={manifest_sha256}",
            "--build-arg",
            f"HARNESS_REVISION={revision}",
            "-t",
            tag,
        )
    )
    subprocess.run(command, cwd=root, check=True)
    inspect = command_output(["docker", "image", "inspect", tag], cwd=root)
    try:
        validate_built_config(json.loads(inspect), manifest_sha256, revision)
    except json.JSONDecodeError as exc:
        raise BuildError(f"built image inspect returned invalid JSON: {exc}") from None
    return manifest_sha256


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-inputs", required=True, type=Path)
    parser.add_argument("--harness-revision", required=True)
    parser.add_argument("--tag", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(__file__).parents[1].resolve()
    try:
        digest = build_image(root, args.build_inputs, args.harness_revision, args.tag)
    except (BuildError, BuildSelectionError, subprocess.CalledProcessError) as exc:
        print(f"build-image: {exc}", file=sys.stderr)
        return 1
    print(f"toolbox_manifest_sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
