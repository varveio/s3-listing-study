"""Validate and build one slug-selected image from the shared study base.

``adapter_bundle_sha256`` is SHA-256 over this canonical byte manifest:

* ASCII ``s3-listing-study-adapter-bundle-v1\\0``;
* for ``command.py`` then ``normalize.py``: the UTF-8 filename, NUL, the file
  length as an unsigned eight-byte big-endian integer, then the exact bytes.

The normalizer is read as opaque bytes. It is never imported while selecting an
image or resolving an attempt command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from s3_listing_study import __version__
from s3_listing_study.common.command_adapter import CommandAdapterError, load_command_adapter
from s3_listing_study.common.duckdb_runtime import DuckDBRuntimeError
from s3_listing_study.common.duckdb_runtime import ensure_runtime as ensure_duckdb
from s3_listing_study.common.ijson_runtime import IjsonRuntimeError
from s3_listing_study.common.ijson_runtime import ensure_runtime as ensure_ijson

SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
IMMUTABLE_IMAGE_RE = re.compile(
    r"(?:[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?/)*"
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*@sha256:[0-9a-f]{64}"
)
TOOL_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
"""A tool version that is also a legal Docker tag component.

The final per-tool image name embeds it verbatim, so a value Docker would refuse has
to fail at registration rather than at ``docker build``.
"""

ADAPTER_FILES = ("command.py", "normalize.py")
ADAPTER_MANIFEST_HEADER = b"s3-listing-study-adapter-bundle-v1\0"
DERIVED_IMAGE_NAMESPACE = "s3-listing-study"
"""Repository component that distinguishes a final image from a tool's own image."""

REQUIRED_FIELDS = {
    "tool",
    "tool_version",
    "shared_base_source_sha256",
    "tool_build_sha256",
    "tool_artifact",
    "subject_workdir",
    "executable",
    "command",
    "normalizer",
    "adapter_bundle_sha256",
}


class BuildSelectionError(ValueError):
    """A registered final-image selection is invalid or inconsistent."""


@dataclass(frozen=True, slots=True)
class BuildSelection:
    tool: str
    tool_version: str
    shared_base_source_sha256: str
    tool_build_sha256: str
    tool_artifact_kind: str
    tool_artifact_locator: str
    tool_artifact_sha256: str
    subject_workdir: str
    executable: tuple[str, ...]
    command: str
    normalizer: str
    adapter_bundle_sha256: str
    selection_sha256: str
    metadata_path: Path
    adapter_dir: Path


def validate_tool_slug(tool: str) -> str:
    """Return a canonical capsule slug or raise a controlled selection error."""
    if SLUG_RE.fullmatch(tool) is None:
        raise BuildSelectionError(f"invalid tool slug: {tool!r}")
    return tool


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BuildSelectionError(f"duplicate JSON key in build metadata: {key}")
        result[key] = value
    return result


