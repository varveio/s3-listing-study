"""Typed command-adapter boundary owned by the benchmark runtime."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, ModuleType
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
    signed: bool = False
    """Whether the subject should sign its requests.

    Derived by the harness from the case's auth role -- null role, unsigned --
    and passed as a boolean because signing is an argv decision several
    capsules make (``--no-sign-request`` and friends), while *which* identity
    signs is the harness's business and never the subject's.
    """

    config: Mapping[str, object] = MappingProxyType({})
    """The capsule's own knobs, forwarded verbatim and never read by the harness.

    A capsule declares the keys it accepts in ``CONFIG_KEYS``; anything else is
    refused before ``build_command`` runs, so a misspelled knob is an error
    rather than a sweep whose cells are all identical.
    """

    sink_dir: str = ""
    """Container-local directory a mode with a native file sink may write into.

    Absent for every mode that writes its listing to stdout, which is most of
    them. A mode whose tool refuses to stream — Swath's sorted Parquet requires
    a directory dataset — builds its destination path under this directory, and
    the attempt engine collects, scans, and publishes whatever lands there. An
    adapter never chooses its own path: a container-local path the engine does
    not know about is output the attempt record cannot account for.
    """


class CommandBuilder(Protocol):
    """The importable function every capsule's ``command.py`` exports."""

    def __call__(self, request: CommandRequest) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class LoadedCommandAdapter:
    """A capsule's complete-argv builder and its recorded subject prefix."""

    build: CommandBuilder
    fixed_command_prefix: tuple[str, ...]
    config_keys: frozenset[str]
    supports_unsigned: bool
    """Whether this subject can list without a credential.

    The interesting half of the question: signing is the default credential
    chain and every subject here can do it, while four have no unsigned request
    path at all. A capsule states this because it is a fact about the tool, and
    a stratum it cannot issue is refused rather than silently ignored — which is
    what let a signing case run unsigned while every receipt recorded it signed.
    """

    supports_signed: bool
    """Almost always true, and false only where the capsule's own mechanism cannot
    carry a credential — mc resolves one from a static alias, not per request.

    A capsule omits ``SUPPORTS_SIGNED`` and the loader defaults it to true."""

    tool: str
    functional_env: dict[str, str]
    """Non-secret, tool-specific environment the subject structurally needs.

    Empty for most tools. Exists for a subject like mc, which has no
    ``--no-sign-request`` flag and instead resolves an anonymous alias from an
    endpoint URL it must be told — configuration, not a credential. Reviewed
    as non-secret at capsule-authoring time, same as ``DECLARED_FUNCTIONAL_ENV``
    in the attempt engine; the engine still refuses any key that collides with
    a reserved or credential name.
    """

    def compile(self, request: CommandRequest) -> tuple[str, ...]:
        """Return the complete subject argv the attempt engine will execute."""
        undeclared = sorted(set(request.config) - self.config_keys)
        if undeclared:
            raise CommandAdapterError(
                f"{self.tool} does not accept config key(s): {', '.join(undeclared)}"
            )
        if request.signed and not self.supports_signed:
            raise CommandAdapterError(f"{self.tool} cannot sign its requests")
        if not request.signed and not self.supports_unsigned:
            raise CommandAdapterError(f"{self.tool} has no unsigned request path")
        return self.build(request)


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
    config_keys: frozenset[str] = getattr(module, "CONFIG_KEYS", frozenset())
    supports_unsigned = getattr(module, "SUPPORTS_UNSIGNED", None)
    supports_signed = getattr(module, "SUPPORTS_SIGNED", True)
    functional_env = getattr(module, "FUNCTIONAL_ENV", {})
    if not callable(build):
        raise CommandAdapterError(f"{path} does not export callable build_command")
    if not isinstance(prefix, tuple) or not all(isinstance(item, str) for item in prefix):
        raise CommandAdapterError(f"{path} does not export tuple[str, ...] FIXED_COMMAND_PREFIX")
    if not isinstance(tool, str) or not tool:
        raise CommandAdapterError(f"{path} does not export non-empty TOOL")
    if not isinstance(functional_env, dict) or not all(
        isinstance(key, str) and key and isinstance(value, str) and "\x00" not in key + value
        for key, value in functional_env.items()
    ):
        raise CommandAdapterError(f"{path} exports invalid FUNCTIONAL_ENV; expected dict[str, str]")
    if expected_tool is not None and tool != expected_tool:
        raise CommandAdapterError(
            f"bundled driver is for {tool}, not requested tool {expected_tool}"
        )
    if not isinstance(config_keys, frozenset) or not all(
        isinstance(key, str) and key for key in config_keys
    ):
        raise CommandAdapterError(f"{path} exports invalid CONFIG_KEYS; expected frozenset[str]")
    if not isinstance(supports_unsigned, bool):
        raise CommandAdapterError(f"{path} does not export bool SUPPORTS_UNSIGNED")
    if not isinstance(supports_signed, bool):
        raise CommandAdapterError(f"{path} exports a non-bool SUPPORTS_SIGNED")
    if not supports_unsigned and not supports_signed:
        raise CommandAdapterError(f"{path} declares a subject that can issue no request at all")
    return LoadedCommandAdapter(
        cast(CommandBuilder, build),
        prefix,
        config_keys,
        supports_unsigned,
        supports_signed,
        tool,
        cast(dict[str, str], functional_env),
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
    parser.add_argument("--signed", action="store_true")
    parser.add_argument("--config", default="{}", metavar="JSON")
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
        config = json.loads(args.config)
        if not isinstance(config, dict):
            raise CommandAdapterError("--config must be a JSON object")
        command = build(
            CommandRequest(
                args.mode,
                args.bucket,
                args.region,
                args.prefix,
                signed=args.signed,
                config=config,
            )
        )
    except json.JSONDecodeError as exc:
        print(f"{prog}: --config is not valid JSON: {exc}", file=sys.stderr)
        return 2
    except CommandAdapterError as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return 2
    json.dump(command, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0
