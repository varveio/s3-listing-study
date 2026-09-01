"""GCP Batch request rendering and provider lifecycle commands."""

from __future__ import annotations

import argparse
import copy
import json
import re
import shlex
import sqlite3
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from google.api_core.exceptions import GoogleAPIError
from google.cloud import batch_v1

from benchmark import batch_client, identity
from benchmark import replay as replay_contract
from benchmark.campaign import (
    EXECUTOR,
    ImageSet,
    _replay_document,
    abandon_slot,
    job_name_for,
    load_image_set,
    result_prefix_for,
    settle_dependents,
    worker_argument_pairs,
)
from benchmark.contract import CREDENTIAL_ENV_VAR, canonical_json
from benchmark.ledger import (
    INSERT_COLUMNS,
    RETRYABLE_STATES,
    TERMINAL_STATES,
    Attempt,
    CampaignError,
    attempt_rows,
    journal_intent,
    ledger,
    ledger_suite,
    set_state,
)

DEADLINE_SLACK_S = 600
"""What the provider deadline adds over the worker's own, for every attempt.

The worker may run an untimed setup exec ahead of the timed subject, each with
its own deadline, and a provider hard-kill takes the container down with all of
its evidence — so this outer bound must never be the one that fires. It is a
safety net rather than a measurement, which is why it is one flat figure and not
a per-mode sum.
"""
REPLAY_READINESS_TIMEOUT_S = 600
"""Allowance for a replay server to derive its serving index before timing."""

HYPERDISK_BOOT_DISK = {
    "type": "hyperdisk-balanced",
    "image": "batch-cos",
}
REPLAY_STAGING_IMAGE = (
    "gcr.io/google.com/cloudsdktool/google-cloud-cli@sha256:"
    "cf72dd63b7643c117ef53378a41bef6db6a01fa3d561f2b456d7abd8bbeb9ba6"
)
# COS mounts /mnt/disks as a small tmpfs until a separate disk is attached.
# The enlarged boot disk's writable capacity is /mnt/stateful_partition.
REPLAY_FIXTURE_HOST_DIR = "/mnt/stateful_partition/replay-fixture"
REPLAY_FIXTURE_CONTAINER_DIR = "/fixtures/source"
REPLAY_FIXTURE_HINTS_NAME = "s3-fast-list-hints.input"
REPLAY_FIXTURE_S5CMD_SHARDS_NAME = "s5cmd-shards.input"
S7CMD_NOFILE = "nofile=1048576:1048576"
SUBJECT_OUTPUT_HOST_DIR = "/mnt/stateful_partition/attempt"
SUBJECT_OUTPUT_CONTAINER_DIR = "/tmp/attempt"
SECRET_RE = re.compile(r"\Aprojects/[^/]+/secrets/[^/]+/versions/[^/]+\Z")
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class BatchOptions:
    anonymous_worker_sa: str
    authenticated_worker_sa: str | None
    network: str | None
    subnetwork: str | None
    zone: str | None
    provisioning: str
    project: str
    location: str
    # The Secret Manager version holding the authenticated stratum's credential
    # payload. Only a signing case's job carries it, and only the authenticated
    # worker identity can read it.
    aws_credential_secret: str | None = None
    term_grace: float = 5.0
    retain_products: bool = False
    boot_disk_size_gb: int = 100

    def service_account_for(self, auth_role: str | None) -> str:
        if auth_role is None:
            return self.anonymous_worker_sa
        if not self.authenticated_worker_sa:
            raise CampaignError(
                f"case signs with role {auth_role} and requires a signing worker service account"
            )
        return self.authenticated_worker_sa

    def secret_for(self, auth_role: str | None) -> str | None:
        if auth_role is None:
            return None
        secret = self.aws_credential_secret
        if not secret or SECRET_RE.fullmatch(secret) is None:
            raise CampaignError(
                f"case signs with role {auth_role} and requires a Secret Manager version resource"
            )
        return secret

    def executor_env(self) -> str:
        """The estate detail a row records and identity deliberately ignores."""
        return canonical_json(
            {
                "project": self.project,
                "provisioning": self.provisioning,
                "boot_disk": {
                    "type": "hyperdisk-balanced",
                    "size_gb": self.boot_disk_size_gb,
                },
                "network": self.network,
                "subnetwork": self.subnetwork,
                "zone": self.zone,
                "retain_products": self.retain_products,
            }
        )


