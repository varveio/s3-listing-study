"""``s3-listing-study verify`` — the only thing that issues a verdict.

Centralised because a FAIL here is a finding this study publishes about
another project's tool, and because twelve agents each rolling their own diff
is twelve chances to record a moving bucket as a tool defect.

Verdicts:

* ``PASS`` (0)  — complete, no duplicates, fields match where the mode exposes them.
* ``FAIL`` (1)  — a real discrepancy, and a reference re-list says the bucket did
  not move — so it belongs to the tool OR to this mode's ``normalize.py``
  adapter. This verdict does not distinguish them.
* ``DRIFT`` (4) — the bucket moved since the snapshot. STOP. Not a tool finding.
* ``ERROR`` (3) — the verifier could not run. Not a pass.

``--registry PATH`` is the only way to redirect the registry, and identity is
``sha256`` of the file's raw bytes, so a receipt binds to exact bytes and no
environment variable can point the verifier at a registry nobody reviewed.

The security boundary is deliberately NOT on that surface. It is a keyword
argument to :func:`run`, so the shipped entry point always runs the mandatory
runner preflight and only an in-process caller — ``tests/tier3`` — can supply
:class:`~s3_listing_study.verify.security.PreflightSkipped`.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Never

from ..argparse_utils import UniqueStoreAction
from ..registry import default_path
from . import single, union
from .errors import ERROR_EXIT, VerifierError
from .registry_source import RegistrySource
from .security import SecurityBoundary


class _VerifierParser(argparse.ArgumentParser):
    """Turn argparse usage failures into the verifier's ERROR domain."""

    def error(self, message: str) -> Never:
        raise VerifierError(message)


_SINGLETON_OPTIONS = (
    "tool",
    "mode",
    "normalize",
    "bucket",
    "scope",
    "scope_prefix",
    "scope_delimiter",
    "receipts_dir",
    "remainder",
    "out",
    "stream",
    "registry",
)


@dataclass(slots=True)
class _Args:
    tool: str = ""
    mode: str = ""
    normalize: str = ""
    bucket: str = ""
    scope: str = ""
    scope_prefix: str = ""
    scope_delimiter: str = ""
    inputs: list[str] = field(default_factory=list)
    receipts: list[str] = field(default_factory=list)
    receipts_dir: str = ""
    remainder: str = ""
    out: str = ""
    stream: str = ""
    registry: str = ""


def build_parser() -> argparse.ArgumentParser:
    parser = _VerifierParser(
        prog="s3-listing-study verify",
        description="Audit normalized listing output against a recorded manifest.",
        allow_abbrev=False,
    )
    for destination in _SINGLETON_OPTIONS:
        parser.add_argument(f"--{destination.replace('_', '-')}", action=UniqueStoreAction)
    parser.add_argument("--input", action="append", dest="inputs", default=[])
    parser.add_argument("--receipt", action="append", dest="receipts", default=[])
    return parser


def _parse(argv: Sequence[str]) -> _Args:
    values = build_parser().parse_args(argv)
    return _Args(
        **{destination: getattr(values, destination) or "" for destination in _SINGLETON_OPTIONS},
        inputs=values.inputs,
        receipts=values.receipts,
    )


def _expand_receipts_dir(args: _Args) -> None:
    """Every immediate subdirectory carrying a run.meta, in sorted order.

    A convenience for a fan-out mode whose shards were each staged as a receipt
    directory. A symlink to a directory is NOT one, because the shell's
    ``find -type d`` does not follow symlinks either: the same argv resolving to
    a different shard set on the two implementations is a different union
    verdict.
    """
    directory = args.receipts_dir
    if not directory:
        return
    root = Path(directory)
    if not root.is_dir():
        raise VerifierError(f"--receipts-dir is not a directory: {directory}")
    for child in sorted(root.iterdir(), key=lambda p: str(p)):
        if child.is_dir() and not child.is_symlink() and (child / "run.meta").exists():
            args.receipts.append(str(child))


def run(argv: Sequence[str], security: SecurityBoundary | None = None) -> int:
    """Parse, dispatch, and return the process exit code."""
    args = _parse(argv)
    scope = args.scope
    if not scope:
        raise VerifierError("--scope is required (full|prefix|delimiter|union)")
    adapter = args.normalize
    if not adapter or not os.access(adapter, os.X_OK):
        raise VerifierError("--normalize must be an executable adapter")
    if args.stream not in ("", "stdout", "stderr"):
        raise VerifierError(
            "--stream must be stdout or stderr (it pins the verified stream for --scope union)"
        )
    _expand_receipts_dir(args)
    if not args.receipts:
        raise VerifierError(
            "--receipt is required: the verifier binds to the run that produced the output"
        )

    registry_arg = args.registry
    registry = RegistrySource.load(Path(registry_arg) if registry_arg else default_path())
    if security is None:
        security = SecurityBoundary()

    if scope == "union":
        if not args.receipts:
            raise VerifierError("--scope union needs at least one --receipt or a --receipts-dir")
        if args.scope_prefix or args.scope_delimiter:
            raise VerifierError(
                "--scope union takes no --scope-prefix / --scope-delimiter; coverage is derived "
                "from the shards"
            )
        if not args.out:
            raise VerifierError(
                "--scope union requires --out <dir> — the union verdict must land in a durable "
                "artifact (union-verify.md), not only on stdout"
            )
        return union.run(
            union.Options(
                receipts=args.receipts,
                normalize=adapter,
                out=args.out,
                registry=registry,
                security=security,
                remainder=args.remainder,
                stream=args.stream,
                mode=args.mode,
            )
        )

    if not args.inputs:
        raise VerifierError("at least one --input is required")
    return single.run(
        single.Options(
            receipt=args.receipts[-1],
            receipts=args.receipts,
            inputs=args.inputs,
            normalize=adapter,
            registry=registry,
            security=security,
            scope_kind=scope,
            scope_prefix=args.scope_prefix,
            scope_delimiter=args.scope_delimiter,
            tool=args.tool,
            mode=args.mode,
            bucket=args.bucket,
        )
    )


def main(argv: Sequence[str] | None = None, security: SecurityBoundary | None = None) -> int:
    """Every death is ERROR (3). A crash is not a verdict.

    Exit 1 is FAIL — a published finding about another project's tool. An
    unhandled exception escaping here would exit 1 through the interpreter and
    say FAIL about a tool the verifier never finished checking, so anything that
    is not a :class:`VerifierError` is reported with its traceback and mapped to
    the same ERROR the shell's ``die`` reaches.
    """
    try:
        return run(sys.argv[1:] if argv is None else argv, security)
    except VerifierError as exc:
        sys.stderr.write(f"\nverify-listing: {exc}\n")
        return ERROR_EXIT
    except Exception:
        traceback.print_exc()
        sys.stderr.write(
            "\nverify-listing: the verifier crashed and issued no verdict (ERROR, not FAIL)\n"
        )
        return ERROR_EXIT
