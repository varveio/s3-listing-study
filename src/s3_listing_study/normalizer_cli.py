"""Shared argparse boundary for capsule-owned output normalizers."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Collection, Sequence
from typing import IO, Never

Normalizer = Callable[[IO[bytes], bytes, str, str], int]


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
    return parser


def normalizer_main(
    normalize: Normalizer,
    *,
    modes: Collection[str],
    prog: str,
    argv: Sequence[str] | None = None,
    broken_pipe_is_success: bool = False,
    error_exit: int = 2,
) -> int:
    """Parse CLI inputs, preserve stdin bytes, and invoke a clean normalizer."""
    args = build_parser(prog, modes, error_exit).parse_args(argv)
    try:
        return normalize(sys.stdout.buffer, sys.stdin.buffer.read(), args.mode, args.prefix)
    except BrokenPipeError:
        if broken_pipe_is_success:
            return 0
        raise