def _validate_batch_options(options: BatchOptions) -> None:
    if not options.anonymous_worker_sa or any(c.isspace() for c in options.anonymous_worker_sa):
        raise CampaignError("anonymous worker service account is required")
    if (options.network is None) != (options.subnetwork is None):
        raise CampaignError("network and subnetwork must be supplied together")
    if options.provisioning not in {"SPOT", "STANDARD"}:
        raise CampaignError("provisioning must be SPOT or STANDARD")
    if (
        isinstance(options.boot_disk_size_gb, bool)
        or not isinstance(options.boot_disk_size_gb, int)
        or options.boot_disk_size_gb < 1
    ):
        raise CampaignError("boot disk size must be a positive integer")
    if (
        options.authenticated_worker_sa
        and options.authenticated_worker_sa == options.anonymous_worker_sa
    ):
        raise CampaignError("authenticated and anonymous service accounts must differ")


def request_argument(document: Mapping[str, Any], name: str) -> str:
    """One `--flag value` pair out of a frozen provider request, or `""`."""
    try:
        commands = _subject_commands(document)
        pairs_list = list(zip(commands[::2], commands[1::2], strict=True))
        if len({key for key, _value in pairs_list}) != len(pairs_list):
            raise ValueError
        pairs = dict(pairs_list)
    except (IndexError, KeyError, TypeError, ValueError):
        raise CampaignError("recorded provider request cannot be read back") from None
    return str(pairs.get(name, ""))


def _subject_commands(document: Mapping[str, Any]) -> list[Any]:
    """Find the one worker runnable without relying on provider runnable order."""
    groups = document["taskGroups"]
    if not isinstance(groups, list) or len(groups) != 1:
        raise ValueError
    runnables = groups[0]["taskSpec"]["runnables"]
    if not isinstance(runnables, list) or not runnables:
        raise ValueError
    candidates: list[list[Any]] = []
    for runnable in runnables:
        commands = runnable["container"]["commands"]
        if not isinstance(commands, list):
            raise ValueError
        if "--attempt-id" not in commands:
            continue
        if len(commands) % 2:
            raise ValueError
        flags = commands[::2]
        if not all(isinstance(flag, str) and flag.startswith("--") for flag in flags):
            raise ValueError
        if len(set(flags)) != len(flags):
            raise ValueError
        if "--attempt-id" not in flags:
            raise ValueError
        candidates.append(commands)
    if len(candidates) != 1:
        raise ValueError
    return candidates[0]


def request_max_run_duration(document: Mapping[str, Any]) -> str:
    """The provider deadline a frozen request was launched under."""
    try:
        return str(document["taskGroups"][0]["taskSpec"]["maxRunDuration"])
    except (IndexError, KeyError, TypeError):
        raise CampaignError("recorded provider request cannot be read back") from None


def _runnable_options(cpuset: str, memory_gb: int | None) -> str:
    options = ["--network", "host", f"--cpuset-cpus={cpuset}"]
    if memory_gb is not None:
        options.extend((f"--memory={memory_gb}g", f"--memory-swap={memory_gb}g"))
    return shlex.join(options)


def _subject_ulimit(tool: str) -> tuple[str, ...]:
    """Fixed process headroom for s7cmd's wide prefix-discovery socket set."""
    return ("--ulimit", S7CMD_NOFILE) if tool == "s7cmd" else ()


