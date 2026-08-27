"""Build the one self-contained benchmark toolbox image from repository recipes."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark.contract import TOOL_IMAGE_FIELDS, TOOLBOX_TOOLS
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
    "s4cmd": ("tools/s4cmd/build/requirements.txt",),
    "s3p": ("tools/s3p/build/package.json", "tools/s3p/build/package-lock.json"),
}
TOOL_STAGES = {
    "aws-cli": "aws_cli_install",
    "minio-mc": "minio_mc_install",
    "ps3": "ps3_install",
    "rclone": "rclone_install",
    "s3-fast-list": "s3_fast_list_build",
    "s4cmd": "s4cmd_install",
    "s3kor": "s3kor_install",
    "s3p": "s3p_install",
    "s5cmd": "s5cmd_install",
    "s7cmd": "s7cmd_install",
    "swath": "swath_install",
}
SLICE_DOMAIN = b"s3-listing-study-image-slice-v1\0"
TOOL_SLICE_FACTS = (
    "tool_version",
    "tool_build_sha256",
    "tool_artifact_kind",
    "tool_artifact_locator",
    "tool_artifact_sha256",
    "recipe_sha256",
    "build_inputs_sha256",
    "adapter_bundle_sha256",
)
WORKER_REQUIREMENTS = "benchmark/build/requirements-worker.txt"
FROM_RE = re.compile(r"\AFROM\s+(?P<base>\S+)(?:\s+AS\s+(?P<name>\S+))?\s*\Z")
COPY_FROM_RE = re.compile(r"\ACOPY\s+--from=(?P<stage>\S+)\s")
SLICE_MARKER_RE = re.compile(r"\A#\s*slice:\s*(?P<tool>\S+)\s*\Z")
HEREDOC_RE = re.compile(r"""<<-?\s*(?P<quote>['"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)""")


class BuildError(RuntimeError):
    """The requested image cannot be bound to the checked-in recipes."""


@dataclass(frozen=True, slots=True)
class Instruction:
    """One logical recipe line, with the `# slice:` marker written above it."""

    text: str
    marker: str | None


@dataclass(frozen=True, slots=True)
class Stage:
    name: str
    base: str
    instructions: tuple[Instruction, ...]

    @property
    def text(self) -> str:
        return "\n".join(instruction.text for instruction in self.instructions)


@dataclass(frozen=True, slots=True)
class RecipeSlices:
    """The recipe partitioned into what belongs to one tool and what to all."""

    tool_stages: dict[str, dict[str, str]]
    tool_lines: dict[str, tuple[str, ...]]
    platform_stages: dict[str, str]
    platform_lines: tuple[str, ...]


def parse_recipe(source: str) -> tuple[Stage, ...]:
    """Physical lines to stages, keeping each instruction's exact source text.

    Continuations and heredoc bodies belong to the instruction that opened them;
    splitting on newlines alone would read a heredoc body as instructions.
    """
    lines = source.splitlines()
    stages: list[Stage] = []
    current: list[Instruction] | None = None
    name = base = ""
    marker: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip():
            marker = None
            continue
        if line.lstrip().startswith("#"):
            match = SLICE_MARKER_RE.match(line.strip())
            marker = None if match is None else match.group("tool")
            continue
        collected = [line]
        while collected[-1].rstrip().endswith("\\") and index < len(lines):
            collected.append(lines[index])
            index += 1
        for heredoc in HEREDOC_RE.finditer("\n".join(collected)):
            tag = heredoc.group("tag")
            while index < len(lines):
                collected.append(lines[index])
                index += 1
                if collected[-1].strip() == tag:
                    break
        instruction = Instruction("\n".join(collected), marker)
        marker = None
        opening = FROM_RE.match(instruction.text)
        if opening is not None:
            if opening.group("name") is None:
                raise BuildError("every toolbox recipe stage must be named with AS")
            if current is not None:
                stages.append(Stage(name, base, tuple(current)))
            name, base, current = opening.group("name"), opening.group("base"), [instruction]
            continue
        if current is None:
            raise BuildError("toolbox recipe has an instruction before its first stage")
        current.append(instruction)
    if current is None:
        raise BuildError("toolbox recipe declares no stage")
    stages.append(Stage(name, base, tuple(current)))
    return tuple(stages)


def _closure(stages: Mapping[str, Stage], start: str) -> set[str]:
    reached: set[str] = set()
    pending = [start]
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        base = stages[name].base
        if base in stages:
            pending.append(base)
    return reached


def attribute_recipe(source: str) -> RecipeSlices:
    """Partition the recipe by the three rules in `docs/identity.md`.

    Refusing an unpartitionable file is the point: a dropped line is a slice that
    silently under-invalidates.
    """
    stages = parse_recipe(source)
    by_name = {stage.name: stage for stage in stages}
    final = stages[-1]
    build_stages = {name: stage for name, stage in by_name.items() if name != final.name}
    owners: dict[str, set[str]] = {name: set() for name in build_stages}
    for tool, stage_name in sorted(TOOL_STAGES.items()):
        if stage_name not in build_stages:
            raise BuildError(f"{tool}: build stage {stage_name!r} is unreachable")
        for reached in _closure(by_name, stage_name):
            owners[reached].add(tool)
    # Reached by more than one tool, or by none, is platform: a change there can
    # move every subject, and erring coarse costs re-runs rather than evidence.
    owner_of = {
        name: next(iter(tools)) if len(tools) == 1 else None for name, tools in owners.items()
    }
    tool_lines: dict[str, list[str]] = {tool: [] for tool in TOOL_STAGES}
    platform_lines: list[str] = []
    for instruction in final.instructions:
        marker = instruction.marker
        if marker is not None and marker not in TOOL_STAGES:
            raise BuildError(f"final stage marks a line for unregistered tool {marker!r}")
        copied = COPY_FROM_RE.match(instruction.text)
        if copied is None:
            owner = marker
        else:
            stage_name = copied.group("stage")
            if stage_name not in build_stages:
                raise BuildError(f"final stage copies from unknown stage {stage_name!r}")
            if marker is not None:
                raise BuildError(f"COPY --from={stage_name} attributes itself; drop its marker")
            owner = owner_of[stage_name]
        if owner is None:
            platform_lines.append(instruction.text)
        else:
            tool_lines[owner].append(instruction.text)
    return RecipeSlices(
        tool_stages={
            tool: {
                name: by_name[name].text
                for name in sorted(_closure(by_name, stage))
                if owner_of[name] == tool
            }
            for tool, stage in TOOL_STAGES.items()
        },
        tool_lines={tool: tuple(lines) for tool, lines in tool_lines.items()},
        platform_stages={
            name: stage.text
            for name, stage in sorted(build_stages.items())
            if owner_of[name] is None
        },
        platform_lines=tuple(platform_lines),
    )


def _slice_digest(kind: str, document: Mapping[str, object]) -> str:
    canonical = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()
    return hashlib.sha256(SLICE_DOMAIN + kind.encode() + b"\0" + canonical).hexdigest()


def slice_digests(
    recipe: RecipeSlices,
    facts: Mapping[str, Mapping[str, str]],
    worker_requirements_sha256: str,
    revision: str,
) -> tuple[dict[str, str], str]:
    """The per-tool slices and the one platform slice they all share."""
    platform = _slice_digest(
        "platform",
        {
            "harness_revision": revision,
            "worker_requirements_sha256": worker_requirements_sha256,
            "stages": recipe.platform_stages,
            "final_stage_lines": list(recipe.platform_lines),
        },
    )
    tools: dict[str, str] = {}
    for tool in sorted(facts):
        if set(facts[tool]) != set(TOOL_SLICE_FACTS):
            raise BuildError(f"{tool}: slice inputs must be exactly {sorted(TOOL_SLICE_FACTS)}")
        tools[tool] = _slice_digest(
            "tool",
            {
                "tool": tool,
                **{name: facts[tool][name] for name in TOOL_SLICE_FACTS},
                "stages": recipe.tool_stages[tool],
                "final_stage_lines": list(recipe.tool_lines[tool]),
            },
        )
    return tools, platform


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
    """Load the active toolbox roster; retired capsules may remain as history."""
    tools = sorted(TOOLBOX_TOOLS)
    if set(TOOL_STAGES) != TOOLBOX_TOOLS:
        missing = sorted(TOOLBOX_TOOLS - set(TOOL_STAGES))
        extra = sorted(set(TOOL_STAGES) - TOOLBOX_TOOLS)
        raise BuildError(f"toolbox recipe roster changed (missing={missing}, extra={extra})")
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
        if selection.tool_artifact_kind == "container-image":
            pinned_parent = (
                f"FROM {selection.tool_artifact_locator}"
                f"@sha256:{selection.tool_artifact_sha256} AS {stage_name}"
            )
            if pinned_parent not in match.group(0):
                raise BuildError(f"{tool}: toolbox stage does not use the declared image digest")
            continue
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
    selections: Mapping[str, BuildSelection], root: Path, revision: str
) -> tuple[dict[str, object], str]:
    toolbox_recipe = root / "benchmark/build/Dockerfile"
    toolbox_recipe_bytes = toolbox_recipe.read_bytes()
    source = toolbox_recipe_bytes.decode("utf-8")
    validate_executed_sources(selections, root, source)
    facts: dict[str, dict[str, str]] = {}
    tools: dict[str, object] = {}
    for tool, selection in sorted(selections.items()):
        recipe = selection.metadata_path.parent / "Dockerfile"
        inputs = [selection.metadata_path, recipe]
        inputs.extend(root / path for path in SUPPORT_INPUTS.get(tool, ()))
        facts[tool] = {
            "tool_version": selection.tool_version,
            "tool_build_sha256": selection.tool_build_sha256,
            "tool_artifact_kind": selection.tool_artifact_kind,
            "tool_artifact_locator": selection.tool_artifact_locator,
            "tool_artifact_sha256": selection.tool_artifact_sha256,
            "recipe_sha256": hashlib.sha256(recipe.read_bytes()).hexdigest(),
            "build_inputs_sha256": _input_digest(root, inputs),
            "adapter_bundle_sha256": selection.adapter_bundle_sha256,
        }
    # The slices are manifest keys so the image's own recomputation covers them:
    # a digest the controller never checks cannot bind evidence to what ran.
    tool_slices, platform = slice_digests(
        attribute_recipe(source),
        facts,
        hashlib.sha256((root / WORKER_REQUIREMENTS).read_bytes()).hexdigest(),
        revision,
    )
    for tool, selection in sorted(selections.items()):
        tools[tool] = {
            **{name: facts[tool][name] for name in facts[tool] if name != "adapter_bundle_sha256"},
            "subject_workdir": selection.subject_workdir,
            "executable": list(selection.executable),
            "tool_slice_sha256": tool_slices[tool],
            "platform_sha256": platform,
        }
    manifest: dict[str, object] = {
        "schema_version": 3,
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
        "schema_version": 5,
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
    manifest, manifest_sha256 = toolbox_manifest(selections, root, revision)
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
        "benchmark/build/Dockerfile",
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


def image_set_document(root: Path, revision: str, image_uri: str) -> dict[str, object]:
    """The controller's image set for the toolbox built at `revision`.

    The same computation the build embeds in `image-metadata.json`, projected to
    what the controller accepts: that document carries `executable`, which the
    worker needs to exec a subject and an image set is refused for carrying, so
    emitting the set by hand has meant hand-filtering it every time. It is
    derived rather than transcribed for the reason every digest here is —
    a set that describes a different tree than the image is a lie the evidence
    would inherit.

    The revision is asserted clean, as it is for a build: the set describes the
    tree the image was built from or it describes nothing.
    """
    assert_clean_revision(root, revision)
    selections = registered_selections(root)
    manifest, manifest_sha256 = toolbox_manifest(selections, root, revision)
    metadata = final_image_metadata(manifest, selections, manifest_sha256, revision)
    entries = metadata["tools"]
    if not isinstance(entries, dict):
        raise BuildError("built image metadata tools are malformed")
    document = {
        **metadata,
        "image_uri": image_uri,
        "tools": {
            tool: {name: value for name, value in entry.items() if name in TOOL_IMAGE_FIELDS}
            for tool, entry in entries.items()
        },
    }
    # Validated by the code that will read it, here rather than at submit time,
    # so a set that cannot be used is a build failure and not a campaign one.
    # Imported locally: emitting a set must not need the controller's provider
    # libraries loaded to do it.
    from benchmark.campaign import load_image_set
    from benchmark.ledger import CampaignError

    with tempfile.NamedTemporaryFile("w", suffix=".json") as scratch:
        json.dump(document, scratch)
        scratch.flush()
        try:
            load_image_set(scratch.name, set(TOOLBOX_TOOLS))
        except CampaignError as exc:
            raise BuildError(
                f"emitted image set is not one the controller accepts: {exc}"
            ) from None
    return document


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness-revision", required=True)
    parser.add_argument("--tag", help="build the image and attest it under this tag")
    parser.add_argument(
        "--image-set",
        help="write the controller's image set here, for an image already pushed and pinned",
    )
    parser.add_argument("--image-uri", help="the pinned registry@sha256 URI the image set records")
    args = parser.parse_args(argv)
    # The digest is only knowable after a push, so a set is emitted in a second
    # invocation rather than by the build that has not been pushed yet.
    if (args.tag is None) == (args.image_set is None):
        parser.error("pass exactly one of --tag (build) or --image-set (emit)")
    if args.image_set is not None and args.image_uri is None:
        parser.error("--image-set needs the --image-uri it records")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # benchmark/src/benchmark/build_image.py -> repository root.
    root = Path(__file__).parents[3].resolve()
    try:
        if args.image_set is not None:
            document = image_set_document(root, args.harness_revision, args.image_uri)
            Path(args.image_set).write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(f"image_set={args.image_set}")
            return 0
        digest = build_image(root, args.harness_revision, args.tag)
    except (BuildError, BuildSelectionError, subprocess.CalledProcessError) as exc:
        print(f"build-image: {exc}", file=sys.stderr)
        return 1
    print(f"toolbox_manifest_sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
