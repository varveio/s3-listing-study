"""Validate tool-owned build facts used by the self-contained toolbox."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from benchmark.runtime.command_adapter import CommandAdapterError, load_command_adapter

SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
TOOL_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
ADAPTER_FILES = ("command.py", "normalize.py")
ADAPTER_MANIFEST_HEADER = b"s3-listing-study-adapter-bundle-v1\0"
REQUIRED_FIELDS = {
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


class BuildSelectionError(ValueError):
    """Checked-in build facts are malformed or disagree with their files."""


@dataclass(frozen=True, slots=True)
class BuildSelection:
    tool: str
    tool_version: str
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
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
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


def _absolute_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise BuildSelectionError(f"{field} must be a non-empty canonical absolute path")
    path = PurePosixPath(value)
    if not value.startswith("/") or "\x00" in value or str(path) != value or ".." in path.parts:
        raise BuildSelectionError(f"{field} must be a canonical absolute path without traversal")
    return value


def adapter_bundle_sha256(adapter_dir: Path) -> str:
    root = adapter_dir.resolve(strict=True)
    digest = hashlib.sha256(ADAPTER_MANIFEST_HEADER)
    for filename in ADAPTER_FILES:
        path = _contained(root / filename, root, f"adapter component {filename}")
        payload = path.read_bytes()
        digest.update(filename.encode())
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _input_bundle_sha256(root: Path, inputs: Sequence[Path], header: bytes) -> str:
    digest = hashlib.sha256(header)
    files: list[Path] = []
    for item in inputs:
        files.extend(
            path for path in item.rglob("*") if path.is_file()
        ) if item.is_dir() else files.append(item)
    for path in sorted(files, key=lambda value: value.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        payload = path.read_bytes()
        digest.update(relative + b"\0" + len(payload).to_bytes(8, "big") + payload)
    return digest.hexdigest()


def load_staged_selection(
    metadata_path: Path, adapter_dir: Path, *, expected_tool: str | None = None
) -> BuildSelection:
    raw = _read_metadata(metadata_path)
    tool = raw["tool"]
    if not isinstance(tool, str):
        raise BuildSelectionError("registered tool must be a string")
    validate_tool_slug(tool)
    if expected_tool is not None and tool != expected_tool:
        raise BuildSelectionError("selected tool does not match registered build metadata")
    version = raw["tool_version"]
    if not isinstance(version, str) or TOOL_VERSION_RE.fullmatch(version) is None:
        raise BuildSelectionError("tool_version must name the pinned release as a token")
    tool_build = raw["tool_build_sha256"]
    if not isinstance(tool_build, str) or SHA256_RE.fullmatch(tool_build) is None:
        raise BuildSelectionError("tool_build_sha256 must be 64 lowercase hexadecimal digits")
    artifact = raw["tool_artifact"]
    if not isinstance(artifact, dict) or set(artifact) != {"kind", "locator", "sha256"}:
        raise BuildSelectionError("tool_artifact must contain exactly kind, locator, and sha256")
    kind, locator, artifact_sha = artifact["kind"], artifact["locator"], artifact["sha256"]
    if kind not in {"release-archive", "release-binary", "npm-package", "source-archive"}:
        raise BuildSelectionError("tool_artifact kind is unsupported")
    if not isinstance(locator, str) or not locator or any(c.isspace() for c in locator):
        raise BuildSelectionError("tool_artifact locator must be a non-empty token")
    if not isinstance(artifact_sha, str) or SHA256_RE.fullmatch(artifact_sha) is None:
        raise BuildSelectionError("tool_artifact sha256 must be 64 lowercase hexadecimal digits")
    executable_raw = raw["executable"]
    if not isinstance(executable_raw, list) or not executable_raw:
        raise BuildSelectionError("registered executable must be a non-empty path array")
    executable = (
        _absolute_path(executable_raw[0], "executable[0]"),
        *(value for value in executable_raw[1:] if isinstance(value, str) and value),
    )
    if len(executable) != len(executable_raw):
        raise BuildSelectionError("executable tokens must be non-empty strings")
    if raw["command"] != "adapter/command.py" or raw["normalizer"] != "adapter/normalize.py":
        raise BuildSelectionError("adapter paths must use the fixed registered capsule layout")
    recorded = raw["adapter_bundle_sha256"]
    if not isinstance(recorded, str) or SHA256_RE.fullmatch(recorded) is None:
        raise BuildSelectionError("adapter_bundle_sha256 must be 64 lowercase hexadecimal digits")
    if adapter_bundle_sha256(adapter_dir) != recorded:
        raise BuildSelectionError("adapter bundle digest does not match registered build metadata")
    try:
        adapter = load_command_adapter(adapter_dir / "command.py", expected_tool=tool)
    except CommandAdapterError as exc:
        raise BuildSelectionError(str(exc)) from exc
    if adapter.fixed_command_prefix != executable:
        raise BuildSelectionError("registered executable does not match command adapter")
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    return BuildSelection(
        tool,
        version,
        tool_build,
        kind,
        locator,
        artifact_sha,
        _absolute_path(raw["subject_workdir"], "subject_workdir"),
        executable,
        raw["command"],
        raw["normalizer"],
        recorded,
        hashlib.sha256(canonical).hexdigest(),
        metadata_path.resolve(strict=True),
        adapter_dir.resolve(strict=True),
    )


def load_selection(path: Path, *, expected_tool: str) -> BuildSelection:
    capsule = path.parent.parent
    if path != capsule / "build" / "image.json" or capsule.name != expected_tool:
        raise BuildSelectionError("build metadata is not at the expected capsule location")
    return load_staged_selection(path, capsule / "adapter", expected_tool=expected_tool)


def load_registered_selection(repo_root: Path, tool: str) -> BuildSelection:
    validate_tool_slug(tool)
    root = repo_root.resolve(strict=True)
    metadata = _contained(
        root / "tools" / tool / "build" / "image.json", root / "tools", "build metadata"
    )
    selection = load_staged_selection(
        metadata, root / "tools" / tool / "adapter", expected_tool=tool
    )
    inputs = tuple(path for path in sorted(metadata.parent.iterdir()) if path.name != "image.json")
    expected = _input_bundle_sha256(
        root,
        inputs,
        b"s3-listing-study-tool-build-input-v1\0"
        + selection.tool_artifact_kind.encode()
        + b"\0"
        + selection.tool_artifact_locator.encode()
        + b"\0"
        + selection.tool_artifact_sha256.encode()
        + b"\0",
    )
    if expected != selection.tool_build_sha256:
        raise BuildSelectionError("tool_build_sha256 does not match tool build inputs")
    return selection