def _fixture_staging_script(
    uri: str,
    expected_sha256: str,
    *,
    hints_uri: str | None = None,
    hints_sha256: str | None = None,
    s5cmd_shards_uri: str | None = None,
    s5cmd_shards_sha256: str | None = None,
) -> str:
    """Download a staged fixture and refuse bytes outside its recorded identity.

    The digest is over sorted ``name<TAB>size<TAB>sha256<NL>`` rows for the
    immediate ``*.parquet`` children. The check runs before the replay server
    starts, so mutable storage can serve only the bytes the case named. The
    s3-fast-list hints companion and the s5cmd shards companion are each
    digest-bound the same way: neither is trusted on emptiness alone.
    """
    destination = shlex.quote(REPLAY_FIXTURE_CONTAINER_DIR)
    source = shlex.quote(uri)
    expected = shlex.quote(expected_sha256)
    commands = [
        "set -o pipefail",
        f"mkdir -p {destination}",
        f"gcloud storage cp {source} {destination}/",
        'manifest="$(mktemp)"',
        f"files=({destination}/*.parquet)",
        '[[ -f "${files[0]}" ]] || { echo "no staged parquet files" >&2; exit 1; }',
        'for file in "${files[@]}"; do',
        '  digest="$(sha256sum "$file")"; digest="${digest%% *}"',
        '  size="$(stat -c %s "$file")"',
        '  printf \'%s\\t%s\\t%s\\n\' "${file##*/}" "$size" "$digest"',
        'done | LC_ALL=C sort > "$manifest"',
        'actual="$(sha256sum "$manifest")"; actual="${actual%% *}"',
        f'[[ "$actual" == {expected} ]] || '
        '{ echo "fixture digest mismatch: $actual" >&2; exit 1; }',
        'rm -f "$manifest"',
    ]
    if hints_uri is not None:
        assert hints_sha256 is not None
        hints_source = shlex.quote(hints_uri)
        hints_path = f"{destination}/{REPLAY_FIXTURE_HINTS_NAME}"
        hints_expected = shlex.quote(hints_sha256)
        commands.extend(
            (
                f"gcloud storage cp {hints_source} {hints_path}",
                f"[[ -s {hints_path} ]] || "
                '{ echo "fixture hints are missing or empty" >&2; exit 1; }',
                f'actual="$(sha256sum {hints_path})"; actual="${{actual%% *}}"',
                f'[[ "$actual" == {hints_expected} ]] || '
                '{ echo "fixture hints digest mismatch: $actual" >&2; exit 1; }',
                f"IFS= read -r first_hint < {hints_path}",
                '[[ -n "$first_hint" ]] || '
                '{ echo "fixture hints begin with an empty cut point" >&2; exit 1; }',
            )
        )
    if s5cmd_shards_uri is not None:
        assert s5cmd_shards_sha256 is not None
        shards_source = shlex.quote(s5cmd_shards_uri)
        shards_path = f"{destination}/{REPLAY_FIXTURE_S5CMD_SHARDS_NAME}"
        shards_expected = shlex.quote(s5cmd_shards_sha256)
        commands.extend(
            (
                f"gcloud storage cp {shards_source} {shards_path}",
                f"[[ -s {shards_path} ]] || "
                '{ echo "fixture s5cmd shards are missing or empty" >&2; exit 1; }',
                f'actual="$(sha256sum {shards_path})"; actual="${{actual%% *}}"',
                f'[[ "$actual" == {shards_expected} ]] || '
                '{ echo "fixture s5cmd shard digest mismatch: $actual" >&2; exit 1; }',
                f'while IFS= read -r shard; do [[ -n "$shard" ]] || exit 1; done < {shards_path}',
            )
        )
    return "\n".join(commands)


def _fixture_companion_uri(uri: str, name: str) -> str:
    """The fixed companion object beside a staged multipart fixture."""
    parent, separator, leaf = uri.rpartition("/")
    if not separator or leaf != "part-*.parquet":
        raise CampaignError(
            "fixture-backed companion requires fixture_uri ending in part-*.parquet"
        )
    return f"{parent}/{name}"


def _fixture_hints_uri(uri: str) -> str:
    return _fixture_companion_uri(uri, REPLAY_FIXTURE_HINTS_NAME)


