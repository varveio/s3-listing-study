"""Retain command, version, wall and child peak RSS for the bounded wire pilot."""

import json
import os
import platform
import resource
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

name, *command = sys.argv[1:]
metadata = json.loads(Path("/opt/benchmark/image-metadata.json").read_text())
started = datetime.now(UTC).isoformat()
clock = time.monotonic()
child = subprocess.Popen(command, start_new_session=True)
timed_out = False
try:
    exit_code = child.wait(timeout=60)
except subprocess.TimeoutExpired:
    timed_out = True
    os.killpg(child.pid, signal.SIGKILL)
    child.wait()
    exit_code = 124
record = {
    "tool": name,
    "version": metadata["tools"][name]["tool_version"],
    "image": os.environ["PILOT_IMAGE"],
    "argv": command,
    "bucket": os.environ["PILOT_BUCKET"],
    "prefix": os.environ["PILOT_PREFIX"],
    "fixture_sha256": os.environ["PILOT_FIXTURE_SHA256"],
    "machine": {
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "allowed_cpus": sorted(os.sched_getaffinity(0)),
    },
    "started_at": started,
    "finished_at": datetime.now(UTC).isoformat(),
    "exit_code": exit_code,
    "timed_out": timed_out,
    "wall_seconds": time.monotonic() - clock,
    "peak_rss_kb": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
    "row_count": None,
    "row_count_note": "count native output after the probe",
}
Path("/out", name + ".runtime.json").write_text(json.dumps(record, indent=2) + "\n")
sys.exit(exit_code)
