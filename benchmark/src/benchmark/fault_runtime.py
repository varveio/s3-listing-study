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
from pathlib import Path

from benchmark import gcs
from benchmark.contract import sha256_of
from benchmark.faults import (
    SIDECAR_READINESS_TIMEOUT_S,
    SIDECAR_STOP_GRACE_S,
    SIDECAR_TIMELINE_NAME,
    Sidecar,
    stdout_totals,
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

        A sidecar that died while the subject ran stood nothing between the
        subject and the server for some part of the run, which is a different
        treatment from the one the case declared; one whose timeline never
        landed has no evidence of what it did.
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
        try:
            process.wait(timeout=SIDECAR_STOP_GRACE_S)
            forced = False
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            forced = True
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        self.block["stop"] = {
            "signal": signal_sent,
            "exit_code": process.returncode,
            "forced_kill": forced,
            "wait_ms": round((time.monotonic() - started) * 1000),
        }
        stdout = self.directory / "stdout.log"
        with contextlib.suppress(OSError, UnicodeDecodeError):
            self.block["stdout_totals"] = stdout_totals(stdout.read_text())
        if self.timeline.is_file():
            text = self.timeline.read_text(errors="replace")
            self.block["timeline"] = {
                "name": f"{SIDECAR_DIR}/{SIDECAR_TIMELINE_NAME}",
                "size_bytes": self.timeline.stat().st_size,
                "sha256": sha256_of(self.timeline),
                **timeline_summary(text),
            }
        if self.block.get("state") == "failed":
            return str(self.block.get("error"))
        if not alive:
            self.fail(f"sidecar exited {process.returncode} while the subject ran")
        elif forced:
            self.fail("sidecar did not exit within its stop grace after SIGTERM")
        elif self.block["timeline"] is None:
            self.fail("sidecar exited without writing its timeline")
        else:
            self.block["state"] = "stopped"
            return None
        return str(self.block["error"])


def copy_resume_evidence(sink: Path, names: Sequence[str], into: Path) -> dict[str, str | None]:
    """Copy what the killed step left at each declared path; record what was absent.

    Only regular files are copied. A directory, link, or special file at a
    declared path is recorded as absent rather than followed, for the reason
    the publisher refuses them: nothing under ``sink`` is trusted to be a file.
    """
    recorded: dict[str, str | None] = {}
    for name in names:
        source = sink / name
        if source.is_symlink() or not source.is_file():
            recorded[name] = None
            continue
        target = into / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        recorded[name] = sha256_of(target)
    return recorded
