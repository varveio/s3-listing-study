"""``python3 -m tests.adapters <tool>…`` — report shell/Python adapter equivalence.

Exit 0 = every committed payload for every named tool normalised to identical
bytes under both adapters. Exit 1 = at least one did not, or the corpus is not
the pinned one. Exit 42 = ORACLE_UNAVAILABLE: a payload the corpus names could
not be read or did not match its recorded digest, so part of the corpus was not
judged at all. The same 0/1/42 contract `tests/differential` speaks, and for the
same reason — 42 is never a pass and never a skip.

The per-mode payload count is printed whether or not the run is green: a mode
nothing exercises is a mode this harness did not judge, and that is the risk a
later port inherits.
"""

from __future__ import annotations

import argparse
import sys

from tests.differential.oracle import EXIT_GREEN, EXIT_MISMATCH, EXIT_ORACLE_UNAVAILABLE

from .equivalence import (
    compare_tool,
    corpus_shortfall,
    discover_cases,
    payloads_per_mode,
    repo_root,
    unexercised_modes,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m tests.adapters")
    parser.add_argument("tools", nargs="+", help="tool name, e.g. aws-cli rclone")
    args = parser.parse_args(argv)

    repo = repo_root()
    failed = 0
    unavailable = 0
    for tool in args.tools:
        cases = discover_cases(repo, tool)
        counts = payloads_per_mode(cases)
        print(f"== {tool}: {len(cases)} committed payload(s) across {len(counts)} mode(s)")
        for mode, count in sorted(counts.items()):
            print(f"   {mode:<24} {count}")
        unexercised = unexercised_modes(repo, tool)
        print(
            "   modes with no committed payload: "
            + (", ".join(sorted(unexercised)) if unexercised else "none")
        )
        shortfall = corpus_shortfall(repo, tool)
        if shortfall:
            failed += 1
            print(f"   CORPUS SIZE MISMATCH: {shortfall}", file=sys.stderr)
        report = compare_tool(repo, tool)
        for comparison in report.comparisons:
            if comparison.identical:
                print(f"   ok   {comparison.case.name} ({len(comparison.shell_stdout)} bytes)")
            else:
                failed += 1
                print(f"   DIFF {comparison.case.name}: {comparison.detail()}", file=sys.stderr)
                if comparison.python_stderr:
                    print(f"        py stderr: {comparison.python_stderr!r}", file=sys.stderr)
        for case, reason in report.unavailable:
            unavailable += 1
            print(f"   UNAVAILABLE {case.name}: {reason}", file=sys.stderr)
        print(
            f"   {len(report.comparisons)} judged, "
            f"{len(report.differing)} mismatched, {len(report.unavailable)} unavailable"
        )

    if failed:
        print("MISMATCHES")
        return EXIT_MISMATCH
    if unavailable:
        print(
            f"ORACLE_UNAVAILABLE: {unavailable} payload(s) could not be read or did not match "
            "their recorded digest\n"
            "== part of the corpus was NOT judged, so this is not a green run "
            "(set $S3_STUDY_DATA to the directory holding manifests/ and receipts/)"
        )
        return EXIT_ORACLE_UNAVAILABLE
    print("byte-identical")
    return EXIT_GREEN


if __name__ == "__main__":
    sys.exit(main())
