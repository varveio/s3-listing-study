"""Run one capsule-compiled subject and publish its bounded attempt evidence.

The worker validates campaign claims against immutable in-image metadata before
exec, supervises the complete process group through TERM/KILL, retains stream
and native-directory outputs, counts through the capsule, and uploads the
result marker last. Attempt uploads are create-only; the deterministic prefix
cannot be merged with a second execution.

Usage:
    measure.py --tool s5cmd --mode recursive --bucket some-bucket --region us-east-1 \\
        --output /tmp/attempt --destination gs://results-bucket/job-id/
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import os
import platform
import re
import resource
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from benchmark import adapters, gcs, procs, replay, replay_runtime
from benchmark.contract import (
    AWS_CREDENTIAL_ENV_KEYS,
    AWS_CREDENTIAL_REQUIRED_ENV_KEYS,
    CREDENTIAL_ENV_VAR,
    TOOLBOX_TOOLS,
    canonical_json,
    sha256_of,
)
from benchmark.runtime.command_adapter import (
    HEAP_PERCENT,
    PURPOSES,
    CommandRequest,
    LoadedCommandAdapter,
    Mode,
    shared_axis_values,
)

EXIT_ADAPTER_ERROR = 3
EXIT_SECRET_DETECTED = 9
EXIT_IMAGE_MISMATCH = 10
EXIT_POSTPROCESSING_FAILED = 11
EXIT_ARTIFACT_UNUSABLE = 12
"""The bytes a case turns on are unusable, or a clean subject never wrote them.

Both are the same verdict for a reader: this attempt has no measurement in it.
A missing declared product belongs here rather than under postprocessing —
nothing failed to process, there was nothing published to process."""
EXIT_SETUP_FAILED = 13
"""The untimed inline setup exec did not leave the subject something to run on.

Distinct from EXIT_ARTIFACT_UNUSABLE, which stays the verdict on *bytes* that
exist and are not usable, wherever they came from: this one says the setup exec
itself failed — nonzero, timed out, left a process behind, or published anything
other than exactly one file.
"""
EXIT_REPLAY_EVIDENCE_FAILED = 14
"""The replay server did not provide the evidence protocol this attempt required."""
REPLAY_ENDPOINT_URL = replay.REPLAY_ENDPOINT_URL
REPLAY_HTTP_TIMEOUT_S = replay_runtime.REPLAY_HTTP_TIMEOUT_S
SETUP_TIMEOUT_S = 300
"""The most an untimed setup exec gets, whatever the subject's deadline is.

Both phases run inside one provider deadline that covers the container, not a
phase: a setup allowed the subject's full timeout could push the measurement
past it and have the whole attempt hard-killed, evidence and all. A setup exec
is by contract a cheap local transform of what the chain already staged, so a
bound this far below any subject's deadline costs a legitimate one nothing.
"""
PINNED_IMAGE_RE = re.compile(r"\A[^\s@]+@sha256:[0-9a-f]{64}\Z")

SECRET_PATTERNS = {
    "credential-shaped value": re.compile(
        rb"AKIA[A-Z0-9]{16}"
        rb"|ASIA[A-Z0-9]{16}"
        rb"|(?i:X-Amz-Signature=[A-Fa-f0-9]{16,}"
        rb"|X-Amz-Credential=[A-Za-z0-9%/+-]{10,}"
        rb"|X-Amz-Security-Token=[A-Za-z0-9%/+=]{20,}"
        rb"|(AWS_SESSION_TOKEN|AWS_SECRET_ACCESS_KEY)=[A-Za-z0-9/+=]{16,}"
        rb"|aws_secret_access_key[ \t\n\r\f\v]*=[ \t\n\r\f\v]*[A-Za-z0-9/+=]{20,}"
        rb"|Authorization:[ \t\n\r\f\v]*(AWS4-HMAC-SHA256|Bearer|Basic)[ \t\n\r\f\v])"
    ),
    "GCP private key": re.compile(rb"BEGIN PRIVATE KEY"),
}
SECRET_SCAN_CHUNK = 1024 * 1024
SECRET_SCAN_OVERLAP = 512

SUBJECT_ENV = {
    # A small, stable environment rather than whatever ambient variables
    # happen to be set in the runner. Credential variables are admitted only
    # through the explicit pass-through path. A capsule's FUNCTIONAL_ENV (see
    # adapters.compile_command) is merged on top.
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/home/s3study",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "AWS_EC2_METADATA_DISABLED": "true",
}


def parse_credential_env(blob: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines into a validated AWS credential mapping.

    Matches the Secret Manager payload format in
    ``infra/terraform/modules/gcp/s3-listing-study/aws-credentials.tf``: one
    ``KEY=VALUE`` per line, ``AWS_SESSION_TOKEN`` optional.
    """
    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(blob.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        key, separator, value = line.partition("=")
        if not separator:
            raise ValueError(f"credential line {line_number} is not KEY=VALUE")
        key = key.strip()
        value = value.strip()
        if key not in AWS_CREDENTIAL_ENV_KEYS:
            raise ValueError(f"credential line {line_number} names an unsupported key: {key}")
        if key in result:
            raise ValueError(f"credential line {line_number} duplicates key: {key}")
        if not value:
            raise ValueError(f"credential line {line_number} has an empty value")
        result[key] = value
    missing = sorted(AWS_CREDENTIAL_REQUIRED_ENV_KEYS - set(result))
    if missing:
        raise ValueError(f"credential payload is missing required key(s): {', '.join(missing)}")
    return result


def resolve_credential_env(auth_role: str | None, environ: Mapping[str, str]) -> dict[str, str]:
    """Return the subject's credential variables for this case's role.

    The credential arrives as the single Batch secretVariable the controller
    attaches to a signing case's job and nothing else. A case that resolved to
    no role, yet whose environment carries a credential, was submitted wrong —
    a refusal rather than something to drop silently.
    """
    blob = environ.get(CREDENTIAL_ENV_VAR)
    if auth_role is not None:
        if not blob:
            raise ValueError(f"case signing with role {auth_role} requires {CREDENTIAL_ENV_VAR}")
        return parse_credential_env(blob)
    if blob:
        raise ValueError(f"{CREDENTIAL_ENV_VAR} is set but the case resolved to no auth role")
    return {}


def validate_environment_inputs(functional_env: dict[str, str]) -> str | None:
    """Refuse environment names that could widen or shadow the auth boundary."""
    reserved = set(SUBJECT_ENV) | AWS_CREDENTIAL_ENV_KEYS | {"AWS_REGION", "AWS_DEFAULT_REGION"}
    collisions = sorted(set(functional_env) & reserved)
    if collisions:
        return f"capsule environment collides with reserved key(s): {', '.join(collisions)}"
    return None


def parse_optional_gb(value: str) -> int | None:
    if value == "none":
        return None
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("memory must be a positive GiB value or 'none'")
    return parsed


class ArtifactSafetyError(RuntimeError):
    """A retained path cannot be proven to be a contained regular file."""


def retained_files(source: Path) -> Iterator[Path]:
    """Yield contained regular files without following links or special files."""
    if source.is_symlink():
        raise ArtifactSafetyError(f"symlink is not publishable: {source}")
    if not source.exists():
        return
    if source.is_file():
        yield source
        return
    if not source.is_dir():
        raise ArtifactSafetyError(f"special path is not publishable: {source}")
    root = source.resolve(strict=True)
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name, reverse=True)
        except OSError as exc:
            raise ArtifactSafetyError(
                f"cannot traverse retained output: {directory}: {exc}"
            ) from exc
        for entry in entries:
            path = Path(entry.path)
            if entry.is_symlink():
                raise ArtifactSafetyError(f"symlink is not publishable: {path.relative_to(root)}")
            if entry.is_dir(follow_symlinks=False):
                stack.append(path)
            elif entry.is_file(follow_symlinks=False):
                try:
                    path.resolve(strict=True).relative_to(root)
                except (OSError, ValueError) as exc:
                    raise ArtifactSafetyError(f"retained output escapes its root: {path}") from exc
                yield path
            else:
                raise ArtifactSafetyError(
                    f"special path is not publishable: {path.relative_to(root)}"
                )