def render_batch_job(
    attempt: Attempt,
    image: Mapping[str, str],
    *,
    suite: str,
    options: BatchOptions,
    artifact_uri: str = "",
    max_run_duration: str = "",
) -> dict[str, Any]:
    """The provider request an attempt freezes, rendered from the row alone."""
    _validate_batch_options(options)
    container_memory = attempt.container_memory_gb
    pairs = worker_argument_pairs(
        attempt,
        image,
        output=SUBJECT_OUTPUT_CONTAINER_DIR,
        destination=attempt.result_prefix,
        term_grace=options.term_grace,
        artifact_uri=artifact_uri,
        retain_products=options.retain_products,
    )
    replay = _replay_document(attempt)
    commands = [item for pair in pairs for item in pair]
    container: dict[str, Any] = {"imageUri": image["image_uri"], "commands": commands}
    subject_runnable: dict[str, Any] = {"container": container}
    output_volume = f"--volume={SUBJECT_OUTPUT_HOST_DIR}:{SUBJECT_OUTPUT_CONTAINER_DIR}"
    output_initializer = {
        "container": {
            "imageUri": image["image_uri"],
            "commands": ["10001:10001", SUBJECT_OUTPUT_CONTAINER_DIR],
            "options": shlex.join(("--user", "0:0", "--entrypoint", "/bin/chown", output_volume)),
        }
    }
    runnables = [output_initializer, subject_runnable]
    if replay is None:
        plain_subject_options = [output_volume]
        if container_memory is not None:
            plain_subject_options.extend(
                (f"--memory={container_memory}g", f"--memory-swap={container_memory}g")
            )
        container["options"] = shlex.join((*plain_subject_options, *_subject_ulimit(attempt.tool)))
    else:
        allocation = replay.allocation
        subject_config = json.loads(attempt.config)
        mode = subject_config.get("mode")
        fixture_hints = attempt.tool == "s3-fast-list" and mode == "list-hinted-fixture"
        fixture_s5cmd_shards = attempt.tool == "s5cmd" and mode == "fanout-fixture-with-dirs"
        if (fixture_hints or fixture_s5cmd_shards) and replay.backend.fixture_uri is None:
            # Otherwise this renders and submits cleanly, and the subject dies at
            # runtime on a companion path staging never mounted -- a burned VM
            # instead of a refusal here, before anything is spent.
            raise CampaignError(
                f"fixture-companion mode {mode!r} requires a staged fixture bundle: "
                "replay.backend.fixture_uri must end in part-*.parquet"
            )
        hints_file_sha256 = subject_config.get("hints_file_sha256")
        if fixture_hints and (
            not isinstance(hints_file_sha256, str) or SHA256_RE.fullmatch(hints_file_sha256) is None
        ):
            raise CampaignError("fixture-backed s3-fast-list requires config.hints_file_sha256")
        s5cmd_shards_sha256 = subject_config.get("shard_file_sha256")
        if fixture_s5cmd_shards and (
            not isinstance(s5cmd_shards_sha256, str)
            or SHA256_RE.fullmatch(s5cmd_shards_sha256) is None
        ):
            raise CampaignError("fixture-backed s5cmd requires config.shard_file_sha256")
        summary = replay_contract.allocation_summary(
            replay,
            box_vcpus=attempt.vcpus,
            box_memory_gb=attempt.memory_gb,
            container_memory_gb=container_memory,
        )
        container["options"] = _runnable_options(summary.subject_cpuset, container_memory)
        backend = replay.backend
        fixture_path = (
            REPLAY_FIXTURE_CONTAINER_DIR
            if backend.fixture_uri is not None
            else f"/fixtures/{attempt.target_bucket}"
        )
        server_commands = [
            "serve",
            "--fixture",
            fixture_path,
            "--bucket",
            attempt.target_bucket,
            "--host",
            "127.0.0.1",
            "--port",
            str(replay_contract.REPLAY_ENDPOINT_PORT),
            "--metrics-port",
            str(replay_contract.REPLAY_METRICS_PORT),
            "--serving-mode",
            backend.serving_mode,
            "--parquet-connections",
            str(allocation.replay_parquet_connections),
            "--max-concurrent-requests",
            str(allocation.replay_max_concurrent_requests),
        ]
        profile_spec = backend.profile_spec
        if profile_spec is not None:
            assert backend.latency_scale is not None
            server_commands.extend(
                (
                    "--inject-latency",
                    profile_spec,
                    "--latency-scale",
                    str(backend.latency_scale),
                )
            )
        server_options = _runnable_options(summary.server_cpuset, allocation.replay_memory_gb)
        subject_options = " ".join(
            part
            for part in (
                _runnable_options(summary.subject_cpuset, container_memory),
                output_volume,
                shlex.join(_subject_ulimit(attempt.tool)),
            )
            if part
        )
        staging_runnable = None
        if backend.fixture_uri is not None:
            volume = f"--volume={REPLAY_FIXTURE_HOST_DIR}:{REPLAY_FIXTURE_CONTAINER_DIR}"
            server_options = f"{server_options} {volume}"
            hints_uri = _fixture_hints_uri(backend.fixture_uri) if fixture_hints else None
            s5cmd_shards_uri = (
                _fixture_companion_uri(backend.fixture_uri, REPLAY_FIXTURE_S5CMD_SHARDS_NAME)
                if fixture_s5cmd_shards
                else None
            )
            if fixture_hints or fixture_s5cmd_shards:
                subject_options = f"{subject_options} {volume}:ro"
            staging_runnable = {
                "container": {
                    "imageUri": REPLAY_STAGING_IMAGE,
                    "commands": [
                        "-ceu",
                        _fixture_staging_script(
                            backend.fixture_uri,
                            backend.fixture_sha256,
                            hints_uri=hints_uri,
                            hints_sha256=(hints_file_sha256 if fixture_hints else None),
                            s5cmd_shards_uri=s5cmd_shards_uri,
                            s5cmd_shards_sha256=(
                                s5cmd_shards_sha256 if fixture_s5cmd_shards else None
                            ),
                        ),
                    ],
                    "options": shlex.join(("--entrypoint", "/bin/bash", volume)),
                },
                # Batch injects /usr/bin/python3, which the slim Cloud CLI
                # image does not contain. Pin its own bundled interpreter.
                "environment": {
                    "variables": {
                        "CLOUDSDK_PYTHON": (
                            "/usr/lib/google-cloud-sdk/platform/bundledpythonunix/bin/python3"
                        )
                    }
                },
            }
        server_runnable = {
            "background": True,
            "container": {
                "imageUri": backend.server_image_uri,
                "commands": server_commands,
                "options": server_options,
            },
            "environment": {
                "variables": {
                    "JAVA_TOOL_OPTIONS": (
                        f"-XX:MaxRAMPercentage={allocation.replay_heap_percent} "
                        "-Dswath.replay.prefetch.enabled="
                        f"{'true' if allocation.replay_prefetch else 'false'} "
                        "-Dswath.replay.prefetch.max-windows="
                        f"{allocation.replay_prefetch_max_windows}"
                    )
                }
            },
        }
        container["options"] = subject_options
        runnables = [output_initializer, server_runnable, subject_runnable]
        if staging_runnable is not None:
            runnables.insert(0, staging_runnable)
    readiness_allowance = REPLAY_READINESS_TIMEOUT_S if replay is not None else 0
    default_duration = (
        attempt.timeout_s + int(options.term_grace) + DEADLINE_SLACK_S + readiness_allowance
    )
    task_spec: dict[str, Any] = {
        "runnables": runnables,
        "computeResource": {
            "cpuMilli": str(attempt.vcpus * 1000),
            "memoryMib": str(attempt.memory_gb * 1024),
        },
        "maxRetryCount": 0,
        "maxRunDuration": max_run_duration or f"{default_duration}s",
    }
    if attempt.secret_resource is not None:
        # One variable, whose payload the worker parses. A case that lists
        # unsigned has no environment block at all.
        credential = {"secretVariables": {CREDENTIAL_ENV_VAR: attempt.secret_resource}}
        if replay is None:
            # Preserve the settled one-runnable request document byte-for-byte.
            task_spec["environment"] = credential
        else:
            subject_runnable["environment"] = credential
    policy: dict[str, Any] = {
        "machineType": attempt.machine_type,
        "provisioningModel": options.provisioning,
    }
    if attempt.machine_type.startswith(("n4-", "c4-", "c4d-")):
        policy["bootDisk"] = {
            **HYPERDISK_BOOT_DISK,
            "sizeGb": str(options.boot_disk_size_gb),
        }
    allocation_policy: dict[str, Any] = {
        "instances": [{"policy": policy}],
        "serviceAccount": {"email": attempt.service_account},
    }
    if options.zone:
        allocation_policy["location"] = {
            "allowedLocations": [f"zones/{options.zone.removeprefix('zones/')}"]
        }
    if options.network and options.subnetwork:
        allocation_policy["network"] = {
            "networkInterfaces": [{"network": options.network, "subnetwork": options.subnetwork}]
        }
    return {
        # The suite itself, so one polling pass filters exactly rather than
        # scanning a shared project for anything benchmark-shaped.
        "labels": {"suite": suite},
        "taskGroups": [{"taskCount": "1", "parallelism": "1", "taskSpec": task_spec}],
        "allocationPolicy": allocation_policy,
        "logsPolicy": {"destination": "CLOUD_LOGGING"},
    }


