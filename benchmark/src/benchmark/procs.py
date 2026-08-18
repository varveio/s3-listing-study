"""What the worker asks the kernel about processes and about its own cgroup.

Reads and signals only: nothing here decides anything about a measurement, and
nothing here knows what an attempt is. It is separate because supervising a
subject and *observing* one are different jobs — these functions are what
`measure.run_tool` uses to answer "is the process tree actually empty" and "what
did this exec peak at", and they are testable without running a subject at all.

Linux-only, by construction: procfs, cgroup v2, `prctl` and `clear_refs` have no
portable equivalents and the harness runs in one container image.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import signal
import time
from pathlib import Path


def enable_child_subreaper() -> None:
    """Make daemonizing grandchildren remain observable by this worker."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        error = ctypes.get_errno()
        raise OSError(error, f"could not enable child subreaper: {os.strerror(error)}")


def process_table() -> dict[int, tuple[int, int, str]]:
    """Return pid -> (parent pid, process group, state) from Linux procfs."""
    table: dict[int, tuple[int, int, str]] = {}
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            pid = int(stat_path.parent.name)
            fields = stat_path.read_text().rsplit(") ", 1)[1].split()
            table[pid] = (int(fields[1]), int(fields[2]), fields[0])
        except (OSError, IndexError, ValueError):
            continue
    return table


def descendant_pids(
    root_pid: int, table: dict[int, tuple[int, int, str]] | None = None
) -> set[int]:
    table = table or process_table()
    found: set[int] = set()
    frontier = {root_pid}
    while frontier:
        children = {
            pid
            for pid, (parent, _group, state) in table.items()
            if parent in frontier and state != "Z" and pid not in found
        }
        found.update(children)
        frontier = children
    return found


def subject_processes(root_pid: int, tracked: set[int], baseline_descendants: set[int]) -> set[int]:
    """Find the subject family, including children that escaped with setsid()."""
    table = process_table()
    family = {root_pid, *tracked}
    # A subreaper adopts daemonized descendants. This worker is dedicated to
    # one synchronous subject, so any newly adopted child belongs to it; the
    # baseline prevents touching a child that predated this invocation.
    family.update(
        pid
        for pid, (parent, _group, state) in table.items()
        if parent == os.getpid() and pid not in baseline_descendants and state != "Z"
    )
    frontier = set(family)
    while frontier:
        children = {
            pid
            for pid, (parent, _group, state) in table.items()
            if parent in frontier and state != "Z" and pid not in family
        }
        family.update(children)
        frontier = children
    return family


def live_pids(pids: set[int]) -> set[int]:
    table = process_table()
    return {pid for pid in pids if pid in table and table[pid][2] != "Z"}


def signal_pids(pids: set[int], sig: signal.Signals) -> None:
    for pid in sorted(pids):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, sig)


def wait_for_pids_to_exit(pids: set[int], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while live_pids(pids) and time.monotonic() < deadline:
        time.sleep(0.01)


def reap_children(pids: set[int]) -> None:
    for pid in sorted(pids):
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)


def process_group_exists(process_group: int) -> bool:
    """Whether a live (non-zombie) process remains in the subject group."""
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat_path.read_text().rsplit(") ", 1)[1].split()
            state, group = fields[0], int(fields[2])
        except (OSError, IndexError, ValueError):
            continue
        if group == process_group and state != "Z":
            return True
    return False


def cgroup_v2_directory() -> Path | None:
    override = os.environ.get("BENCHMARK_CGROUP_DIR")
    if override:
        return Path(override)
    try:
        relative = Path(
            Path("/proc/self/cgroup").read_text().split("0::", 1)[1].splitlines()[0].lstrip("/")
        )
        return Path("/sys/fs/cgroup") / relative
    except (OSError, IndexError):
        return None


def reset_memory_peak(directory: Path | None) -> bool:
    """Try to clear the container's memory high-water mark, and say whether it took.

    ``memory.peak`` is per container and accepts a reset write only on Linux
    6.12 and later, so an attempt that ran an untimed setup exec first may be
    stuck publishing the larger of the two phases. Recorded either way, because a
    reader cannot otherwise tell which of the two the number describes.
    """
    if directory is None:
        return False
    try:
        (directory / "memory.peak").write_text("reset")
    except OSError:
        return False
    return True


def self_peak_rss_kb() -> int | None:
    """This worker's own resident high-water mark, or None where procfs has none."""
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def reset_self_peak_rss() -> bool:
    """Drop this worker's own high-water mark to its live footprint, and say whether it took.

    A fork hands the child ``mm->hiwater_rss``, not the parent's current
    residency, so ``ru_maxrss`` carries a floor equal to the fattest this worker
    has ever been -- measured here: a parent that touched 300 MB and freed it
    makes ``python -c pass`` report 318 MB. Writing 5 to ``clear_refs`` resets
    the mark to the current RSS (``Documentation/filesystems/proc.rst``), which
    is the smallest floor a fork can carry and leaves a genuinely fat subject
    reporting its own peak unchanged.
    """
    try:
        Path("/proc/self/clear_refs").write_text("5\n")
    except OSError:
        return False
    return True


def cgroup_snapshot(directory: Path | None) -> dict[str, object]:
    if directory is None:
        return {"memory_current_bytes": None, "memory_peak_bytes": None, "memory_events": None}
    try:
        events = {
            name: int(value)
            for name, value in (
                line.split() for line in (directory / "memory.events").read_text().splitlines()
            )
        }
        return {
            "memory_current_bytes": int((directory / "memory.current").read_text()),
            "memory_peak_bytes": int((directory / "memory.peak").read_text()),
            "memory_events": events,
        }
    except (OSError, ValueError):
        return {"memory_current_bytes": None, "memory_peak_bytes": None, "memory_events": None}


def _event_delta(before: object, after: object, name: str) -> int | None:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    return int(after.get(name, 0)) - int(before.get(name, 0))