def scan_for_secrets(paths: list[Path]) -> str | None:
    """Scan every retained regular file as bounded binary chunks, failing closed."""
    try:
        files = (path for source in paths for path in retained_files(source))
        for path in files:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                raise ArtifactSafetyError(f"retained path is not a regular file: {path}")
            with open(descriptor, "rb", closefd=True) as source:
                carry = b""
                while chunk := source.read(SECRET_SCAN_CHUNK):
                    window = carry + chunk
                    for name, pattern in SECRET_PATTERNS.items():
                        if pattern.search(window):
                            return f"{path}: {name}"
                    carry = window[-SECRET_SCAN_OVERLAP:]
    except (OSError, ArtifactSafetyError) as exc:
        return f"unsafe or unreadable retained output ({exc})"
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile, run, and upload one case's attempt.")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument(
        "--purpose",
        required=True,
        choices=PURPOSES,
        help="What this attempt is for. A preparation publishes an artifact for "
        "a later case and no measured product.",
    )
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--prefix", default="")
    parser.add_argument(
        "--auth-role",
        default=None,
        help="Logical role this case signs with. Absent lists unsigned.",
    )
    parser.add_argument(
        "--adapter-root",
        default=adapters.DEFAULT_ADAPTER_ROOT,
        help="Root containing one bundled <tool>/adapter directory per registered tool.",
    )
    parser.add_argument("--output", required=True, help="Local attempt directory to write into.")
    parser.add_argument(
        "--destination",
        required=True,
        help="The attempt's result prefix, computed from its ledger row. Written "
        "into as-is: the prefix is the identity, so nothing is appended to it.",
    )
    parser.add_argument(
        "--timeout", type=int, default=3600, help="Seconds before the subject is killed."
    )
    parser.add_argument("--term-grace", type=float, default=5.0)
    parser.add_argument("--image", required=True)
    parser.add_argument("--toolbox-manifest-sha256", required=True)
    parser.add_argument("--toolbox-recipe-sha256", required=True)
    parser.add_argument("--tool-recipe-sha256", required=True)
    parser.add_argument("--tool-build-inputs-sha256", required=True)
    parser.add_argument("--tool-version", required=True)
    parser.add_argument("--tool-build-sha256", required=True)
    parser.add_argument("--adapter-bundle-sha256", required=True)
    parser.add_argument("--harness-revision", required=True)
    parser.add_argument("--subject-workdir", required=True)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--image-set-sha256", required=True)
    parser.add_argument("--machine-type", required=True)
    parser.add_argument("--vcpus", required=True, type=int)
    parser.add_argument("--memory-gb", required=True, type=int)
    parser.add_argument("--container-memory-gb", required=True, type=parse_optional_gb)
    parser.add_argument(
        "--config",
        required=True,
        metavar="JSON",
        help="The case's effective capsule config blob (LoadedCommandAdapter.effective_config).",
    )
    parser.add_argument("--endpoint-url", default="")
    parser.add_argument("--replay-config", default="", metavar="JSON")
    parser.add_argument(
        "--input-artifact",
        default="",
        metavar="gs://...",
        help="The object holding the artifact this case consumes, staged locally "
        "before the subject runs. Empty for the many modes that consume nothing.",
    )
    parser.add_argument(
        "--input-artifact-sha256",
        default="",
        help="The content digest this case hashed. The staged bytes are verified "
        "against it, and a mismatch refuses the attempt.",
    )
    parser.add_argument("--image-metadata", default="/opt/benchmark/image-metadata.json")
    return parser.parse_args(argv)


def parse_replay_config(endpoint_url: str, raw: str) -> replay.ReplayConfig | None:
    """Validate the paired replay flags and return the typed canonical config."""
    if not endpoint_url and not raw:
        return None
    if not endpoint_url or not raw:
        raise ValueError("--endpoint-url and --replay-config must be stated together")
    if endpoint_url != REPLAY_ENDPOINT_URL:
        raise ValueError(f"replay endpoint must be {REPLAY_ENDPOINT_URL}")
    try:
        return replay.parse_document(raw)
    except replay.ReplayError as exc:
        raise ValueError(
            f"--replay-config does not match the resolved replay schema: {exc}"
        ) from None


def validate_replay_allocation(
    config: replay.ReplayConfig,
    *,
    vcpus: int,
    memory_gb: int,
    container_memory_gb: int | None,
) -> None:
    """Bind the replay document to the resource flags the provider request stated."""
    try:
        replay.allocation_summary(
            config,
            box_vcpus=vcpus,
            box_memory_gb=memory_gb,
            container_memory_gb=container_memory_gb,
        )
    except replay.ReplayError as exc:
        raise ValueError(str(exc)) from None


def stage_artifact(uri: str, expected_sha256: str, into: Path) -> Path:
    """Download the artifact this case consumes and refuse bytes that moved.

    Verified before the subject ever sees it: the case hashed this digest, so
    content that does not match it is a different case wearing this one's
    identity. Staged outside the attempt directory, because what lands there is
    this attempt's own evidence and a consumed artifact is somebody else's.
    """
    if not expected_sha256:
        raise ValueError(f"--input-artifact {uri} carries no digest to verify it against")
    into.mkdir(parents=True, exist_ok=True)
    target = into / uri.rstrip("/").rsplit("/", 1)[-1]
    target.write_bytes(gcs.download_bytes(uri))
    staged = sha256_of(target)
    if staged != expected_sha256:
        raise ValueError(
            f"staged artifact {uri} digests {staged}, not the {expected_sha256} this case consumes"
        )
    return target