def retry_request_document(
    previous: dict[str, Any], *, job_name: str, result_prefix: str, attempt_id: str
) -> dict[str, Any]:
    """The frozen request with only the new attempt's identities rewritten."""
    document = copy.deepcopy(previous)
    try:
        commands = _subject_commands(document)
        replacements = {
            "--job-name": job_name,
            "--destination": result_prefix,
            "--attempt-id": attempt_id,
        }
        seen: set[str] = set()
        for index in range(0, len(commands), 2):
            name = commands[index]
            if name in replacements:
                commands[index + 1] = replacements[name]
                seen.add(name)
        if seen != set(replacements):
            raise ValueError
    except (IndexError, KeyError, TypeError, ValueError):
        raise CampaignError("recorded provider request is not safely retryable") from None
    return document


def _submit(con: sqlite3.Connection, attempt: Attempt, request: str, options: BatchOptions) -> str:
    """Call the provider for an already-journaled row and record what came back."""
    try:
        state, detail = batch_client.ensure_job(
            options.project, options.location, attempt.job_name, json.loads(request)
        )
    except CampaignError as exc:
        # The row stays SUBMITTING: intent is durable and this launch's
        # relationship with the provider is unresolved, which is what that state
        # means. The detail says what to look at.
        set_state(con, attempt.attempt_id, "SUBMITTING", str(exc))
        raise
    set_state(con, attempt.attempt_id, state, detail)
    return state


