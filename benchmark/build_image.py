"""Build the one self-contained benchmark toolbox image from repository recipes."""

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

from benchmark.contract import TOOLBOX_TOOLS
from benchmark.runtime.build_selection import (
    BuildSelection,
    BuildSelectionError,
    load_registered_selection,
)

REVISION_RE = re.compile(r"\A[0-9a-f]{40}\Z")
TOOLBOX_LABEL = "io.varve.s3-listing-study.toolbox-manifest-sha256"
TOOLBOX_RECIPE_LABEL = "io.varve.s3-listing-study.toolbox-recipe-sha256"
SUPPORT_INPUTS = {
    "s3-fast-list": ("tools/s3-fast-list/build/Cargo.lock",),
    "s3p": ("tools/s3p/build/package.json", "tools/s3p/build/package-lock.json"),
    "s4cmd": ("tools/s4cmd/build/requirements.txt",),
}
TOOL_STAGES = {
    "aws-cli": "aws_cli_install",
    "minio-mc": "minio_mc_install",
    "ps3": "ps3_install",
    "rclone": "rclone_install",
    "s3-fast-list": "s3_fast_list_build",
    "s3kor": "s3kor_install",
    "s3p": "s3p_install",
    "s4cmd": "s4cmd_install",
    "s5cmd": "s5cmd_install",
    "s7cmd": "s7cmd_install",
    "swath": "swath_install",
}


class BuildError(RuntimeError):
    """The requested image cannot be bound to the checked-in recipes."""


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
        raise BuildError("refusing to attest an image from a dirty checkout")


def registered_selections(root: Path) -> dict[str, BuildSelection]:
    tools = sorted(path.parent.parent.name for path in (root / "tools").glob("*/build/image.json"))
    if set(tools) != TOOLBOX_TOOLS:
        missing = sorted(TOOLBOX_TOOLS - set(tools))
        extra = sorted(set(tools) - TOOLBOX_TOOLS)
        raise BuildError(f"registered toolbox roster changed (missing={missing}, extra={extra})")
    return {tool: load_registered_selection(root, tool) for tool in tools}


def _input_digest(root: Path, paths: list[Path]) -> str:
    digest = hashlib.sha256(b"s3-listing-study-toolbox-input-v1\0")
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix().encode()
        payload = path.read_bytes()
        digest.update(relative + b"\0" + len(payload).to_bytes(8, "big") + payload)
    return digest.hexdigest()


def validate_executed_sources(
    selections: Mapping[str, BuildSelection], root: Path, dockerfile: str
) -> None:
    """Bind capsule artifact facts to the sources the consolidated recipe executes."""
    for tool, selection in selections.items():
        stage_name = TOOL_STAGES[tool]
        match = re.search(
            rf"^FROM [^\n]+ AS {re.escape(stage_name)}\n(?P<body>.*?)(?=^FROM |\Z)",
            dockerfile,
            re.MULTILINE | re.DOTALL,
        )
        if match is None:
            raise BuildError(f"{tool}: toolbox recipe has no isolated build stage")
        stage = match.group("body")
        checksum = f"--checksum=sha256:{selection.tool_artifact_sha256}"
        if checksum not in stage or selection.tool_artifact_locator not in stage:
            raise BuildError(f"{tool}: toolbox stage does not use the declared artifact")
        if selection.tool_artifact_kind != "npm-package":
            continue
        package = json.loads((root / "tools/s3p/build/package.json").read_text())
        lock = json.loads((root / "tools/s3p/build/package-lock.json").read_text())
        locked = lock.get("packages", {}).get("node_modules/s3p", {})
        if (
            package.get("dependencies", {}).get("s3p") != selection.tool_artifact_locator
            or locked.get("resolved") != selection.tool_artifact_locator
            or not isinstance(locked.get("integrity"), str)
            or not locked["integrity"].startswith("sha512-")
            or "COPY tools/s3p/build/package.json tools/s3p/build/package-lock.json" not in stage
            or "package-lock integrity" not in stage
            or "npm ci --ignore-scripts" not in stage
        ):
            raise BuildError("s3p: toolbox npm stage does not match the declared locked artifact")


