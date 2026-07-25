"""Re-issue every committed verdict and require the bytes back, unchanged.

Two invariants govern this module:

* Nothing outside the temp root is ever written. `$S3_STUDY_DATA` and
  `tools/*/receipts` are read-only inputs — the oracle cannot be allowed to drift
  toward whatever the code under test happens to produce.
* Judgement is byte equality, not "equivalent". `verify.md` is the acceptance bar
  for the port; a formatting improvement is a separate change made after
  equivalence is proven.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .corpus import SingleCase, UnionCase
from .oracle import Oracle

_STAMP = re.compile(r"\*\*(?:PASS|FAIL|DRIFT|ERROR)\*\* — see `verify\.md`")
_PLACEHOLDER = "_(filled in by `harness/verify-listing.sh`)_"

# The one line a union report is allowed to differ on: it records when the report
# was generated, and the replay is by definition generating it again. Matched as
# bytes: a union report is compared byte for byte like every other artifact, so
# decoding it first — which would fold CRLF into LF and raise on non-UTF-8 —
# would quietly widen the acceptance bar.
_GENERATED = re.compile(rb"^- Generated \(UTC\): .*$", re.M)

_GENERATED_PLACEHOLDER = b"- Generated (UTC): <replay>"


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    log: str = ""


def make_link_farm(root: Path, repo: Path, manifests: Path, receipts: Path) -> Path:
    """Build the working directory the committed `run.meta` paths resolve against.

    The corpus mixes two conventions — payloads recorded as `receipts/...` (in the
    external data dir) and as `tools/...` (committed in-repo) — and no committed
    record declares `payload_path_base`, so the verifier resolves every relative
    path against the caller's working directory. A directory of symlinks makes
    both conventions resolve, without copying 531 MB and without the verifier
    seeing anything but the paths its records name.
    """
    farm = root / "cwd"
    farm.mkdir(parents=True, exist_ok=True)
    for name, target in (
        ("manifests", manifests),
        ("receipts", receipts),
        ("tools", repo / "tools"),
    ):
        link = farm / name
        # --keep is the debugging flag, so it is the one most likely to be
        # pointed at the same directory twice.
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target)
    return farm


def normalize_generated(report: bytes) -> bytes:
    """Blank the one line a union report is allowed to differ on.

    Bytes in, bytes out. Reading the report as text would fold CRLF into LF and
    raise on any byte the locale codec cannot decode — the first silently widens
    byte equality, the second turns a mismatch into a crash.
    """
    return _GENERATED.sub(_GENERATED_PLACEHOLDER, report)


def unstamp(receipt_md: Path) -> None:
    """Restore the verdict placeholder so the re-verify guard lets the replay run.

    The guard at harness/verify-listing.sh:671-680 refuses to restamp an already
    stamped receipt. Putting the placeholder back is exactly the state the receipt
    was in when the committed verdict was issued, which is what makes the restamped
    output comparable byte for byte.
    """
    text = receipt_md.read_text()
    stamps = _STAMP.findall(text)
    if len(stamps) != 1:
        raise RuntimeError(
            f"{receipt_md} carries {len(stamps)} stamped verdicts — expected exactly 1"
        )
    receipt_md.write_text(_STAMP.sub(_PLACEHOLDER, text, count=1))


IMPLEMENTATION = os.environ.get("S3STUDY_VERIFIER", "shell")
"""Which verifier the oracle judges: ``shell`` (the reference) or ``python``.

