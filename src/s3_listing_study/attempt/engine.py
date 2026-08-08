"""Execute one subject command and commit its minimal attempt artifacts."""

from __future__ import annotations

import contextlib
import ctypes
import gzip
import hashlib
import json
import math
import os
import platform
import re
import signal
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, Final

from s3_listing_study.secret_scan import Outcome as ScanOutcome
from s3_listing_study.secret_scan import scan_binary_file

SCHEMA_VERSION: Final = 1
STREAM_NAMES: Final = ("stdout", "stderr")
IMAGE_DIGEST_RE: Final = re.compile(r"sha256:[0-9a-f]{64}")
BASE_SUBJECT_ENV: Final[Mapping[str, str]] = MappingProxyType(
    {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "AWS_EC2_METADATA_DISABLED": "true",
    }
)
# Future tool-specific functional settings must be declared here (or through a
# policy layer that replaces this seam), reviewed as non-secret, and recorded.
# No ambient value is copied merely because it happens to exist in the runner.
DECLARED_FUNCTIONAL_ENV: Final[Mapping[str, str]] = MappingProxyType({})


class AttemptError(RuntimeError):
    """The runner could not create a trustworthy attempt record."""


@dataclass(frozen=True)
class AttemptOptions:
    """Validated inputs for one direct-argv attempt."""

    output: Path
    argv: tuple[str, ...]
    timeout_s: float
    adapter_bundle_sha256: str
    subject_image: str
    derived_image: str
    term_grace_s: float = 5.0
    attempt_id: str = ""
    tool: str = "unknown"
    tool_version: str | None = None
    harness_revision: str | None = None
    operation: str = "list"
    auth: str = "anonymous"
    mode: str | None = None
    bucket: str | None = None
    region: str | None = None
    prefix: str | None = None
    scope: str | None = None
    concurrency: int | None = None


@dataclass(frozen=True)
class _Execution:
    elapsed_ns: int
    returncode: int
    timed_out: bool
    term_sent: bool
    kill_sent: bool
    group_empty: bool
    escaped_descendants: tuple[int, ...]


