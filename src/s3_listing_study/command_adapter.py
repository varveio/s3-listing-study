"""Typed command-adapter boundary shared by every runnable tool capsule."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast


class CommandAdapterError(ValueError):
    """A friendly command request cannot be represented by the subject CLI."""


@dataclass(frozen=True, slots=True)
class CommandRequest:
    """Tool-neutral inputs from which a capsule compiles exact subject argv."""

    mode: str
    bucket: str
    region: str
    prefix: str = ""
    tool: str = ""
    operation: str = "list"
    auth: str = "anonymous"
    concurrency: int | None = None


class CommandBuilder(Protocol):
    """The importable function every capsule's ``command.py`` exports."""

    def __call__(self, request: CommandRequest) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class LoadedCommandAdapter:
    """A capsule's complete-argv builder and its recorded subject prefix."""

    build: CommandBuilder
    fixed_command_prefix: tuple[str, ...]
    concurrency_range: tuple[int, int] | None
    tool: str

    def compile(self, request: CommandRequest) -> tuple[str, ...]:
        """Return the complete subject argv the attempt engine will execute."""
        validate_concurrency(
            request,
            tool=self.tool,
            supported_range=self.concurrency_range,
        )
        return self.build(request)


def validate_concurrency(
    request: CommandRequest,
    *,
    tool: str,
    supported_range: tuple[int, int] | None = None,
) -> int | None:
    """Validate an explicit logical concurrency for one adapter.

    Absence means the adapter keeps its registered default. An explicit value
    is accepted only when the adapter declares a finite inclusive range.
    """
    value = request.concurrency
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandAdapterError("concurrency must be an integer")
    if supported_range is None:
        raise CommandAdapterError(f"{tool} does not support logical concurrency")
    minimum, maximum = supported_range
    if not minimum <= value <= maximum:
        raise CommandAdapterError(
            f"{tool} concurrency must be an integer in {minimum}..{maximum}; got: {value}"
        )
    return value


def _load_module(path: Path) -> ModuleType:
    name = f"_s3_listing_command_{path.parent.parent.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CommandAdapterError(f"cannot load command adapter: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_command_adapter(
    path: Path,
    *,
    expected_tool: str | None = None,
) -> LoadedCommandAdapter:
    """Load and validate one capsule-owned ``adapter/command.py`` module."""
    module = _load_module(path)
    build = getattr(module, "build_command", None)
    prefix = getattr(module, "FIXED_COMMAND_PREFIX", None)
    tool = getattr(module, "TOOL", None)
    concurrency_range = getattr(module, "CONCURRENCY_RANGE", None)
    if not callable(build):
        raise CommandAdapterError(f"{path} does not export callable build_command")
    if not isinstance(prefix, tuple) or not all(isinstance(item, str) for item in prefix):
        raise CommandAdapterError(f"{path} does not export tuple[str, ...] FIXED_COMMAND_PREFIX")
    if not isinstance(tool, str) or not tool:
        raise CommandAdapterError(f"{path} does not export non-empty TOOL")
    if expected_tool is not None and tool != expected_tool:
        raise CommandAdapterError(
            f"bundled driver is for {tool}, not requested tool {expected_tool}"
        )
    if concurrency_range is not None and (
        not isinstance(concurrency_range, tuple)
        or len(concurrency_range) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) for value in concurrency_range)
        or concurrency_range[0] < 1
        or concurrency_range[0] > concurrency_range[1]
    ):
        raise CommandAdapterError(
            f"{path} exports invalid CONCURRENCY_RANGE; expected positive (minimum, maximum)"
        )
    return LoadedCommandAdapter(
        cast(CommandBuilder, build),
        prefix,
        cast(tuple[int, int] | None, concurrency_range),
        tool,
    )


def build_parser(prog: str) -> argparse.ArgumentParser:
    """Build the common inspection CLI used by every command adapter."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Compile friendly listing parameters into a JSON argv array.",
        allow_abbrev=False,
    )
    parser.add_argument("mode")
    parser.add_argument("bucket")
    parser.add_argument("region")
    parser.add_argument("prefix", nargs="?", default="")
    parser.add_argument("--concurrency", type=int)
    return parser


def command_adapter_main(
    build: Callable[[CommandRequest], tuple[str, ...]],
    *,
    prog: str,
    argv: Sequence[str] | None = None,
) -> int:
    """Parse one request and print adapter argv as JSON for human inspection."""
    args = build_parser(prog).parse_args(argv)
    try:
        command = build(
            CommandRequest(
                args.mode,
                args.bucket,
                args.region,
                args.prefix,
                concurrency=args.concurrency,
            )
        )
    except CommandAdapterError as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return 2
    json.dump(command, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0