A parameter rather than a one-way switch, so the two implementations can be run
against the same corpus and compared. Repointing the oracle is a deliberate
change with its own justification (`README.md` § "The fixture is markdown"), not
a silent edit that happens to turn a gate green: nothing else about the replay
moves — same staging, same argv, same byte comparison.
"""


def verifier_argv(repo: Path, registry: Path) -> list[str]:
    """The command that issues the verdict, and how the registry reaches it.

    The shell verifier offers only the `SMOKE_REGISTRY` environment hook. The
    ported one takes `--registry PATH` and digests its raw bytes, so pointing it
    at the committed markdown fixture reproduces `254c8cfe…` naturally — the
    injection becomes an explicit argument rather than inherited environment.
    """
    if IMPLEMENTATION == "python":
        return [
            sys.executable,
            "-m",
            "s3_listing_study.verify",
            "--registry",
            str(registry),
        ]
    if IMPLEMENTATION != "shell":
        raise RuntimeError(f"S3STUDY_VERIFIER must be 'shell' or 'python', not {IMPLEMENTATION!r}")
    return [str(repo / "harness" / "verify-listing.sh")]


def _run(cmd: list[str], cwd: Path, registry: Path) -> tuple[int, str]:
    env = dict(os.environ)
    env["SMOKE_REGISTRY"] = str(registry)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).resolve().parents[2] / "src"), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    return proc.returncode, proc.stdout


def _tail(log: str, lines: int = 5) -> str:
    return "\n".join(log.strip().splitlines()[-lines:])


def replay_single(case: SingleCase, oracle: Oracle, farm: Path, work: Path) -> Result:
    """Replay one committed `verify.md` + its receipt stamp."""
    staged = work / case.rel.replace("/", "_")
    if staged.exists():
        shutil.rmtree(staged)
    shutil.copytree(case.receipt, staged, symlinks=True)

    committed_verify = (staged / "verify.md").read_bytes()
    committed_receipt = (staged / "receipt.md").read_bytes()
    (staged / "verify.md").unlink()
    unstamp(staged / "receipt.md")

    normalize = oracle.repo / "tools" / case.tool / "adapter" / "normalize.sh"
    cmd = [
        *verifier_argv(oracle.repo, oracle.registry),
        "--receipt",
        str(staged),
        "--input",
        case.input_path,
        "--normalize",
        str(normalize),
        *case.scope_args,
    ]
    rc, log = _run(cmd, farm, oracle.registry)
    if rc != 0:
        return Result(case.name, False, f"verifier exit {rc}", _tail(log))

    produced = staged / "verify.md"
    if not produced.is_file():
        return Result(case.name, False, "no verify.md was written", _tail(log))
    if produced.read_bytes() != committed_verify:
        return Result(case.name, False, "verify.md differs from the committed bytes", _tail(log))
    if (staged / "receipt.md").read_bytes() != committed_receipt:
        return Result(
            case.name, False, "restamped receipt.md differs from the committed bytes", _tail(log)
        )

    shutil.rmtree(staged, ignore_errors=True)
    return Result(case.name, True)


def replay_union(case: UnionCase, oracle: Oracle, farm: Path, work: Path) -> Result:
    """Replay one committed `union-verify.md`.

    The union path writes only into `--out`, and re-derives each shard from its own
    `run.meta` without stamping it, so the shards are read straight out of the repo
    at the exact relative paths the shard table spells — which is also what makes
    the regenerated shard table byte-identical.
    """
    out = work / (case.rel.replace("/", "_") + "__out")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    cmd = [
        *verifier_argv(oracle.repo, oracle.registry),
        "--scope",
        "union",
        "--normalize",
        str(oracle.repo / "tools" / case.tool / "adapter" / "normalize.sh"),
        "--out",
        str(out),
    ]
    for shard in case.shards:
        cmd += ["--receipt", shard]
    if case.remainder:
        cmd += ["--remainder", case.remainder]

    rc, log = _run(cmd, farm, oracle.registry)
    if rc != 0:
        return Result(case.name, False, f"verifier exit {rc}", _tail(log))

    produced = out / "union-verify.md"
    if not produced.is_file():
        return Result(case.name, False, "no union-verify.md was written", _tail(log))

    got = normalize_generated(produced.read_bytes())
    want = normalize_generated(case.union_md.read_bytes())
    if got != want:
        return Result(
            case.name,
            False,
            "union-verify.md differs from the committed bytes (modulo the Generated line)",
            _tail(log),
        )

    shutil.rmtree(out, ignore_errors=True)
    return Result(case.name, True)