def validate_image_metadata(args: argparse.Namespace) -> str | None:
    """Return a refusal message when CLI provenance differs from the image."""
    try:
        metadata = json.loads(Path(args.image_metadata).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return f"image metadata is unreadable: {exc}"
    metadata_fields = {
        "schema_version",
        "tools",
        "toolbox_manifest_sha256",
        "toolbox_recipe_sha256",
        "harness_revision",
    }
    if not isinstance(metadata, dict) or set(metadata) != metadata_fields:
        return "image metadata schema is not supported"
    tools = metadata.get("tools")
    tool_fields = {
        "tool_version",
        "tool_build_sha256",
        "tool_artifact_kind",
        "tool_artifact_locator",
        "tool_artifact_sha256",
        "recipe_sha256",
        "build_inputs_sha256",
        "adapter_bundle_sha256",
        "subject_workdir",
        "executable",
        "tool_slice_sha256",
        "platform_sha256",
    }
    if (
        metadata.get("schema_version") != 5
        or not isinstance(tools, dict)
        or set(tools) != TOOLBOX_TOOLS
        or any(not isinstance(value, dict) or set(value) != tool_fields for value in tools.values())
    ):
        return "image metadata schema is not supported"
    toolbox_projection = {
        "schema_version": 3,
        "toolbox_recipe_sha256": metadata.get("toolbox_recipe_sha256"),
        "tools": {
            tool: {
                name: value
                for name, value in selected_tool.items()
                if name != "adapter_bundle_sha256"
            }
            for tool, selected_tool in tools.items()
        },
    }
    canonical = canonical_json(toolbox_projection).encode()
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
        "recipe_sha256": args.tool_recipe_sha256,
        "build_inputs_sha256": args.tool_build_inputs_sha256,
        "subject_workdir": args.subject_workdir,
    }
    if any(selected.get(name) != value for name, value in expected_selected.items()):
        return "campaign provenance does not match immutable image metadata"
    if metadata.get("harness_revision") != args.harness_revision:
        return "campaign provenance does not match immutable image metadata"
    if metadata.get("toolbox_manifest_sha256") != args.toolbox_manifest_sha256:
        return "campaign provenance does not match immutable image metadata"
    if metadata.get("toolbox_recipe_sha256") != args.toolbox_recipe_sha256:
        return "campaign provenance does not match immutable image metadata"
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
    reset_peak: bool = False,
    stdout_path: Path | None = None,
) -> dict[str, object]:
    """Run argv, capture stdout/stderr to files, return
    (exit_code, wall_s, max_rss_kb, timed_out).

    ``stdout_path`` is where fd 1 lands. It defaults to this phase's own log,
    and a subject that only prints its listing is handed its declared product
    file instead: those bytes *are* the product, and writing them to a log and
    a copy would double a listing that can run to gigabytes.
    """
    stdout_path = attempt_dir / "stdout.log" if stdout_path is None else stdout_path
    stderr_path = attempt_dir / "stderr.log"

    procs.enable_child_subreaper()
    baseline_descendants = procs.descendant_pids(os.getpid())
    cgroup = procs.cgroup_v2_directory()
    peak_reset = procs.reset_memory_peak(cgroup) if reset_peak else False
    cgroup_before = procs.cgroup_snapshot(cgroup)
    # Shrink the fork-inherited floor to this worker's live footprint, then
    # record what is left of it: the child's `ru_maxrss` starts from the mark
    # this worker carries into the fork, so a figure near this number has
    # measured nothing about the subject. Read just before the spawn, and a
    # bound on what the subject can *show*, not one the figure must clear --
    # the child re-execs, so it can land marginally under.
    rss_floor_reset = procs.reset_self_peak_rss()
    rss_floor_kb = procs.self_peak_rss_kb()
    start_ns = time.monotonic_ns()
    timed_out = False
    term_sent = False
    kill_sent = False
    process_tree_clean = True
    subject_usage: resource.struct_rusage | None = None
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

        def reap_subject(timeout: float | None) -> bool:
            """Wait the subject with ``os.wait4``, and let nothing wait it first.

            ``Popen.poll``/``wait`` reap the child themselves, which folds its
            rusage into this worker's process-lifetime ``RUSAGE_CHILDREN``
            high-water mark — and with two execs per attempt, that mark reports
            the fatter phase for both. So the first successful wait on this pid
            is this one, and the status goes back onto the Popen object so
            nothing re-waits it. ``None`` waits without a deadline.
            """
            nonlocal subject_usage
            wait_until = None if timeout is None else time.monotonic() + timeout
            while proc.returncode is None:
                try:
                    reaped, status, usage = os.wait4(proc.pid, os.WNOHANG)
                except ChildProcessError:
                    # The status is unobtainable and the child is gone either
                    # way, which is what subprocess itself records here.
                    proc.returncode = 0
                    break
                if reaped != 0:
                    subject_usage = usage
                    proc.returncode = os.waitstatus_to_exitcode(status)
                    break
                if wait_until is not None and time.monotonic() >= wait_until:
                    return False
                time.sleep(0.01)
            return True

        tracked_pids.add(proc.pid)
        deadline = time.monotonic() + timeout
        while True:
            reap_subject(0)
            tracked_pids.update(
                procs.subject_processes(proc.pid, tracked_pids, baseline_descendants)
            )
            if proc.returncode is not None:
                exit_code = proc.returncode
                residual = procs.live_pids(tracked_pids - {proc.pid})
                if residual or procs.process_group_exists(proc.pid):
                    process_tree_clean = False
                break
            if time.monotonic() >= deadline:
                exit_code = 124
                timed_out = True
                break
            time.sleep(0.01)

        residual = procs.live_pids(tracked_pids - {proc.pid})
        if timed_out or residual or procs.process_group_exists(proc.pid):
            if timed_out:
                exit_code = 124  # conventional timeout exit code
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                term_sent = True
            except ProcessLookupError:
                pass
            procs.signal_pids(residual, signal.SIGTERM)
            term_sent = term_sent or bool(residual)
            grace_deadline = time.monotonic() + term_grace
            while time.monotonic() < grace_deadline:
                reap_subject(0)
                tracked_pids.update(
                    procs.subject_processes(proc.pid, tracked_pids, baseline_descendants)
                )
                residual = procs.live_pids(tracked_pids - {proc.pid})
                if not procs.process_group_exists(proc.pid) and not residual:
                    break
                time.sleep(0.01)
            residual = procs.live_pids(tracked_pids - {proc.pid})
            if procs.process_group_exists(proc.pid) or residual:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                    kill_sent = True
                except ProcessLookupError:
                    pass
                procs.signal_pids(residual, signal.SIGKILL)
                kill_sent = kill_sent or bool(residual)
            if not reap_subject(max(term_grace, 1.0)):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                reap_subject(None)
        else:
            reap_subject(None)
    elapsed_ns = time.monotonic_ns() - start_ns
    group_empty = not procs.process_group_exists(proc.pid)
    if not group_empty:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            kill_sent = True
        except ProcessLookupError:
            pass
        group_empty = not procs.process_group_exists(proc.pid)
    cleanup_deadline = time.monotonic() + max(term_grace, 1.0)
    while True:
        tracked_pids.update(procs.subject_processes(proc.pid, tracked_pids, baseline_descendants))
        descendants = procs.live_pids(tracked_pids - {proc.pid})
        if not descendants or time.monotonic() >= cleanup_deadline:
            break
        procs.signal_pids(descendants, signal.SIGKILL)
        kill_sent = True
        procs.wait_for_pids_to_exit(
            descendants, min(0.1, max(0.0, cleanup_deadline - time.monotonic()))
        )
    tracked_pids.update(procs.subject_processes(proc.pid, tracked_pids, baseline_descendants))
    descendants_empty = not procs.live_pids(tracked_pids - {proc.pid})
    group_empty = not procs.process_group_exists(proc.pid)
    procs.reap_children(tracked_pids - {proc.pid})

    cgroup_after = procs.cgroup_snapshot(cgroup)
    before_events = cgroup_before.get("memory_events")
    after_events = cgroup_after.get("memory_events")
    return {
        "exit_code": exit_code,
        "elapsed_ns": elapsed_ns,
        "wall_seconds": round(elapsed_ns / 1_000_000_000, 6),
        "max_rss_kb": subject_usage.ru_maxrss if subject_usage is not None else 0,
        "max_rss_floor_kb": rss_floor_kb,
        "max_rss_floor_reset": rss_floor_reset,
        "user_cpu_seconds": subject_usage.ru_utime if subject_usage is not None else 0.0,
        "system_cpu_seconds": subject_usage.ru_stime if subject_usage is not None else 0.0,
        "timed_out": timed_out,
        "term_sent": term_sent,
        "kill_sent": kill_sent,
        "process_group_empty": group_empty,
        "descendants_empty": descendants_empty,
        "process_tree_clean": process_tree_clean,
        "subreaper_enabled": True,
        "cgroup": {
            "location": str(cgroup) if cgroup else None,
            "memory_peak_reset": peak_reset,
            "before": cgroup_before,
            "after": cgroup_after,
            "oom_delta": procs._event_delta(before_events, after_events, "oom"),
            "oom_kill_delta": procs._event_delta(before_events, after_events, "oom_kill"),
        },
    }


