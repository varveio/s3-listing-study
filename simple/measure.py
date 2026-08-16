"""Run one capsule-compiled subject and publish its bounded attempt evidence.

The worker validates campaign claims against immutable in-image metadata before
exec, supervises the complete process group through TERM/KILL, retains stream
and native-directory outputs, counts through the capsule, and uploads the
result marker last. Create-only evidence sealing remains deliberately outside
this candidate; see README.md.

Usage:
    measure.py --tool s5cmd --mode recursive --bucket some-bucket --region us-east-1 \\
        --output /tmp/attempt --destination gs://results-bucket/job-id/
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import gzip
import hashlib
import json
import os
import platform
import re
import resource
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import gcs
from contract import TOOLBOX_TOOLS, sha256_of

import adapters

EXIT_ADAPTER_ERROR = 3
EXIT_SECRET_DETECTED = 9
EXIT_IMAGE_MISMATCH = 10
EXIT_POSTPROCESSING_FAILED = 11
PINNED_IMAGE_RE = re.compile(r"\A[^\s@]+@sha256:[0-9a-f]{64}\Z")
CASE_ENV_KEYS = frozenset({"JAVA_TOOL_OPTIONS", "NODE_OPTIONS"})
AWS_CREDENTIAL_ENV_KEYS = frozenset(
    {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}
)
AWS_CREDENTIAL_REQUIRED_ENV_KEYS = frozenset({"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"})

SECRET_PATTERNS = {
    "AWS access key id": re.compile(rb"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "aws_secret_access_key assignment": re.compile(
        rb"aws_secret_access_key\s*=\s*\S+", re.IGNORECASE
    ),
    "GCP private key": re.compile(rb"BEGIN PRIVATE KEY"),
}
SECRET_SCAN_CHUNK = 1024 * 1024
SECRET_SCAN_OVERLAP = (
    64  # wider than any pattern above, so a match can't split across a chunk boundary
)

SUBJECT_ENV = {
    # Mirrors worker/engine.py's BASE_SUBJECT_ENV: a small, stable
    # environment rather than whatever ambient variables happen to be set
    # in the runner. The real engine also strips secret-carrying variables;
    # this sketch just fixes the basics and disables the EC2 metadata probe
    # every AWS SDK tries first. A capsule's own FUNCTIONAL_ENV (see
    # adapters.compile_command) is merged in on top of this.
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/home/s3study",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "AWS_EC2_METADATA_DISABLED": "true",
}


def parse_case_env(pairs: list[str]) -> dict[str, str]:
    env = {}
    for pair in pairs:
        name, _, value = pair.partition("=")
        if not name or not _ or "\x00" in pair:
            raise ValueError(f"--case-env must be NAME=VALUE without NUL bytes: {pair!r}")
        if name not in CASE_ENV_KEYS:
            raise ValueError(f"--case-env names unsupported key: {name}")
        if not value:
            raise ValueError(f"--case-env {name} value must not be empty")
        if name in env:
            raise ValueError(f"--case-env repeats {name}")
        env[name] = value
    return env


def validate_environment_inputs(
    auth: str,
    pass_env: list[str],
    functional_env: dict[str, str],
    case_env: dict[str, str],
) -> str | None:
    """Refuse environment names that could widen or shadow the auth boundary."""
    passed = set(pass_env)
    if len(passed) != len(pass_env):
        return "--pass-env repeats a variable"
    if auth == "anonymous" and passed:
        return "anonymous cases must not carry credential variables"
    if auth == "authenticated":
        unknown = sorted(passed - AWS_CREDENTIAL_ENV_KEYS)
        missing = sorted(AWS_CREDENTIAL_REQUIRED_ENV_KEYS - passed)
        if unknown:
            return f"authenticated case has unsupported credential key(s): {', '.join(unknown)}"
        if missing:
            return f"authenticated case is missing credential key(s): {', '.join(missing)}"
    reserved = set(SUBJECT_ENV) | AWS_CREDENTIAL_ENV_KEYS | {"AWS_REGION", "AWS_DEFAULT_REGION"}
    collisions = sorted(set(functional_env) & reserved)
    if collisions:
        return f"capsule environment collides with reserved key(s): {', '.join(collisions)}"
    overlap = sorted((set(functional_env) & set(case_env)) | (set(case_env) & passed))
    if overlap:
        return f"environment sources collide on key(s): {', '.join(overlap)}"
    return None


def parse_optional_gb(value: str) -> int | None:
    if value == "none":
        return None
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("memory must be a positive GiB value or 'none'")
    return parsed


def scan_for_secrets(paths: list[Path]) -> str | None:
    """The name of the first stream+pattern a possible secret matched, or None.

    A leak gate, not redaction: on a hit the caller uploads nothing and never
    prints the matched text itself, only which stream and which pattern.
    Streamed in overlapping chunks rather than one read_bytes() call, so a
    large capture never sits in memory whole. Bounded gap, not a full scan:
    only stdout/stderr are checked here, never a dataset-sink tool's native
    output -- the real engine's secret_scan.py scans everything an attempt
    could publish.
    """
    for path in paths:
        if not path.exists():
            continue
        with open(path, "rb") as f:
            carry = b""
            while chunk := f.read(SECRET_SCAN_CHUNK):
                window = carry + chunk
                for name, pattern in SECRET_PATTERNS.items():
                    if pattern.search(window):
                        return f"{path.name}: {name}"
                carry = window[-SECRET_SCAN_OVERLAP:]
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile, run, and upload one case's attempt.")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--prefix", default="")
    parser.add_argument("--auth", default="anonymous", choices=("anonymous", "authenticated"))
    parser.add_argument(
        "--adapter-root",
        default=adapters.DEFAULT_ADAPTER_ROOT,
        help="Root containing one bundled <tool>/adapter directory per registered tool.",
    )
    parser.add_argument("--output", required=True, help="Local attempt directory to write into.")
    parser.add_argument(
        "--destination",
        required=True,
        help="GCS destination prefix; this invocation appends its own uuid4 leaf.",
    )
    parser.add_argument(
        "--timeout", type=int, default=3600, help="Seconds before the subject is killed."
    )
    parser.add_argument("--term-grace", type=float, default=5.0)
    parser.add_argument(
        "--case-env",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Extra environment for the subject, e.g. JAVA_TOOL_OPTIONS for swath. Repeatable.",
    )
    parser.add_argument(
        "--pass-env",
        action="append",
        default=[],
        metavar="NAME",
        help="Copy NAME from this process's own environment into the subject's env "
        "(e.g. a Batch secretVariable). Repeatable. Never recorded in result.json.",
    )
    parser.add_argument("--image", required=True)
    parser.add_argument("--shared-base-uri", required=True)
    parser.add_argument("--shared-base-digest", required=True)
    parser.add_argument("--shared-base-source-sha256", required=True)
    parser.add_argument("--toolbox-manifest-sha256", required=True)
    parser.add_argument("--tool-parent-image", required=True)
    parser.add_argument("--tool-version", required=True)
    parser.add_argument("--tool-build-sha256", required=True)
    parser.add_argument("--adapter-bundle-sha256", required=True)
    parser.add_argument("--harness-revision", required=True)
    parser.add_argument("--subject-workdir", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--case-fingerprint", required=True)
    parser.add_argument("--image-set-sha256", required=True)
    parser.add_argument("--run-ordinal", required=True, type=int)
    parser.add_argument("--submission-number", required=True, type=int)
    parser.add_argument("--machine-type", required=True)
    parser.add_argument("--vcpus", required=True, type=int)
    parser.add_argument("--memory-gb", required=True, type=int)
    parser.add_argument("--container-memory-gb", required=True, type=parse_optional_gb)
    parser.add_argument("--image-metadata", default="/opt/simple/image-metadata.json")
    return parser.parse_args(argv)


def validate_image_metadata(args: argparse.Namespace) -> str | None:
    """Return a refusal message when CLI provenance differs from the image."""
    try:
        metadata = json.loads(Path(args.image_metadata).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return f"image metadata is unreadable: {exc}"
    metadata_fields = {
        "schema_version",
        "shared_base_uri",
        "shared_base_digest",
        "shared_base_source_sha256",
        "tools",
        "toolbox_manifest_sha256",
        "harness_revision",
    }
    if not isinstance(metadata, dict) or set(metadata) != metadata_fields:
        return "image metadata schema is not supported"
    tools = metadata.get("tools")
    tool_fields = {
        "tool_parent_image",
        "tool_version",
        "tool_build_sha256",
        "adapter_bundle_sha256",
        "subject_workdir",
        "executable",
    }
    if (
        metadata.get("schema_version") != 2
        or not isinstance(tools, dict)
        or set(tools) != TOOLBOX_TOOLS
        or any(not isinstance(value, dict) or set(value) != tool_fields for value in tools.values())
    ):
        return "image metadata schema is not supported"
    toolbox_projection = {
        "schema_version": 1,
        "shared_base_uri": metadata["shared_base_uri"],
        "shared_base_digest": metadata["shared_base_digest"],
        "shared_base_source_sha256": metadata["shared_base_source_sha256"],
        "tools": {
            tool: {
                name: value
                for name, value in selected_tool.items()
                if name != "adapter_bundle_sha256"
            }
            for tool, selected_tool in tools.items()
        },
    }
    canonical = json.dumps(toolbox_projection, sort_keys=True, separators=(",", ":")).encode()
    computed_toolbox_sha256 = hashlib.sha256(canonical).hexdigest()
    if metadata.get("toolbox_manifest_sha256") != computed_toolbox_sha256:
        return "immutable image metadata has an invalid toolbox manifest hash"
    selected = tools.get(args.tool) if isinstance(tools, dict) else None
    if not isinstance(selected, dict):
        return "selected tool is not present in immutable image metadata"
    expected_selected = {
        "tool_version": args.tool_version,
        "tool_build_sha256": args.tool_build_sha256,
        "adapter_bundle_sha256": args.adapter_bundle_sha256,
        "tool_parent_image": args.tool_parent_image,
        "subject_workdir": args.subject_workdir,
    }
    if any(selected.get(name) != value for name, value in expected_selected.items()):
        return "campaign provenance does not match immutable image metadata"
    if metadata.get("harness_revision") != args.harness_revision:
        return "campaign provenance does not match immutable image metadata"
    if metadata.get("toolbox_manifest_sha256") != args.toolbox_manifest_sha256:
        return "campaign provenance does not match immutable image metadata"
    expected_base = {
        "shared_base_uri": args.shared_base_uri,
        "shared_base_digest": args.shared_base_digest,
        "shared_base_source_sha256": args.shared_base_source_sha256,
    }
    if any(metadata.get(name) != value for name, value in expected_base.items()):
        return "campaign provenance does not match immutable image metadata"
    if (
        PINNED_IMAGE_RE.fullmatch(args.shared_base_uri) is None
        or not args.shared_base_uri.endswith(f"@{args.shared_base_digest}")
        or re.fullmatch(r"[0-9a-f]{64}", args.shared_base_source_sha256) is None
    ):
        return "shared base provenance is malformed"
    if PINNED_IMAGE_RE.fullmatch(args.image) is None:
        return "executing image URI is not digest-pinned"
    workdir = Path(args.subject_workdir)
    if not workdir.is_absolute() or workdir.as_posix() != args.subject_workdir:
        return "registered subject working directory is not canonical"
    if not workdir.is_dir():
        return "registered subject working directory is unavailable"
    return None


def preflight(argv: tuple[str, ...]) -> bool:
    """Check the subject binary exists before we bother creating an attempt dir.

    The real engine resolves this implicitly by attempting exec() and
    reading the OS error; a preflight check just gets to the same verdict
    with a clearer message.
    """
    binary = shutil.which(argv[0])
    if binary is None:
        print(f"measure: {argv[0]!r} not found on PATH", file=sys.stderr)
        return False
    return True


def run_tool(
    argv: tuple[str, ...],
    attempt_dir: Path,
    timeout: int,
    term_grace: float,
    env: dict[str, str],
    *,
    cwd: str | None = None,
) -> dict[str, object]:
    """Run argv, capture stdout/stderr to files, return
    (exit_code, wall_s, max_rss_kb, timed_out).
    """
    stdout_path = attempt_dir / "stdout.log"
    stderr_path = attempt_dir / "stderr.log"

    enable_child_subreaper()
    baseline_descendants = descendant_pids(os.getpid())
    cgroup = cgroup_v2_directory()
    cgroup_before = cgroup_snapshot(cgroup)
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start_ns = time.monotonic_ns()
    timed_out = False
    term_sent = False
    kill_sent = False
    process_tree_clean = True
    tracked_pids: set[int] = set()
    with open(stdout_path, "wb") as stdout_f, open(stderr_path, "wb") as stderr_f:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=stdout_f,
            stderr=stderr_f,
            env=env,
            cwd=cwd,
            start_new_session=True,
        )
        tracked_pids.add(proc.pid)
        deadline = time.monotonic() + timeout
        while True:
            proc.poll()
            tracked_pids.update(subject_processes(proc.pid, tracked_pids, baseline_descendants))
            if proc.returncode is not None:
                exit_code = proc.returncode
                residual = live_pids(tracked_pids - {proc.pid})
                if residual or process_group_exists(proc.pid):
                    process_tree_clean = False
                break
            if time.monotonic() >= deadline:
                exit_code = 124
                timed_out = True
                break
            time.sleep(0.01)

        residual = live_pids(tracked_pids - {proc.pid})
        if timed_out or residual or process_group_exists(proc.pid):
            if timed_out:
                exit_code = 124  # conventional timeout exit code
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                term_sent = True
            except ProcessLookupError:
                pass
            signal_pids(residual, signal.SIGTERM)
            term_sent = term_sent or bool(residual)
            grace_deadline = time.monotonic() + term_grace
            while time.monotonic() < grace_deadline:
                proc.poll()
                tracked_pids.update(subject_processes(proc.pid, tracked_pids, baseline_descendants))
                residual = live_pids(tracked_pids - {proc.pid})
                if not process_group_exists(proc.pid) and not residual:
                    break
                time.sleep(0.01)
            residual = live_pids(tracked_pids - {proc.pid})
            if process_group_exists(proc.pid) or residual:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                    kill_sent = True
                except ProcessLookupError:
                    pass
                signal_pids(residual, signal.SIGKILL)
                kill_sent = kill_sent or bool(residual)
            try:
                proc.wait(timeout=max(term_grace, 1.0))
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        else:
            proc.wait()
    elapsed_ns = time.monotonic_ns() - start_ns
    group_empty = not process_group_exists(proc.pid)
    if not group_empty:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            kill_sent = True
        except ProcessLookupError:
            pass
        group_empty = not process_group_exists(proc.pid)
    cleanup_deadline = time.monotonic() + max(term_grace, 1.0)
    while True:
        tracked_pids.update(subject_processes(proc.pid, tracked_pids, baseline_descendants))
        descendants = live_pids(tracked_pids - {proc.pid})
        if not descendants or time.monotonic() >= cleanup_deadline:
            break
        signal_pids(descendants, signal.SIGKILL)
        kill_sent = True
        wait_for_pids_to_exit(descendants, min(0.1, max(0.0, cleanup_deadline - time.monotonic())))
    tracked_pids.update(subject_processes(proc.pid, tracked_pids, baseline_descendants))
    descendants_empty = not live_pids(tracked_pids - {proc.pid})
    group_empty = not process_group_exists(proc.pid)
    reap_children(tracked_pids - {proc.pid})

    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cgroup_after = cgroup_snapshot(cgroup)
    before_events = cgroup_before.get("memory_events")
    after_events = cgroup_after.get("memory_events")
    return {
        "exit_code": exit_code,
        "elapsed_ns": elapsed_ns,
        "wall_seconds": round(elapsed_ns / 1_000_000_000, 6),
        "max_rss_kb": usage_after.ru_maxrss,
        "user_cpu_seconds": usage_after.ru_utime - usage_before.ru_utime,
        "system_cpu_seconds": usage_after.ru_stime - usage_before.ru_stime,
        "timed_out": timed_out,
        "term_sent": term_sent,
        "kill_sent": kill_sent,
        "process_group_empty": group_empty,
        "descendants_empty": descendants_empty,
        "process_tree_clean": process_tree_clean,
        "subreaper_enabled": True,
        "cgroup": {
            "location": str(cgroup) if cgroup else None,
            "before": cgroup_before,
            "after": cgroup_after,
            "oom_delta": _event_delta(before_events, after_events, "oom"),
            "oom_kill_delta": _event_delta(before_events, after_events, "oom_kill"),
        },
    }


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
    override = os.environ.get("SIMPLE_CGROUP_DIR")
    if override:
        return Path(override)
    try:
        relative = Path(
            Path("/proc/self/cgroup").read_text().split("0::", 1)[1].splitlines()[0].lstrip("/")
        )
        return Path("/sys/fs/cgroup") / relative
    except (OSError, IndexError):
        return None


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


def final_exit_code(
    exit_code: int,
    timed_out: bool,
    row_count_error: str | None,
    *,
    oom_kill_delta: int | None = None,
    process_group_empty: bool = True,
    descendants_empty: bool = True,
    process_tree_clean: bool = True,
) -> int:
    # The tool's nonzero exit or timeout is a captured outcome, not a worker
    # failure: once result.json is uploaded, Batch must not retry and hide it.
    if not process_group_empty or not descendants_empty or not process_tree_clean:
        return 1
    if oom_kill_delta is not None and oom_kill_delta > 0:
        return 1
    if row_count_error is not None:
        return EXIT_POSTPROCESSING_FAILED
    return 0


def gzip_file(path: Path) -> Path:
    gz_path = path.with_suffix(path.suffix + ".gz")
    with open(path, "rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    path.unlink()
    return gz_path


def native_manifest(native_root: Path) -> dict[str, str]:
    """Content hashes for every native-output file, keyed by relative path."""
    return {
        path.relative_to(native_root).as_posix(): sha256_of(path)
        for path in sorted(native_root.rglob("*"))
        if path.is_file()
    }


def row_count_for(
    adapter_dir: str,
    tool: str,
    mode: str,
    prefix: str,
    stdout_path: Path,
    native_root: Path,
) -> tuple[int | None, str | None]:
    """(row_count, error) -- mirrors worker/summary.py's post-measurement,
    bounded native counting, simplified to "run the normalizer, count its
    output lines" rather than importing each capsule's own count_rows().
    """
    try:
        count = adapters.count_rows(adapter_dir, tool, mode, prefix, stdout_path, native_root)
    except adapters.AdapterError as exc:
        return None, str(exc)[:300]
    return count, None


def write_result_atomic(path: Path, result: dict) -> None:
    """Write result.json via temp-file-then-rename so a reader never observes
    a partially-written marker -- only ever "absent" or "complete".
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(result, indent=2) + "\n")
    os.replace(tmp_path, path)


