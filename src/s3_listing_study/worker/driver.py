"""Resolve one typed logical request through the driver bundled in this image."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from s3_listing_study.common.build_selection import load_staged_selection
from s3_listing_study.common.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    load_command_adapter,
)

BUNDLED_COMMAND = Path("/opt/s3-listing-study/tool/command.py")
BUNDLED_ADAPTER = BUNDLED_COMMAND.parent
BUNDLED_SELECTION = Path("/opt/s3-listing-study/selection.json")


@dataclass(frozen=True, slots=True)
class ResolvedInvocation:
    """Complete subject argv and its canonical adapter-bundle identity."""

    argv: tuple[str, ...]
    adapter_bundle_sha256: str
    functional_env: dict[str, str] = field(default_factory=dict)
    shared_base_source_sha256: str = "0" * 64
    tool_build_sha256: str = "0" * 64
    tool_artifact: dict[str, str] = field(
        default_factory=lambda: {"kind": "synthetic", "locator": "test", "sha256": "0" * 64}
    )
    subject_workdir: str = "/"


def validate_request(request: CommandRequest) -> None:
    if request.operation != "list":
        raise CommandAdapterError(f"unsupported operation: {request.operation}")
    if not request.tool:
        raise CommandAdapterError("tool is required")
    if not request.mode:
        raise CommandAdapterError("mode is required")
    if not request.bucket:
        raise CommandAdapterError("bucket is required")
    if not request.region:
        raise CommandAdapterError("region is required")
    if request.auth not in ("anonymous", "authenticated"):
        raise CommandAdapterError("auth must be anonymous or authenticated")
    if request.sink_dir and not request.sink_dir.startswith("/"):
        raise CommandAdapterError("sink directory must be an absolute path")
    if "\x00" in request.sink_dir:
        raise CommandAdapterError("sink directory contains a NUL byte")
    if "\x00" in request.prefix:
        raise CommandAdapterError("prefix contains a NUL byte")
    if len(request.prefix.encode("utf-8")) > 1024:
        raise CommandAdapterError("prefix exceeds the 1,024-byte S3 key limit")


def resolve_command(
    request: CommandRequest,
    command_path: Path = BUNDLED_COMMAND,
) -> tuple[str, ...]:
    """Validate and resolve complete subject argv without importing a normalizer."""
    validate_request(request)
    return load_command_adapter(command_path, expected_tool=request.tool).compile(request)


def resolve_invocation(
    request: CommandRequest,
    *,
    selection_path: Path = BUNDLED_SELECTION,
    adapter_dir: Path = BUNDLED_ADAPTER,
) -> ResolvedInvocation:
    """Resolve a selected in-image adapter and validate its bundle attestation."""
    validate_request(request)
    selection = load_staged_selection(
        selection_path,
        adapter_dir,
        expected_tool=request.tool,
    )
    adapter = load_command_adapter(
        adapter_dir / "command.py",
        expected_tool=request.tool,
    )
    command = adapter.compile(request)
    return ResolvedInvocation(
        argv=command,
        adapter_bundle_sha256=selection.adapter_bundle_sha256,
        functional_env=adapter.functional_env,
        shared_base_source_sha256=selection.shared_base_source_sha256,
        tool_build_sha256=selection.tool_build_sha256,
        tool_artifact={
            "kind": selection.tool_artifact_kind,
            "locator": selection.tool_artifact_locator,
            "sha256": selection.tool_artifact_sha256,
        },
        subject_workdir=selection.subject_workdir,
    )