def final_exit_code(
    exit_code: int,
    timed_out: bool,
    row_count_error: str | None,
    *,
    product_error: str | None = None,
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
    if product_error is not None:
        return EXIT_ARTIFACT_UNUSABLE
    if row_count_error is not None:
        return EXIT_POSTPROCESSING_FAILED
    return 0


class SetupFailed(RuntimeError):
    """The untimed setup exec left the timed subject nothing it could run on.

    Carries the setup block as far as it got, because what the failed exec
    captured is the evidence for why the attempt has no measurement in it.
    """

    def __init__(self, message: str, code: int, setup: Mapping[str, object]) -> None:
        super().__init__(message)
        self.code = code
        self.setup = dict(setup)


def subject_env(
    region: str, functional_env: Mapping[str, str], credential_env: Mapping[str, str]
) -> dict[str, str]:
    """The environment an exec of this attempt gets: harness base, capsule, credential.

    The credential is deliberately kept out of the capsule's environment, which is
    recorded in result.json (a published artifact) through the argv it compiled;
    these values are secret material. Batch's secretVariable lands in os.environ,
    and this is how it reaches the subject without being written down.
    """
    return {
        **SUBJECT_ENV,
        "AWS_REGION": region,
        "AWS_DEFAULT_REGION": region,
        **functional_env,
        **credential_env,
    }


def run_inline_setup(
    adapter: LoadedCommandAdapter,
    mode: str,
    args: argparse.Namespace,
    *,
    consumer_config: Mapping[str, object],
    artifact_path: str,
    attempt_dir: Path,
    visible_memory_gb: float,
) -> tuple[str, dict[str, object]]:
    """Run one mode's declared setup exec untimed, and return what the subject reads.

    Same container and same process hygiene as the subject, and explicitly not
    the same clock: the returned block records the setup's wall time as
    evidence, and nothing merges it into the measurement's timing. Its deadline
    is its own too — :data:`SETUP_TIMEOUT_S`, so the two phases together stay
    inside the one deadline the provider gives the container.

    Its sink is under the attempt directory rather than the native root, because
    what it publishes is setup evidence and never the subject's product — a
    consumer that found it in ``native/`` would count the harness's own scaffolding
    as listed rows. Exactly one file, the same contract a consumed preparation
    holds to: the harness has no way to choose between two.

    It runs without the credential. A setup exec is by contract a local transform
    of what the chain already staged, so it has nothing to sign, and the fewer
    execs that hold secret material the smaller the surface that can leak it.
    """
    # The same inheritance a chain link gets, from the same rule.
    shared = shared_axis_values(adapter.modes[mode], consumer_config)
    setup_dir = attempt_dir / "inline"
    sink = setup_dir / "sink"
    sink.mkdir(parents=True, exist_ok=True)
    # Filled in as the phase gets through it, so a refusal at any point still
    # says what happened rather than only that something did.
    setup: dict[str, object] = {
        "mode": mode,
        "command": [],
        "exit_code": None,
        "wall_s": None,
        "output": {},
        "validated": False,
    }

    def failed(message: str, code: int) -> SetupFailed:
        return SetupFailed(message, code, setup)

    try:
        request = CommandRequest(
            mode=mode,
            bucket=args.bucket,
            region=args.region,
            prefix=args.prefix,
            tool=args.tool,
            signed=args.auth_role is not None,
            config=adapter.effective_config(mode, shared),
            sink_dir=str(sink),
            artifact_path=artifact_path,
            visible_memory_gb=visible_memory_gb,
            heap_percent=HEAP_PERCENT,
            endpoint_url=args.endpoint_url,
        )
        command = adapter.compile(request)
        functional_env = adapter.build_env(request)
    except Exception as exc:
        raise failed(f"{args.tool} setup {mode!r}: {exc}", EXIT_ADAPTER_ERROR) from None
    setup["command"] = list(command)

    environment_error = validate_environment_inputs(functional_env)
    if environment_error:
        raise failed(f"setup {mode!r}: {environment_error}", 2)
    if not preflight(command):
        raise failed(f"setup {mode!r} has no executable to run", EXIT_SETUP_FAILED)

    execution = run_tool(
        command,
        setup_dir,
        min(args.timeout, SETUP_TIMEOUT_S),
        args.term_grace,
        subject_env(args.region, functional_env, {}),
        cwd=args.subject_workdir,
    )
    setup["exit_code"] = execution["exit_code"]
    setup["wall_s"] = execution["wall_seconds"]
    settled = all(
        execution.get(field) is True
        for field in ("process_group_empty", "descendants_empty", "process_tree_clean")
    )
    if execution["exit_code"] != 0 or execution["timed_out"] or not settled:
        raise failed(
            f"setup {mode!r} exited {execution['exit_code']} "
            f"(timed_out={execution['timed_out']}, settled={settled})",
            EXIT_SETUP_FAILED,
        )
    try:
        produced = sorted(retained_files(sink))
    except ArtifactSafetyError as exc:
        raise failed(
            f"setup {mode!r} published unusable output: {exc}", EXIT_SETUP_FAILED
        ) from None
    setup["output"] = {path.name: sha256_of(path) for path in produced}
    if len(produced) != 1:
        raise failed(
            f"setup {mode!r} publishes exactly one artifact into its sink, and this one "
            f"published {len(produced)}",
            EXIT_SETUP_FAILED,
        )
    output = produced[0]
    validator = adapter.validate_artifact.get(mode)
    if validator is not None:
        try:
            validator(output)
        except Exception as exc:
            raise failed(
                f"setup {mode!r} produced no usable artifact: {exc}", EXIT_ARTIFACT_UNUSABLE
            ) from None
    setup["validated"] = validator is not None
    return str(output), setup


@dataclass(frozen=True, slots=True)
class Product:
    """The file this attempt publishes its measured product as.

    Resolved from the capsule's own declaration, never from what the sink turns
    out to hold: a subject with a side output writes a file whichever channel
    its product travels on, so the sink cannot answer the question.
    """

    artifact: str
    """The logical name the mode declares it under."""

    name: str
    """Its path relative to the sink."""

    path: Path
    channel: str
    """One of :data:`~benchmark.runtime.command_adapter.PRODUCT_CHANNELS`."""

    compress: bool = False
    """Whether these bytes are gzipped before they are uploaded."""

    @property
    def takes_stdout(self) -> bool:
        """Whether fd 1 is the product, so this attempt has no stdout log."""
        return self.channel == "stdout"


def declared_product(manifest: Mode | None, native_root: Path, *, purpose: str) -> Product | None:
    """Where this attempt publishes its measured product, or ``None`` when it has none.

    Gated on the attempt's purpose, not only on the mode's declaration. A mode
    capped at ``preparation`` declares no product at all, but a measuring mode
    demoted to ``preparation`` by a plan -- the bootstrap ``list`` a hinted-only
    plan still mints -- publishes for the chain and for nothing else. Measuring
    its product would upload a 131 MB listing no consumer reads, and count rows
    for a comparison it is not in.
    """
    if manifest is None or not manifest.product_artifact or purpose == "preparation":
        return None
    name = manifest.product_file
    return Product(
        manifest.product_artifact,
        name,
        native_root / name,
        manifest.product_channel,
        manifest.compresses_product,
    )


def product_gap(product: Product) -> str | None:
    """Why the declared product is not there, or ``None`` when it is.

    A subject that exited clean and wrote nothing where its capsule says it
    writes has published no measurement, however good its timing looks.
    """
    try:
        if product.channel == "dataset":
            if not product.path.is_dir() or not any(retained_files(product.path)):
                return f"declared product dataset {product.name} holds no file"
        elif not product.path.is_file():
            return f"declared product {product.name} was not written"
    except ArtifactSafetyError as exc:
        return f"declared product {product.name} is not publishable: {exc}"
    return None


def capture_block(path: Path | None, *, hash_content: bool = True) -> dict[str, object] | None:
    """Name and size of one uploaded capture, plus its digest when requested.

    ``None`` is the honest record for a subject that only prints: fd 1 carried
    the product, so no stdout log exists to describe.
    """
    if path is None or not path.is_file():
        return None
    return {
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_of(path) if hash_content else None,
    }


def product_block(
    product: Product | None, *, hash_content: bool = True
) -> dict[str, object] | None:
    """What the attempt published as its product, and which channel carried it.

    ``None`` where nothing landed at the declared path -- a subject killed before
    it opened its output file published no product, and describing one with a
    null digest would read downstream as evidence that disagrees with itself
    rather than as the honest failure it is. Whatever debris the sink does hold
    stays bound by ``native_manifest``.

    ``sha256`` is null for a dataset, which is many files and has no one digest;
    ``native_manifest`` binds every part of it either way, so nothing is lost.
    """
    if product is None or product_gap(product) is not None:
        return None
    return {
        "artifact": product.artifact,
        "name": f"native/{product.name}",
        "channel": product.channel,
        "size_bytes": sum(path.stat().st_size for path in retained_files(product.path)),
        "sha256": (
            sha256_of(product.path) if hash_content and product.channel != "dataset" else None
        ),
    }


def published_product(product: Product | None, *, gap: str | None) -> Product | None:
    """Compress the product where its mode says these bytes are worth compressing.

    Called after the row count, which reads what the subject wrote, and before
    the manifest and the product block, which describe what is
    uploaded — so `result.json` names, sizes and digests the file the sink
    actually holds, under a name that says what it is.

    A product that never landed is left alone: there is nothing to compress, and
    `product_gap` has already said so.
    """
    if product is None or not product.compress or gap is not None:
        return product
    return replace(product, name=f"{product.name}.gz", path=gzip_file(product.path))


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
        for path in sorted(retained_files(native_root))
    }


