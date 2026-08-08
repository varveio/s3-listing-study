"""``receipt.md`` generation.

Golden regeneration from a committed ``run.meta`` is **not** possible: ``run.meta``
does not carry the invocation, the box spec, the payload byte sizes, the
emulation note or the registry shape. The acceptance bar is therefore a frozen
fixture over synthetic values (``tests/fixtures/receipt/``), whose bytes this
renderer must reproduce exactly.

Every dynamic value goes through ONE escaper. HTML entities are safe in Markdown
table cells and cannot terminate a code span, create a new cell, or inject HTML.
Controls are refused, not normalised away — an object key must not be able to
forge a later field.
"""

from __future__ import annotations

from .meta import RunFacts, reject_control
from .redact import PAYLOAD_CAP, Payload

VERDICT_PLACEHOLDER = "_(filled in by `harness/verify-listing.sh`)_"
"""The verdict slot a fresh receipt carries; the verifier splices over it.

Frozen wording: every committed receipt carries these exact bytes, and the
replay oracle restores the slot by matching them.
"""

_ENTITIES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ("|", "&#124;"), ("`", "&#96;"))


def html_escape(value: str) -> str:
    """``&``, ``<``, ``>``, ``|`` and a backtick, as entities. ``&`` first."""
    for char, entity in _ENTITIES:
        value = value.replace(char, entity)
    return value


def md_safe_inline(label: str, value: str) -> str:
    """A table-cell value: no control byte at all, then escaped."""
    return html_escape(reject_control(f"receipt field '{label}'", value))


def md_safe_block(label: str, value: str) -> str:
    """A block value: LF is layout, every other control byte is forbidden."""
    if any(char < " " and char != "\n" for char in value):
        raise ReceiptBlockError(f"receipt block '{label}' contains a forbidden control character")
    return html_escape(value)


class ReceiptBlockError(Exception):
    """A block value carried a control byte that is not a line feed."""


def _megabytes(value: str, divisor: int) -> str:
    """``unavailable`` stays ``unavailable``. A measurement never taken is not 0.0."""
    if value == "unavailable":
        return "unavailable"
    return f"{int(value) / divisor:.1f}"


