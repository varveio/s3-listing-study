"""Shared argparse boundary for capsule-owned output normalizers."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Collection, Sequence
from typing import IO, Never, Protocol


class Normalizer(Protocol):
    """``(out, data, mode, prefix)``, plus ``dataset`` for a dataset-shaped mode.

    Almost every mode's whole output is one stream, so ``data`` is stdin and that
    is the entire input. A mode whose tool refuses to stream writes a
    *directory* — a Parquet dataset is parts plus sidecars — which no amount of
    stdin can carry, so those modes are named in ``dataset_modes`` and receive
    the published directory path instead.
    """

    def __call__(
        self,
        out: IO[bytes],
        data: bytes,
        mode: str,
        prefix: str = "",
        dataset: str = "",
    ) -> int: ...


class _ArgumentParser(argparse.ArgumentParser):
    error_exit = 2

    def error(self, message: str) -> Never:
        self.print_usage(sys.stderr)
        self.exit(self.error_exit, f"{self.prog}: error: {message}\n")


def build_parser(prog: str, modes: Collection[str], error_exit: int) -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog=prog,
        description="Normalize one subject output stream to the five-field study contract.",
        allow_abbrev=False,
    )
    parser.error_exit = error_exit
    parser.add_argument("mode", metavar="{" + ",".join(sorted(modes)) + "}")
    parser.add_argument("prefix", nargs="?", default="")
    parser.add_argument(
        "--dataset",
        default="",
        metavar="DIR",
        help="published native output directory, for a mode whose sink is a directory",
    )
    return parser


def normalizer_main(
    normalize: Normalizer,
    *,
    modes: Collection[str],
    prog: str,
    argv: Sequence[str] | None = None,
    broken_pipe_is_success: bool = False,
    error_exit: int = 2,
    dataset_modes: Collection[str] = (),
) -> int:
    """Parse CLI inputs, preserve stdin bytes, and invoke a clean normalizer."""
    parser = build_parser(prog, modes, error_exit)
    args = parser.parse_args(argv)
    if args.mode in dataset_modes:
        if not args.dataset:
            parser.error(f"mode {args.mode} reads a directory dataset; pass --dataset DIR")
    elif args.dataset:
        parser.error(f"mode {args.mode} reads its output on stdin; --dataset does not apply")
    try:
        if args.mode in dataset_modes:
            return normalize(sys.stdout.buffer, b"", args.mode, args.prefix, args.dataset)
        return normalize(sys.stdout.buffer, sys.stdin.buffer.read(), args.mode, args.prefix)
    except BrokenPipeError:
        if broken_pipe_is_success:
            return 0
        raise
