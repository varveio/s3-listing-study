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
import resource
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, Final

from s3_listing_study.common.secret_scan import Outcome as ScanOutcome
from s3_listing_study.common.secret_scan import scan_binary_file
from s3_listing_study.worker.runtime_identity import interpreter_identity

from .summary import summarize

SCHEMA_VERSION: Final = 3
STREAM_NAMES: Final = ("stdout", "stderr")
NATIVE_DIRECTORY: Final = "native"
"""Where a mode's native file-sink output is published inside the attempt.

Most modes write their listing to stdout and produce nothing here. A mode whose
tool refuses to stream — Swath's Parquet dataset — writes into the sink
directory the engine hands it, and every file that lands there is scanned,
hashed, and published under this name. Output the engine cannot account for is
output that cannot appear in a receipt.
"""
NATIVE_MAX_FILES: Final = 4096
NATIVE_MAX_BYTES: Final = 8 * 1024**3
IMAGE_DIGEST_RE: Final = re.compile(r"sha256:[0-9a-f]{64}")
FINGERPRINT_RE: Final = re.compile(r"[0-9a-f]{64}")
CAMPAIGN_ID_RE: Final = re.compile(r"\d{4}-\d{2}-\d{2}-[a-z][a-z0-9]*")
JOB_ID_RE: Final = re.compile(r"[a-z](?:[a-z0-9-]*[a-z0-9])?")
BASE_SUBJECT_ENV: Final[Mapping[str, str]] = MappingProxyType(
    {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/home/s3study",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "AWS_EC2_METADATA_DISABLED": "true",
    }
)
# Future tool-specific functional settings must be declared here (or through a
# policy layer that replaces this seam), reviewed as non-secret, and recorded.
# No ambient value is copied merely because it happens to exist in the runner.
DECLARED_FUNCTIONAL_ENV: Final[Mapping[str, str]] = MappingProxyType({})

AWS_CREDENTIAL_ENV_KEYS: Final = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
"""The only keys an authenticated child's credential material may set.

Matches the Secret Manager payload lines documented in
``infra/terraform/modules/gcp/s3-listing-study/aws-credentials.tf``. A
credential mapping naming anything else is refused rather than forwarded.
"""

AWS_CREDENTIAL_REQUIRED_ENV_KEYS: Final = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")

CREDENTIAL_ENV_VAR: Final = "S3_STUDY_AWS_CREDENTIAL"
"""Ambient variable carrying the raw ``KEY=VALUE`` credential payload.

Deliberately not an ``AWS_*`` name, so an SDK never picks it up directly and it
is never itself forwarded to a child; only the keys parsed out of it are, and
only for an authenticated attempt.
"""

_RESERVED_ENV_KEYS: Final = (
    frozenset(BASE_SUBJECT_ENV)
    | frozenset(AWS_CREDENTIAL_ENV_KEYS)
    | {CREDENTIAL_ENV_VAR, "AWS_REGION", "AWS_DEFAULT_REGION"}
)
"""Keys a capsule's ``FUNCTIONAL_ENV`` may never set.

Reserved so a tool-specific declaration can't silently widen the ambient
boundary or shadow the credential path — either would defeat the allowlist
construction ``anonymous_environment``/``authenticated_environment`` exist for.
"""


class AttemptError(RuntimeError):
    """The runner could not create a trustworthy attempt record."""


@dataclass(frozen=True, slots=True)
class DeclaredResources:
    """The allocation the campaign declared for this attempt."""

    machine_type: str
    vcpus: int
    memory_gb: int
    container_memory_gb: int | None


@dataclass(frozen=True, slots=True)
class CampaignProvenance:
    """Correlation fields supplied together by a campaign submission."""

    campaign_id: str
    job_id: str
    case_id: str
    case_fingerprint: str
    attempt_fingerprint: str
    run_ordinal: int
    submission_number: int
    resources: DeclaredResources