def anonymous_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Return the complete explicit environment passed to an anonymous child.

    ``source`` is accepted so callers can make the ambient boundary explicit;
    none of its values are inherited. This allowlist construction prevents new
    credential, endpoint, proxy, loader, SDK, or arbitrary variables from
    bypassing an inevitably incomplete denylist.
    """
    del source
    child = dict(BASE_SUBJECT_ENV)
    child.update(DECLARED_FUNCTIONAL_ENV)
    return child


def _validate(options: AttemptOptions) -> AttemptOptions:
    if not options.argv:
        raise AttemptError("the subject argv is empty")
    if any("\x00" in argument for argument in options.argv):
        raise AttemptError("the subject argv contains a NUL byte")
    if not math.isfinite(options.timeout_s) or options.timeout_s <= 0:
        raise AttemptError("timeout must be a finite number greater than zero")
    if not math.isfinite(options.term_grace_s) or options.term_grace_s < 0:
        raise AttemptError("TERM grace must be a finite nonnegative number")
    if re.fullmatch(r"[0-9a-f]{64}", options.adapter_bundle_sha256) is None:
        raise AttemptError("adapter bundle identity must be 64 lowercase hexadecimal digits")
    if IMAGE_DIGEST_RE.fullmatch(options.subject_image) is None:
        raise AttemptError("subject image identity must be sha256:<64 lowercase hex digits>")
    if IMAGE_DIGEST_RE.fullmatch(options.derived_image) is None:
        raise AttemptError("derived image identity must be sha256:<64 lowercase hex digits>")
    if options.auth != "anonymous":
        raise AttemptError("only anonymous S3 attempts are implemented")
    attempt_id = options.attempt_id or str(uuid.uuid4())
    if not attempt_id or any(character.isspace() for character in attempt_id):
        raise AttemptError("attempt ID must be a non-empty token")
    return AttemptOptions(
        output=options.output,
        argv=options.argv,
        timeout_s=options.timeout_s,
        adapter_bundle_sha256=options.adapter_bundle_sha256,
        term_grace_s=options.term_grace_s,
        attempt_id=attempt_id,
        tool=options.tool,
        tool_version=options.tool_version,
        subject_image=options.subject_image,
        derived_image=options.derived_image,
        harness_revision=options.harness_revision,
        operation=options.operation,
        auth=options.auth,
        mode=options.mode,
        bucket=options.bucket,
        region=options.region,
        prefix=options.prefix,
        scope=options.scope,
        concurrency=options.concurrency,
    )


@contextlib.contextmanager
def _open_output_directory(output: Path) -> Iterator[int]:
    """Open/create ``output`` without following links and anchor it by descriptor."""
    if not output.parts:
        raise AttemptError("output path is empty")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    start = Path("/") if output.is_absolute() else Path(".")
    descriptor = os.open(start, flags)
    try:
        parts = output.parts[1:] if output.is_absolute() else output.parts
        if not parts:
            raise AttemptError("output path must name a child directory")
        for index, component in enumerate(parts):
            is_leaf = index == len(parts) - 1
            try:
                next_descriptor = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not is_leaf:
                    raise AttemptError(f"output parent does not exist: {output.parent}") from None
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    next_descriptor = os.open(component, flags, dir_fd=descriptor)
                except FileExistsError:
                    try:
                        next_descriptor = os.open(component, flags, dir_fd=descriptor)
                    except OSError as exc:
                        raise AttemptError(
                            f"output path contains a symlink or non-directory component: {output}"
                        ) from exc
            except OSError as exc:
                raise AttemptError(
                    f"output path contains a symlink or non-directory component: {output}"
                ) from exc
            os.close(descriptor)
            descriptor = next_descriptor
        occupied = sorted(os.listdir(descriptor))
        if occupied:
            raise AttemptError(f"output directory is populated; refusing to overwrite: {output}")
        yield descriptor
    finally:
        os.close(descriptor)


def _enable_child_subreaper() -> None:
    """Adopt double-forked descendants when the runner is not already PID 1."""
    if platform.system() != "Linux":
        raise AttemptError("the attempt engine requires Linux process supervision")
    if os.getpid() == 1:
        return
    pr_set_child_subreaper = 36
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(pr_set_child_subreaper, 1, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise AttemptError(
            f"could not enable Linux child-subreaper supervision: errno {error_number}"
        )


def _direct_children() -> set[int]:
    path = Path("/proc/thread-self/children")
    try:
        return {int(value) for value in path.read_text().split()}
    except OSError as exc:
        raise AttemptError(f"could not inspect supervised child processes: {exc}") from exc


def _process_facts(pid: int) -> tuple[str, int, int] | None:
    """Read state, namespace PID, and namespace process group from procfs."""
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AttemptError(f"could not inspect supervised process {pid}: {exc}") from exc
    fields = {
        name: value.strip()
        for line in status.splitlines()
        if ":" in line
        for name, value in [line.split(":", 1)]
    }
    try:
        return (
            fields["State"].split()[0],
            int(fields["NSpid"].split()[-1]),
            int(fields["NSpgid"].split()[-1]),
        )
    except (KeyError, ValueError):
        raise AttemptError(f"invalid process facts in /proc/{pid}/status") from None


def _reap_adopted_child(pid: int) -> bool:
    """Reap an exited adopted child, returning whether it was reaped."""
    try:
        reaped_pid, _status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError as exc:
        raise AttemptError(f"lost supervision of adopted child process {pid}") from exc
    return reaped_pid == pid


def _inspect_adopted_children(
    baseline_children: set[int],
    root_pid: int,
    process_group: int,
    escaped_descendants: set[int],
) -> set[int]:
    """Return live adopted children, killing any that escaped the root group."""
    live: set[int] = set()
    for proc_pid in _direct_children() - baseline_children:
        facts = _process_facts(proc_pid)
        if facts is None:
            raise AttemptError(f"lost process facts for supervised process {proc_pid}")
        state, child_pid, child_process_group = facts
        # A procfs mount can expose PIDs from an ancestor namespace while
        # Popen/waitpid use the runner's namespace. Match on NSpid instead of
        # assuming the directory name and Popen.pid are comparable.
        if child_pid == root_pid:
            continue
        if _reap_adopted_child(child_pid):
            continue
        if state == "Z":
            if _reap_adopted_child(child_pid):
                continue
            raise AttemptError(f"could not reap adopted child process {child_pid}")
        live.add(child_pid)
        if child_process_group != process_group:
            escaped_descendants.add(child_pid)
            with contextlib.suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)
    return live


def _wait_for_supervised_processes(
    process: subprocess.Popen[bytes],
    process_group: int,
    baseline_children: set[int],
    escaped_descendants: set[int],
    deadline_ns: int,
) -> bool:
    """Wait for the root and every descendant adopted by this runner."""
    while True:
        process.poll()
        adopted = _inspect_adopted_children(
            baseline_children,
            process.pid,
            process_group,
            escaped_descendants,
        )
        if process.returncode is not None and not adopted:
            return True
        remaining_ns = deadline_ns - time.monotonic_ns()
        if remaining_ns <= 0:
            return False
        time.sleep(min(0.01, remaining_ns / 1_000_000_000))


def _signal_group(process_group: int, subject_signal: signal.Signals) -> bool:
    try:
        os.killpg(process_group, subject_signal)
    except ProcessLookupError:
        return False
    return True


def _emergency_cleanup(
    process: subprocess.Popen[bytes],
    process_group: int,
    baseline_children: set[int],
) -> None:
    """Best-effort fail-closed cleanup when supervision inspection fails."""
    _signal_group(process_group, signal.SIGKILL)
    deadline_ns = time.monotonic_ns() + 1_000_000_000
    while time.monotonic_ns() < deadline_ns:
        process.poll()
        while True:
            try:
                reaped_pid, _status = os.waitpid(-process_group, os.WNOHANG)
            except ChildProcessError:
                break
            if reaped_pid == 0:
                break
        current = _direct_children() - baseline_children
        adopted = False
        for proc_pid in current:
            try:
                facts = _process_facts(proc_pid)
            except AttemptError:
                adopted = True
                continue
            if facts is None:
                continue
            _state, child_pid, _child_process_group = facts
            if child_pid == process.pid:
                continue
            adopted = True
            with contextlib.suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(child_pid, os.WNOHANG)
        if process.returncode is not None and not adopted:
            return
        time.sleep(0.01)


def _execute(
    argv: Sequence[str],
    stdout_raw: Path,
    stderr_raw: Path,
    *,
    timeout_s: float,
    term_grace_s: float,
    env: Mapping[str, str],
) -> _Execution:
    _enable_child_subreaper()
    baseline_children = _direct_children()
    process: subprocess.Popen[bytes] | None = None
    timed_out = False
    term_sent = False
    kill_sent = False
    escaped: set[int] = set()
    supervised_empty = False
    start_ns = 0
    with stdout_raw.open("xb") as stdout_file, stderr_raw.open("xb") as stderr_file:
        start_ns = time.monotonic_ns()
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            raise AttemptError(f"could not start subject argv: {exc}") from exc

        process_group = process.pid
        deadline_ns = start_ns + int(timeout_s * 1_000_000_000)
        try:
            supervised_empty = _wait_for_supervised_processes(
                process,
                process_group,
                baseline_children,
                escaped,
                deadline_ns,
            )
            if not supervised_empty:
                timed_out = True
                term_sent = _signal_group(process_group, signal.SIGTERM)
                grace_deadline_ns = time.monotonic_ns() + int(term_grace_s * 1_000_000_000)
                supervised_empty = _wait_for_supervised_processes(
                    process,
                    process_group,
                    baseline_children,
                    escaped,
                    grace_deadline_ns,
                )
                if not supervised_empty:
                    kill_sent = _signal_group(process_group, signal.SIGKILL)
                    reap_deadline_ns = time.monotonic_ns() + max(
                        int(term_grace_s * 1_000_000_000), 1_000_000_000
                    )
                    supervised_empty = _wait_for_supervised_processes(
                        process,
                        process_group,
                        baseline_children,
                        escaped,
                        reap_deadline_ns,
                    )
        except AttemptError:
            with contextlib.suppress(AttemptError):
                _emergency_cleanup(process, process_group, baseline_children)
            raise

        try:
            returncode = process.wait(timeout=max(term_grace_s, 1.0))
        except subprocess.TimeoutExpired:
            _signal_group(process_group, signal.SIGKILL)
            raise AttemptError("subject root process could not be reaped after SIGKILL") from None
        escaped_descendants = tuple(sorted(escaped))
        group_empty = supervised_empty
    end_ns = time.monotonic_ns()
    return _Execution(
        elapsed_ns=end_ns - start_ns,
        returncode=returncode,
        timed_out=timed_out,
        term_sent=term_sent,
        kill_sent=kill_sent,
        group_empty=group_empty,
        escaped_descendants=escaped_descendants,
    )


def _sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _publish_exclusive(
    output_fd: int,
    output_label: Path,
    name: str,
    writer: Callable[[BinaryIO], None],
) -> tuple[int, str]:
    temporary = f".{name}.{uuid.uuid4().hex}.tmp"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=output_fd,
        )
        with os.fdopen(descriptor, "wb") as output:
            writer(output)
            output.flush()
            os.fsync(output.fileno())
        stored_size, stored_digest = _sha256_at(output_fd, temporary)
        try:
            os.link(
                temporary,
                name,
                src_dir_fd=output_fd,
                dst_dir_fd=output_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            raise AttemptError(
                f"artifact already exists; refusing to overwrite: {output_label / name}"
            ) from None
        os.fsync(output_fd)
        return stored_size, stored_digest
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=output_fd)


def _sha256_at(directory_fd: int, name: str) -> tuple[int, str]:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    with os.fdopen(descriptor, "rb") as source:
        digest = hashlib.sha256()
        size = 0
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _gzip(source: Path, output_fd: int, output_label: Path, name: str) -> dict[str, object]:
    raw_size, raw_digest = _sha256(source)

    def write_gzip(output: BinaryIO) -> None:
        with (
            gzip.GzipFile(
                filename="", mode="wb", compresslevel=9, fileobj=output, mtime=0
            ) as archive,
            source.open("rb") as raw,
        ):
            for chunk in iter(lambda: raw.read(1024 * 1024), b""):
                archive.write(chunk)

    stored_size, stored_digest = _publish_exclusive(output_fd, output_label, name, write_gzip)
    return {
        "path": name,
        "compression": "gzip",
        "raw_bytes": raw_size,
        "raw_sha256": raw_digest,
        "stored_bytes": stored_size,
        "stored_sha256": stored_digest,
    }


def _scan_raw_streams(scratch: Path) -> dict[str, str]:
    """Scan complete raw streams before any artifact is published."""
    outcomes = {stream: scan_binary_file(scratch / f"{stream}.raw") for stream in STREAM_NAMES}
    failed = [stream for stream, outcome in outcomes.items() if outcome is not ScanOutcome.CLEAN]
    if failed:
        details = ", ".join(f"{stream}={outcomes[stream].name.lower()}" for stream in failed)
        raise AttemptError(
            f"secret scan did not clear raw subject output ({details}); "
            "refusing to publish attempt artifacts"
        )
    return {stream: outcome.name.lower() for stream, outcome in outcomes.items()}


def _outcome(execution: _Execution) -> dict[str, object]:
    exit_code = execution.returncode if execution.returncode >= 0 else None
    subject_signal = -execution.returncode if execution.returncode < 0 else None
    if execution.escaped_descendants:
        status = "harness_error"
    elif execution.timed_out:
        status = "timed_out"
    elif subject_signal is not None:
        status = "signaled"
    elif exit_code == 0:
        status = "completed"
    else:
        status = "failed"
    if not execution.group_empty or execution.escaped_descendants:
        cleanup_state = "failed"
    elif execution.kill_sent:
        cleanup_state = "killed"
    elif execution.term_sent:
        cleanup_state = "terminated"
    else:
        cleanup_state = "not_needed"
    return {
        "status": status,
        "exit_code": exit_code,
        "signal": subject_signal,
        "timed_out": execution.timed_out,
        "cleanup": {
            "state": cleanup_state,
            "term_sent": execution.term_sent,
            "kill_sent": execution.kill_sent,
            "process_group_empty": execution.group_empty,
            "escaped_descendants": list(execution.escaped_descendants),
        },
    }


def run_attempt(
    options: AttemptOptions,
    *,
    source_env: Mapping[str, str] | None = None,
    post_measure_hook: Callable[[], None] | None = None,
) -> tuple[dict[str, object], int]:
    """Run and finalize one attempt; return its record and runner exit code.

    ``post_measure_hook`` is an injection seam for post-processing tests. It runs
    after the monotonic end timestamp and therefore cannot affect ``elapsed_ns``.
    """
    options = _validate(options)
    child_env = anonymous_environment(os.environ if source_env is None else source_env)
    with _open_output_directory(options.output) as output_fd:
        with tempfile.TemporaryDirectory(prefix=".s3-attempt-") as scratch_name:
            scratch = Path(scratch_name)
            execution = _execute(
                options.argv,
                scratch / "stdout.raw",
                scratch / "stderr.raw",
                timeout_s=options.timeout_s,
                term_grace_s=options.term_grace_s,
                env=child_env,
            )
            if post_measure_hook is not None:
                post_measure_hook()
            scan_outcomes = _scan_raw_streams(scratch)
            streams = {
                stream: _gzip(
                    scratch / f"{stream}.raw",
                    output_fd,
                    options.output,
                    f"{stream}.raw.gz",
                )
                for stream in STREAM_NAMES
            }

        result: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": options.attempt_id,
            "tool": {"name": options.tool, "version": options.tool_version},
            "images": {
                "subject": options.subject_image,
                "derived": options.derived_image,
            },
            "harness_revision": options.harness_revision,
            "adapter_bundle_sha256": options.adapter_bundle_sha256,
            "platform": {
                "architecture": platform.machine(),
                "operating_system": platform.system(),
            },
            "invocation": {
                "argv": list(options.argv),
                "authentication": options.auth,
                "environment": child_env,
            },
            "logical_request": {
                "schema_version": 1,
                "operation": options.operation,
                "mode": options.mode,
                "bucket": options.bucket,
                "region": options.region,
                "prefix": options.prefix,
                "authentication": options.auth,
                "concurrency": options.concurrency,
            },
            "target": {
                "mode": options.mode,
                "bucket": options.bucket,
                "region": options.region,
                "prefix": options.prefix,
                "scope": options.scope,
            },
            "timing": {
                "clock": "time.monotonic_ns",
                "elapsed_ns": execution.elapsed_ns,
                "timeout_ns": int(options.timeout_s * 1_000_000_000),
                "term_grace_ns": int(options.term_grace_s * 1_000_000_000),
            },
            "outcome": _outcome(execution),
            "secret_scan": {"status": "clean", "streams": scan_outcomes},
            "streams": streams,
        }
        encoded = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()

        def write_result(output: BinaryIO) -> None:
            output.write(encoded)

        _publish_exclusive(output_fd, options.output, "result.json", write_result)
    runner_exit = 0 if execution.group_empty and not execution.escaped_descendants else 2
    return result, runner_exit