def _read_metadata(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except BuildSelectionError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildSelectionError(f"cannot read registered build metadata: {exc}") from exc
    if not isinstance(value, dict) or set(value) != REQUIRED_FIELDS:
        raise BuildSelectionError("registered build metadata has an unexpected field set")
    return value


def _contained(path: Path, root: Path, description: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise BuildSelectionError(f"{description} escapes its registered directory") from exc
    return resolved


def _canonical_absolute_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise BuildSelectionError(f"{field} must be a non-empty canonical absolute path")
    path = PurePosixPath(value)
    if not value.startswith("/") or "\x00" in value or str(path) != value or ".." in path.parts:
        raise BuildSelectionError(f"{field} must be a canonical absolute path without traversal")
    return value


def _argv_token(value: object, field: str) -> str:
    """One literal argv element of a multi-token program prefix.

    A token is not path-checked here because containment for these elements is
    the capsule gate's cross-check in ``capsule.py:596``: the registered
    executable must equal the adapter's ``fixed_command_prefix``, so a token
    cannot name anything the reviewed adapter does not already name.
    """
    if not isinstance(value, str) or not value or "\x00" in value:
        raise BuildSelectionError(f"{field} must be a non-empty NUL-free argv token")
    return value


def adapter_bundle_sha256(adapter_dir: Path) -> str:
    """Hash the canonical command/normalizer bundle without importing either file."""
    root = adapter_dir.resolve(strict=True)
    digest = hashlib.sha256(ADAPTER_MANIFEST_HEADER)
    for filename in ADAPTER_FILES:
        path = _contained(root / filename, root, f"adapter component {filename}")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise BuildSelectionError(f"cannot read adapter component {filename}: {exc}") from exc
        encoded_name = filename.encode("utf-8")
        digest.update(encoded_name)
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def load_staged_selection(
    metadata_path: Path,
    adapter_dir: Path,
    *,
    expected_tool: str | None = None,
) -> BuildSelection:
    """Validate already-selected metadata and its exact staged adapter bundle."""
    raw = _read_metadata(metadata_path)
    tool = raw["tool"]
    if not isinstance(tool, str):
        raise BuildSelectionError("registered tool must be a string")
    validate_tool_slug(tool)
    if expected_tool is not None and tool != expected_tool:
        raise BuildSelectionError("selected tool does not match registered build metadata")

    # This is the human-readable release name for the selected tool artifact.
    # It is declared here rather than read from `data/tool.json`, whose
    # `tested.version` records the version the study's receipts and claims are
    # anchored to — a different fact that legitimately lags the pinned digest
    # during a version bump.
    tool_version = raw["tool_version"]
    if not isinstance(tool_version, str) or not TOOL_VERSION_RE.fullmatch(tool_version):
        raise BuildSelectionError(
            "tool_version must name the pinned tool release as a Docker tag component"
        )
    shared_base_source_sha256 = raw["shared_base_source_sha256"]
    tool_build_sha256 = raw["tool_build_sha256"]
    for field, value in (
        ("shared_base_source_sha256", shared_base_source_sha256),
        ("tool_build_sha256", tool_build_sha256),
    ):
        if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
            raise BuildSelectionError(f"{field} must be 64 lowercase hexadecimal digits")
    artifact = raw["tool_artifact"]
    if not isinstance(artifact, dict) or set(artifact) != {"kind", "locator", "sha256"}:
        raise BuildSelectionError("tool_artifact must contain exactly kind, locator, and sha256")
    artifact_kind = artifact["kind"]
    artifact_locator = artifact["locator"]
    artifact_sha256 = artifact["sha256"]
    if artifact_kind not in {"release-archive", "release-binary", "npm-package", "source-archive"}:
        raise BuildSelectionError("tool_artifact kind is unsupported")
    if (
        not isinstance(artifact_locator, str)
        or not artifact_locator
        or any(character.isspace() for character in artifact_locator)
    ):
        raise BuildSelectionError("tool_artifact locator must be a non-empty token")
    if not isinstance(artifact_sha256, str) or SHA256_RE.fullmatch(artifact_sha256) is None:
        raise BuildSelectionError("tool_artifact sha256 must be 64 lowercase hexadecimal digits")
    subject_workdir = _canonical_absolute_path(raw["subject_workdir"], "subject_workdir")

    executable_raw = raw["executable"]
    if not isinstance(executable_raw, list) or not executable_raw:
        raise BuildSelectionError("registered executable must be a non-empty path array")
    # Only the program itself is a path. A JVM tool's prefix continues with
    # literal argv tokens — Swath's is java, -jar, then the jar — and requiring
    # every element to be an absolute path would exclude every such tool.
    executable = (
        _canonical_absolute_path(executable_raw[0], "executable[0]"),
        *(
            _argv_token(value, f"executable[{index}]")
            for index, value in enumerate(executable_raw[1:], start=1)
        ),
    )

    if raw["command"] != "adapter/command.py" or raw["normalizer"] != "adapter/normalize.py":
        raise BuildSelectionError("adapter paths must use the fixed registered capsule layout")

    recorded_digest = raw["adapter_bundle_sha256"]
    if not isinstance(recorded_digest, str) or SHA256_RE.fullmatch(recorded_digest) is None:
        raise BuildSelectionError("adapter_bundle_sha256 must be 64 lowercase hexadecimal digits")
    actual_digest = adapter_bundle_sha256(adapter_dir)
    if actual_digest != recorded_digest:
        raise BuildSelectionError("adapter bundle digest does not match registered build metadata")

    command_path = _contained(adapter_dir / "command.py", adapter_dir, "command adapter")
    _contained(adapter_dir / "normalize.py", adapter_dir, "normalizer adapter")
    try:
        adapter = load_command_adapter(command_path, expected_tool=tool)
    except CommandAdapterError as exc:
        raise BuildSelectionError(str(exc)) from exc
    if adapter.fixed_command_prefix != executable:
        raise BuildSelectionError(
            "registered executable does not match the selected command adapter"
        )

    selection_digest = hashlib.sha256(
        b"s3-listing-study-selection-v1\0"
        + json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return BuildSelection(
        tool=tool,
        tool_version=tool_version,
        shared_base_source_sha256=shared_base_source_sha256,
        tool_build_sha256=tool_build_sha256,
        tool_artifact_kind=artifact_kind,
        tool_artifact_locator=artifact_locator,
        tool_artifact_sha256=artifact_sha256,
        subject_workdir=subject_workdir,
        executable=executable,
        command=raw["command"],
        normalizer=raw["normalizer"],
        adapter_bundle_sha256=recorded_digest,
        selection_sha256=selection_digest,
        metadata_path=metadata_path.resolve(strict=True),
        adapter_dir=adapter_dir.resolve(strict=True),
    )


def load_registered_selection(repo_root: Path, tool: str) -> BuildSelection:
    """Load the one capsule selection named by ``tool`` from a repository root."""
    validate_tool_slug(tool)
    try:
        root = repo_root.resolve(strict=True)
    except OSError as exc:
        raise BuildSelectionError(f"cannot resolve repository root: {exc}") from exc
    metadata = root / "tools" / tool / "build" / "image.json"
    adapter_dir = root / "tools" / tool / "adapter"
    resolved_metadata = _contained(metadata, root / "tools", "build metadata")
    expected_metadata = metadata.resolve(strict=True)
    if resolved_metadata != expected_metadata:
        raise BuildSelectionError("build metadata is not at the expected capsule location")
    _contained(adapter_dir, root / "tools", "adapter directory")
    selection = load_staged_selection(metadata, adapter_dir, expected_tool=tool)
    shared_digest = shared_base_source_sha256(root)
    if shared_digest != selection.shared_base_source_sha256:
        raise BuildSelectionError("shared_base_source_sha256 does not match shared-base inputs")
    build_inputs = tuple(
        path for path in sorted(metadata.parent.iterdir()) if path.name != "image.json"
    )
    tool_digest = _input_bundle_sha256(
        root,
        build_inputs,
        (
            b"s3-listing-study-tool-build-input-v1\0"
            + selection.tool_artifact_kind.encode()
            + b"\0"
            + selection.tool_artifact_locator.encode()
            + b"\0"
            + selection.tool_artifact_sha256.encode()
            + b"\0"
        ),
    )
    if tool_digest != selection.tool_build_sha256:
        raise BuildSelectionError("tool_build_sha256 does not match tool build inputs")
    return selection


def _input_bundle_sha256(root: Path, inputs: Sequence[Path], header: bytes) -> str:
    """Hash named input bytes with paths, lengths, and no mutable filesystem metadata."""
    digest = hashlib.sha256(header)
    files: list[Path] = []
    for item in inputs:
        if item.is_dir():
            files.extend(
                path
                for path in item.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            )
        elif item.is_file():
            files.append(item)
        else:
            raise BuildSelectionError(f"registered build input is missing: {item}")
    for path in sorted(files, key=lambda value: value.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(relative)
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def shared_base_source_sha256(root: Path) -> str:
    """Hash every repository input that can affect the stable shared base."""
    return _input_bundle_sha256(
        root,
        (
            root / ".dockerignore",
            root / "harness/shared-image/Dockerfile",
            root / "harness/shared-image/debian-packages.lock",
            root / "src/s3_listing_study/common/runtime_identity.py",
            root / "src/s3_listing_study/common/duckdb_runtime.py",
            root / "src/s3_listing_study/common/ijson_runtime.py",
        ),
        b"s3-listing-study-shared-base-input-v1\0",
    )


def load_selection(path: Path, *, expected_tool: str) -> BuildSelection:
    """Validate capsule metadata for the repository's capsule gate.

    Image construction does not expose this compatibility validator; the build
    CLI accepts only a tool slug and tag and resolves these values itself.
    """
    capsule = path.parent.parent
    expected_path = capsule / "build" / "image.json"
    if path != expected_path or capsule.name != expected_tool:
        raise BuildSelectionError("build metadata is not at the expected capsule location")
    selection = load_staged_selection(
        path,
        capsule / "adapter",
        expected_tool=expected_tool,
    )
    return selection


def derived_image_tag(selection: BuildSelection) -> str:
    """The default name for the final per-tool image from one registration.

    The tag combines the tool version, harness version, and the first twelve
    characters of ``tool_build_sha256`` — for example,
    ``s3-listing-study/swath:0.2.4-h0.1.0-092e413676ef``. The adapter bundle has
    its own canonical hash and deliberately does not affect this human-readable
    tag.

    The tag is a label, not an identity. Identity stays the final image's own
    digest, which the attempt engine requires as ``--derived-image`` and records
    in ``result.json``; builds sharing a tag are told apart there.
    """
    digest = selection.tool_build_sha256[:12]
    return (
        f"{DERIVED_IMAGE_NAMESPACE}/{selection.tool}"
        f":{selection.tool_version}-h{__version__}-{digest}"
    )


def tool_image_tag(selection: BuildSelection) -> str:
    """Default local tag for the durable tool parent."""
    return (
        f"s3-listing-study-tool/{selection.tool}:"
        f"{selection.tool_version}-{selection.selection_sha256}"
    )


def tool_image_build_command(
    root: Path, selection: BuildSelection, tag: str, shared_base_image: str
) -> tuple[str, ...]:
    """Build one real tool image from an immutable shared-runtime parent."""
    dockerfile = _contained(
        selection.metadata_path.parent / "Dockerfile", root / "tools", "tool Dockerfile"
    )
    if IMMUTABLE_IMAGE_RE.fullmatch(shared_base_image) is None:
        raise BuildSelectionError("shared base image must be an immutable digest reference")
    return (
        "docker",
        "build",
        "--file",
        str(dockerfile),
        "--build-arg",
        f"SHARED_BASE_IMAGE={shared_base_image}",
        "--build-arg",
        f"TOOL_BUILD_SHA256={selection.tool_build_sha256}",
        "--build-arg",
        f"SELECTION_SHA256={selection.selection_sha256}",
        "--build-context",
        f"tool_build={selection.metadata_path.parent}",
        "--tag",
        tag,
        str(root),
    )


def derived_image_build_command(
    root: Path, selection: BuildSelection, tag: str, tool_image: str
) -> tuple[str, ...]:
    """Build the thin worker layer from an immutable tool parent."""
    dockerfile = _contained(
        root / "harness/derived-image/Dockerfile", root, "derived image Dockerfile"
    )
    if IMMUTABLE_IMAGE_RE.fullmatch(tool_image) is None:
        raise BuildSelectionError("tool image must be an immutable digest reference")
    _, _, digest = tool_image.rpartition("@")
    return (
        "docker",
        "build",
        "--file",
        str(dockerfile),
        "--build-arg",
        f"TOOL_IMAGE={tool_image}",
        "--build-arg",
        f"TOOL_IMAGE_DIGEST={digest}",
        "--build-arg",
        f"TOOL_IMAGE_URI={tool_image}",
        "--build-arg",
        f"SELECTION_SHA256={selection.selection_sha256}",
        "--build-context",
        f"adapter={selection.adapter_dir}",
        "--build-context",
        f"selection={selection.metadata_path.parent}",
        "--tag",
        tag,
        str(root),
    )


def shared_base_build_command(root: Path, tag: str) -> tuple[str, ...]:
    """Build the one shared runtime image before any per-tool image is built."""
    dockerfile = _contained(
        root / "harness/shared-image/Dockerfile", root, "shared image Dockerfile"
    )
    source_sha256 = shared_base_source_sha256(root)
    duckdb_runtime = ensure_duckdb(platform.machine())
    ijson_runtime = ensure_ijson(platform.machine())
    return (
        "docker",
        "build",
        "--file",
        str(dockerfile),
        "--build-arg",
        f"SHARED_BASE_SOURCE_SHA256={source_sha256}",
        "--build-context",
        f"duckdb={duckdb_runtime}",
        "--build-context",
        f"ijson={ijson_runtime}",
        "--tag",
        tag,
        str(root),
    )


def build_shared_image_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="s3-listing-study build-shared-image")
    parser.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    if not args.tag or "\x00" in args.tag:
        parser.error("--tag must be a non-empty Docker image tag")
    try:
        root = Path.cwd().resolve(strict=True)
        command = shared_base_build_command(root, args.tag)
    except (BuildSelectionError, DuckDBRuntimeError, IjsonRuntimeError) as exc:
        print(f"build-shared-image: {exc}", file=sys.stderr)
        return 2
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as exc:
        print(f"build-shared-image: cannot invoke Docker: {exc}", file=sys.stderr)
        return 2


def build_derived_image_main(argv: Sequence[str] | None = None) -> int:
    """Build one registered final per-tool image from only its slug and output tag."""
    parser = argparse.ArgumentParser(prog="s3-listing-study build-derived-image")
    parser.add_argument("--tool", required=True)
    parser.add_argument(
        "--tag",
        help="output image name; defaults to the name derived from the registration",
    )
    parser.add_argument("--tool-image", required=True)
    args = parser.parse_args(argv)
    if args.tag is not None and (not args.tag or "\x00" in args.tag):
        parser.error("--tag must be a non-empty Docker image tag")
    try:
        root = Path.cwd().resolve(strict=True)
        selection = load_registered_selection(root, args.tool)
        tag = derived_image_tag(selection) if args.tag is None else args.tag
        command = derived_image_build_command(root, selection, tag, args.tool_image)
    except BuildSelectionError as exc:
        print(f"build-derived-image: {exc}", file=sys.stderr)
        return 2
    print(f"build-derived-image: building {tag}", file=sys.stderr)
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as exc:
        print(f"build-derived-image: cannot invoke Docker: {exc}", file=sys.stderr)
        return 2


def build_tool_image_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="s3-listing-study build-tool-image")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--tag")
    parser.add_argument("--shared-base-image", required=True)
    args = parser.parse_args(argv)
    try:
        root = Path.cwd().resolve(strict=True)
        selection = load_registered_selection(root, args.tool)
        tag = tool_image_tag(selection) if args.tag is None else args.tag
        if not tag or "\x00" in tag:
            raise BuildSelectionError("--tag must be a non-empty Docker image tag")
        command = tool_image_build_command(root, selection, tag, args.shared_base_image)
    except BuildSelectionError as exc:
        print(f"build-tool-image: {exc}", file=sys.stderr)
        return 2
    print(f"build-tool-image: building {tag}", file=sys.stderr)
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as exc:
        print(f"build-tool-image: cannot invoke Docker: {exc}", file=sys.stderr)
        return 2
