"""Stage the shipped verifier so its FAIL / DRIFT / ERROR branches are reachable.

Every committed verdict in this repo is a PASS, so the differential replay
(tier 1) never enters the branch that matters most: on any discrepancy the
verifier re-lists the reference bucket *before* blaming anyone, and splits the
outcome three ways — reference agrees (FAIL, an accusation about a third party's
software), reference disagrees (DRIFT, explicitly not a tool finding), re-list
did not run (ERROR, no attribution possible). That branch shells out to `docker`
against real S3 behind the runner-security gate, so offline it dies rc=3 and all
three verdicts are untested.

Tier 3 makes them reachable without weakening a single check:

* the verifier is **copied byte-identical** into a staged harness tree
  (`staged_sha256` proves it), so what runs is the shipped implementation;
* only `runner-security-lib.sh` is replaced, by a stub that keeps the production
  argv construction and neuters just the runner preflight;
* `docker` is a fixture replayer on PATH (`fake-docker.sh`), driven by
  environment variables and argv alone.

Nothing here knows the verifier is written in bash except `_stage_harness`,
which is where a ported verifier is pointed instead. The reference listing, the
receipts, the manifest, and the fake docker are the oracle, and they are
implementation-agnostic by construction.

`S3STUDY_VERIFIER` selects which implementation the tier judges — `shell` (the
reference) or `python`. It is a parameter, not a one-way switch, so both can be
run against the identical cases and compared. The shipped shell verifier is
staged byte-identical either way, which is what `staged_sha256` proves: `harness/`
is never written to, because it is the reference side of the port's differential.
The one shell-specific piece of the rig is `runner-security-lib.stub.sh`. The
ported verifier's equivalent seam is `tests/tier3/verifier_entry.py`, which is
not installed and passes `PreflightSkipped()` in process — the port has no
argv that disables the preflight, exactly as the shell has none.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TIER3 = Path(__file__).resolve().parent
FIXTURES = REPO / "tests" / "fixtures" / "tier3"

VERIFIER = REPO / "harness" / "verify-listing.sh"
REGISTRY_LOOKUP = REPO / "harness" / "registry-lookup.sh"
REGISTRY_FIXTURE = REPO / "tests" / "fixtures" / "registry-254c8cfe.md"
NORMALIZE = REPO / "tools" / "aws-cli" / "adapter" / "normalize.sh"

PLACEHOLDER = "_(filled in by `harness/verify-listing.sh`)_"
RECEIPT_MD = f"# Receipt (tier-3 fixture)\n\n- Verdict: {PLACEHOLDER}\n"

# The fake docker's whole contract.
ENV_REFERENCE = "S3STUDY_FAKE_DOCKER_REFERENCE"
ENV_RC = "S3STUDY_FAKE_DOCKER_RC"
ENV_STDERR = "S3STUDY_FAKE_DOCKER_STDERR"

# Committed receipts the staged ones are derived from: the run.meta shape is
# real, only the prefix, the paths and the digests of the synthetic payloads are
# rewritten. The listing itself is invented (`tests/tier3/make_fixtures.py`) —
# tier 3 tests verdict logic, not the contents of anyone's bucket.
ALPHA_META = REPO / "tools/aws-cli/receipts/smoke/s3api-v2-text-hourly/run.meta"
BETA_META = REPO / "tools/aws-cli/receipts/smoke/fanout/shard-monthly/run.meta"
REMAINDER_META = REPO / "tools/aws-cli/receipts/smoke/fanout/remainder/run.meta"

IMPLEMENTATION = os.environ.get("S3STUDY_VERIFIER", "shell")

ALPHA_PREFIX = "alpha/"
BETA_PREFIX = "beta/"

# run.meta records both streams; the staged receipts carry only the payload the
# verdict is formed from, so the stderr binding is dropped rather than left
# pointing at a file that is not there.
_DROP = ("stderr_path", "stderr_sha256", "stderr_truncated", "stderr_dropped_bytes")


def fixture(name: str) -> list[str]:
    return (FIXTURES / name).read_text().splitlines()


def manifest_rows() -> list[str]:
    return fixture("manifest.tsv")


def alpha_rows() -> list[str]:
    return fixture("payload-alpha.txt")


def beta_rows() -> list[str]:
    return fixture("payload-beta.txt")


def remainder_rows() -> list[str]:
    """The root-level key, under neither shard prefix: the unprefixed remainder."""
    return fixture("payload-root.txt")


def bucket_rows() -> list[str]:
    """The reference listing a re-list would return if the bucket had not moved."""
    return alpha_rows() + beta_rows() + remainder_rows()


def key_of(row: str) -> str:
    return row.split("\t", 1)[0]


def bump_mtime(row: str, mtime: str = "2026-07-24T09:15:00+00:00") -> str:
    """Rewrite ONLY the mtime — the identical-byte overwrite the verifier calls out.

    Same size, same ETag, same key: a drift check over key sets (or even over
    key/size/etag) sees no drift at all, the reference is read as agreeing with
    the manifest, and the tool eats a FAIL for correctly reporting the new
    mtime. `harness/verify-listing.sh:878-885` exists to stop exactly that.
    """
    key, size, etag, _, storage_class = row.split("\t")
    return "\t".join((key, size, etag, mtime, storage_class))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_meta(dst: Path, source: Path, overrides: dict[str, str]) -> None:
    applied: set[str] = set()
    lines: list[str] = []
    for line in source.read_text().splitlines():
        name = line.partition("=")[0]
        if name in _DROP:
            continue
        if name in overrides:
            lines.append(f"{name}={overrides[name]}")
            applied.add(name)
        else:
            lines.append(line)
    lines += [f"{k}={v}" for k, v in overrides.items() if k not in applied]
    dst.write_text("".join(f"{line}\n" for line in lines))


@dataclass(frozen=True)
class Outcome:
    """What one verifier invocation produced."""

    returncode: int
    report: str
    log: str
    receipt: str = ""

    @property
    def verdict(self) -> str:
        for line in self.report.splitlines():
            if line.startswith("**Verdict:"):
                return line.removeprefix("**Verdict:").removesuffix("**").strip()
        return ""

    @property
    def verdict_line(self) -> str:
        for line in self.report.splitlines():
            if line.startswith("**Verdict:"):
                return line
        return ""


class Stage:
    """A staged harness tree, a staged manifest, and staged receipts under `root`."""

    def __init__(self, root: Path, manifest: Sequence[str] | None = None) -> None:
        self.root = root
        self.harness = root / "harness"
        self.bin = root / "bin"
        self.verifier: list[str] = []
        self._stage_harness()
        self.manifest = self._stage_manifest(manifest if manifest is not None else manifest_rows())
        self.manifest_sha256 = sha256_file(self.manifest)

    # ---------------------------------------------------------------- staging
    def _stage_harness(self) -> None:
        self.harness.mkdir(parents=True)
        for src in (VERIFIER, REGISTRY_LOOKUP):
            shutil.copy2(src, self.harness / src.name)
        # The one substitution. harness/ itself is never written to: the shipped
        # tree is the reference side of the port's differential.
        shutil.copy2(TIER3 / "runner-security-lib.stub.sh", self.harness / "runner-security-lib.sh")
        self.bin.mkdir(parents=True)
        shutil.copy2(TIER3 / "fake-docker.sh", self.bin / "docker")
        (self.bin / "docker").chmod(0o755)
        self.verifier = self._verifier_argv()

    @staticmethod
    def _verifier_argv() -> list[str]:
        """The command the cases drive. The rig is otherwise identical either way."""
        if IMPLEMENTATION == "shell":
            return []
        if IMPLEMENTATION != "python":
            raise ValueError(
                f"S3STUDY_VERIFIER must be 'shell' or 'python', not {IMPLEMENTATION!r}"
            )
        # --registry is the explicit redirection the port takes in place of the
        # SMOKE_REGISTRY environment hook. The preflight seam is the entry point
        # itself, not an argument: see tests/tier3/verifier_entry.py.
        return [
            sys.executable,
            "-m",
            "tests.tier3.verifier_entry",
            "--registry",
            str(REGISTRY_FIXTURE),
        ]

    def _stage_manifest(self, rows: Sequence[str]) -> Path:
        path = self.root / "manifest" / "tier3.tsv.gz"
        path.parent.mkdir(parents=True)
        with gzip.open(path, "wt") as fh:
            fh.write("".join(f"{row}\n" for row in rows))
        return path

    @property
    def staged_sha256(self) -> str:
        """Digest of the verifier that actually runs — asserted against the shipped one."""
        return sha256_file(self.harness / "verify-listing.sh")

    def receipt(
        self,
        name: str,
        source: Path,
        rows: Sequence[str],
        prefix: str | None = None,
    ) -> Path:
        directory = self.root / "receipts" / name
        directory.mkdir(parents=True)
        payload = directory / "stdout.txt"
        text = "".join(f"{row}\n" for row in rows)
        payload.write_text(text)
        overrides = {
            "manifest": str(self.manifest),
            "manifest_sha256": self.manifest_sha256,
            "stdout_path": str(payload),
            "stdout_sha256": sha256_text(text),
            "stdout_truncated": "no",
        }
        if prefix is not None:
            overrides["prefix"] = prefix
        _write_meta(directory / "run.meta", source, overrides)
        (directory / "receipt.md").write_text(RECEIPT_MD)
        return directory

    def reference(self, rows: Sequence[str], name: str = "reference") -> Path:
        path = self.root / "reference" / f"{name}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"{row}\n" for row in rows))
        return path

    # ------------------------------------------------------------- invocation
    def _run(self, args: Sequence[str], reference: Path | None, rc: int, message: str) -> Outcome:
        env = dict(os.environ)
        env["SMOKE_REGISTRY"] = str(REGISTRY_FIXTURE)
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        # REPO as well as REPO/src: `-m tests.tier3.verifier_entry` is deliberately
        # importable only from a checkout.
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO / "src"), str(REPO), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        env[ENV_RC] = str(rc)
        env[ENV_STDERR] = message
        if reference is not None:
            env[ENV_REFERENCE] = str(reference)
        proc = subprocess.run(
            [*(self.verifier or [str(self.harness / "verify-listing.sh")]), *args],
            cwd=self.root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            check=False,
        )
        return Outcome(returncode=proc.returncode, report="", log=proc.stdout)

    def verify(
        self,
        receipt: Path,
        prefix: str,
        reference: Path | None = None,
        docker_rc: int = 0,
        docker_stderr: str = "fake docker: reference re-list refused",
    ) -> Outcome:
        args = [
            "--receipt",
            str(receipt),
            "--input",
            str(receipt / "stdout.txt"),
            "--normalize",
            str(NORMALIZE),
            "--scope",
            "prefix",
            "--scope-prefix",
            prefix,
        ]
        outcome = self._run(args, reference, docker_rc, docker_stderr)
        report = receipt / "verify.md"
        return Outcome(
            returncode=outcome.returncode,
            report=report.read_text() if report.is_file() else "",
            log=outcome.log,
            receipt=(receipt / "receipt.md").read_text(),
        )

    def verify_union(
        self,
        receipts: Sequence[Path],
        remainder: Path | None = None,
        reference: Path | None = None,
        docker_rc: int = 0,
        docker_stderr: str = "fake docker: reference re-list refused",
        out: str = "union",
        normalize: Path = NORMALIZE,
    ) -> Outcome:
        out_dir = self.root / "out" / out
        args = ["--scope", "union", "--normalize", str(normalize), "--out", str(out_dir)]
        for receipt in receipts:
            args += ["--receipt", str(receipt)]
        if remainder is not None:
            args += ["--remainder", str(remainder)]
        outcome = self._run(args, reference, docker_rc, docker_stderr)
        report = out_dir / "union-verify.md"
        return Outcome(
            returncode=outcome.returncode,
            report=report.read_text() if report.is_file() else "",
            log=outcome.log,
        )