def native_inventory(native_root: Path) -> dict[str, int]:
    """File sizes for minimal replay evidence, keyed by relative path."""
    return {
        path.relative_to(native_root).as_posix(): path.stat().st_size
        for path in sorted(retained_files(native_root))
    }


def row_count_for(
    adapter_dir: str,
    tool: str,
    mode: str,
    prefix: str,
    stdout_path: Path,
    native_root: Path,
) -> tuple[int | None, str | None]:
    """Return ``(row_count, error)`` from the tool capsule after timing.

    Delegate bounded native counting to the capsule's own ``count_rows()``.
    """
    try:
        count = adapters.count_rows(adapter_dir, tool, mode, prefix, stdout_path, native_root)
    except adapters.AdapterError as exc:
        return None, str(exc)[:300]
    return count, None


def write_result_atomic(path: Path, result: dict[str, object]) -> None:
    """Write result.json via temp-file-then-rename so a reader never observes
    a partially-written marker -- only ever "absent" or "complete".
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(result, indent=2) + "\n")
    os.replace(tmp_path, path)


def upload(
    attempt_dir: Path,
    destination: str,
    postprocessing_seconds: dict[str, float] | None = None,
) -> bool:
    """Publish everything except result.json first, then result.json alone,
    last. A leaf whose upload dies between the two steps is left with
    artifacts but no marker -- exactly the shape verify.py treats as
    "incomplete", never as a passing (or failing) verdict.

    A ``gs://`` destination uses the production uploader. An absolute local
    destination gets the same create-only, marker-last contract so the Docker
    executor does not need a second measurement worker.
    """
    artifacts = sorted(p for p in attempt_dir.iterdir() if p.name != "result.json")
    try:
        upload_started = time.monotonic()
        local_destination: Path | None = None
        if destination.startswith("gs://"):
            for path in artifacts:
                if path.is_dir():
                    gcs.upload_tree(
                        path, destination.rstrip("/") + "/" + path.name, create_only=True
                    )
                else:
                    gcs.upload_file(
                        path, destination.rstrip("/") + "/" + path.name, create_only=True
                    )
        else:
            local_destination = Path(destination)
            if not local_destination.is_absolute():
                raise ValueError("local destination must be an absolute path")
            local_destination.mkdir(parents=True, exist_ok=False)
            for path in artifacts:
                target = local_destination / path.name
                if path.is_dir():
                    shutil.copytree(path, target)
                else:
                    shutil.copy2(path, target)
        if postprocessing_seconds is not None:
            postprocessing_seconds["artifact_upload"] = time.monotonic() - upload_started
            result_path = attempt_dir / "result.json"
            result = json.loads(result_path.read_text())
            result["postprocessing_seconds"] = postprocessing_seconds
            write_result_atomic(result_path, result)
        if local_destination is None:
            gcs.upload_file(
                attempt_dir / "result.json",
                destination.rstrip("/") + "/result.json",
                create_only=True,
            )
        else:
            pending = local_destination / ".result.json.pending"
            shutil.copy2(attempt_dir / "result.json", pending)
            os.replace(pending, local_destination / "result.json")
    except Exception as exc:
        print(f"measure: publish failed: {exc}", file=sys.stderr)
        return False
    return True


def attempt_identity(
    args: argparse.Namespace, config: Mapping[str, object], destination: str
) -> dict[str, object]:
    """What result.json says about *which* attempt this is, whatever became of it.

    Written by both markers a worker can publish — a measurement, and a setup
    exec that failed before the subject was ever compiled — so the second is
    this same document with its execution fields empty rather than a shape of
    its own that every reader would have to learn.
    """
    return {
        "tool": args.tool,
        "mode": args.mode,
        "bucket": args.bucket,
        "region": args.region,
        "prefix": args.prefix,
        "auth_role": args.auth_role,
        "destination": destination,
        "config": config,
        "replay": getattr(args, "replay_document", None),
        # Lineage, beside the timing: which bytes this case consumed, and where
        # the harness staged them from.
        "input_artifact": args.input_artifact or None,
        "input_artifact_sha256": args.input_artifact_sha256 or None,
        "image": args.image,
        "toolbox_manifest_sha256": args.toolbox_manifest_sha256,
        "toolbox_recipe_sha256": args.toolbox_recipe_sha256,
        "tool_recipe_sha256": args.tool_recipe_sha256,
        "tool_build_inputs_sha256": args.tool_build_inputs_sha256,
        "tool_version": args.tool_version,
        "tool_build_sha256": args.tool_build_sha256,
        "adapter_bundle_sha256": args.adapter_bundle_sha256,
        "harness_revision": args.harness_revision,
        "subject_workdir": args.subject_workdir,
        "applied_subject_workdir": args.subject_workdir,
        "worker_workdir": os.getcwd(),
        "image_set_sha256": args.image_set_sha256,
        "group_id": args.group_id,
        "job_name": args.job_name,
        "case_id": args.case_id,
        "attempt_id": args.attempt_id,
        "declared_resources": {
            "machine_type": args.machine_type,
            "vcpus": args.vcpus,
            "memory_gb": args.memory_gb,
            "container_memory_gb": args.container_memory_gb,
        },
        "observed_architecture": platform.machine(),
        "batch_job_uid": os.environ.get("BATCH_JOB_UID"),
    }


def publish_pre_subject_failure(
    args: argparse.Namespace,
    *,
    config: Mapping[str, object],
    attempt_dir: Path,
    destination: str,
    exit_code: int,
    setup: Mapping[str, object] | None,
    started_at: str | None,
    replay_evidence: Mapping[str, object] | None,
) -> int:
    """Publish why an attempt failed before the subject could run."""
    if setup is not None:
        secret_hit = scan_for_secrets([attempt_dir / "inline"])
        if secret_hit:
            print(
                f"measure: possible secret in {secret_hit}; refusing to upload this attempt",
                file=sys.stderr,
            )
            return EXIT_SECRET_DETECTED
    result = {
        **attempt_identity(args, config, destination),
        "replay_evidence": (None if replay_evidence is None else dict(replay_evidence)),
        "argv": None,
        "setup": None if setup is None else dict(setup),
        "exit_code": exit_code,
        "worker_exit_code": exit_code,
        "timed_out": False,
        "execution": None,
        "wall_seconds": None,
        "max_rss_kb": None,
        "row_count": None,
        "row_count_error": None,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "product": None,
        "product_error": None,
        "stdout": None,
        "stderr": None,
        "native_manifest": {},
        "artifacts_size_bytes": sum(
            p.stat().st_size for p in attempt_dir.rglob("*") if p.is_file()
        ),
    }
    write_result_atomic(attempt_dir / "result.json", result)
    return exit_code if upload(attempt_dir, destination) else 1


@dataclass(frozen=True)
class ResolvedInputs:
    credential_env: dict[str, str]
    config: dict[str, object]
    replay_config: replay.ReplayConfig | None
    replay_document: dict[str, object] | None
    minimal_evidence: bool
    replay_evidence: dict[str, object] | None


def _resolve_inputs(args: argparse.Namespace) -> ResolvedInputs:
    try:
        credential_env = resolve_credential_env(args.auth_role, os.environ)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    try:
        config = json.loads(args.config)
        if not isinstance(config, dict):
            raise ValueError("--config must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"--config is not valid JSON: {exc}") from exc

    replay_config = parse_replay_config(args.endpoint_url, args.replay_config)
    if replay_config is not None:
        validate_replay_allocation(
            replay_config,
            vcpus=args.vcpus,
            memory_gb=args.memory_gb,
            container_memory_gb=args.container_memory_gb,
        )
        if args.purpose == "measurement" and replay_config.capacity_status != "calibrated":
            raise ValueError("replay measurement is refused while capacity is uncalibrated")
    replay_document = None if replay_config is None else replay_config.as_dict()
    args.replay_document = replay_document
    minimal_evidence = replay_document is not None and args.purpose == "diagnostic"
    replay_evidence = replay_runtime.evidence() if replay_document is not None else None

    if args.mode != config.get("mode"):
        raise ValueError(
            f"--mode {args.mode!r} is not the {config.get('mode')!r} its config "
            "states; the config is what the case hashed"
        )
    return ResolvedInputs(
        credential_env=credential_env,
        config=config,
        replay_config=replay_config,
        replay_document=replay_document,
        minimal_evidence=minimal_evidence,
        replay_evidence=replay_evidence,
    )


def _replay_phase(
    replay_evidence: dict[str, object] | None,
    replay_config: replay.ReplayConfig | None,
    attempt_destination: str,
    publish_failure: Callable[..., int],
) -> tuple[int | None, Callable[[], None]]:
    """Start replay evidence collection and return its matching finalizer."""
    if replay_evidence is None:
        return None, lambda: None
    assert replay_config is not None
    readiness = replay_runtime.wait_for_replay()
    replay_evidence["readiness"] = readiness
    if readiness.get("state") != "ready":
        errors = replay_evidence["errors"]
        assert isinstance(errors, list)
        errors.append(
            {
                "phase": "readiness",
                "error": str(readiness.get("last_error") or "readiness deadline expired"),
            }
        )
        return (
            publish_failure(exit_code=EXIT_REPLAY_EVIDENCE_FAILED, setup=None, started_at=None),
            lambda: None,
        )
    replay_evidence["before"] = replay_runtime.scrape_metrics(replay_evidence, "before")
    if replay_evidence["before"] is None:
        return (
            publish_failure(exit_code=EXIT_REPLAY_EVIDENCE_FAILED, setup=None, started_at=None),
            lambda: None,
        )

    sampler_stop = threading.Event()
    sampler_thread = threading.Thread(
        target=replay_runtime.sample_metrics,
        args=(replay_evidence, sampler_stop, replay_config, attempt_destination),
        name="replay-metrics",
        daemon=True,
    )
    sampler_thread.start()

    def finalize() -> None:
        sampler_stop.set()
        sampler_thread.join(REPLAY_HTTP_TIMEOUT_S + 1.0)
        if sampler_thread.is_alive():
            errors = replay_evidence["errors"]
            assert isinstance(errors, list)
            errors.append({"phase": "sample", "error": "metrics sampler did not stop"})
        else:
            replay_evidence["after"] = replay_runtime.scrape_metrics(replay_evidence, "after")

    return None, finalize


def _result_document(
    args: argparse.Namespace,
    config: Mapping[str, object],
    attempt_destination: str,
    *,
    replay_evidence: Mapping[str, object] | None,
    command: Sequence[str],
    setup: Mapping[str, object] | None,
    exit_code: int,
    completion: int,
    timed_out: bool,
    execution: Mapping[str, object],
    row_count: int | None,
    row_count_error: str | None,
    started_at: str,
    finished_at: str,
    minimal_evidence: bool,
    postprocessing_seconds: Mapping[str, float],
    product: Product | None,
    product_error: str | None,
    stdout_gz: Path | None,
    stderr_gz: Path | None,
    native_files: Mapping[str, str],
    native_sizes: Mapping[str, int] | None,
    artifacts_size_bytes: int,
) -> dict[str, object]:
    return {
        **attempt_identity(args, config, attempt_destination),
        "replay_evidence": replay_evidence,
        "argv": list(command),
        # The untimed pre-phase, when this mode declared one: what it ran, what
        # it made, and how long it took — beside the timing and never inside it.
        "setup": setup,
        "exit_code": exit_code,
        # The subject's exit and the worker's acceptance are different facts.
        # This value is final before the marker exists, so a reader never sees a
        # subject exit 0 in a marker the worker later rejects.
        "worker_exit_code": completion,
        "timed_out": timed_out,
        "execution": execution,
        "wall_seconds": execution["wall_seconds"],
        "max_rss_kb": execution["max_rss_kb"],
        "row_count": row_count,
        "row_count_error": row_count_error,
        "started_at": started_at,
        "finished_at": finished_at,
        "evidence_profile": "minimal-replay" if minimal_evidence else "full",
        "postprocessing_seconds": postprocessing_seconds,
        # What was measured, and which channel carried it. A subject that only
        # prints has no stdout log at all: those bytes are the product.
        "product": product_block(product, hash_content=not minimal_evidence),
        "product_error": product_error,
        "stdout": capture_block(stdout_gz, hash_content=not minimal_evidence),
        "stderr": capture_block(stderr_gz, hash_content=not minimal_evidence),
        "native_manifest": native_files,
        "native_files": native_sizes,
        "artifacts_size_bytes": artifacts_size_bytes,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Resolve all harness-owned paths before selecting the subject's cwd. This
    # keeps captures and dataset sinks anchored even when a capsule runs in /.
    attempt_dir = Path(args.output).resolve()
    attempt_dir.mkdir(parents=True, exist_ok=True)

    try:
        resolved = _resolve_inputs(args)
    except ValueError as exc:
        print(f"measure: {exc}", file=sys.stderr)
        return 2
    credential_env = resolved.credential_env
    config = resolved.config
    replay_config = resolved.replay_config
    minimal_evidence = resolved.minimal_evidence
    replay_evidence = resolved.replay_evidence

    metadata_error = validate_image_metadata(args)
    if metadata_error:
        print(f"measure: {metadata_error}; refusing to execute", file=sys.stderr)
        return EXIT_IMAGE_MISMATCH

    native_root = (attempt_dir / "native").resolve()
    native_root.mkdir(exist_ok=True)
    adapter_dir = adapters.adapter_dir_for(args.tool, args.adapter_root).resolve()
    # What the subject can see: the container's cgroup ceiling, or the whole
    # box when the case set none. A managed runtime's share of it is fixed at
    # the harness's one methodology constant, never a per-case choice.
    visible_memory_gb = float(
        args.container_memory_gb if args.container_memory_gb is not None else args.memory_gb
    )
    artifact_path = ""
    if args.input_artifact:
        try:
            artifact_path = str(
                stage_artifact(
                    args.input_artifact,
                    args.input_artifact_sha256,
                    attempt_dir.parent / f"{attempt_dir.name}-inbound",
                )
            )
        except Exception as exc:
            print(f"measure: {exc}", file=sys.stderr)
            return EXIT_ARTIFACT_UNUSABLE

    # The attempt's own prefix, and no leaf below it: the ledger row already
    # names one attempt, so "is this attempt complete" is one existence test on
    # a known prefix rather than a listing that resolves which leaf is
    # authoritative. Create-only writes are what keep a second execution of this
    # attempt from merging into the first.
    attempt_destination = args.destination.rstrip("/") + "/"

    def publish_failure(
        *, exit_code: int, setup: Mapping[str, object] | None, started_at: str | None
    ) -> int:
        return publish_pre_subject_failure(
            args,
            config=config,
            attempt_dir=attempt_dir,
            destination=attempt_destination,
            exit_code=exit_code,
            setup=setup,
            started_at=started_at,
            replay_evidence=replay_evidence,
        )

    # An untimed pre-phase, before the subject argv exists: what it publishes is
    # what the subject consumes, so it runs here rather than as its own attempt.
    setup: dict[str, object] | None = None
    try:
        adapter = adapters.load_adapter(adapter_dir, args.tool)
    except adapters.AdapterError as exc:
        print(f"measure: {exc}", file=sys.stderr)
        return EXIT_ADAPTER_ERROR
    # A mode the capsule does not have declares nothing; compiling it below is
    # what says so, in the one place that already refuses it.
    manifest = adapter.modes.get(args.mode)
    inline_mode = manifest.inline if manifest is not None else ""
    if inline_mode:
        setup_started_at = datetime.now(UTC).isoformat()
        try:
            artifact_path, setup = run_inline_setup(
                adapter,
                inline_mode,
                args,
                consumer_config=config,
                artifact_path=artifact_path,
                attempt_dir=attempt_dir,
                visible_memory_gb=visible_memory_gb,
            )
        except SetupFailed as exc:
            print(f"measure: {exc}", file=sys.stderr)
            return publish_failure(
                setup=exc.setup,
                exit_code=exc.code,
                started_at=setup_started_at,
            )

    try:
        command, functional_env = adapters.compile_command(
            adapter_dir,
            args.tool,
            mode=args.mode,
            bucket=args.bucket,
            region=args.region,
            prefix=args.prefix,
            signed=args.auth_role is not None,
            config=config,
            sink_dir=str(native_root),
            artifact_path=artifact_path,
            visible_memory_gb=visible_memory_gb,
            heap_percent=HEAP_PERCENT,
            endpoint_url=args.endpoint_url,
        )
    except adapters.AdapterError as exc:
        print(f"measure: {exc}", file=sys.stderr)
        return EXIT_ADAPTER_ERROR

    if not preflight(command):
        return 127

    environment_error = validate_environment_inputs(functional_env)
    if environment_error:
        print(f"measure: {environment_error}", file=sys.stderr)
        return 2
    env = subject_env(args.region, functional_env, credential_env)

    # Where this attempt's product is published, and so where fd 1 goes: a
    # subject that only prints has its listing landed in the declared file
    # directly, and one with an output flag has already been pointed at it by
    # its capsule, leaving stdout to be the log it claims to be.
    product = declared_product(manifest, native_root, purpose=args.purpose)
    if product is not None:
        product.path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path = attempt_dir / "stdout.log"
    subject_stdout = product.path if product is not None and product.takes_stdout else stdout_path

    replay_failure, finish_replay = _replay_phase(
        replay_evidence, replay_config, attempt_destination, publish_failure
    )
    if replay_failure is not None:
        return replay_failure

    started_at = datetime.now(UTC).isoformat()
    execution = run_tool(
        command,
        attempt_dir,
        args.timeout,
        args.term_grace,
        env,
        cwd=args.subject_workdir,
        # The container's peak is not per exec: only an attempt whose setup exec
        # already ran has something to clear out of it.
        reset_peak=setup is not None,
        stdout_path=subject_stdout,
    )
    exit_value = execution["exit_code"]
    if isinstance(exit_value, bool) or not isinstance(exit_value, int):
        raise TypeError("run_tool returned a non-integer exit_code")
    exit_code = exit_value
    timed_out = bool(execution["timed_out"])
    finished_at = datetime.now(UTC).isoformat()
    postprocessing_started = time.monotonic()
    postprocessing_seconds: dict[str, float] = {}
    phase_started = time.monotonic()
    finish_replay()
    postprocessing_seconds["replay_evidence_finalize"] = time.monotonic() - phase_started

    if any(
        execution.get(field) is not True
        for field in ("process_group_empty", "descendants_empty", "process_tree_clean")
    ):
        print(
            "measure: subject process tree is not settled; refusing to retain or upload output",
            file=sys.stderr,
        )
        return 1

    stderr_path = attempt_dir / "stderr.log"

    # Scan the uncompressed captures before any upload. A retained credential
    # turns the whole attempt into a refusal rather than public evidence that a
    # later reader is expected to notice and clean up.
    phase_started = time.monotonic()
    scanned = [stderr_path, native_root]
    if subject_stdout == stdout_path:
        scanned.insert(0, stdout_path)
    if setup is not None:
        scanned.append(attempt_dir / "inline")
    secret_hit = scan_for_secrets(scanned)
    postprocessing_seconds["secret_scan"] = time.monotonic() - phase_started
    if secret_hit:
        print(
            f"measure: possible secret in {secret_hit}; refusing to upload this attempt",
            file=sys.stderr,
        )
        return EXIT_SECRET_DETECTED

    # Tool failures and partial runs are deliberately not counted: their raw
    # output remains evidence, but its row
    # count is not the target's completed logical object count.
    #
    # Neither is a preparation's, whether its mode is capped there or a plan
    # demoted it: a preparation never enters a completeness comparison, and what
    # it publishes is an artifact rather than a listing — s3-fast-list's cut
    # points are key *prefixes* — so a row count is a question that does not
    # apply. Asking it anyway failed a perfect preparation on a normalizer that
    # rightly refused the mode.
    counts_a_listing = args.purpose != "preparation" and (
        manifest is None or manifest.purpose_ceiling != "preparation"
    )
    product_error = (
        product_gap(product) if product is not None and exit_code == 0 and not timed_out else None
    )
    if product_error:
        print(f"measure: {product_error}", file=sys.stderr)
    row_count = row_count_error = None
    phase_started = time.monotonic()
    if exit_code == 0 and not timed_out and counts_a_listing and not product_error:
        # The product, not the log: a listing whose subject prints it and one
        # whose subject writes it are the same bytes at the same path now. A
        # dataset has no single path to hand over, and the capsule reads it off
        # the sink root it is given anyway.
        countable = product is not None and product.channel != "dataset"
        row_count, row_count_error = row_count_for(
            str(adapter_dir),
            args.tool,
            args.mode,
            args.prefix,
            product.path if product is not None and countable else stdout_path,
            native_root,
        )
    postprocessing_seconds["row_count"] = time.monotonic() - phase_started

    phase_started = time.monotonic()
    product = published_product(product, gap=product_error)
    stdout_gz = gzip_file(stdout_path) if subject_stdout == stdout_path else None
    stderr_gz = gzip_file(stderr_path) if stderr_path.exists() else None
    postprocessing_seconds["capture_finalize"] = time.monotonic() - phase_started
    phase_started = time.monotonic()
    native_files = {} if minimal_evidence else native_manifest(native_root)
    native_sizes = native_inventory(native_root) if minimal_evidence else None
    postprocessing_seconds["native_inventory" if minimal_evidence else "native_manifest"] = (
        time.monotonic() - phase_started
    )
    # Computed once, before the marker is written -- nothing after this adds
    # another artifact, so there is no stale-then-corrected total to chase.
    artifacts_size_bytes = sum(p.stat().st_size for p in attempt_dir.rglob("*") if p.is_file())

    cgroup_result = execution["cgroup"]
    assert isinstance(cgroup_result, dict)
    oom_kill_delta = cgroup_result.get("oom_kill_delta")
    completion = final_exit_code(
        exit_code,
        timed_out,
        row_count_error,
        product_error=product_error,
        oom_kill_delta=oom_kill_delta if isinstance(oom_kill_delta, int) else None,
        process_group_empty=bool(execution["process_group_empty"]),
        descendants_empty=bool(execution["descendants_empty"]),
        process_tree_clean=bool(execution["process_tree_clean"]),
    )
    replay_refusals: tuple[str, ...] = ()
    if replay_config is not None:
        replay_refusals = replay.replay_refusals(
            replay_config, replay_evidence, purpose=args.purpose
        )
        if replay_refusals:
            completion = EXIT_REPLAY_EVIDENCE_FAILED
    postprocessing_seconds["pre_upload_total"] = time.monotonic() - postprocessing_started

    result = _result_document(
        args,
        config,
        attempt_destination,
        replay_evidence=replay_evidence,
        command=command,
        setup=setup,
        exit_code=exit_code,
        completion=completion,
        timed_out=timed_out,
        execution=execution,
        row_count=row_count,
        row_count_error=row_count_error,
        started_at=started_at,
        finished_at=finished_at,
        minimal_evidence=minimal_evidence,
        postprocessing_seconds=postprocessing_seconds,
        product=product,
        product_error=product_error,
        stdout_gz=stdout_gz,
        stderr_gz=stderr_gz,
        native_files=native_files,
        native_sizes=native_sizes,
        artifacts_size_bytes=artifacts_size_bytes,
    )
    write_result_atomic(attempt_dir / "result.json", result)
    if not upload(attempt_dir, attempt_destination, postprocessing_seconds):
        return 1
    if exit_code != 0:
        print(f"measure: {args.tool} exited {exit_code}", file=sys.stderr)
    elif completion == EXIT_ARTIFACT_UNUSABLE:
        print("measure: the subject exited clean and published no product", file=sys.stderr)
    elif completion == EXIT_POSTPROCESSING_FAILED:
        print("measure: successful subject output could not be counted", file=sys.stderr)
    elif completion == EXIT_REPLAY_EVIDENCE_FAILED:
        print(
            "measure: replay evidence protocol was refused: " + "; ".join(replay_refusals),
            file=sys.stderr,
        )
    elif completion != 0:
        print(
            "measure: subject process group or cgroup OOM evidence was not clean", file=sys.stderr
        )
    return completion


if __name__ == "__main__":
    sys.exit(main())
