"""Gate entrypoint: replay the whole corpus and speak the 0/1/42 exit contract.

    python3 -m tests.differential            # from the repo root

Exit 0 = every committed verdict came back byte-identical. Exit 1 = at least one
did not, i.e. the implementation under test is wrong. Exit 42 = the oracle is
unavailable and no judgement was made at all.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from .corpus import CorpusError, SingleCase, UnionCase, discover_singles, discover_unions
from .oracle import (
    EXIT_GREEN,
    EXIT_MISMATCH,
    EXIT_ORACLE_UNAVAILABLE,
    Oracle,
    OracleUnavailable,
    check_payloads,
    preflight,
)
from .replay import Result, make_link_farm, replay_single, replay_union

CaseT = TypeVar("CaseT", SingleCase, UnionCase)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_STATE = "notes/agent-state/cleanup.json"

# The size of the corpus, pinned. Discovery alone cannot police this: if a
# verdict artifact stops being discovered — a restructure moves it, a merge
# drops it, a glob stops matching — the denominator shrinks with the numerator
# and the gate reports a clean sweep of whatever survived. That is the exact
# false green this harness exists to prevent, so an unfiltered run that does not
# find precisely these counts fails.
#
# Changing either number is a DECISION, not a formality: it asserts that the set
# of committed verdicts in the repo genuinely changed, and it must be made in the
# same commit that adds or removes the verdict, with the reason in the message.
# Never adjust one to make a red gate go green.
EXPECTED_SINGLES = 67
EXPECTED_UNIONS = 2


class StateFileError(Exception):
    """The state file exists but could not be read back as a record."""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _head(repo: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return "unknown"


def emit_state(
    path: Path,
    unit: str,
    status: str,
    gate: str,
    gate_sha256: str,
    head: str,
    started: str,
    finished: str,
    counts: dict[str, int],
    selection: dict[str, object],
) -> None:
    """Write the unit's record and append it to the run log (cleanup plan §7)."""
    record = {
        "unit": unit,
        "status": status,
        "gate": gate,
        "selection": selection,
        "gate_output_sha256": gate_sha256,
        "head": head,
        "started": started,
        "finished": finished,
        "counts": counts,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    state: dict[str, dict[str, object]] = {"units": {}}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            # Silently starting over would delete every prior unit's record —
            # the one artifact an unattended run has to reason about what passed.
            raise StateFileError(
                f"{path} is not valid JSON ({exc}); refusing to overwrite"
            ) from exc
        if not (isinstance(loaded, dict) and isinstance(loaded.get("units"), dict)):
            raise StateFileError(f"{path} has no 'units' object; refusing to overwrite")
        state = loaded
    state["units"][unit] = record
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    with path.with_suffix(".log.jsonl").open("a") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tests.differential", description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO, help="repository root")
    parser.add_argument(
        "--jobs", type=int, default=4, help="replays to run concurrently (default 4)"
    )
    parser.add_argument(
        "--filter",
        default="",
        help="only replay cases whose path contains this substring",
    )
    parser.add_argument("--singles-only", action="store_true", help="skip the union replays")
    parser.add_argument(
        "--unions-only", action="store_true", help="skip the single-receipt replays"
    )
    parser.add_argument(
        "--keep", type=Path, default=None, help="work under this directory and keep it"
    )
    parser.add_argument("--unit", default="U0", help="unit name recorded in the state file")
    parser.add_argument(
        "--state",
        type=Path,
        default=None,
        help=f"state file to update (default {DEFAULT_STATE}, relative to --repo)",
    )
    parser.add_argument("--no-state", action="store_true", help="do not write a state file")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    raw_argv = list(sys.argv[1:]) if argv is None else list(argv)
    # The real invocation, not a literal. The state file is the record an
    # unattended run reads to decide whether a unit passed; a `gate` field that
    # cannot distinguish a full run from `--filter foo` is worse than no field.
    gate = shlex.join(["python3", "-m", "tests.differential", *raw_argv])
    restricted = bool(args.filter or args.singles_only or args.unions_only)
    selection: dict[str, object] = {
        "filter": args.filter,
        "singles_only": bool(args.singles_only),
        "unions_only": bool(args.unions_only),
        "restricted": restricted,
        "expected_singles": EXPECTED_SINGLES,
        "expected_unions": EXPECTED_UNIONS,
    }
    started = _now()
    counts = {"singles_ok": 0, "singles_bad": 0, "unions_ok": 0, "unions_bad": 0}

    def finish(status: str, summary: str, code: int) -> int:
        print(summary)
        if args.no_state:
            return code
        state = args.state if args.state else repo / DEFAULT_STATE
        try:
            emit_state(
                path=state,
                unit=args.unit,
                status=status,
                gate=gate,
                gate_sha256=hashlib.sha256(summary.encode()).hexdigest(),
                head=_head(repo),
                started=started,
                finished=_now(),
                counts=counts,
                selection=selection,
            )
        except (StateFileError, OSError) as exc:
            print(f"== STATE FILE NOT WRITTEN: {exc}", file=sys.stderr)
            # No record means no evidence the unit passed, so never hand back 0.
            return max(code, EXIT_MISMATCH)
        return code

    def unavailable(exc: OracleUnavailable) -> int:
        return finish(
            "oracle-unavailable",
            f"ORACLE_UNAVAILABLE: {exc}\n"
            "== the oracle could not be read; NO verdict was replayed and nothing was judged",
            EXIT_ORACLE_UNAVAILABLE,
        )

    try:
        oracle = preflight(repo)
    except OracleUnavailable as exc:
        return unavailable(exc)

    try:
        singles = [] if args.unions_only else discover_singles(repo)
        unions = [] if args.singles_only else discover_unions(repo)
    except CorpusError as exc:
        return finish("red", f"CORPUS ERROR: {exc}", EXIT_MISMATCH)

    if not args.filter:
        shortfall = []
        if not args.unions_only and len(singles) != EXPECTED_SINGLES:
            shortfall.append(f"singles: expected {EXPECTED_SINGLES}, discovered {len(singles)}")
        if not args.singles_only and len(unions) != EXPECTED_UNIONS:
            shortfall.append(f"unions: expected {EXPECTED_UNIONS}, discovered {len(unions)}")
        if shortfall:
            return finish(
                "corpus-mismatch",
                "== CORPUS SIZE MISMATCH: " + "; ".join(shortfall) + "\n"
                "== the corpus is not the one this gate is pinned to, so a clean sweep of "
                "what survives proves nothing\n"
                "== if the change is deliberate, update EXPECTED_SINGLES/EXPECTED_UNIONS in "
                "tests/differential/__main__.py in the same commit",
                EXIT_MISMATCH,
            )

    if args.filter:
        singles = [c for c in singles if args.filter in c.rel]
        unions = [c for c in unions if args.filter in c.rel]

    if not singles and not unions:
        return finish(
            "no-cases",
            "== NO CASES SELECTED: nothing was replayed and nothing was judged"
            + (f" (--filter {args.filter!r} matched no case)" if args.filter else ""),
            EXIT_MISMATCH,
        )

    try:
        checked = check_payloads(
            oracle,
            [p for c in singles for p in c.payloads] + [p for c in unions for p in c.payloads],
        )
    except OracleUnavailable as exc:
        return unavailable(exc)

    root = Path(tempfile.mkdtemp(prefix="s3study-replay-")) if args.keep is None else args.keep
    root.mkdir(parents=True, exist_ok=True)
    work = root / "staged"
    work.mkdir(parents=True, exist_ok=True)
    farm = make_link_farm(root, repo, oracle.manifests, oracle.receipts)

    failures: list[Result] = []

    def run_all(
        cases: Sequence[CaseT],
        fn: Callable[[CaseT, Oracle, Path, Path], Result],
        ok_key: str,
        bad_key: str,
    ) -> None:
        if not cases:
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            futures = {pool.submit(fn, c, oracle, farm, work): c for c in cases}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result.ok:
                    counts[ok_key] += 1
                else:
                    counts[bad_key] += 1
                    failures.append(result)
                print(f"{'ok  ' if result.ok else 'MISMATCH'} {result.name}", flush=True)

    try:
        run_all(singles, replay_single, "singles_ok", "singles_bad")
        run_all(unions, replay_union, "unions_ok", "unions_bad")

        for failure in sorted(failures, key=lambda r: r.name):
            print(f"\n--- MISMATCH {failure.name}: {failure.detail}", file=sys.stderr)
            if failure.log:
                print(failure.log, file=sys.stderr)

        summary = (
            f"== singles byte-identical: {counts['singles_ok']}/{len(singles)}"
            f"  unions byte-identical: {counts['unions_ok']}/{len(unions)}"
            f"  mismatched: {counts['singles_bad'] + counts['unions_bad']}"
            f"  payloads sha-verified: {checked}"
        )
        if restricted:
            summary += "\n== RESTRICTED RUN: a subset was replayed; this is not a green gate"
        if args.keep is not None:
            summary += f"\n== work root kept at {root}"

        if failures:
            return finish("red", summary, EXIT_MISMATCH)
        # A run that judged only part of the corpus never claims green: the
        # status an agent reads has to mean what it says.
        return finish("partial" if restricted else "green", summary, EXIT_GREEN)
    except Exception:
        # A crash is not a mismatch and not an oracle failure. Without this the
        # process dies before any record is written, leaving the previous —
        # possibly green — record standing as the last word on this unit.
        return finish(
            "error",
            "== GATE CRASHED: no verdict was formed\n" + traceback.format_exc(),
            EXIT_MISMATCH,
        )
    finally:
        if args.keep is None:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
