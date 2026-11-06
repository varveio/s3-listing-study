"""Worker-side lifecycle of a sidecar and the evidence a killed step leaves.

The contract (what a plan states, what a worker is handed) is
:mod:`benchmark.faults`; this module only runs things and records what they did.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import socket
import stat
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from benchmark import gcs
from benchmark.contract import sha256_of
from benchmark.faults import (
    SIDECAR_KILL_WAIT_S,
    SIDECAR_READINESS_TIMEOUT_S,
    SIDECAR_STOP_GRACE_S,
    SIDECAR_TIMELINE_NAME,
    Sidecar,
    stdout_totals,
    timeline_errors,
    timeline_summary,
)

SIDECAR_DIR = "sidecar"
"""The attempt subdirectory a sidecar's timeline and logs are published under."""


class SidecarFailed(RuntimeError):
    """The sidecar could not be staged, would not start, or did not answer."""


def stage_executable(sidecar: Sidecar, into: Path) -> Path:
    """Download the declared executable and refuse bytes that moved.

    Staged outside the attempt directory, like an inbound artifact: the
    executable is an input the case hashed by digest, not this attempt's
    evidence.
    """
    into.mkdir(parents=True, exist_ok=True)
    target = into / sidecar.executable_uri.rsplit("/", 1)[-1]
    target.write_bytes(gcs.download_bytes(sidecar.executable_uri))
    staged = sha256_of(target)
    if staged != sidecar.executable_sha256:
        raise SidecarFailed(
            f"sidecar {sidecar.executable_uri} digests {staged}, not the "
            f"{sidecar.executable_sha256} this case declared"
        )
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    return target


def _listening(listen: str) -> bool:
    host, _, port = listen.rpartition(":")
    with contextlib.suppress(OSError), socket.create_connection((host, int(port)), timeout=0.5):
        return True
    return False


@dataclass
class SidecarRun:
    """One sidecar process and everything recorded about it, as it happens.

    ``block`` is filled in phase by phase so that a refusal at any point still
    says how far the sidecar got.
    """

    sidecar: Sidecar
    directory: Path
    process: subprocess.Popen[bytes] | None = None
    block: dict[str, object] = field(default_factory=dict)

    @property
    def timeline(self) -> Path:
        return self.directory / SIDECAR_TIMELINE_NAME

    def start(self, executable: Path, env: Mapping[str, str]) -> None:
        """Start the sidecar and wait until its listen address accepts a connection."""
        self.directory.mkdir(parents=True, exist_ok=True)
        argv = self.sidecar.argv(str(executable), str(self.timeline))
        self.block.update(
            {
                "state": "starting",
                "name": self.sidecar.name,
                "argv": list(argv),
                "executable_sha256": sha256_of(executable),
                "endpoint_url": self.sidecar.endpoint_url,
                "started_at": datetime.now(UTC).isoformat(),
                "readiness_ms": None,
                "alive_through_subject": None,
                "stop": None,
                "timeline": None,
                "stdout_totals": None,
                "error": None,
            }
        )
        with (
            open(self.directory / "stdout.log", "wb") as stdout,
            open(self.directory / "stderr.log", "wb") as stderr,
        ):
            self.process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                env=dict(env),
                start_new_session=True,
            )
        started = time.monotonic()
        while time.monotonic() - started < SIDECAR_READINESS_TIMEOUT_S:
            if self.process.poll() is not None:
                self.fail(f"sidecar exited {self.process.returncode} before it listened")
                raise SidecarFailed(str(self.block["error"]))
            if _listening(self.sidecar.listen):
                self.block["readiness_ms"] = round((time.monotonic() - started) * 1000)
                self.block["state"] = "running"
                return
            time.sleep(0.05)
        self.fail(f"sidecar did not listen on {self.sidecar.listen} within the readiness deadline")
        raise SidecarFailed(str(self.block["error"]))

    def fail(self, message: str) -> None:
        self.block["state"] = "failed"
        self.block["error"] = message

    def stop(self) -> str | None:
        """SIGTERM the sidecar, wait for its timeline, and say why it is unusable, if it is.

        The one acceptable shutdown is: alive through the subject, SIGTERMed by
        the worker, exited ``0`` within the stop grace, and a usable timeline on
        disk (`faults.timeline_errors`). Anything else is refused, with what
        the sidecar did recorded all the same: one that died while the subject
        ran stood nothing between the subject and the server for some part of
        the run, which is a different treatment from the one the case declared;
        one that failed during its own shutdown may have written a timeline it
        never finished; one whose timeline is empty or malformed has no evidence
        of what it did. Every wait is bounded, the last one after SIGKILL too.
        """
        process = self.process
        if process is None:
            return None
        alive = process.poll() is None
        if self.block.get("state") == "running":
            self.block["alive_through_subject"] = alive
        started = time.monotonic()
        signal_sent = None
        if alive:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
                signal_sent = "SIGTERM"
        forced = False
        reaped = True
        try:
            process.wait(timeout=SIDECAR_STOP_GRACE_S)
        except subprocess.TimeoutExpired:
            forced = True
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            try:
                process.wait(timeout=SIDECAR_KILL_WAIT_S)
            except subprocess.TimeoutExpired:
                reaped = False
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        self.block["stop"] = {
            "signal": signal_sent,
            "exit_code": process.returncode,
            "forced_kill": forced,
            "reaped": reaped,
            "wait_ms": round((time.monotonic() - started) * 1000),
        }
        stdout = self.directory / "stdout.log"
        with contextlib.suppress(OSError, UnicodeDecodeError):
            self.block["stdout_totals"] = stdout_totals(stdout.read_text())
        timeline_error: str | None = "sidecar exited without writing its timeline"
        if self.timeline.is_file() and not self.timeline.is_symlink():
            text = self.timeline.read_text(errors="replace")
            timeline_error_detail = timeline_errors(text)
            self.block["timeline"] = {
                "name": f"{SIDECAR_DIR}/{SIDECAR_TIMELINE_NAME}",
                "size_bytes": self.timeline.stat().st_size,
                "sha256": sha256_of(self.timeline),
                "usable": timeline_error_detail is None,
                **timeline_summary(text),
            }
            timeline_error = (
                None
                if timeline_error_detail is None
                else f"sidecar timeline is unusable: {timeline_error_detail}"
            )
        if self.block.get("state") == "failed":
            return str(self.block.get("error"))
        if not alive:
            self.fail(f"sidecar exited {process.returncode} while the subject ran")
        elif not reaped:
            self.fail("sidecar did not die within its wait after SIGKILL")
        elif forced:
            self.fail("sidecar did not exit within its stop grace after SIGTERM")
        elif process.returncode != 0:
            self.fail(f"sidecar exited {process.returncode} on SIGTERM, not 0")
        elif timeline_error is not None:
            self.fail(timeline_error)
        else:
            self.block["state"] = "stopped"
            return None
        return str(self.block["error"])


