"""``python -m s3_listing_study.receipt`` — the wrapper's offline half.

``harness/smoke-run.sh`` keeps everything that runs against the host in order —
Docker lifecycle, argv, platform selection, the timeout, the cleanup trap, the
memory sampling loop — and calls in here for the data-shaped work. Every
subcommand runs either **before** the container is created or **after** it has
exited; none is on the measured clock, which is Docker's own
``FinishedAt`` minus ``StartedAt`` and the container's cgroup, never a wrapper-side
timer.

Operator-facing warnings keep the ``smoke-run:`` prefix. The split is an
implementation detail of one wrapper; a reader watching a run should not have to
learn that the truncation warning now comes from a different process.

Exit codes on the wrapper's path (``registry``, ``finish``): ``0`` clean, ``2``
harness error, and never ``1`` — nothing in here is a tool result, so nothing in
here may be reported as one. That is enforced rather than asserted: an
unexpected exception would otherwise leave the interpreter's own ``1``, which
``smoke-run.sh`` reserves for the subject tool, so :func:`main` maps anything
unhandled to ``2``. The standalone ``scan-tree`` subcommand is not part of a run
and keeps grep's three outcomes, ``1`` among them.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from s3_listing_study.registry import FIELDS, Registry, RegistryError

from .errors import ReceiptError
from .meta import RunFacts
from .meta import render as render_meta
from .redact import PAYLOAD_CAP, Payload, place, redact_file, truncate_head
from .render import ReceiptBlockError, render
from .scan import Outcome, TreeScanError, scan_file, scan_tree

STREAMS = ("stdout", "stderr")


def say(message: str) -> None:
    print(f"smoke-run: {message}", file=sys.stderr)


def _registry(args: argparse.Namespace) -> int:
    registry = Registry.load(Path(args.registry))
    if args.list_buckets:
        for name in registry.bucket_names():
            print(name)
        return 0
    bucket = registry.bucket(args.bucket)
    if args.field:
        print(bucket.field(args.field))
        return 0
    # The single-line facts the shell itself needs, plus the registry identity a
    # receipt binds to. `shape` is multi-line and is asked for by name.
    print(f"path={registry.path}")
    print(f"digest={registry.digest}")
    for field in ("region", "manifest", "manifest_sha256", "snapshot_date", "keys"):
        print(f"{field}={bucket.field(field)}")
    return 0


def _scan_or_die(path: Path, label: str) -> None:
    outcome = scan_file(path)
    if outcome is Outcome.FLAGGED:
        raise ReceiptError(
            f"secret scan flagged {label} — refusing to stage. Inspect before anything else."
        )
    if outcome is Outcome.ERROR:
        raise ReceiptError(
            f"secret scan errored on {label}. A scanner error is not a pass — refusing to stage."
        )


def _scan_or_quarantine(path: Path, label: str, out: Path) -> None:
    """Payload variant: on a flag, QUARANTINE the bytes before dying.

    ``$OUT/quarantine`` is outside the wrapper's ``$TMP``, so the cleanup trap
    never touches it and the evidence survives for inspection rather than being
    removed by the very failure that found it.
    """
    outcome = scan_file(path)
    if outcome is Outcome.FLAGGED:
        quarantine = out / "quarantine"
        quarantine.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, quarantine / label)
        raise ReceiptError(
            f"secret scan flagged the REDACTED {label} stream — a credential-shaped value"
            " survived redaction.\n"
            f"        Quarantined to: {quarantine / label}\n"
            "        Refusing to stage. Inspect the quarantined file before anything else."
        )
    if outcome is Outcome.ERROR:
        raise ReceiptError(
            f"secret scan errored on {label}. A scanner error is not a pass — refusing to stage."
        )


def _finish(args: argparse.Namespace) -> int:
    tmp = Path(args.tmp)
    out = Path(args.out)
    data_dir = Path(args.data_dir)

    # --- redact (full) → scan (full) → truncate → hash ------------------------
    # Redaction runs over the WHOLE raw stream first (a legitimate object key
    # shaped like a credential is scrubbed rather than falsely flagged), then the
    # secret scan runs over the FULL REDACTED stream — every byte, BEFORE
    # truncation — so a credential the redaction did not catch is still flagged
    # even when it sits beyond the 64 MiB cap, instead of being silently dropped
    # with the truncated tail. Anonymous runs sign nothing, so redaction is a
    # no-op in the normal case and any change is surfaced loudly.
    redaction_changed = False
    for stream in STREAMS:
        if redact_file(tmp / f"{stream}.raw", tmp / f"{stream}.txt"):
            redaction_changed = True
            say(
                f"WARNING: redaction altered {stream} bytes. Either a real secret was present,"
                " or the\n        scrubber corrupted legitimate data. The verifier's verdict on"
                " this payload is\n        NOT trustworthy until the orchestrator reviews it."
            )
        _scan_or_quarantine(tmp / f"{stream}.txt", stream, out)

    # Truncation is recorded loudly: the verifier refuses a completeness verdict
    # on a truncated stream. The full redacted stream was already scanned, so the
    # dropped tail cannot hide a secret.
    dropped = {}
    for stream in STREAMS:
        dropped[stream] = truncate_head(tmp / f"{stream}.txt", PAYLOAD_CAP)
        if dropped[stream]:
            say(
                f"[{args.tool}/{args.mode}] {stream} exceeded {PAYLOAD_CAP}-byte cap —"
                f" TRUNCATED, dropped {dropped[stream]} bytes (head kept)."
            )

    payloads: dict[str, Payload] = {
        stream: place(
            stream=stream,
            source=tmp / f"{stream}.txt",
            out=out,
            data_dir=data_dir,
            tool=args.tool,
            mode=args.mode,
            bucket=args.bucket,
            prefix=args.prefix,
            auth=args.auth,
            truncated=bool(dropped[stream]),
            dropped_bytes=dropped[stream],
        )
        for stream in STREAMS
    }

    facts = RunFacts(
        tool=args.tool,
        mode=args.mode,
        auth=args.auth,
        bucket=args.bucket,
        region=args.region,
        prefix=args.prefix,
        image=args.image,
        image_arch=args.image_arch,
        host_arch=args.host_arch,
        entrypoint=args.entrypoint,
        measured_process=args.measured_process,
        security_profile=args.security_profile,
        security_provider=args.security_provider,
        docker_network=args.docker_network,
        network_mtu=args.network_mtu,
        firewall_policy_sha256=args.firewall_policy_sha256,
        container_cap_drop=args.container_cap_drop,
        container_no_new_privileges=args.container_no_new_privileges,
        docker_pull_policy=args.docker_pull_policy,
        docker_control_timeout_s=args.docker_control_timeout_s,
        docker_cleanup_timeout_s=args.docker_cleanup_timeout_s,
        docker_log_driver=args.docker_log_driver,
        docker_log_config_sha256=args.docker_log_config_sha256,
        docker_log_option_keys_base64=args.docker_log_option_keys_base64,
        exit_code=args.exit_code,
        timed_out=args.timed_out,
        wall_clock_s=args.wall_clock_s,
        registry_path=args.registry_path,
        registry_sha256=args.registry_sha256,
        manifest=args.manifest,
        manifest_sha256=args.manifest_sha256,
        snapshot_date=args.snapshot_date,
        manifest_keys=args.manifest_keys,
        shape=args.shape,
        passed_env=args.passed_env,
        observability_env=args.observability_env,
        functional_env=args.functional_env,
        tool_version=args.tool_version,
        tool_version_source=args.tool_version_source,
        redaction_changed_bytes="yes" if redaction_changed else "no",
        peak_rss_kb=args.peak_rss_kb,
        cgroup_peak_bytes=args.cgroup_peak_bytes,
        rss_samples=args.rss_samples,
        cgroup_samples=args.cgroup_samples,
        poll_ms=args.poll_ms,
        utc_start=args.utc_start,
        runner_location=args.runner_location,
        arch=args.arch,
        cores=args.cores,
        ram_gb=args.ram_gb,
        host_kernel=args.host_kernel,
        invocation=args.invocation,
        emulated=args.emulated,
        env_note=args.env_note,
        timeout=args.timeout,
    )

    (tmp / "run.meta").write_bytes(render_meta(facts, payloads["stdout"], payloads["stderr"]))
    (tmp / "receipt.md").write_bytes(render(facts, payloads["stdout"], payloads["stderr"]))

    # Scan in TMP, place only after passing. Writing the receipt into $OUT and
    # then scanning it detects a leak but does not prevent staging it — the
    # secret-bearing file is already sitting in the receipt directory.
    # Scan-then-place, never place-then-scan: the same ordering rule as
    # redact-before-hash.
    _scan_or_die(tmp / "receipt.md", str(tmp / "receipt.md"))
    _scan_or_die(tmp / "run.meta", str(tmp / "run.meta"))
    shutil.move(str(tmp / "receipt.md"), str(out / "receipt.md"))
    shutil.move(str(tmp / "run.meta"), str(out / "run.meta"))
    return 0


def _scan_tree(args: argparse.Namespace) -> int:
    """Three outcomes, never conflated: 0 clean, 1 flagged, 2 scanner error."""
    try:
        flagged, scanned = scan_tree(Path(args.directory))
    except TreeScanError as exc:
        print(f"scan-tree: {exc}", file=sys.stderr)
        return 2
    for path in flagged:
        print(f"scan-tree: FLAGGED {path}", file=sys.stderr)
    if flagged:
        print(
            f"scan-tree: {len(flagged)} of {scanned} file(s) FLAGGED — quarantine and inspect,"
            " never delete evidence.",
            file=sys.stderr,
        )
        return 1
    print(f"scan-tree: clean — {scanned} file(s) scanned, 0 flagged.", file=sys.stderr)
    return 0


_FACT_ARGS = (
    "tool",
    "mode",
    "auth",
    "bucket",
    "region",
    "prefix",
    "image",
    "image-arch",
    "host-arch",
    "entrypoint",
    "measured-process",
    "security-profile",
    "security-provider",
    "docker-network",
    "network-mtu",
    "firewall-policy-sha256",
    "container-cap-drop",
    "container-no-new-privileges",
    "docker-pull-policy",
    "docker-control-timeout-s",
    "docker-cleanup-timeout-s",
    "docker-log-driver",
    "docker-log-config-sha256",
    "docker-log-option-keys-base64",
    "exit-code",
    "timed-out",
    "wall-clock-s",
    "registry-path",
    "registry-sha256",
    "manifest",
    "manifest-sha256",
    "snapshot-date",
    "manifest-keys",
    "shape",
    "passed-env",
    "observability-env",
    "functional-env",
    "tool-version",
    "tool-version-source",
    "peak-rss-kb",
    "cgroup-peak-bytes",
    "rss-samples",
    "cgroup-samples",
    "poll-ms",
    "utc-start",
    "runner-location",
    "arch",
    "cores",
    "ram-gb",
    "host-kernel",
    "invocation",
    "emulated",
    "env-note",
    "timeout",
)
"""Every run fact the wrapper observed, passed as argv.