def retry_attempt(
    con: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    suite: str,
    image_set: ImageSet,
    results_bucket: str,
    options: BatchOptions,
) -> Attempt:
    """Re-run one settled failure under a fresh ordinal, diffing against its request."""
    previous = Attempt.from_row(row)
    image = image_set.image_for(previous.tool)
    frozen = json.loads(row["request_json"])

    def build(ordinal: int) -> tuple[Attempt, str]:
        attempt_id = identity.attempt_id(previous.case_id, ordinal)
        attempt = Attempt(
            **{
                **{name: getattr(previous, name) for name in INSERT_COLUMNS},
                "attempt": ordinal,
                "origin": "retry",
                "job_name": job_name_for(suite, previous.case_id, ordinal),
                "result_prefix": result_prefix_for(
                    results_bucket, suite, previous.target_bucket, attempt_id
                ),
                "image_uri": image["image_uri"],
                "image_set_sha256": image_set.sha256,
                "executor_env": options.executor_env(),
            }
        )
        request = render_batch_job(
            attempt,
            image,
            suite=suite,
            options=options,
            # Read back rather than re-resolved: a retry re-runs the attempt that
            # was recorded, over the same bytes it consumed — and under the
            # deadline it was launched with, so widening the slack does not turn
            # every ledger frozen before it into "a new campaign".
            artifact_uri=request_argument(frozen, "--input-artifact"),
            max_run_duration=request_max_run_duration(frozen),
        )
        expected = retry_request_document(
            frozen,
            job_name=attempt.job_name,
            result_prefix=attempt.result_prefix,
            attempt_id=attempt_id,
        )
        if request != expected:
            raise CampaignError(
                f"{attempt_id}: retry would change the frozen request — that is a new "
                "campaign, not a retry"
            )
        return attempt, canonical_json(request)

    attempt, request = journal_intent(
        con, case_id=previous.case_id, case_inputs=previous.case_inputs, build=build
    )
    _submit(con, attempt, request, options)
    return attempt