def upload(attempt_dir: Path, destination: str) -> bool:
    """Upload everything except result.json first, then result.json alone,
    last. A leaf whose upload dies between the two steps is left with
    artifacts but no marker -- exactly the shape verify.py treats as
    "incomplete", never as a passing (or failing) verdict.
    """
    artifacts = sorted(p for p in attempt_dir.iterdir() if p.name != "result.json")
    try:
        for path in artifacts:
            if path.is_dir():
                gcs.upload_tree(path, destination.rstrip("/") + "/" + path.name)
            else:
                gcs.upload_file(path, destination.rstrip("/") + "/" + path.name)
        gcs.upload_file(attempt_dir / "result.json", destination.rstrip("/") + "/result.json")
    except Exception as exc:
        print(f"measure: upload failed: {exc}", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Resolve all harness-owned paths before selecting the subject's cwd. This
    # keeps captures and dataset sinks anchored even when a capsule runs in /.
    attempt_dir = Path(args.output).resolve()
    attempt_dir.mkdir(parents=True, exist_ok=True)

    missing_pass_env = [name for name in args.pass_env if name not in os.environ]
    if missing_pass_env:
        print(
            f"measure: --pass-env variable(s) not set in this environment: {missing_pass_env}",
            file=sys.stderr,
        )
        return 2

    metadata_error = validate_image_metadata(args)
    if metadata_error:
        print(f"measure: {metadata_error}; refusing to execute", file=sys.stderr)
        return EXIT_IMAGE_MISMATCH

    native_root = (attempt_dir / "native").resolve()
    native_root.mkdir(exist_ok=True)
    adapter_dir = adapters.adapter_dir_for(args.tool, args.adapter_root).resolve()
    try:
        command, functional_env = adapters.compile_command(
            adapter_dir,
            args.tool,
            mode=args.mode,
            bucket=args.bucket,
            region=args.region,
            prefix=args.prefix,
            auth=args.auth,
            sink_dir=str(native_root),
        )
    except adapters.AdapterError as exc:
        print(f"measure: {exc}", file=sys.stderr)
        return EXIT_ADAPTER_ERROR

    if not preflight(command):
        return 127

    try:
        case_env = parse_case_env(args.case_env)
    except ValueError as exc:
        print(f"measure: {exc}", file=sys.stderr)
        return 2
    environment_error = validate_environment_inputs(
        args.auth, args.pass_env, functional_env, case_env
    )
    if environment_error:
        print(f"measure: {environment_error}", file=sys.stderr)
        return 2
    # --pass-env is deliberately kept out of case_env: case_env is recorded
    # in result.json (a published artifact), and a value copied in here is a
    # credential -- e.g. Batch's secretVariables land in os.environ, and this
    # is how they reach the subject without ever being written down.
    passthrough_env = {name: os.environ[name] for name in args.pass_env}
    env = {
        **SUBJECT_ENV,
        "AWS_REGION": args.region,
        "AWS_DEFAULT_REGION": args.region,
        **functional_env,
        **case_env,
        **passthrough_env,
    }

    # Every invocation gets its own leaf: two launches of the same case
    # never contend for the same destination, and there is no "last write
    # wins" to reason about.
    attempt_uuid = str(uuid.uuid4())
    leaf_destination = args.destination.rstrip("/") + "/" + attempt_uuid + "/"

    started_at = datetime.now(UTC).isoformat()
    execution = run_tool(
        command,
        attempt_dir,
        args.timeout,
        args.term_grace,
        env,
        cwd=args.subject_workdir,
    )
    exit_code = int(execution["exit_code"])
    timed_out = bool(execution["timed_out"])
    finished_at = datetime.now(UTC).isoformat()

    stdout_path = attempt_dir / "stdout.log"
    stderr_path = attempt_dir / "stderr.log"
    stdout_size = stdout_path.stat().st_size if stdout_path.exists() else 0
    stderr_size = stderr_path.stat().st_size if stderr_path.exists() else 0

    # Scanned uncompressed, before anything is uploaded: a hit here refuses
    # the whole leaf outright rather than uploading the captured output and
    # hoping something downstream notices.
    secret_hit = scan_for_secrets([stdout_path, stderr_path])
    if secret_hit:
        print(
            f"measure: possible secret in {secret_hit}; refusing to upload this attempt",
            file=sys.stderr,
        )
        return EXIT_SECRET_DETECTED

    # Tool failures and partial runs are deliberately not counted (mirrors
    # worker/summary.py): their raw output remains evidence, but its row
    # count is not the target's completed logical object count.
    row_count = row_count_error = None
    if exit_code == 0 and not timed_out:
        row_count, row_count_error = row_count_for(
            str(adapter_dir), args.tool, args.mode, args.prefix, stdout_path, native_root
        )

    stdout_gz = gzip_file(stdout_path) if stdout_path.exists() else None
    stderr_gz = gzip_file(stderr_path) if stderr_path.exists() else None
    native_files = native_manifest(native_root)
    # Computed once, before the marker is written -- nothing after this adds
    # another artifact, so there is no stale-then-corrected total to chase.
    artifacts_size_bytes = sum(p.stat().st_size for p in attempt_dir.rglob("*") if p.is_file())

    result = {
        "tool": args.tool,
        "mode": args.mode,
        "bucket": args.bucket,
        "region": args.region,
        "prefix": args.prefix,
        "auth": args.auth,
        "attempt_uuid": attempt_uuid,
        "destination": leaf_destination,
        "argv": list(command),
        "case_env": case_env,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "execution": execution,
        "wall_seconds": execution["wall_seconds"],
        "max_rss_kb": execution["max_rss_kb"],
        "row_count": row_count,
        "row_count_error": row_count_error,
        "started_at": started_at,
        "finished_at": finished_at,
        "stdout_size": stdout_size,
        "stderr_size": stderr_size,
        "stdout_gz": stdout_gz.name if stdout_gz else None,
        "stdout_gz_sha256": sha256_of(stdout_gz) if stdout_gz else None,
        "stderr_gz": stderr_gz.name if stderr_gz else None,
        "stderr_gz_sha256": sha256_of(stderr_gz) if stderr_gz else None,
        "native_manifest": native_files,
        "artifacts_size_bytes": artifacts_size_bytes,
        "image": args.image,
        "shared_base_uri": args.shared_base_uri,
        "shared_base_digest": args.shared_base_digest,
        "shared_base_source_sha256": args.shared_base_source_sha256,
        "toolbox_manifest_sha256": args.toolbox_manifest_sha256,
        "tool_parent_image": args.tool_parent_image,
        "tool_version": args.tool_version,
        "tool_build_sha256": args.tool_build_sha256,
        "adapter_bundle_sha256": args.adapter_bundle_sha256,
        "harness_revision": args.harness_revision,
        "subject_workdir": args.subject_workdir,
        "applied_subject_workdir": args.subject_workdir,
        "worker_workdir": os.getcwd(),
        "image_set_sha256": args.image_set_sha256,
        "campaign_id": args.campaign_id,
        "job_id": args.job_id,
        "case_id": args.case_id,
        "case_fingerprint": args.case_fingerprint,
        "run_ordinal": args.run_ordinal,
        "submission_number": args.submission_number,
        "declared_resources": {
            "machine_type": args.machine_type,
            "vcpus": args.vcpus,
            "memory_gb": args.memory_gb,
            "container_memory_gb": args.container_memory_gb,
        },
        "observed_architecture": platform.machine(),
        "batch_job_uid": os.environ.get("BATCH_JOB_UID"),
    }
    write_result_atomic(attempt_dir / "result.json", result)

    if not upload(attempt_dir, leaf_destination):
        return 1

    cgroup_result = execution["cgroup"]
    assert isinstance(cgroup_result, dict)
    oom_kill_delta = cgroup_result.get("oom_kill_delta")
    completion = final_exit_code(
        exit_code,
        timed_out,
        row_count_error,
        oom_kill_delta=oom_kill_delta if isinstance(oom_kill_delta, int) else None,
        process_group_empty=bool(execution["process_group_empty"]),
        descendants_empty=bool(execution["descendants_empty"]),
        process_tree_clean=bool(execution["process_tree_clean"]),
    )
    if exit_code != 0:
        print(f"measure: {args.tool} exited {exit_code}", file=sys.stderr)
    elif completion == EXIT_POSTPROCESSING_FAILED:
        print("measure: successful subject output could not be counted", file=sys.stderr)
    elif completion != 0:
        print(
            "measure: subject process group or cgroup OOM evidence was not clean", file=sys.stderr
        )
    return completion


if __name__ == "__main__":
    sys.exit(main())