class UnsafeEvidence(ValueError):
    """A declared evidence path resolves through something that is not a plain directory."""


def _regular_file_beneath(sink: Path, name: str) -> Path | None:
    """The declared file under ``sink``, ``None`` when absent, or :class:`UnsafeEvidence`.

    Every component is checked with ``lstat``, not only the last: a symlinked
    parent directory would otherwise let ``checkpoint/report.json`` read bytes
    from anywhere the worker can. Absent is a real outcome (a step killed
    before its first checkpoint); anything other than real directories down to
    one regular file is not, and is refused rather than recorded as absent.
    """
    parts = PurePosixPath(name).parts
    current = sink
    for index, part in enumerate(parts):
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return None
        last = index == len(parts) - 1
        if stat.S_ISLNK(mode):
            raise UnsafeEvidence(f"{name}: {'/'.join(parts[: index + 1])} is a symlink")
        if not last and not stat.S_ISDIR(mode):
            raise UnsafeEvidence(f"{name}: {'/'.join(parts[: index + 1])} is not a directory")
        if last and not stat.S_ISREG(mode):
            raise UnsafeEvidence(f"{name}: not a regular file")
    return current


def _copy_regular(source: Path, target: Path, name: str) -> None:
    """Copy one leaf without following a link, re-checked on the open descriptor."""
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    with open(descriptor, "rb") as reader:
        if not stat.S_ISREG(os.fstat(reader.fileno()).st_mode):
            raise UnsafeEvidence(f"{name}: not a regular file when opened")
        with open(target, "xb") as writer:
            shutil.copyfileobj(reader, writer)


def copy_resume_evidence(
    sink: Path, names: Sequence[str], into: Path
) -> tuple[dict[str, str | None], str | None]:
    """Copy what the killed step left at each declared path, and say why not if it cannot.

    Returns the record — each name's copied sha256, or ``None`` when the killed
    step never wrote it — and, separately, the reason the copy stopped: a path
    that is not real directories down to one regular file (`_regular_file_beneath`)
    or an I/O error. Nothing under ``sink`` is trusted to be a file, so the leaf
    is opened without following a link and re-checked on the open descriptor.
    """
    recorded: dict[str, str | None] = {}
    for name in names:
        try:
            source = _regular_file_beneath(sink, name)
            if source is None:
                recorded[name] = None
                continue
            target = into / name
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_regular(source, target, name)
            recorded[name] = sha256_of(target)
        except UnsafeEvidence as exc:
            return recorded, f"unsafe: {exc}"
        except OSError as exc:
            return recorded, f"{name}: {type(exc).__name__}: {exc.strerror or exc}"
    return recorded, None