def poll_once(con: sqlite3.Connection, suite: str, *, client: batch_v1.BatchServiceClient) -> bool:
    """Write the provider's lifecycle through for every non-terminal row.

    Polling never invents a state: a describe that fails leaves the row untouched
    and the pass reports "not all terminal".
    """
    rows = [
        row
        for row in attempt_rows(con)
        if row["executor"] == EXECUTOR and row["state"] not in TERMINAL_STATES
    ]
    if not rows:
        return True
    listed: dict[tuple[str, str], dict[str, str]] = {}
    all_terminal = True
    for row in rows:
        attempt = Attempt.from_row(row)
        project = str(json.loads(attempt.executor_env)["project"])
        parent = (project, attempt.location)
        if parent not in listed:
            try:
                listed[parent] = batch_client.list_job_states(
                    project, attempt.location, suite, client=client
                )
            except GoogleAPIError as exc:
                # A listing that fails costs the pass nothing: every row below
                # falls back to the point read it would have done anyway.
                print(f"campaign: job listing failed: {exc}", file=sys.stderr)
                listed[parent] = {}
        state = listed[parent].get(attempt.job_name)
        if state is None:
            try:
                state = batch_client.describe_job(
                    project, attempt.location, attempt.job_name, client=client
                )
            except GoogleAPIError as exc:
                print(f"campaign: describe failed for {attempt.job_name}: {exc}", file=sys.stderr)
                all_terminal = False
                continue
        set_state(con, attempt.attempt_id, state, row["state_detail"])
        if state in TERMINAL_STATES:
            # A settled preparation unblocks whatever awaited it, in the same
            # pass that noticed it settled (`model.md` § *Scope under
            # accumulation*).
            settle_dependents(con, attempt, state, suite=suite)
        all_terminal &= state in TERMINAL_STATES
    return all_terminal


def _options(args: argparse.Namespace) -> BatchOptions:
    missing = [
        name
        for name in ("project", "location", "results_bucket", "image_set", "anonymous_worker_sa")
        if not getattr(args, name, None)
    ]
    if missing:
        raise CampaignError(
            "the gcp-batch executor requires "
            + ", ".join(f"--{name.replace('_', '-')}" for name in missing)
        )
    return BatchOptions(
        anonymous_worker_sa=args.anonymous_worker_sa,
        authenticated_worker_sa=args.authenticated_worker_sa,
        network=args.network,
        subnetwork=args.subnetwork,
        zone=args.zone,
        provisioning=args.provisioning,
        project=args.project,
        location=args.location,
        aws_credential_secret=args.secret_resource,
        retain_products=getattr(args, "retain_products", False),
        boot_disk_size_gb=getattr(args, "boot_disk_gb", 100),
    )


def cmd_poll(args: argparse.Namespace) -> int:
    with ledger(args.state) as con:
        if not any(
            row["executor"] == EXECUTOR and row["state"] not in TERMINAL_STATES
            for row in attempt_rows(con)
        ):
            print("campaign: no gcp-batch attempts to poll")
            return 0
        with batch_client.client_session() as client:
            suite = ledger_suite(con)
            if not args.watch:
                poll_once(con, suite, client=client)
                return 0
            while not poll_once(con, suite, client=client):
                time.sleep(args.interval)
            return 0