def toolbox_manifest(
    selections: Mapping[str, BuildSelection], root: Path
) -> tuple[dict[str, object], str]:
    toolbox_recipe = root / "benchmark/Dockerfile"
    toolbox_recipe_bytes = toolbox_recipe.read_bytes()
    validate_executed_sources(selections, root, toolbox_recipe_bytes.decode("utf-8"))
    tools: dict[str, object] = {}
    for tool, selection in sorted(selections.items()):
        recipe = selection.metadata_path.parent / "Dockerfile"
        inputs = [selection.metadata_path, recipe]
        inputs.extend(root / path for path in SUPPORT_INPUTS.get(tool, ()))
        tools[tool] = {
            "tool_version": selection.tool_version,
            "tool_build_sha256": selection.tool_build_sha256,
            "tool_artifact_kind": selection.tool_artifact_kind,
            "tool_artifact_locator": selection.tool_artifact_locator,
            "tool_artifact_sha256": selection.tool_artifact_sha256,
            "recipe_sha256": hashlib.sha256(recipe.read_bytes()).hexdigest(),
            "build_inputs_sha256": _input_digest(root, inputs),
            "subject_workdir": selection.subject_workdir,
            "executable": list(selection.executable),
        }
    manifest: dict[str, object] = {
        "schema_version": 2,
        "toolbox_recipe_sha256": hashlib.sha256(toolbox_recipe_bytes).hexdigest(),
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
    tools = {
        tool: {**value, "adapter_bundle_sha256": selections[tool].adapter_bundle_sha256}
        for tool, value in manifest_tools.items()
        if isinstance(tool, str) and isinstance(value, dict)
    }
    return {
        "schema_version": 4,
        "toolbox_manifest_sha256": manifest_sha256,
        "toolbox_recipe_sha256": manifest["toolbox_recipe_sha256"],
        "harness_revision": revision,
        "tools": tools,
    }


def validate_built_config(
    document: Any, manifest_sha256: str, recipe_sha256: str, revision: str
) -> None:
    if not isinstance(document, list) or len(document) != 1 or not isinstance(document[0], dict):
        raise BuildError("docker inspect returned an unexpected image document")
    if document[0].get("Architecture") != "amd64" or document[0].get("Os") != "linux":
        raise BuildError("built image is not linux/amd64")
    config = document[0].get("Config")
    if not isinstance(config, dict):
        raise BuildError("built image has no inspectable Config")
    if config.get("User") != "10001:10001" or config.get("WorkingDir") != "/home/s3study":
        raise BuildError("built image has an unexpected runtime identity")
    if config.get("Entrypoint") != ["/usr/bin/python3", "/opt/benchmark/benchmark/measure.py"]:
        raise BuildError("built image has an unexpected worker entrypoint")
    labels = config.get("Labels")
    if not isinstance(labels, dict) or labels.get(TOOLBOX_LABEL) != manifest_sha256:
        raise BuildError("built image toolbox label is missing or incorrect")
    if labels.get(TOOLBOX_RECIPE_LABEL) != recipe_sha256:
        raise BuildError("built image toolbox recipe label is missing or incorrect")
    if labels.get("io.varve.s3-listing-study.harness-revision") != revision:
        raise BuildError("built image harness revision label is missing or incorrect")


def build_image(root: Path, revision: str, tag: str) -> str:
    if not tag or any(character.isspace() for character in tag):
        raise BuildError("output tag must be one non-empty token")
    assert_clean_revision(root, revision)
    selections = registered_selections(root)
    manifest, manifest_sha256 = toolbox_manifest(selections, root)
    recipe_sha256 = str(manifest["toolbox_recipe_sha256"])
    metadata = final_image_metadata(manifest, selections, manifest_sha256, revision)
    encoded = base64.b64encode(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    ).decode("ascii")
    command = [
        "docker",
        "build",
        "--pull",
        "--platform=linux/amd64",
        "-f",
        "benchmark/Dockerfile",
        ".",
        "--build-arg",
        f"IMAGE_METADATA_B64={encoded}",
        "--build-arg",
        f"TOOLBOX_MANIFEST_SHA256={manifest_sha256}",
        "--build-arg",
        f"TOOLBOX_RECIPE_SHA256={recipe_sha256}",
        "--build-arg",
        f"HARNESS_REVISION={revision}",
        "-t",
        tag,
    ]
    subprocess.run(command, cwd=root, check=True)
    inspect = command_output(["docker", "image", "inspect", tag], cwd=root)
    try:
        validate_built_config(json.loads(inspect), manifest_sha256, recipe_sha256, revision)
    except json.JSONDecodeError as exc:
        raise BuildError(f"built image inspect returned invalid JSON: {exc}") from None
    return manifest_sha256


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness-revision", required=True)
    parser.add_argument("--tag", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(__file__).parents[1].resolve()
    try:
        digest = build_image(root, args.harness_revision, args.tag)
    except (BuildError, BuildSelectionError, subprocess.CalledProcessError) as exc:
        print(f"build-image: {exc}", file=sys.stderr)
        return 1
    print(f"toolbox_manifest_sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
