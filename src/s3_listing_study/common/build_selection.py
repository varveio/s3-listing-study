"""Validate and build one slug-selected shared derived image.

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
from s3_listing_study.common.python_runtime import LIBC_VALUES, PythonRuntimeError, ensure_runtime

SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SUBJECT_IMAGE_RE = re.compile(
    r"(?:[a-z0-9]+(?:[._-][a-z0-9]+)*/)*"
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*@sha256:[0-9a-f]{64}"
)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SUBJECT_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
"""A subject version that is also a legal Docker tag component.

The derived-image name embeds it verbatim, so a value Docker would refuse has
to fail at registration rather than at ``docker build``.
"""

ADAPTER_FILES = ("command.py", "normalize.py")
ADAPTER_MANIFEST_HEADER = b"s3-listing-study-adapter-bundle-v1\0"
DERIVED_IMAGE_NAMESPACE = "s3-listing-study"
"""Repository component of a derived image name, so it cannot read as upstream's."""

REQUIRED_FIELDS = {
    "tool",
    "subject_image",
    "subject_version",
    "python_libc",
    "subject_workdir",
    "executable",
    "command",
    "normalizer",
    "adapter_bundle_sha256",
}


class BuildSelectionError(ValueError):
    """A registered derived-image selection is invalid or inconsistent."""


@dataclass(frozen=True, slots=True)
class BuildSelection:
    tool: str
    subject_image: str
    subject_version: str
    python_libc: str
    subject_workdir: str
    executable: tuple[str, ...]
    command: str
    normalizer: str
    adapter_bundle_sha256: str
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

    subject_image = raw["subject_image"]
    if not isinstance(subject_image, str) or SUBJECT_IMAGE_RE.fullmatch(subject_image) is None:
        raise BuildSelectionError("subject_image must be pinned by a lowercase sha256 digest")

    # The digest is the subject's identity; this is the human-readable name for
    # the same thing, and it exists so a derived image can say which release it
    # wraps. It is declared here rather than read from `data/tool.json`, whose
    # `tested.version` records the version the study's receipts and claims are
    # anchored to — a different fact that legitimately lags the pinned digest
    # during a version bump.
    subject_version = raw["subject_version"]
    if not isinstance(subject_version, str) or not SUBJECT_VERSION_RE.fullmatch(subject_version):
        raise BuildSelectionError(
            "subject_version must name the pinned image's release as a Docker tag component"
        )

    python_libc = raw["python_libc"]
    if python_libc not in LIBC_VALUES:
        raise BuildSelectionError(
            f"python_libc must be one of {LIBC_VALUES}; got: {python_libc!r}. "
            "It selects the pinned interpreter build for this subject's base image, "
            "and an Alpine subject needs the musl one."
        )
    subject_workdir = _canonical_absolute_path(raw["subject_workdir"], "subject_workdir")

    executable_raw = raw["executable"]
    if not isinstance(executable_raw, list) or not executable_raw:
        raise BuildSelectionError("registered executable must be a non-empty path array")
    # Only the program itself is a path. A JVM tool's prefix continues with
    # literal argv tokens — Swath's is java, -jar, then the jar — and requiring
    # every element to be an absolute path would exclude every such subject.
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

    return BuildSelection(
        tool=tool,
        subject_image=subject_image,
        subject_version=subject_version,
        python_libc=python_libc,
        subject_workdir=subject_workdir,
        executable=executable,
        command=raw["command"],
        normalizer=raw["normalizer"],
        adapter_bundle_sha256=recorded_digest,
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
    return load_staged_selection(metadata, adapter_dir, expected_tool=tool)


def load_selection(path: Path, *, expected_tool: str, subject_image: str) -> BuildSelection:
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
    if selection.subject_image != subject_image:
        raise BuildSelectionError("subject image does not match the selected tool registration")
    return selection


def derived_image_tag(selection: BuildSelection) -> str:
    """The default name for the derived image built from one registration.

    A derived image is neither the subject nor the harness alone, so a name
    carrying one version misreads as the other: ``swath:0.2.2`` sat next to
    upstream's own ``ghcr.io/varveio/swath:0.2.2`` in a local image list and was
    taken for it. The name therefore states both versions and the subject digest
    it wraps — ``s3-listing-study/swath:0.2.2-h0.1.0-e03f7be9c025``.

    The tag is a label for humans, not an identity. Identity stays the derived
    image's own digest, which the attempt engine requires as ``--derived-image``
    and records in ``result.json``; two builds differing only in adapter bytes
    share this name and are told apart there.
    """
    digest = selection.subject_image.partition("@sha256:")[2][:12]
    return (
        f"{DERIVED_IMAGE_NAMESPACE}/{selection.tool}"
        f":{selection.subject_version}-h{__version__}-{digest}"
    )


def derived_image_build_command(root: Path, selection: BuildSelection, tag: str) -> tuple[str, ...]:
    """Resolve the shared builder inputs and return its exact Docker argv."""
    dockerfile = _contained(
        root / "harness" / "derived-image" / "Dockerfile",
        root,
        "shared derived-image Dockerfile",
    )
    # Native-only build: the host architecture selects the subject and pinned
    # interpreter payloads. A foreign build needs a separate explicit contract.
    interpreter = ensure_runtime(platform.machine(), selection.python_libc)
    duckdb_runtime = ensure_duckdb(platform.machine())
    return (
        "docker",
        "build",
        "--file",
        str(dockerfile),
        "--build-context",
        f"subject=docker-image://{selection.subject_image}",
        "--build-context",
        f"adapter={selection.adapter_dir}",
        "--build-context",
        f"selection={selection.metadata_path.parent}",
        "--build-context",
        f"python={interpreter}",
        "--build-context",
        f"duckdb={duckdb_runtime}",
        "--tag",
        tag,
        str(root),
    )


def build_derived_image_main(argv: Sequence[str] | None = None) -> int:
    """Build one registered derived image from only its slug and output tag."""
    parser = argparse.ArgumentParser(prog="s3-listing-study build-derived-image")
    parser.add_argument("--tool", required=True)
    parser.add_argument(
        "--tag",
        help="output image name; defaults to the name derived from the registration",
    )
    args = parser.parse_args(argv)
    if args.tag is not None and (not args.tag or "\x00" in args.tag):
        parser.error("--tag must be a non-empty Docker image tag")
    try:
        root = Path.cwd().resolve(strict=True)
        selection = load_registered_selection(root, args.tool)
        tag = derived_image_tag(selection) if args.tag is None else args.tag
        command = derived_image_build_command(root, selection, tag)
    except BuildSelectionError as exc:
        print(f"build-derived-image: {exc}", file=sys.stderr)
        return 2
    except PythonRuntimeError as exc:
        print(f"build-derived-image: {exc}", file=sys.stderr)
        return 2
    except DuckDBRuntimeError as exc:
        print(f"build-derived-image: {exc}", file=sys.stderr)
        return 2

    print(f"build-derived-image: building {tag}", file=sys.stderr)
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as exc:
        print(f"build-derived-image: cannot invoke Docker: {exc}", file=sys.stderr)
        return 2
