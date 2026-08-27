#!/usr/bin/env python3
"""Exec s5cmd run with an in-memory, reviewable set of disjoint prefix shards."""

from __future__ import annotations

import argparse
import os
import shlex


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--s5cmd", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="")
    parser.add_argument("--shard", action="append", required=True)
    parser.add_argument("--numworkers", required=True, type=int)
    parser.add_argument("--endpoint-url", default="")
    parser.add_argument("--unsigned", action="store_true")
    return parser


def commands(bucket: str, prefix: str, shards: list[str]) -> bytes:
    """Render the command-file bytes whose digest-equivalent inputs are in argv."""
    return "".join(
        f"ls -e -s {shlex.quote(f's3://{bucket}/{prefix}{shard}*')}\n" for shard in shards
    ).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = commands(args.bucket, args.prefix, args.shard)

    # s5cmd accepts a positional commands file but the benchmark worker offers
    # stdin as DEVNULL. A Linux memfd gives it ordinary seekable file semantics
    # without an untracked temporary file. Make the descriptor survive exec;
    # after exec the measured process is s5cmd itself, not a supervising shim.
    descriptor = os.memfd_create("s5cmd-fanout")
    os.set_inheritable(descriptor, True)
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]
    os.lseek(descriptor, 0, os.SEEK_SET)

    command = [args.s5cmd]
    if args.endpoint_url:
        command.extend(("--endpoint-url", args.endpoint_url))
    if args.unsigned:
        command.append("--no-sign-request")
    command.extend(("--numworkers", str(args.numworkers), "run", f"/proc/self/fd/{descriptor}"))
    os.execv(args.s5cmd, command)
    raise AssertionError("execv returned")


if __name__ == "__main__":
    raise SystemExit(main())