argv, not a file: a value carrying a newline is refused by
:func:`~s3_listing_study.receipt.meta.reject_control` rather than silently
becoming two lines of something, and a reader can see in the wrapper exactly
which measured value reaches which receipt field.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="s3-listing-study receipt")
    sub = parser.add_subparsers(dest="command", required=True)

    registry = sub.add_parser("registry", help="resolve registry facts for a bucket")
    # Explicit at the call site, deliberately: the registry a receipt cites is
    # never redirectable through the environment.
    registry.add_argument("--registry", required=True)
    registry.add_argument("--bucket")
    registry.add_argument("--field", choices=FIELDS)
    registry.add_argument("--list-buckets", action="store_true")

    finish = sub.add_parser("finish", help="redact, scan, hash, place, and render one run")
    finish.add_argument("--tmp", required=True)
    finish.add_argument("--out", required=True)
    finish.add_argument("--data-dir", required=True)
    for name in _FACT_ARGS:
        finish.add_argument(f"--{name}", required=True)

    tree = sub.add_parser("scan-tree", help="credential value-shape scan over a tree")
    tree.add_argument("directory")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        if args.command == "registry":
            if not args.list_buckets and not args.bucket:
                print("receipt: registry needs --bucket or --list-buckets", file=sys.stderr)
                return 2
            return _registry(args)
        if args.command == "finish":
            return _finish(args)
        return _scan_tree(args)
    except (ReceiptError, ReceiptBlockError, RegistryError) as exc:
        print(f"receipt: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # A non-integer measurement reaching int(), an ENOSPC mid-copy: anything
        # unforeseen is still a harness error, and exits like one.
        print(f"receipt: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
