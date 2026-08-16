"""Build one simplified worker image from its registered immutable parent."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from s3_listing_study.common.build_selection import (
    BuildSelection,
    BuildSelectionError,
    load_registered_selection,
)

PINNED_IMAGE_RE = re.compile(r"\A[^\s@]+@sha256:[0-9a-f]{64}\Z")
REVISION_RE = re.compile(r"\A[0-9a-f]{40}\Z")
TOOL_BUILD_LABEL = "io.varve.s3-listing-study.tool-build-sha256"


class BuildError(RuntimeError):
    """The requested image cannot be bound to the checked-in capsule."""


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
    if actual_build != selection.tool_build_sha256:
        raise BuildError("parent tool-build label does not match the registered capsule")
    if config.get("WorkingDir") != selection.subject_workdir:
        raise BuildError("parent working directory does not match the registered capsule")


def build_image(root: Path, tool: str, parent: str, revision: str, tag: str) -> None:
    if PINNED_IMAGE_RE.fullmatch(parent) is None:
        raise BuildError("tool parent must be pinned by an immutable sha256 digest")
    if not tag or any(character.isspace() for character in tag):
        raise BuildError("output tag must be one non-empty token")
    assert_clean_revision(root, revision)
    selection = load_registered_selection(root, tool)
    command_output(["docker", "pull", parent], cwd=root)
    inspect = command_output(["docker", "image", "inspect", parent], cwd=root)
    try:
        document = json.loads(inspect)
    except json.JSONDecodeError as exc:
        raise BuildError(f"docker inspect returned invalid JSON: {exc}") from None
    validate_parent_config(selection, document)
    subprocess.run(
        [
            "docker",
            "build",
            "--pull=false",
            "--platform=linux/amd64",
            "-f",
            "simple/Dockerfile",
            ".",
            "--build-arg",
            f"TOOL={selection.tool}",
            "--build-arg",
            f"TOOL_PARENT={parent}",
            "--build-arg",
            f"TOOL_BUILD_SHA256={selection.tool_build_sha256}",
            "--build-arg",
            f"HARNESS_REVISION={revision}",
            "-t",
            tag,
        ],
        cwd=root,
        check=True,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--tool-parent", required=True)
    parser.add_argument("--harness-revision", required=True)
    parser.add_argument("--tag", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(__file__).parents[1].resolve()
    try:
        build_image(root, args.tool, args.tool_parent, args.harness_revision, args.tag)
    except (BuildError, BuildSelectionError, subprocess.CalledProcessError) as exc:
        print(f"build-image: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
