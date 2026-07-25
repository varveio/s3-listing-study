"""``run.meta`` writing, and the control-byte guard every field passes through.

The single emission path for ``run.meta``, the record the verifier treats as
the source of truth it refuses to second-guess.

``run.meta`` is line-oriented and its parsers take the FIRST match, so an
embedded newline in a caller-supplied value — an ``--env`` value, a
``--tool-version``, an object key echoed back through a payload path — plants an
earlier forged field that the verifier's altered-evidence refusal then reads
instead of the genuine value. Every field is checked, on the one emission path,
and a control byte is refused rather than normalised away.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import ReceiptError
from .redact import Payload

CONTROL_RE = re.compile(r"[\x00-\x1f]")
"""Any byte below 0x20 — newline, CR and tab included."""


def reject_control(label: str, value: str) -> str:
    """Return ``value`` unchanged, or refuse it for carrying a control byte."""
    if CONTROL_RE.search(value):
        raise ReceiptError(
            f"{label} contains a control character (byte < 0x20 — newline, CR or tab).\n"
            "        run.meta is line-oriented, so an embedded newline forges a later field."
            " Refused.\n"
            "        Provide a single-line value."
        )
    return value


@dataclass(frozen=True)
class RunFacts:
    """Everything the wrapper observed about one run, as strings.

    Strings, not parsed types: these are the values that go verbatim into
    ``run.meta`` and the receipt, and a number re-formatted on the way through
    would make the receipt disagree with what was measured. ``unavailable`` is a
    legal value for a measurement that was never taken, and is not zero.
    """

    tool: str
    mode: str
    auth: str
    bucket: str
    region: str
    prefix: str
    image: str
    image_arch: str
    host_arch: str
    entrypoint: str
    measured_process: str
    security_profile: str
    security_provider: str
    docker_network: str
    network_mtu: str
    firewall_policy_sha256: str
    container_cap_drop: str
    container_no_new_privileges: str
    docker_pull_policy: str
    docker_control_timeout_s: str
    docker_cleanup_timeout_s: str
    docker_log_driver: str
    docker_log_config_sha256: str
    docker_log_option_keys_base64: str
    exit_code: str
    timed_out: str
    wall_clock_s: str
    registry_path: str
    registry_sha256: str
    manifest: str
    manifest_sha256: str
    snapshot_date: str
    manifest_keys: str
    shape: str
    passed_env: str
    observability_env: str
    functional_env: str
    tool_version: str
    tool_version_source: str
    redaction_changed_bytes: str
    peak_rss_kb: str
    cgroup_peak_bytes: str
    rss_samples: str
    cgroup_samples: str
    poll_ms: str
    utc_start: str
    runner_location: str
    arch: str
    cores: str
    ram_gb: str
    host_kernel: str
    invocation: str
    emulated: str
    env_note: str
    timeout: str


def fields(facts: RunFacts, stdout: Payload, stderr: Payload) -> list[tuple[str, str]]:
    """The ``run.meta`` key/value pairs, in emission order."""
    return [
        ("tool", facts.tool),
        ("mode", facts.mode),
        ("auth", facts.auth),
        ("bucket", facts.bucket),
        ("region", facts.region),
        ("prefix", facts.prefix),
        ("image", facts.image),
        ("image_arch", facts.image_arch),
        ("host_arch", facts.host_arch),
        ("entrypoint", facts.entrypoint or "none"),
        ("measured_process", facts.measured_process),
        ("security_profile", facts.security_profile),
        ("security_provider", facts.security_provider),
        ("docker_network", facts.docker_network),
        ("network_mtu", facts.network_mtu),
        ("firewall_policy_sha256", facts.firewall_policy_sha256),
        ("container_cap_drop", facts.container_cap_drop),
        ("container_no_new_privileges", facts.container_no_new_privileges),
        ("docker_pull_policy", facts.docker_pull_policy),
        ("docker_control_timeout_s", facts.docker_control_timeout_s),
        ("docker_cleanup_timeout_s", facts.docker_cleanup_timeout_s),
        ("docker_log_driver", facts.docker_log_driver),
        ("docker_log_config_sha256", facts.docker_log_config_sha256),
        ("docker_log_option_keys_base64", facts.docker_log_option_keys_base64),
        ("exit_code", facts.exit_code),
        ("timed_out", facts.timed_out),
        ("wall_clock_s", facts.wall_clock_s),
        ("registry_path", facts.registry_path),
        ("registry_sha256", facts.registry_sha256),
        ("manifest", facts.manifest),
        ("manifest_sha256", facts.manifest_sha256),
        ("snapshot_date", facts.snapshot_date),
        # Relative stream paths are resolved from the directory containing this
        # run.meta. External payloads remain absolute until the release-asset
        # index gives them content-addressed public identities.
        ("payload_path_base", "run-meta-directory"),
        ("stdout_path", stdout.recorded_path),
        ("stdout_sha256", stdout.sha256),
        ("stderr_path", stderr.recorded_path),
        ("stderr_sha256", stderr.sha256),
        ("stdout_truncated", stdout.truncated),
        ("stdout_dropped_bytes", str(stdout.dropped_bytes)),
        ("stderr_truncated", stderr.truncated),
        ("stderr_dropped_bytes", str(stderr.dropped_bytes)),
        ("passed_env", facts.passed_env),
        ("observability_env", facts.observability_env),
        ("functional_env", facts.functional_env),
        ("tool_version", facts.tool_version or "unavailable"),
        ("tool_version_source", facts.tool_version_source),
        ("redaction_changed_bytes", facts.redaction_changed_bytes),
        ("peak_rss_kb", facts.peak_rss_kb),
        ("cgroup_peak_bytes", facts.cgroup_peak_bytes),
        ("rss_samples", facts.rss_samples),
        ("cgroup_samples", facts.cgroup_samples),
        ("poll_ms", facts.poll_ms),
        ("utc_start", facts.utc_start),
        ("runner_location", facts.runner_location),
    ]


def render(facts: RunFacts, stdout: Payload, stderr: Payload) -> bytes:
    """``run.meta`` as bytes. The only emission path, so the only guard needed."""
    out = bytearray()
    for key, value in fields(facts, stdout, stderr):
        reject_control(f"run.meta field '{key}'", value)
        out += f"{key}={value}\n".encode()
    return bytes(out)