def cmd_retry(args: argparse.Namespace) -> int:
    with ledger(args.state) as con:
        suite = ledger_suite(con)
        rows = attempt_rows(con, group_id=args.group)
        if any(row["executor"] != EXECUTOR for row in rows):
            raise CampaignError(
                "retry manages gcp-batch attempts only; rerun a Docker case with submit --repeat"
            )
        image_set = load_image_set(args.image_set, set())
        options = _options(args)
        latest: dict[str, sqlite3.Row] = {}
        retryable_cases: set[str] = set()
        for row in rows:
            case_id = str(row["case_id"])
            if row["state"] in RETRYABLE_STATES:
                retryable_cases.add(case_id)
            current = latest.get(case_id)
            if current is None or int(row["attempt"]) > int(current["attempt"]):
                latest[case_id] = row

        for case_id, row in latest.items():
            if row["state"] not in RETRYABLE_STATES:
                if case_id in retryable_cases:
                    print(
                        f"campaign: {case_id} latest attempt {row['attempt_id']} is "
                        f"{row['state']}; older failures not retried"
                    )
                continue
            # A rate case's failures are its data points; retrying one would be
            # resampling the statistic.
            if row["statistic"] == "rate":
                print(f"campaign: {row['attempt_id']} is a rate case; its failure is data")
                continue
            # One row's refusal must not abort the sweep: a case whose later
            # ordinal already succeeded raises here (its failure is answered,
            # not retryable), and the first live sweep died on exactly that,
            # leaving a preempted sibling behind it unretried.
            try:
                attempt = retry_attempt(
                    con,
                    row,
                    suite=suite,
                    image_set=image_set,
                    results_bucket=args.results_bucket,
                    options=options,
                )
            except CampaignError as exc:
                print(f"campaign: {row['attempt_id']} not retried: {exc}")
                continue
            print(f"campaign: {row['attempt_id']} -> {attempt.attempt_id}")
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    with ledger(args.state) as con:
        rows = attempt_rows(con, group_id=args.group)
        if any(row["executor"] != EXECUTOR for row in rows):
            raise CampaignError(
                "cancel manages gcp-batch attempts only; Docker submit owns its foreground session"
            )
        with batch_client.client_session() as client:
            for row in rows:
                if row["state"] in TERMINAL_STATES:
                    continue
                attempt = Attempt.from_row(row)
                project = str(json.loads(attempt.executor_env)["project"])
                batch_client.cancel_job(project, attempt.location, attempt.job_name, client=client)
                set_state(con, attempt.attempt_id, "CANCELLED", "cancelled by the operator")
    return 0


def cmd_accept_failure(args: argparse.Namespace) -> int:
    with ledger(args.state) as con:
        if args.slot is not None:
            for reference, reason in abandon_slot(con, args.slot):
                print(f"campaign: slot {reference} ABANDONED: {reason}")
            return 0
        row = con.execute("SELECT * FROM attempts WHERE attempt_id=?", (args.attempt,)).fetchone()
        if row is None or row["state"] not in RETRYABLE_STATES:
            raise CampaignError("accept-failure requires one settled failed attempt")
        # An absent measurement, recorded as absent: the detail keeps which
        # failure was accepted, since ACCEPTED itself does not say.
        detail = f"accepted {row['state']}"
        if row["state_detail"]:
            detail = f"{detail}: {row['state_detail']}"
        set_state(con, args.attempt, "ACCEPTED", detail)
        print(f"campaign: {args.attempt} marked ACCEPTED ({row['state']})")
        # A preparation nobody will retry owes every measurement behind it, and
        # the slot is what records that absence rather than losing it.
        settle_dependents(con, Attempt.from_row(row), "ACCEPTED", suite=ledger_suite(con))
    return 0