@dataclass(frozen=True)
class AttemptOptions:
    """Validated inputs for one direct-argv attempt."""

    output: Path
    argv: tuple[str, ...]
    timeout_s: float
    adapter_bundle_sha256: str
    shared_base_digest: str
    shared_base_uri: str
    derived_image: str
    tool_image_digest: str = "sha256:" + "0" * 64
    tool_image_uri: str = "local/tool@sha256:" + "0" * 64
    selection_sha256: str = "0" * 64
    subject_workdir: str = "/"
    shared_base_source_sha256: str = "0" * 64
    tool_build_sha256: str = "0" * 64
    tool_artifact: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType(
            {"kind": "synthetic", "locator": "test", "sha256": "0" * 64}
        )
    )
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
    sink_dir: str | None = None
    adapter_path: Path | None = None
    campaign: CampaignProvenance | None = None
    results_destination: str | None = None
    credential_env: Mapping[str, str] | None = None
    """Validated AWS credential keys for an authenticated attempt.

    ``None`` for ``auth="anonymous"`` — carrying any value there is refused,
    not ignored, so an anonymous receipt can never silently follow from a run
    that actually had credential material available to it.
    """
    functional_env: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    """The selected capsule's declared non-secret functional environment."""


@dataclass(frozen=True)
class _CgroupSnapshot:
    location: str | None
    memory_current_bytes: int | None
    memory_peak_bytes: int | None
    memory_events: Mapping[str, int] | None
    error: str | None = None


@dataclass(frozen=True)
class _Execution:
    elapsed_ns: int
    started_at_utc: str
    ended_at_utc: str
    returncode: int
    timed_out: bool
    term_sent: bool
    kill_sent: bool
    group_empty: bool
    escaped_descendants: tuple[int, ...]
    rusage_children_max_child_peak_rss_kb: int
    user_cpu_s: float
    system_cpu_s: float
    peak_disk_delta_bytes: int
    cgroup_before: _CgroupSnapshot
    cgroup_after: _CgroupSnapshot


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _cgroup_v2_directory(
    proc_cgroup: Path = Path("/proc/self/cgroup"),
    mountinfo: Path = Path("/proc/self/mountinfo"),
) -> Path | None:
    """Resolve this worker's cgroup v2 directory from procfs mount facts."""
    try:
        cgroup_line = next(
            line for line in proc_cgroup.read_text().splitlines() if line.startswith("0::")
        )
        cgroup_path = Path(cgroup_line.partition("::")[2])
        for line in mountinfo.read_text().splitlines():
            before, separator, after = line.partition(" - ")
            if not separator or after.split()[0] != "cgroup2":
                continue
            fields = before.split()
            mount_root = Path(fields[3])
            mount_point = Path(fields[4])
            try:
                relative = cgroup_path.relative_to(mount_root)
            except ValueError:
                continue
            return mount_point / relative
    except (OSError, StopIteration, IndexError):
        return None
    return None


def _read_int(path: Path) -> int:
    return int(path.read_text().strip())


def _read_memory_events(path: Path) -> dict[str, int]:
    events: dict[str, int] = {}
    for line in path.read_text().splitlines():
        name, value = line.split()
        events[name] = int(value)
    return events


def _cgroup_snapshot(directory: Path | None) -> _CgroupSnapshot:
    if directory is None:
        return _CgroupSnapshot(None, None, None, None, "cgroup v2 location unavailable")
    try:
        return _CgroupSnapshot(
            str(directory),
            _read_int(directory / "memory.current"),
            _read_int(directory / "memory.peak"),
            _read_memory_events(directory / "memory.events"),
        )
    except (OSError, ValueError) as exc:
        return _CgroupSnapshot(str(directory), None, None, None, str(exc))


def _memory_total_bytes(path: Path = Path("/proc/meminfo")) -> int | None:
    try:
        for line in path.read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _cgroup_limit(directory: Path | None) -> int | str | None:
    if directory is None:
        return None
    try:
        value = (directory / "memory.max").read_text().strip()
        return "max" if value == "max" else int(value)
    except (OSError, ValueError):
        return None


def _merge_functional_env(child: dict[str, str], functional_env: Mapping[str, str]) -> None:
    collisions = sorted(set(functional_env) & _RESERVED_ENV_KEYS)
    if collisions:
        raise AttemptError(f"functional_env collides with reserved key(s): {', '.join(collisions)}")
    child.update(functional_env)


def _region_env(region: str | None) -> dict[str, str]:
    """``AWS_REGION``/``AWS_DEFAULT_REGION`` for every attempt that names one.

    The region is already public in argv and the logical request; it is not a
    credential. It is declared here, not left to each adapter's ``--region``
    CLI flag, because at least one subject (pS3, its Go AWS SDK v1 session)
    does not thread a CLI-flag-only region into its own API calls and
    silently returns zero results without it — a structural SDK dependency,
    not a tool-specific quirk worth hand-waving around per capsule.
    """
    return {} if region is None else {"AWS_REGION": region, "AWS_DEFAULT_REGION": region}


