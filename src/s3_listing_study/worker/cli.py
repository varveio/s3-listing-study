"""Strict logical-request CLI for the single attempt engine."""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from s3_listing_study.common.argparse_utils import UniqueStoreAction
from s3_listing_study.common.build_selection import BuildSelectionError
from s3_listing_study.common.command_adapter import CommandAdapterError, CommandRequest

from .driver import resolve_invocation
from .engine import (
    CREDENTIAL_ENV_VAR,
    AttemptError,
    AttemptOptions,
    CampaignProvenance,
    DeclaredResources,
    parse_credential_env,
    run_attempt,
)
from .upload import UploadError, upload_attempt

# A listing outcome and a publishing outcome are different facts. The attempt's
# own verdict is sealed in result.json before any of this runs, so a failure to
# count or upload must not be reported as a failed attempt.
POST_ATTEMPT_EXIT = 3
NORMALIZER_PATH = Path("/opt/s3-listing-study/tool/normalize.py")
CASE_ENV_KEYS = frozenset(("JAVA_TOOL_OPTIONS", "NODE_OPTIONS"))


def _normalizer_path(tool: str) -> Path:
    """Use the staged adapter in-image, or its checkout path for local tests."""
    if NORMALIZER_PATH.is_file():
        return NORMALIZER_PATH
    return Path.cwd() / "tools" / tool / "adapter" / "normalize.py"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="/opt/s3-listing-study/attempt.pyz",
        description="Resolve and run one image-owned logical listing request.",
        allow_abbrev=False,
    )
    parser.add_argument("--request-schema", action=UniqueStoreAction, choices=("2",), default="2")
    parser.add_argument(
        "--output", action=UniqueStoreAction, default=os.environ.get("S3_STUDY_ATTEMPT_OUT")
    )
    parser.add_argument("--timeout", action=UniqueStoreAction, default="300.0")
    parser.add_argument("--term-grace", action=UniqueStoreAction, default="5.0")
    parser.add_argument("--tool", action=UniqueStoreAction, required=True)
    parser.add_argument("--tool-version", action=UniqueStoreAction)
    parser.add_argument("--derived-image", action=UniqueStoreAction, required=True)
    parser.add_argument("--shared-base-digest", action=UniqueStoreAction, required=True)
    parser.add_argument("--shared-base-uri", action=UniqueStoreAction, required=True)
    parser.add_argument("--harness-revision", action=UniqueStoreAction)
    parser.add_argument("--operation", action=UniqueStoreAction, choices=("list",), required=True)
    parser.add_argument(
        "--auth",
        action=UniqueStoreAction,
        choices=("anonymous", "authenticated"),
        default="anonymous",
    )
    parser.add_argument("--mode", action=UniqueStoreAction, required=True)
    parser.add_argument("--bucket", action=UniqueStoreAction, required=True)
    parser.add_argument("--region", action=UniqueStoreAction, required=True)
    parser.add_argument("--prefix", action=UniqueStoreAction, default="")
    parser.add_argument("--scope", action=UniqueStoreAction)
    parser.add_argument("--concurrency", action=UniqueStoreAction)
    parser.add_argument(
        "--case-env",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="repeatable managed-runtime environment selected by the resolved case",
    )
    parser.add_argument("--campaign-id", action=UniqueStoreAction)
    parser.add_argument("--job-id", action=UniqueStoreAction)
    parser.add_argument("--case-id", action=UniqueStoreAction)
    parser.add_argument("--case-fingerprint", action=UniqueStoreAction)
    parser.add_argument("--attempt-fingerprint", action=UniqueStoreAction)
    parser.add_argument("--run-ordinal", action=UniqueStoreAction)
    parser.add_argument("--submission-number", action=UniqueStoreAction)
    parser.add_argument("--machine-type", action=UniqueStoreAction)
    parser.add_argument("--vcpus", action=UniqueStoreAction)
    parser.add_argument("--memory-gb", action=UniqueStoreAction)
    parser.add_argument(
        "--container-memory-gb",
        action=UniqueStoreAction,
        help="positive GB value, or 'none' for an unconstrained campaign case",
    )
    # Optional on purpose. Omitting it is what makes a local run — the repo's
    # own smoke campaign — need no bucket, no credentials, and no network
    # beyond the one the subject itself uses.
    parser.add_argument(
        "--destination",
        action=UniqueStoreAction,
        help=(
            "manager-assigned gs://.../case/run-N prefix; the worker appends its UUID leaf; "
            "omit to keep the attempt local"
        ),
    )
    return parser