def render(facts: RunFacts, stdout: Payload, stderr: Payload) -> bytes:
    """The receipt, as bytes."""

    def esc(label: str, value: str) -> str:
        return md_safe_inline(label, value)

    tool = esc("TOOL", facts.tool)
    mode = esc("MODE", facts.mode)
    utc_start = esc("UTC_START", facts.utc_start)
    exit_code = esc("rc", facts.exit_code)
    wall = esc("wall", facts.wall_clock_s)
    auth = esc("AUTH", facts.auth)
    env_note = esc("ENV_NOTE", facts.env_note)
    obs_env = esc("OBS_ENV_NOTE", facts.observability_env)
    functional_env = esc("FUNCTIONAL_ENV_NOTE", facts.functional_env)
    tool_version = esc("TOOL_VERSION", facts.tool_version)
    tool_version_src = esc("TOOL_VERSION_SRC", facts.tool_version_source)
    image = esc("IMAGE", facts.image)
    image_arch = esc("IMG_ARCH", facts.image_arch)
    entrypoint = esc("ENTRYPOINT", facts.entrypoint)
    emulated = esc("EMULATED", facts.emulated)
    measured_process = esc("main_comm", facts.measured_process)
    security_profile = esc("SECURITY_PROFILE", facts.security_profile)
    security_provider = esc("SECURITY_PROVIDER_VALUE", facts.security_provider)
    security_network = esc("SECURITY_NETWORK", facts.docker_network)
    security_mtu = esc("SECURITY_MTU", facts.network_mtu)
    security_policy_sha = esc("SECURITY_POLICY_SHA", facts.firewall_policy_sha256)
    log_driver = esc("DOCKER_LOG_DRIVER", facts.docker_log_driver)
    log_config_sha = esc("DOCKER_LOG_CONFIG_SHA", facts.docker_log_config_sha256)
    log_option_keys = esc("DOCKER_LOG_OPTION_KEYS_B64", facts.docker_log_option_keys_base64)
    arch = esc("ARCH", facts.arch)
    cores = esc("CORES", facts.cores)
    ram_gb = esc("RAM_GB", facts.ram_gb)
    host_kernel = esc("HOST_KERNEL", facts.host_kernel)
    runner_location = esc("RUNNER_LOC", facts.runner_location)
    bucket = esc("BUCKET", facts.bucket)
    region = esc("REGION", facts.region)
    prefix = esc("PREFIX", facts.prefix)
    registry_path = esc("REG_PATH", facts.registry_path)
    registry_sha = esc("REG_DIGEST", facts.registry_sha256)
    manifest = esc("MANIFEST", facts.manifest)
    manifest_sha = esc("MANIFEST_SHA", facts.manifest_sha256)
    snapshot_date = esc("SNAPSHOT_DATE", facts.snapshot_date)
    manifest_keys = esc("MANIFEST_KEYS", facts.manifest_keys)
    peak_rss_mb = esc("peak_rss_mb", _megabytes(facts.peak_rss_kb, 1024))
    cg_peak_mb = esc("cg_peak_mb", _megabytes(facts.cgroup_peak_bytes, 1048576))
    rss_samples = esc("rss_samples", facts.rss_samples)
    cg_samples = esc("cg_samples", facts.cgroup_samples)
    poll_ms = esc("POLL_MS", facts.poll_ms)
    redaction_changed = esc("REDACTION_CHANGED", facts.redaction_changed_bytes)
    timeout = esc("TIMEOUT", facts.timeout)
    docker_control_timeout = esc("DOCKER_CONTROL_TIMEOUT_S", facts.docker_control_timeout_s)
    docker_cleanup_timeout = esc("DOCKER_CLEANUP_TIMEOUT_S", facts.docker_cleanup_timeout_s)
    stdout_note = esc("payload_stdout", stdout.note)
    stderr_note = esc("payload_stderr", stderr.note)
    invocation = md_safe_block("invocation", facts.invocation)
    shape = md_safe_block("shape", facts.shape)
    development = facts.security_profile == "local-development-unisolated"

    timeout_note = f" — **killed at the {timeout}s timeout**" if facts.timed_out == "1" else ""
    obs_cell = "none" if obs_env == "none" else f"`{obs_env}` — recorded verbatim"
    functional_cell = (
        "none"
        if functional_env == "none"
        else f"`{functional_env}` — validated tool configuration, recorded verbatim"
    )
    if tool_version:
        version_row = f"| Tool version | `{tool_version}` — {tool_version_src} |\n"
    else:
        version_row = (
            f"| Tool version | _(TODO: {tool_version_src} — "
            "agent records from the tool manually)_ |\n"
        )
    prefix_row = (
        f"| Prefix scope | `{prefix}` |\n" if prefix else "| Prefix scope | full bucket |\n"
    )

    out = f"# Smoke receipt — `{tool}` / mode `{mode}`\n\n"
    out += (
        "Produced by `harness/smoke-run.sh`. Not a benchmark: this run makes no\n"
        "comparative claim and its duration is a fact about this run only.\n\n"
    )
    if development:
        out += (
            "**Local development capture:** the manifest and host-firewall gate were not\n"
            "checked. This receipt is not evidentiary or correctness-verified.\n\n"
        )

    out += "## Result\n\n| | |\n| --- | --- |\n"
    out += f"| Date (UTC) | {utc_start} |\n"
    out += f"| Exit code | `{exit_code}`{timeout_note} |\n"
    out += f"| Wall-clock | {wall}s (container lifetime, StartedAt→FinishedAt) |\n"
    out += f"| Auth mode | `{auth}` — {env_note} |\n"
    out += f"| Observability env (--env) | {obs_cell} |\n"
    out += f"| Functional env (--env) | {functional_cell} |\n"
    out += f"| Verifier verdict | {VERDICT_PLACEHOLDER} |\n"
    out += version_row

    out += f"\n## Invocation\n\n<pre><code>{invocation}</code></pre>\n\n"
    out += (
        "Serialized from the same argv array that was executed — not reconstructed.\n"
        "Container is created under a stable wrapper-owned name, then started detached,"
        " so the wrapper can sample memory and read the\n"
        "cgroup while the process lives; it is removed by the wrapper afterwards.\n"
    )

    out += "\n## Subject\n\n| | |\n| --- | --- |\n"
    out += f"| Image | `{image}` |\n"
    out += f"| Image arch | `{image_arch}` |\n"
    out += f"| Entrypoint override | {entrypoint or 'none'} |\n"
    out += f"| Emulated | {emulated} |\n"
    out += f"| Measured process | `{measured_process}` (container main process) |\n"

    out += "\n## Security boundary\n\n| | |\n| --- | --- |\n"
    out += f"| Profile | `{security_profile}` |\n"
    out += f"| Provider adapter | `{security_provider}` |\n"
    if development:
        out += (
            f"| Docker network | `{security_network}` (ordinary Docker bridge,"
            f" MTU {security_mtu}) |\n"
        )
        out += "| Firewall policy | not checked — local development only |\n"
    else:
        out += (
            f"| Docker network | `{security_network}` (user-defined bridge,"
            f" MTU {security_mtu}) |\n"
        )
        out += f"| Firewall policy | sha256 `{security_policy_sha}` |\n"
    out += (
        "| Container hardening | `--pull=never`; `--cap-drop ALL`; "
        "`--security-opt no-new-privileges:true` |\n"
    )
    out += (
        f"| Docker control bounds | {docker_control_timeout}s ordinary calls;"
        f" {docker_cleanup_timeout}s cleanup calls |\n"
    )
    out += (
        f"| Docker logging | driver `{log_driver}`; canonical config sha256"
        f" `{log_config_sha}`; option keys (base64) `{log_option_keys}` |\n"
    )
    if development:
        out += (
            "| Development log limit | 1 GiB, one local file. Output at or above this"
            " limit is not completeness-eligible. |\n"
        )

    out += "\n## Box\n\n| | |\n| --- | --- |\n"
    out += f"| Arch | `{arch}` |\n"
    out += f"| Cores | {cores} |\n"
    out += f"| RAM | {ram_gb} GB |\n"
    out += f"| Kernel | `{host_kernel}` |\n"
    out += f"| Runner location | `{runner_location}` |\n"
    out += (
        "\n> Runner location is recorded because RTT sets the ratio of network\n"
        "> time to CPU time in a listing run: a runner outside the bucket region\n"
        "> can mask per-page CPU cost that would be significant in-region. For an\n"
        "> RTT-bound tool it does **not** bias serial-vs-parallel comparison — to\n"
        "> first order that ratio is the concurrency factor — but client CPU,\n"
        "> output back-pressure, and throttling can pull real ratios below it.\n"
        "> Recorded so a reader can judge; irrelevant at smoke scale, which\n"
        "> produces no comparative numbers.\n"
    )

    out += "\n## Bucket\n\n| | |\n| --- | --- |\n"
    out += f"| Bucket | `{bucket}` |\n"
    out += f"| Region | `{region}` |\n"
    out += prefix_row
    out += f"| Registry | `{registry_path}` (sha256 `{registry_sha}`) |\n"
    out += f"| Manifest | `{manifest}` |\n"
    if development:
        out += (
            f"| Manifest sha256 | `{manifest_sha}` — registry expectation only;"
            " file not checked |\n"
        )
    else:
        out += (
            f"| Manifest sha256 | `{manifest_sha}` — verified against the file"
            " before this run |\n"
        )
    out += f"| Snapshot date | {snapshot_date} |\n"
    out += f"| Manifest keys | {manifest_keys} |\n"
    shape_qualifier = ", not verified" if development else ""
    out += f"\n### Measured shape (from the registry{shape_qualifier})\n\n<pre>{shape}</pre>\n"

    out += "\n## Memory\n\n| | | |\n| --- | --- | --- |\n"
    out += (
        f"| `peak_rss` | {peak_rss_mb} MB | `VmHWM` of the container's main process,"
        f" {rss_samples} successful samples. **Main process only** — a multi-process"
        " fan-out mode's children are not included. |\n"
    )
    out += (
        f"| `cgroup_peak_mem` | {cg_peak_mb} MB | cgroup v2 `memory.peak`, whole container"
        f" tree, {cg_samples} successful samples. **Page cache and kernel/socket memory"
        " included. Never present this as RSS.** |\n"
    )
    out += (
        f"\n**Both numbers are sampled**, polled every {poll_ms} ms. Each is a\n"
        "kernel-maintained high-water mark, so a poll returns the true peak as of\n"
        "that read; the unmeasured window is between the last poll and process\n"
        "exit. The container cgroup is destroyed at exit, so neither can be read\n"
        "post-mortem. `unavailable` means the value was never successfully read —\n"
        "it is not zero, and it is not a finding about the tool.\n\n"
    )
    out += (
        "**Neither number bounds the other, and neither is a sanity check on the\n"
        "other.** `VmHWM` counts pages resident in the main process, including\n"
        "shared/file-backed pages that may be charged to a **different** cgroup;\n"
        "`memory.peak` counts memory charged to **this** cgroup and excludes pages\n"
        "charged elsewhere. `peak_rss` > `cgroup_peak_mem` is normal where the\n"
        "image is already hot in page cache.\n"
    )

    out += "\n## API call count\n\n"
    out += (
        "_(TODO: agent fills in where the tool exposes a counter; otherwise\n"
        '"not exposed" — request-shape capture defers to the replay-server phase.)_\n'
    )

    out += "\n## Raw output\n\n"
    out += f"- stdout: {stdout_note}\n"
    out += f"- stderr: {stderr_note}\n"
    out += f"- Redaction altered bytes: **{redaction_changed}**\n"
    for payload in (stdout, stderr):
        if payload.truncated == "yes":
            out += (
                f"- **{payload.stream} TRUNCATED at the {PAYLOAD_CAP}-byte (64 MiB) cap —"
                f" {payload.dropped_bytes} bytes dropped (head kept).**\n"
            )
    if stdout.truncated == "yes" or stderr.truncated == "yes":
        out += (
            "\n> **Truncation warning.** A capped stream is incomplete by construction. The\n"
            "> verifier refuses a completeness verdict on any mode whose *verified* payload was\n"
            "> truncated (a cut-off listing cannot prove it listed everything); truncation of\n"
            "> stderr alone does not block verifying a complete stdout listing.\n"
        )
    out += (
        "\nRedacted and secret-scanned **before** hashing: the hash freezes the bytes,\n"
        "so redaction after it would redact nothing. Machine-readable binding for the\n"
        "verifier is in `run.meta`.\n"
    )
    return out.encode()