def anonymous_environment(
    source: Mapping[str, str],
    functional_env: Mapping[str, str] = MappingProxyType({}),
    region: str | None = None,
) -> dict[str, str]:
    """Return the complete explicit environment passed to an anonymous child.

    ``source`` is accepted so callers can make the ambient boundary explicit;
    none of its values are inherited. This allowlist construction prevents new
    credential, endpoint, proxy, loader, SDK, or arbitrary variables from
    bypassing an inevitably incomplete denylist. ``functional_env`` is the
    capsule's own declared, non-secret, structurally-required configuration
    (e.g. mc's anonymous alias endpoint) — reviewed at capsule-authoring time,
    same trust level as ``DECLARED_FUNCTIONAL_ENV``.
    """
    del source
    child = dict(BASE_SUBJECT_ENV)
    child.update(DECLARED_FUNCTIONAL_ENV)
    child.update(_region_env(region))
    _merge_functional_env(child, functional_env)
    return child


def authenticated_environment(
    source: Mapping[str, str],
    credential_env: Mapping[str, str],
    functional_env: Mapping[str, str] = MappingProxyType({}),
    region: str | None = None,
) -> dict[str, str]:
    """Return the complete explicit environment passed to an authenticated child.

    Identical construction to ``anonymous_environment`` — nothing from
    ``source`` is inherited — plus the caller's already-validated credential
    keys. ``AWS_EC2_METADATA_DISABLED`` stays set: the explicit static
    credential is the only one this child should ever use, never an instance
    metadata fallback.
    """
    del source
    child = dict(BASE_SUBJECT_ENV)
    child.update(DECLARED_FUNCTIONAL_ENV)
    child.update(_region_env(region))
    _merge_functional_env(child, functional_env)
    child.update(credential_env)
    return child


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
        if "=" not in line:
            raise AttemptError(f"credential line {line_number} is not KEY=VALUE")
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key not in AWS_CREDENTIAL_ENV_KEYS:
            raise AttemptError(f"credential line {line_number} names an unsupported key: {key}")
        if key in result:
            raise AttemptError(f"credential line {line_number} duplicates key: {key}")
        if not value:
            raise AttemptError(f"credential line {line_number} has an empty value")
        result[key] = value
    missing = [key for key in AWS_CREDENTIAL_REQUIRED_ENV_KEYS if key not in result]
    if missing:
        raise AttemptError(f"credential payload is missing required key(s): {', '.join(missing)}")
    return result


def _redacted_environment(env: Mapping[str, str]) -> dict[str, str]:
    """The child environment as it may be persisted: credential values masked.

    Key names stay visible so a receipt still shows which variables were set;
    only values that could be secret material are replaced.
    """
    return {
        key: ("<REDACTED>" if key in AWS_CREDENTIAL_ENV_KEYS else value)
        for key, value in env.items()
    }


def _validate_campaign(campaign: CampaignProvenance) -> None:
    if CAMPAIGN_ID_RE.fullmatch(campaign.campaign_id) is None:
        raise AttemptError("campaign ID must be a dated lowercase campaign token")
    if len(campaign.job_id) > 63 or JOB_ID_RE.fullmatch(campaign.job_id) is None:
        raise AttemptError("job ID is not a valid GCP Batch job token")
    if not campaign.case_id or any(character.isspace() for character in campaign.case_id):
        raise AttemptError("case ID must be a non-empty whitespace-free token")
    if FINGERPRINT_RE.fullmatch(campaign.case_fingerprint) is None:
        raise AttemptError("case fingerprint must be 64 lowercase hexadecimal digits")
    if FINGERPRINT_RE.fullmatch(campaign.attempt_fingerprint) is None:
        raise AttemptError("attempt fingerprint must be 64 lowercase hexadecimal digits")
    if campaign.run_ordinal < 1:
        raise AttemptError("run ordinal must be a 1-based positive integer")
    if campaign.submission_number < 1:
        raise AttemptError("submission number must be a positive integer")
    resources = campaign.resources
    if not resources.machine_type or any(
        character.isspace() for character in resources.machine_type
    ):
        raise AttemptError("machine type must be a non-empty whitespace-free token")
    if resources.vcpus < 1 or resources.memory_gb < 1:
        raise AttemptError("declared vCPUs and memory must be positive integers")
    if resources.container_memory_gb is not None and (
        resources.container_memory_gb < 1 or resources.container_memory_gb > resources.memory_gb
    ):
        raise AttemptError("container memory must be positive and no larger than machine memory")