def _parse_numbers(timeout_raw: str, term_grace_raw: str) -> tuple[float, float]:
    try:
        timeout = float(timeout_raw)
        term_grace = float(term_grace_raw)
    except ValueError:
        raise CommandAdapterError("--timeout and --term-grace must be numbers") from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise CommandAdapterError("--timeout must be a finite number greater than zero")
    if not math.isfinite(term_grace) or term_grace < 0:
        raise CommandAdapterError("--term-grace must be a finite nonnegative number")
    return timeout, term_grace


def _parse_concurrency(raw: str | None) -> int | None:
    if raw is None:
        return None
    if re.fullmatch(r"[0-9]+", raw) is None:
        raise CommandAdapterError("--concurrency must be an ASCII integer")
    return int(raw)


def _positive_int(option: str, raw: str) -> int:
    if re.fullmatch(r"[0-9]+", raw) is None or int(raw) < 1:
        raise CommandAdapterError(f"{option} must be a positive ASCII integer")
    return int(raw)


def _parse_case_env(values: Sequence[str], capsule_env: Mapping[str, str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw in values:
        if "\x00" in raw or "=" not in raw:
            raise CommandAdapterError("--case-env must be NAME=VALUE without NUL bytes")
        name, value = raw.split("=", 1)
        if name not in CASE_ENV_KEYS:
            allowed = "|".join(sorted(CASE_ENV_KEYS))
            raise CommandAdapterError(f"--case-env key must be one of {allowed}: {name!r}")
        if not value:
            raise CommandAdapterError(f"--case-env {name} value must not be empty")
        if name in parsed:
            raise CommandAdapterError(f"--case-env repeats {name}")
        if name in capsule_env:
            raise CommandAdapterError(f"--case-env {name} collides with capsule environment")
        parsed[name] = value
    return parsed


def _parse_campaign(args: argparse.Namespace) -> CampaignProvenance | None:
    names = (
        "campaign_id",
        "job_id",
        "case_id",
        "case_fingerprint",
        "attempt_fingerprint",
        "run_ordinal",
        "submission_number",
        "machine_type",
        "vcpus",
        "memory_gb",
        "container_memory_gb",
    )
    present = [name for name in names if getattr(args, name) is not None]
    if not present:
        return None
    if len(present) != len(names):
        missing = ", ".join(f"--{name.replace('_', '-')}" for name in names if name not in present)
        raise CommandAdapterError(f"campaign provenance is all-or-none; missing {missing}")
    container_raw = str(args.container_memory_gb)
    container_memory = (
        None if container_raw == "none" else _positive_int("--container-memory-gb", container_raw)
    )
    return CampaignProvenance(
        campaign_id=str(args.campaign_id),
        job_id=str(args.job_id),
        case_id=str(args.case_id),
        case_fingerprint=str(args.case_fingerprint),
        attempt_fingerprint=str(args.attempt_fingerprint),
        run_ordinal=_positive_int("--run-ordinal", str(args.run_ordinal)),
        submission_number=_positive_int("--submission-number", str(args.submission_number)),
        resources=DeclaredResources(
            machine_type=str(args.machine_type),
            vcpus=_positive_int("--vcpus", str(args.vcpus)),
            memory_gb=_positive_int("--memory-gb", str(args.memory_gb)),
            container_memory_gb=container_memory,
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output is None:
        print("attempt-runner: --output or S3_STUDY_ATTEMPT_OUT is required", file=sys.stderr)
        return 2
    try:
        timeout, term_grace = _parse_numbers(args.timeout, args.term_grace)
        concurrency = _parse_concurrency(args.concurrency)
        campaign = _parse_campaign(args)
        credential_blob = os.environ.get(CREDENTIAL_ENV_VAR)
        if args.auth == "authenticated":
            if not credential_blob:
                raise CommandAdapterError(f"--auth authenticated requires {CREDENTIAL_ENV_VAR}")
            credential_env = parse_credential_env(credential_blob)
        elif credential_blob:
            raise CommandAdapterError(f"{CREDENTIAL_ENV_VAR} is set but --auth is anonymous")
        else:
            credential_env = None
        # The engine, not the adapter, owns where native output may land: a
        # container-local path an adapter picked for itself is output no attempt
        # record can account for. Removed with the container either way.
        with tempfile.TemporaryDirectory(prefix="s3-study-sink-") as sink_dir:
            request = CommandRequest(
                mode=args.mode,
                bucket=args.bucket,
                region=args.region,
                prefix=args.prefix,
                tool=args.tool,
                operation=args.operation,
                auth=args.auth,
                concurrency=concurrency,
                sink_dir=sink_dir,
            )
            invocation = resolve_invocation(request)
            case_env = _parse_case_env(args.case_env, invocation.functional_env)
            result, runner_exit = run_attempt(
                AttemptOptions(
                    output=Path(args.output),
                    argv=invocation.argv,
                    timeout_s=timeout,
                    adapter_bundle_sha256=invocation.adapter_bundle_sha256,
                    term_grace_s=term_grace,
                    tool=args.tool,
                    tool_version=args.tool_version,
                    shared_base_digest=args.shared_base_digest,
                    shared_base_uri=args.shared_base_uri,
                    shared_base_source_sha256=invocation.shared_base_source_sha256,
                    tool_build_sha256=invocation.tool_build_sha256,
                    tool_artifact=invocation.tool_artifact,
                    derived_image=args.derived_image,
                    subject_workdir=invocation.subject_workdir,
                    harness_revision=args.harness_revision,
                    operation=args.operation,
                    auth=args.auth,
                    mode=args.mode,
                    bucket=args.bucket,
                    region=args.region,
                    prefix=args.prefix,
                    scope=args.scope,
                    concurrency=concurrency,
                    sink_dir=sink_dir,
                    normalizer_path=_normalizer_path(args.tool),
                    campaign=campaign,
                    results_destination=args.destination,
                    credential_env=credential_env,
                    functional_env={**invocation.functional_env, **case_env},
                )
            )
    except (AttemptError, BuildSelectionError, CommandAdapterError, OSError) as exc:
        print(f"attempt-runner: {exc}", file=sys.stderr)
        return 2
    if args.destination is None:
        return runner_exit
    return _publish(Path(args.output), args.destination, result, runner_exit)


def _publish(
    attempt_dir: Path,
    destination: str,
    result: dict[str, object],
    runner_exit: int,
) -> int:
    """Upload a finalized attempt; report an upload failure separately.

    Everything here runs after the engine stopped the clock, read ``getrusage``
    and joined the disk sampler, so none of it can reach a figure the attempt
    reports. A failure to upload is reported as ``POST_ATTEMPT_EXIT`` rather
    than as a listing outcome: the attempt itself either ran or it did not, and
    that verdict is already sealed in ``result.json``.

    The worker has already counted locally and sealed the summary in
    ``result.json``. Campaign uploads receive the manager's deterministic
    ``run-N`` prefix and add the worker UUID leaf. Managers discover immediate child
    prefixes with GCS delimiter listing, then fetch only each ``result.json``.
    """
    # The execution leaf is named here because only this process knows the attempt id
    # it minted — a submitter-chosen leaf would be written twice by a task
    # re-execution, after the run was already paid for.
    try:
        leaf = str(result["attempt_id"])
        uploaded = upload_attempt(attempt_dir, f"{destination.rstrip('/')}/{leaf}")
    except (UploadError, OSError) as exc:
        print(f"attempt-runner: upload failed: {exc}", file=sys.stderr)
        return POST_ATTEMPT_EXIT
    for blob_path in uploaded:
        print(f"attempt-runner: uploaded {blob_path}", file=sys.stderr)
    return runner_exit