def _validate(options: AttemptOptions) -> AttemptOptions:
    if not options.argv:
        raise AttemptError("the subject argv is empty")
    if any("\x00" in argument for argument in options.argv):
        raise AttemptError("the subject argv contains a NUL byte")
    workdir = Path(options.subject_workdir)
    if (
        not options.subject_workdir.startswith("/")
        or "\x00" in options.subject_workdir
        or workdir.as_posix() != options.subject_workdir
        or ".." in workdir.parts
    ):
        raise AttemptError("subject workdir must be a canonical absolute path")
    if not workdir.is_dir():
        raise AttemptError(f"subject workdir does not exist: {options.subject_workdir}")
    if not math.isfinite(options.timeout_s) or options.timeout_s <= 0:
        raise AttemptError("timeout must be a finite number greater than zero")
    if not math.isfinite(options.term_grace_s) or options.term_grace_s < 0:
        raise AttemptError("TERM grace must be a finite nonnegative number")
    if re.fullmatch(r"[0-9a-f]{64}", options.adapter_bundle_sha256) is None:
        raise AttemptError("adapter bundle identity must be 64 lowercase hexadecimal digits")
    if IMAGE_DIGEST_RE.fullmatch(options.shared_base_digest) is None:
        raise AttemptError("shared base image identity must be sha256:<64 lowercase hex digits>")
    if not options.shared_base_uri.endswith(f"@{options.shared_base_digest}") or any(
        character.isspace() for character in options.shared_base_uri
    ):
        raise AttemptError("shared base URI must end with its sha256 digest")
    for identity_name, value in (
        ("shared base source", options.shared_base_source_sha256),
        ("tool build", options.tool_build_sha256),
    ):
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise AttemptError(f"{identity_name} identity must be 64 lowercase hexadecimal digits")
    if set(options.tool_artifact) != {"kind", "locator", "sha256"}:
        raise AttemptError("tool artifact identity must contain kind, locator, and sha256")
    if re.fullmatch(r"[0-9a-f]{64}", options.tool_artifact.get("sha256", "")) is None:
        raise AttemptError("tool artifact sha256 must be 64 lowercase hexadecimal digits")
    if IMAGE_DIGEST_RE.fullmatch(options.derived_image) is None:
        raise AttemptError("derived image identity must be sha256:<64 lowercase hex digits>")
    if IMAGE_DIGEST_RE.fullmatch(options.tool_image_digest) is None:
        raise AttemptError("tool image identity must be sha256:<64 lowercase hex digits>")
    if not options.tool_image_uri.endswith(f"@{options.tool_image_digest}") or any(
        character.isspace() for character in options.tool_image_uri
    ):
        raise AttemptError("tool image URI must end with its sha256 digest")
    if re.fullmatch(r"[0-9a-f]{64}", options.selection_sha256) is None:
        raise AttemptError("tool selection identity must be 64 lowercase hexadecimal digits")
    if options.auth not in ("anonymous", "authenticated"):
        raise AttemptError("auth must be anonymous or authenticated")
    if options.auth == "anonymous":
        if options.credential_env:
            raise AttemptError("an anonymous attempt must not carry credential material")
    else:
        if not options.credential_env:
            raise AttemptError("an authenticated attempt requires credential_env")
        unknown = sorted(set(options.credential_env) - set(AWS_CREDENTIAL_ENV_KEYS))
        if unknown:
            raise AttemptError(f"credential_env has unsupported key(s): {', '.join(unknown)}")
        missing = [
            key for key in AWS_CREDENTIAL_REQUIRED_ENV_KEYS if key not in options.credential_env
        ]
        if missing:
            raise AttemptError(f"credential_env is missing required key(s): {', '.join(missing)}")
    if options.campaign is not None:
        _validate_campaign(options.campaign)
    if options.results_destination is not None and options.campaign is None:
        # Non-campaign uploads remain supported by the CLI, but the engine can
        # only put an artifact URI in result.json when the deterministic
        # campaign/job layout exists.
        results_destination = None
    else:
        results_destination = options.results_destination
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
        shared_base_digest=options.shared_base_digest,
        shared_base_uri=options.shared_base_uri,
        shared_base_source_sha256=options.shared_base_source_sha256,
        tool_build_sha256=options.tool_build_sha256,
        tool_artifact=dict(options.tool_artifact),
        derived_image=options.derived_image,
        tool_image_digest=options.tool_image_digest,
        tool_image_uri=options.tool_image_uri,
        selection_sha256=options.selection_sha256,
        subject_workdir=options.subject_workdir,
        harness_revision=options.harness_revision,
        operation=options.operation,
        auth=options.auth,
        mode=options.mode,
        bucket=options.bucket,
        region=options.region,
        prefix=options.prefix,
        scope=options.scope,
        concurrency=options.concurrency,
        sink_dir=options.sink_dir,
        adapter_path=options.adapter_path,
        campaign=options.campaign,
        results_destination=results_destination,
        credential_env=options.credential_env,
        functional_env=options.functional_env,
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


DISK_SAMPLE_PATH: Final = "/"
DISK_SAMPLE_INTERVAL_S: Final = 1.0


class _DiskPeakSampler:
    """Background thread tracking peak filesystem usage during one attempt.

    Independent of, and generic across, whatever any given tool self-reports
    about itself — the harness's own measurement, the same way for every
    subject. Started before the clock and stopped after it, so neither the
    thread's creation nor its teardown lands inside ``elapsed_ns``. Reports a
    delta above a pre-execution baseline so the base image's own footprint is
    never attributed to the run.

    The figure is whole-filesystem, not per-process: two attempts sharing a
    disk are counted into each other, so it is only meaningful when one attempt
    runs at a time on a host. That is true of the production one-attempt-per-
    machine shape and false under ``smoke-campaign.sh --jobs N`` for N>1.

    ``getrusage`` has no filesystem-space equivalent to the memory/CPU figures
    it gives for free (its ``ru_inblock``/``ru_oublock`` counters are I/O
    operation counts, not space, and are unreliable on Linux for this use
    case) — polling is the plain way to get a real number.
    """

    def __init__(
        self, path: str = DISK_SAMPLE_PATH, interval_s: float = DISK_SAMPLE_INTERVAL_S
    ) -> None:
        self._path = path
        self._interval_s = interval_s
        self._baseline = shutil.disk_usage(path).used
        self._peak = self._baseline
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _sample(self) -> None:
        try:
            used = shutil.disk_usage(self._path).used
        except OSError:
            return
        if used > self._peak:
            self._peak = used

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self._interval_s)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> int:
        """Stop sampling and return the peak delta above baseline, in bytes."""
        self._stop.set()
        self._thread.join(timeout=self._interval_s * 2)
        self._sample()  # the interval between the last poll and exit would else be missed
        return max(0, self._peak - self._baseline)


def _execute(
    argv: Sequence[str],
    stdout_raw: Path,
    stderr_raw: Path,
    *,
    timeout_s: float,
    term_grace_s: float,
    env: Mapping[str, str],
    cwd: str,
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
    started_at_utc = ""
    ended_at_utc = ""
    end_ns = 0
    disk_sampler = _DiskPeakSampler()
    cgroup_directory = _cgroup_v2_directory()
    cgroup_before = _cgroup_snapshot(cgroup_directory)
    with stdout_raw.open("xb") as stdout_file, stderr_raw.open("xb") as stderr_file:
        # Started before the clock, stopped after it: thread creation is real
        # work and does not belong inside elapsed_ns. Sampling a slightly wider
        # window than the run can only raise the reported peak, never hide one.
        disk_sampler.start()
        started_at_utc = _utc_now()
        start_ns = time.monotonic_ns()
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                env=env,
                cwd=cwd,
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
        # The timed interval ends at reap.  Every measurement snapshot and all
        # row-counting/compression/upload work follows this point.
        end_ns = time.monotonic_ns()
        ended_at_utc = _utc_now()
        cgroup_after = _cgroup_snapshot(cgroup_directory)
        # Read right after the reap that makes it valid: RUSAGE_CHILDREN is the
        # cumulative account of every child this process has reaped, which is
        # exactly the one subject spawned above as long as one process runs
        # exactly one attempt — true in the derived image (a fresh interpreter
        # per invocation), not guaranteed if a caller reuses a process across
        # attempts (e.g. multiple run_attempt() calls in one test process).
        rusage = resource.getrusage(resource.RUSAGE_CHILDREN)
        escaped_descendants = tuple(sorted(escaped))
        group_empty = supervised_empty
    peak_disk_delta_bytes = disk_sampler.stop()
    return _Execution(
        elapsed_ns=end_ns - start_ns,
        started_at_utc=started_at_utc,
        ended_at_utc=ended_at_utc,
        returncode=returncode,
        timed_out=timed_out,
        term_sent=term_sent,
        kill_sent=kill_sent,
        group_empty=group_empty,
        escaped_descendants=escaped_descendants,
        rusage_children_max_child_peak_rss_kb=rusage.ru_maxrss,
        user_cpu_s=rusage.ru_utime,
        system_cpu_s=rusage.ru_stime,
        peak_disk_delta_bytes=peak_disk_delta_bytes,
        cgroup_before=cgroup_before,
        cgroup_after=cgroup_after,
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


def _mkdir_at(parent_fd: int, name: str) -> int:
    """Create (or reuse) one child directory and return its descriptor.

    Reusing an existing directory is the one place this module does not refuse a
    path it did not create: a dataset's parts share their parent directory, so
    the second part would otherwise fail on the tree the first part built. Every
    artifact inside it is still published with ``O_EXCL``.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with contextlib.suppress(FileExistsError):
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise AttemptError(f"native output path is not a directory: {name}") from exc


@contextlib.contextmanager
def _open_sink_directory(sink: Path) -> Iterator[int]:
    """Anchor the sink by descriptor for the whole plan-then-publish sequence."""
    try:
        descriptor = os.open(sink, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise AttemptError(f"native sink is not a directory: {sink}") from exc
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _sink_walk_failed(error: OSError) -> None:
    raise AttemptError(f"native sink is not fully readable: {error}")


def _plan_native(sink_fd: int) -> tuple[Path, ...]:
    """Account for every file in the sink, in a stable order, publishing nothing.

    The walk, the caps, and the secret scan all run here — BEFORE any artifact
    is published, for the same reason :func:`_scan_raw_streams` runs where it
    does: a partial publish of output that turns out to carry a credential is
    still a leak. The caps are enforced as the walk proceeds, so a subject that
    filled the sink with millions of files stops the walk instead of being
    enumerated into memory first.

    A symlink, socket, or device node is refused rather than skipped, symlinked
    directories included: the sink is meant to hold a listing, and anything else
    in it means the attempt is not the thing the record would claim it is.
    """
    planned: list[Path] = []
    total = 0
    for root, directories, names, directory_fd in os.fwalk(
        ".", dir_fd=sink_fd, onerror=_sink_walk_failed, follow_symlinks=False
    ):
        directories.sort()
        relative_root = Path(root)
        for name in directories:
            if not stat.S_ISDIR(os.lstat(name, dir_fd=directory_fd).st_mode):
                raise AttemptError(
                    f"native sink holds a symlinked directory: {relative_root / name}"
                )
        for name in sorted(names):
            relative = relative_root / name
            if not stat.S_ISREG(os.lstat(name, dir_fd=directory_fd).st_mode):
                raise AttemptError(f"native sink holds a non-regular file: {relative}")
            if len(planned) == NATIVE_MAX_FILES:
                raise AttemptError(f"native sink holds more than {NATIVE_MAX_FILES} files")
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            try:
                total += os.fstat(descriptor).st_size
                if total > NATIVE_MAX_BYTES:
                    raise AttemptError(f"native sink holds more than {NATIVE_MAX_BYTES} bytes")
                # The scanner takes a path; /proc/self/fd names the descriptor
                # already opened at this directory, so the file scanned is the
                # file the walk saw and not whatever the name resolves to now.
                outcome = scan_binary_file(Path(f"/proc/self/fd/{descriptor}"))
            finally:
                os.close(descriptor)
            if outcome is not ScanOutcome.CLEAN:
                raise AttemptError(
                    f"secret scan did not clear native subject output "
                    f"({relative}={outcome.name.lower()}); refusing to publish attempt artifacts"
                )
            planned.append(relative)
    return tuple(planned)


def _open_native_file(sink_fd: int, relative: Path) -> int:
    """Reopen one planned sink file, anchoring every component by descriptor."""
    directory_fd = os.dup(sink_fd)
    try:
        for component in relative.parts[:-1]:
            next_fd = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd
            )
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    except OSError as exc:
        raise AttemptError(f"planned native file is no longer readable: {relative}") from exc
    finally:
        os.close(directory_fd)


def _publish_native(
    sink_fd: int,
    planned: Sequence[Path],
    output_fd: int,
    output_label: Path,
) -> list[dict[str, object]]:
    """Publish the planned sink files under ``native/``, preserving their layout.

    Layout is preserved because a Parquet dataset is a directory of parts plus a
    sidecar, and flattening it would produce something DuckDB cannot read back.
    Only files :func:`_plan_native` already cleared are published.
    """
    if not planned:
        return []
    records: list[dict[str, object]] = []
    native_fd = _mkdir_at(output_fd, NATIVE_DIRECTORY)
    try:
        for relative in planned:
            opened: list[int] = []
            target_fd = native_fd
            try:
                for component in relative.parts[:-1]:
                    target_fd = _mkdir_at(target_fd, component)
                    opened.append(target_fd)

                def write_native(output: BinaryIO, source: Path = relative) -> None:
                    with os.fdopen(_open_native_file(sink_fd, source), "rb") as raw:
                        for chunk in iter(lambda: raw.read(1024 * 1024), b""):
                            output.write(chunk)

                size, digest = _publish_exclusive(
                    target_fd,
                    output_label / NATIVE_DIRECTORY,
                    relative.name,
                    write_native,
                )
            finally:
                for descriptor in reversed(opened):
                    os.close(descriptor)
            records.append(
                {
                    "path": f"{NATIVE_DIRECTORY}/{relative.as_posix()}",
                    "bytes": size,
                    "sha256": digest,
                }
            )
        os.fsync(native_fd)
    finally:
        os.close(native_fd)
    return records


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


def _batch_retry_attempt(source_env: Mapping[str, str]) -> int | None:
    raw = source_env.get("BATCH_TASK_RETRY_ATTEMPT")
    if raw is None:
        return None
    if re.fullmatch(r"[0-9]+", raw) is None:
        raise AttemptError("BATCH_TASK_RETRY_ATTEMPT must be a nonnegative ASCII integer")
    return int(raw)


def _campaign_record(campaign: CampaignProvenance | None) -> dict[str, object] | None:
    if campaign is None:
        return None
    resources = campaign.resources
    return {
        "campaign_id": campaign.campaign_id,
        "job_id": campaign.job_id,
        "case_id": campaign.case_id,
        "case_fingerprint": campaign.case_fingerprint,
        "attempt_fingerprint": campaign.attempt_fingerprint,
        "run_ordinal": campaign.run_ordinal,
        "submission_number": campaign.submission_number,
        "declared_resources": {
            "machine_type": resources.machine_type,
            "vcpus": resources.vcpus,
            "memory_gb": resources.memory_gb,
            "container_memory_gb": resources.container_memory_gb,
        },
    }


def _cgroup_record(execution: _Execution) -> dict[str, object]:
    before = execution.cgroup_before
    after = execution.cgroup_after
    have_events = before.memory_events is not None and after.memory_events is not None
    before_events = before.memory_events or {}
    after_events = after.memory_events or {}
    return {
        "location": after.location or before.location,
        "before": {
            "memory_current_bytes": before.memory_current_bytes,
            "memory_peak_bytes": before.memory_peak_bytes,
            "memory_events": before.memory_events,
            "error": before.error,
        },
        "after": {
            "memory_current_bytes": after.memory_current_bytes,
            "memory_peak_bytes": after.memory_peak_bytes,
            "memory_events": after.memory_events,
            "error": after.error,
        },
        "oom_delta": (
            after_events.get("oom", 0) - before_events.get("oom", 0) if have_events else None
        ),
        "oom_kill_delta": (
            after_events.get("oom_kill", 0) - before_events.get("oom_kill", 0)
            if have_events
            else None
        ),
    }


def _result_locations(options: AttemptOptions) -> tuple[str | None, str | None]:
    if options.results_destination is None or options.campaign is None:
        return None, None
    root = options.results_destination.rstrip("/")
    artifact = f"{root}/{options.attempt_id}"
    return artifact, f"{artifact}/result.json"


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
    env_source = os.environ if source_env is None else source_env
    batch_retry_attempt = _batch_retry_attempt(env_source)
    if options.auth == "anonymous":
        child_env = anonymous_environment(env_source, options.functional_env, options.region)
    else:
        assert options.credential_env is not None  # enforced by _validate above
        child_env = authenticated_environment(
            env_source, options.credential_env, options.functional_env, options.region
        )
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
                cwd=options.subject_workdir,
            )
            if post_measure_hook is not None:
                post_measure_hook()
            outcome = _outcome(execution)
            scan_outcomes = _scan_raw_streams(scratch)
            native_refusal = (
                "a descendant escaped the subject process group, so the sink cannot be "
                "attributed to the supervised run"
                if execution.escaped_descendants
                else None
            )
            with contextlib.ExitStack() as stack:
                sink_fd: int | None = None
                planned: tuple[Path, ...] = ()
                if options.sink_dir and native_refusal is None:
                    sink_fd = stack.enter_context(_open_sink_directory(Path(options.sink_dir)))
                    planned = _plan_native(sink_fd)
                # Everything below publishes. Nothing above it has, so a sink the
                # plan refuses leaves the attempt with no artifacts at all.
                streams = {
                    stream: _gzip(
                        scratch / f"{stream}.raw",
                        output_fd,
                        options.output,
                        f"{stream}.raw.gz",
                    )
                    for stream in STREAM_NAMES
                }
                native_output = (
                    _publish_native(sink_fd, planned, output_fd, options.output)
                    if sink_fd is not None
                    else []
                )
                summary = summarize(
                    outcome_status=str(outcome["status"]),
                    adapter_bundle_sha256=options.adapter_bundle_sha256,
                    adapter_path=options.adapter_path,
                    mode=options.mode,
                    prefix=options.prefix,
                    stdout_path=scratch / "stdout.raw",
                    native_root=Path(options.sink_dir) if options.sink_dir else None,
                )

        artifact_uri, result_uri = _result_locations(options)
        cgroup_directory = _cgroup_v2_directory()
        result: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": options.attempt_id,
            "tool": {"name": options.tool, "version": options.tool_version},
            "images": {
                "derived": options.derived_image,
                "tool": {
                    "digest": options.tool_image_digest,
                    "uri": options.tool_image_uri,
                },
                "shared_base": {
                    "digest": options.shared_base_digest,
                    "uri": options.shared_base_uri,
                },
            },
            "build_inputs": {
                "shared_base": {
                    "source_sha256": options.shared_base_source_sha256,
                },
                "tool": {
                    "build_sha256": options.tool_build_sha256,
                    "artifact": dict(options.tool_artifact),
                    "selection_sha256": options.selection_sha256,
                },
            },
            "harness_revision": options.harness_revision,
            "adapter_bundle_sha256": options.adapter_bundle_sha256,
            "campaign": _campaign_record(options.campaign),
            "scheduler": {"batch_task_retry_attempt": batch_retry_attempt},
            "artifact_uri": artifact_uri,
            "result_uri": result_uri,
            "platform": {
                "architecture": platform.machine(),
                "operating_system": platform.system(),
                "logical_cpus": os.cpu_count(),
                "mem_total_bytes": _memory_total_bytes(),
                "cgroup_v2_location": str(cgroup_directory) if cgroup_directory else None,
                "cgroup_v2_memory_limit_bytes": _cgroup_limit(cgroup_directory),
            },
            "interpreter": interpreter_identity(),
            "invocation": {
                "argv": list(options.argv),
                "working_directory": options.subject_workdir,
                "authentication": options.auth,
                "environment": _redacted_environment(child_env),
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
                "started_at_utc": execution.started_at_utc,
                "ended_at_utc": execution.ended_at_utc,
                "timeout_ns": int(options.timeout_s * 1_000_000_000),
                "term_grace_ns": int(options.term_grace_s * 1_000_000_000),
            },
            "resources": {
                "rusage_children_max_child_peak_rss_kb": (
                    execution.rusage_children_max_child_peak_rss_kb
                ),
                "rusage_children_user_cpu_s": execution.user_cpu_s,
                "rusage_children_system_cpu_s": execution.system_cpu_s,
                "whole_filesystem_peak_used_delta_bytes": execution.peak_disk_delta_bytes,
                "whole_filesystem_sample_path": DISK_SAMPLE_PATH,
                "whole_filesystem_poll_interval_s": DISK_SAMPLE_INTERVAL_S,
                "cgroup_v2_memory": _cgroup_record(execution),
            },
            "outcome": outcome,
            "summary": summary,
            "secret_scan": {"status": "clean", "streams": scan_outcomes},
            "streams": streams,
            "native_output": native_output,
            "native_refusal": native_refusal,
        }
        encoded = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()

        def write_result(output: BinaryIO) -> None:
            output.write(encoded)

        _publish_exclusive(output_fd, options.output, "result.json", write_result)
    if not execution.group_empty or execution.escaped_descendants:
        runner_exit = 2
    elif summary["status"] == "error":
        # The tool outcome and raw evidence are valid and already sealed; 3 is
        # the distinct post-attempt failure policy used for summary/upload.
        runner_exit = 3
    else:
        runner_exit = 0
    return result, runner_exit
