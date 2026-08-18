"""The campaign ledger and the submission lifecycle it journals.

Normative reference: `benchmark/docs/model.md` — the tables, the state
vocabulary, and the object layout are that page's, implemented here. Identity is
`benchmark/docs/identity.md`, minted through :mod:`benchmark.identity`; why the
shape is this shape is `benchmark/docs/architecture.md`.

The file is `campaign.db` and the record inside it is the ledger. A row is one
attempt, nothing is overwritten, and no row is ever deleted.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shlex
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from google.api_core.exceptions import (
    GoogleAPIError,
)
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import batch_v1

from benchmark import batch_client, gcs, identity
from benchmark import plan as bench
from benchmark.contract import CREDENTIAL_ENV_VAR, TOOLBOX_TOOLS
from benchmark.ledger import (
    INSERT_COLUMNS,
    RETRYABLE_STATES,
    STATE_FILENAME,
    TERMINAL_STATES,
    UNSUCCESSFUL_STATES,
    Attempt,
    CampaignError,
    _now,
    attempt_rows,
    blocked_slots,
    journal_intent,
    ledger_suite,
    mint_group_id,
    open_ledger,
    pending_rows,
    producer_summary,
    set_state,
    slot_candidates,
    slot_owed_reason,
    validate_suite,
)
from benchmark.plan import Case, Plan
from benchmark.runtime.command_adapter import CommandAdapterError, LoadedCommandAdapter

# One executor exists. Recorded so a second one is distinguishable when it
# arrives (`identity.md`: hashed then, not before).
EXECUTOR = "gcp-batch"

# Where the subject's output goes, as a hash input. `measure.py` redirects the
# subject's stdout into a file on local disk and gives a native-sink mode a
# directory under the same attempt dir: no subject here streams to a pipe.
OUTPUT_TARGET = "file"

# A plan states no prefix: every case lists a whole bucket. Recorded and hashed
# so a plan that grows one is a different case rather than a silent change.
TARGET_PREFIX = ""

DEADLINE_SLACK_S = 600
"""What the provider deadline adds over the worker's own, for every attempt.

The worker may run an untimed setup exec ahead of the timed subject, each with
its own deadline, and a provider hard-kill takes the container down with all of
its evidence — so this outer bound must never be the one that fires. It is a
safety net rather than a measurement, which is why it is one flat figure and not
a per-mode sum.
"""

N4_BOOT_DISK = {"type": "hyperdisk-balanced", "image": "batch-cos"}
PINNED_IMAGE_RE = re.compile(r"\A[^\s@]+@sha256:([0-9a-f]{64})\Z")
SECRET_RE = re.compile(r"\Aprojects/[^/]+/secrets/[^/]+/versions/[^/]+\Z")
HEX64_RE = re.compile(r"\A[0-9a-f]{64}\Z")
REVISION_RE = re.compile(r"\A[0-9a-f]{40}\Z")
# Batch caps a job ID at 63 characters of lowercase alphanumerics and hyphens,
# starting with a letter. A name that does not fit is refused rather than
# truncated: a truncated name is a name two attempts can collide on.
JOB_NAME_RE = re.compile(r"\A[a-z][a-z0-9-]{0,61}[a-z0-9]\Z")
TOOL_IMAGE_FIELDS = {
    "tool_version",
    "tool_build_sha256",
    "tool_artifact_kind",
    "tool_artifact_locator",
    "tool_artifact_sha256",
    "recipe_sha256",
    "build_inputs_sha256",
    "adapter_bundle_sha256",
    "subject_workdir",
    "tool_slice_sha256",
    "platform_sha256",
}


@dataclass(frozen=True)
class ImageSet:
    """The pinned toolbox a launch runs, and the slices that identify each tool."""

    image_uri: str
    toolbox_manifest_sha256: str
    toolbox_recipe_sha256: str
    harness_revision: str
    tools: dict[str, dict[str, str]]
    sha256: str

    def image_for(self, tool: str) -> dict[str, str]:
        return {
            "image_uri": self.image_uri,
            "toolbox_manifest_sha256": self.toolbox_manifest_sha256,
            "toolbox_recipe_sha256": self.toolbox_recipe_sha256,
            "harness_revision": self.harness_revision,
            **self.tools[tool],
        }


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
        return _canonical(
            {
                "project": self.project,
                "provisioning": self.provisioning,
                "boot_disk": "n4-hyperdisk-balanced",
                "network": self.network,
                "subnetwork": self.subnetwork,
                "zone": self.zone,
            }
        )


def _canonical(document: Mapping[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def load_image_set(path: str | Path, required_tools: set[str]) -> ImageSet:
    source = Path(path).read_bytes()
    try:
        document = json.loads(source)
    except json.JSONDecodeError as exc:
        raise CampaignError(f"image set is not valid JSON: {exc}") from None
    fields = {
        "schema_version",
        "image_uri",
        "toolbox_manifest_sha256",
        "toolbox_recipe_sha256",
        "harness_revision",
        "tools",
    }
    if not isinstance(document, dict) or set(document) != fields:
        raise CampaignError(f"image set must contain exactly {sorted(fields)}")
    if document["schema_version"] != 5 or not isinstance(document["tools"], dict):
        raise CampaignError("image set schema_version must be 5 and tools must be an object")
    image_uri = document["image_uri"]
    toolbox_sha256 = document["toolbox_manifest_sha256"]
    toolbox_recipe_sha256 = document["toolbox_recipe_sha256"]
    harness_revision = document["harness_revision"]
    if not isinstance(image_uri, str) or PINNED_IMAGE_RE.fullmatch(image_uri) is None:
        raise CampaignError("image_uri must be pinned by @sha256 digest")
    if not isinstance(toolbox_sha256, str) or HEX64_RE.fullmatch(toolbox_sha256) is None:
        raise CampaignError("toolbox_manifest_sha256 must be 64 lowercase hex digits")
    if (
        not isinstance(toolbox_recipe_sha256, str)
        or HEX64_RE.fullmatch(toolbox_recipe_sha256) is None
    ):
        raise CampaignError("toolbox_recipe_sha256 must be 64 lowercase hex digits")
    if not isinstance(harness_revision, str) or REVISION_RE.fullmatch(harness_revision) is None:
        raise CampaignError("harness_revision must be a full lowercase commit ID")
    if set(document["tools"]) != TOOLBOX_TOOLS:
        missing = sorted(TOOLBOX_TOOLS - set(document["tools"]))
        extra = sorted(set(document["tools"]) - TOOLBOX_TOOLS)
        raise CampaignError(f"image set toolbox roster mismatch (missing={missing}, extra={extra})")
    tools: dict[str, dict[str, str]] = {}
    for tool, value in document["tools"].items():
        if (
            not isinstance(tool, str)
            or not isinstance(value, dict)
            or set(value) != TOOL_IMAGE_FIELDS
            or any(not isinstance(value[name], str) for name in TOOL_IMAGE_FIELDS)
        ):
            raise CampaignError(f"{tool}: tool entry must contain {sorted(TOOL_IMAGE_FIELDS)}")
        image = {name: value[name] for name in TOOL_IMAGE_FIELDS}
        for digest_field in (
            "tool_build_sha256",
            "tool_artifact_sha256",
            "recipe_sha256",
            "build_inputs_sha256",
            "adapter_bundle_sha256",
            "tool_slice_sha256",
            "platform_sha256",
        ):
            if HEX64_RE.fullmatch(image[digest_field]) is None:
                raise CampaignError(f"{tool}: {digest_field} must be 64 lowercase hex digits")
        if not image["tool_version"] or any(c.isspace() for c in image["tool_version"]):
            raise CampaignError(f"{tool}: tool_version must be a non-empty token")
        workdir = Path(image["subject_workdir"])
        if (
            not image["subject_workdir"].startswith("/")
            or "\x00" in image["subject_workdir"]
            or workdir.as_posix() != image["subject_workdir"]
        ):
            raise CampaignError(f"{tool}: subject_workdir must be a canonical absolute path")
        tools[tool] = image
    # One image has one platform slice; per-tool keys that disagree would make
    # "which platform ran this" unanswerable for the whole set.
    if len({image["platform_sha256"] for image in tools.values()}) != 1:
        raise CampaignError("image set tools disagree on platform_sha256")
    missing = sorted(required_tools - set(tools))
    if missing:
        raise CampaignError(f"image set is missing plan tool(s): {', '.join(missing)}")
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return ImageSet(
        image_uri,
        toolbox_sha256,
        toolbox_recipe_sha256,
        harness_revision,
        tools,
        hashlib.sha256(canonical).hexdigest(),
    )


def job_name_for(suite: str, case_id: str, attempt: int) -> str:
    """`<suite>-<tool>-<hash12>-s<attempt>`, which is what `gcloud` is given.

    Derived from the identity rather than the plan, so two rows cannot claim one
    job; stored anyway, because it is the join to the provider's world and this
    rule may change while the recorded name may not.
    """
    name = f"{suite}-{case_id}-s{attempt}".lower().replace(".", "-")
    if len(name) > 63 or JOB_NAME_RE.fullmatch(name) is None:
        raise CampaignError(
            f"derived job name is not a usable Batch job ID: {name!r} "
            "(63 characters of lowercase alphanumerics and hyphens)"
        )
    return name


def result_prefix_for(results_bucket: str, suite: str, target_bucket: str, attempt: str) -> str:
    """`gs://<results>/<suite>/<target-bucket>/<attempt_id>/` — computed, not discovered."""
    return f"gs://{results_bucket.strip('/')}/{suite}/{target_bucket}/{attempt}/"


def case_identity(
    case: Case,
    *,
    auth_role: str | None,
    target_bucket: str,
    target_region: str,
    location: str,
    tool_slice: str,
    platform: str,
    input_artifact_sha256: str | None = None,
) -> tuple[str, str]:
    """Mint `(case_id, case_inputs)` for one resolved plan case.

    A preparation hashes over content only — `identity.md` § *Two identities,
    two questions* — so the environment document a case gets depends on what the
    attempt is for. Everything else (canary, diagnostic) is hashed the way a
    measurement is: they run on a box the study chose, and reusing one across
    machine shapes would be claiming a comparability nobody asked for.
    """
    config = dict(case.config)
    if case.purpose == "preparation":
        environment = identity.preparation_environment(
            target_bucket=target_bucket,
            target_region=target_region,
            target_prefix=TARGET_PREFIX,
            input_artifact_sha256=input_artifact_sha256,
        )
    else:
        environment = identity.measurement_environment(
            auth_role=auth_role,
            target_bucket=target_bucket,
            target_region=target_region,
            target_prefix=TARGET_PREFIX,
            location=location,
            machine_type=case.resources.machine_type,
            vcpus=case.resources.vcpus,
            memory_gb=case.resources.memory_gb,
            container_memory_gb=case.resources.container_memory_gb,
            output_target=OUTPUT_TARGET,
            timeout_s=case.timeout_s,
            input_artifact_sha256=input_artifact_sha256,
        )
    return (
        identity.case_id(case.tool, environment, config, tool_slice, platform),
        identity.case_inputs_document(environment, config, tool_slice, platform),
    )


@dataclass(frozen=True)
class Inbound:
    """The artifact a case consumes, or the thing that has yet to produce it.

    A mintable inbound completes an identity: the content digest is a hash input
    (`identity.md`), so the consumer can be hashed and submitted now. One that is
    not is why slots exist — the launch knows what it intends and cannot yet name
    it. It says so two ways: `producer` is the *shape* an acceptable root
    producer has, and `awaiting` is the mid-chain slot that has yet to become an
    attempt at all.
    """

    artifact_sha256: str | None = None
    produced_by: str | None = None
    artifact_uri: str | None = None
    awaiting: str | None = None
    producer: str | None = None

    @property
    def mintable(self) -> bool:
        return self.awaiting is None and self.producer is None


CONSUMES_NOTHING = Inbound()
"""The inbound of a case with no prerequisite: mintable, and consuming nothing."""


@dataclass(frozen=True)
class LaunchContext:
    """What one launch resolved once, and every attempt it books renders from.

    Written verbatim into a slot's `known_inputs`, because a measurement booked
    today may be submitted by a `poll` pass days later: it goes out under the
    intent its launch recorded, never under whatever flags an operator happens to
    be typing when the preparation settles.
    """

    suite: str
    group_id: str
    target_bucket: str
    target_region: str
    image: dict[str, str]
    image_set_sha256: str
    results_bucket: str
    options: BatchOptions

    @classmethod
    def for_tool(
        cls,
        tool: str,
        *,
        suite: str,
        group_id: str,
        plan: Plan,
        image_set: ImageSet,
        results_bucket: str,
        options: BatchOptions,
    ) -> LaunchContext:
        return cls(
            suite=suite,
            group_id=group_id,
            target_bucket=plan.bucket,
            target_region=plan.region,
            image=image_set.image_for(tool),
            image_set_sha256=image_set.sha256,
            results_bucket=results_bucket,
            options=options,
        )

    def document(self) -> dict[str, Any]:
        """Everything but the two values the ledger already states: `suite` is in
        `meta` and `group_id` keys the slot's own row."""
        return {
            "target_bucket": self.target_bucket,
            "target_region": self.target_region,
            "image": dict(self.image),
            "image_set_sha256": self.image_set_sha256,
            "results_bucket": self.results_bucket,
            "options": asdict(self.options),
        }

    @classmethod
    def from_document(cls, document: Any, *, suite: str, group_id: str) -> LaunchContext:
        try:
            return cls(
                suite=suite,
                group_id=group_id,
                target_bucket=str(document["target_bucket"]),
                target_region=str(document["target_region"]),
                image={str(k): str(v) for k, v in dict(document["image"]).items()},
                image_set_sha256=str(document["image_set_sha256"]),
                results_bucket=str(document["results_bucket"]),
                options=BatchOptions(**document["options"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CampaignError(
                f"recorded launch context is not one this code understands: {exc}"
            ) from None


def _validate_batch_options(options: BatchOptions) -> None:
    if not options.anonymous_worker_sa or any(c.isspace() for c in options.anonymous_worker_sa):
        raise CampaignError("anonymous worker service account is required")
    if (options.network is None) != (options.subnetwork is None):
        raise CampaignError("network and subnetwork must be supplied together")
    if options.provisioning not in {"SPOT", "STANDARD"}:
        raise CampaignError("provisioning must be SPOT or STANDARD")
    if (
        options.authenticated_worker_sa
        and options.authenticated_worker_sa == options.anonymous_worker_sa
    ):
        raise CampaignError("authenticated and anonymous service accounts must differ")


def _artifact_pairs(attempt: Attempt, artifact_uri: str) -> tuple[tuple[str, str], ...]:
    """What a consuming attempt's job is told about the artifact it reads.

    The object and the digest the case hashed, never the producing attempt: the
    worker verifies the bytes it downloaded against that digest and refuses a
    mismatch, which is the only binding a misfiled artifact cannot survive.
    """
    if attempt.input_artifact_sha256 is None:
        return ()
    if not artifact_uri:
        raise CampaignError(
            f"{attempt.attempt_id} consumes an artifact and no object was resolved to stage"
        )
    return (
        ("--input-artifact", artifact_uri),
        ("--input-artifact-sha256", attempt.input_artifact_sha256),
    )


def request_argument(document: Mapping[str, Any], name: str) -> str:
    """One `--flag value` pair out of a frozen provider request, or `""`."""
    try:
        commands = document["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]["commands"]
        pairs = dict(zip(commands[::2], commands[1::2], strict=True))
    except (IndexError, KeyError, TypeError, ValueError):
        raise CampaignError("recorded provider request cannot be read back") from None
    return str(pairs.get(name, ""))


def request_max_run_duration(document: Mapping[str, Any]) -> str:
    """The provider deadline a frozen request was launched under."""
    try:
        return str(document["taskGroups"][0]["taskSpec"]["maxRunDuration"])
    except (IndexError, KeyError, TypeError):
        raise CampaignError("recorded provider request cannot be read back") from None


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
    pairs = (
        ("--tool", attempt.tool),
        ("--mode", str(json.loads(attempt.config)["mode"])),
        ("--bucket", attempt.target_bucket),
        ("--region", attempt.target_region),
        *(() if attempt.auth_role is None else (("--auth-role", attempt.auth_role),)),
        ("--prefix", attempt.target_prefix),
        ("--output", "/tmp/attempt"),
        ("--destination", attempt.result_prefix),
        ("--timeout", str(attempt.timeout_s)),
        ("--term-grace", str(options.term_grace)),
        ("--image", image["image_uri"]),
        ("--toolbox-manifest-sha256", image["toolbox_manifest_sha256"]),
        ("--toolbox-recipe-sha256", image["toolbox_recipe_sha256"]),
        ("--tool-recipe-sha256", image["recipe_sha256"]),
        ("--tool-build-inputs-sha256", image["build_inputs_sha256"]),
        ("--tool-version", image["tool_version"]),
        ("--tool-build-sha256", image["tool_build_sha256"]),
        ("--adapter-bundle-sha256", image["adapter_bundle_sha256"]),
        ("--harness-revision", image["harness_revision"]),
        ("--subject-workdir", image["subject_workdir"]),
        ("--image-set-sha256", attempt.image_set_sha256),
        ("--group-id", attempt.group_id),
        ("--job-name", attempt.job_name),
        ("--case-id", attempt.case_id),
        ("--attempt-id", attempt.attempt_id),
        ("--machine-type", attempt.machine_type),
        ("--vcpus", str(attempt.vcpus)),
        ("--memory-gb", str(attempt.memory_gb)),
        ("--container-memory-gb", "none" if container_memory is None else str(container_memory)),
        ("--config", attempt.config),
        *_artifact_pairs(attempt, artifact_uri),
    )
    commands = [item for pair in pairs for item in pair]
    container: dict[str, Any] = {"imageUri": image["image_uri"], "commands": commands}
    if container_memory is not None:
        container["options"] = shlex.join(
            (f"--memory={container_memory}g", f"--memory-swap={container_memory}g")
        )
    task_spec: dict[str, Any] = {
        "runnables": [{"container": container}],
        "computeResource": {
            "cpuMilli": str(attempt.vcpus * 1000),
            "memoryMib": str(attempt.memory_gb * 1024),
        },
        "maxRetryCount": 0,
        "maxRunDuration": max_run_duration
        or f"{attempt.timeout_s + int(options.term_grace) + DEADLINE_SLACK_S}s",
    }
    if attempt.secret_resource is not None:
        # One variable, whose payload the worker parses. A case that lists
        # unsigned has no environment block at all.
        task_spec["environment"] = {
            "secretVariables": {CREDENTIAL_ENV_VAR: attempt.secret_resource}
        }
    policy: dict[str, Any] = {
        "machineType": attempt.machine_type,
        "provisioningModel": options.provisioning,
    }
    if attempt.machine_type.startswith("n4-"):
        policy["bootDisk"] = dict(N4_BOOT_DISK)
    allocation: dict[str, Any] = {
        "instances": [{"policy": policy}],
        "serviceAccount": {"email": attempt.service_account},
    }
    if options.zone:
        allocation["location"] = {
            "allowedLocations": [f"zones/{options.zone.removeprefix('zones/')}"]
        }
    if options.network and options.subnetwork:
        allocation["network"] = {
            "networkInterfaces": [{"network": options.network, "subnetwork": options.subnetwork}]
        }
    return {
        # The suite itself, so one polling pass filters exactly rather than
        # scanning a shared project for anything benchmark-shaped.
        "labels": {"suite": suite},
        "taskGroups": [{"taskCount": "1", "parallelism": "1", "taskSpec": task_spec}],
        "allocationPolicy": allocation,
        "logsPolicy": {"destination": "CLOUD_LOGGING"},
    }


def retry_request_document(
    previous: dict[str, Any], *, job_name: str, result_prefix: str, attempt_id: str
) -> dict[str, Any]:
    """The frozen request with only the new attempt's identities rewritten."""
    document = copy.deepcopy(previous)
    try:
        commands = document["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]["commands"]
        if not isinstance(commands, list):
            raise TypeError
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


def planned_attempt(
    case: Case,
    context: LaunchContext,
    *,
    inbound: Inbound = CONSUMES_NOTHING,
) -> tuple[str, str, Callable[[int], tuple[Attempt, str]]]:
    """Mint one plan case's identity and return what builds its nth attempt.

    Split from :func:`submit_case` because the ordinal is not known until the
    journaling transaction holds the lock, and because `--dry-run` renders the
    same request without a ledger to allocate one from.

    `inbound` is the artifact this case consumes: its content digest is a hash
    input, and which attempt produced those bytes is lineage the row records and
    the identity ignores (`identity.md`).
    """
    if not inbound.mintable:
        raise CampaignError(f"{case.tool}: a case awaiting {inbound.awaiting} cannot be minted")
    image, options = context.image, context.options
    case_id, case_inputs = case_identity(
        case,
        auth_role=case.auth_role,
        target_bucket=context.target_bucket,
        target_region=context.target_region,
        location=options.location,
        tool_slice=image["tool_slice_sha256"],
        platform=image["platform_sha256"],
        input_artifact_sha256=inbound.artifact_sha256,
    )
    config = _canonical(dict(case.config))

    def build(ordinal: int) -> tuple[Attempt, str]:
        attempt = Attempt(
            case_id=case_id,
            attempt=ordinal,
            case_inputs=case_inputs,
            group_id=context.group_id,
            tool=case.tool,
            auth_role=case.auth_role,
            executor=EXECUTOR,
            location=options.location,
            machine_type=case.resources.machine_type,
            vcpus=case.resources.vcpus,
            memory_gb=case.resources.memory_gb,
            container_memory_gb=case.resources.container_memory_gb,
            heap_percent=case.heap_percent,
            timeout_s=case.timeout_s,
            target_bucket=context.target_bucket,
            target_region=context.target_region,
            target_prefix=TARGET_PREFIX,
            config=config,
            input_artifact_sha256=inbound.artifact_sha256,
            produced_by=inbound.produced_by,
            tool_slice_sha256=image["tool_slice_sha256"],
            platform_sha256=image["platform_sha256"],
            image_uri=image["image_uri"],
            image_set_sha256=context.image_set_sha256,
            executor_env=options.executor_env(),
            service_account=options.service_account_for(case.auth_role),
            secret_resource=options.secret_for(case.auth_role),
            job_name=job_name_for(context.suite, case_id, ordinal),
            result_prefix=result_prefix_for(
                context.results_bucket,
                context.suite,
                context.target_bucket,
                identity.attempt_id(case_id, ordinal),
            ),
            purpose=case.purpose,
            statistic=case.statistic,
            origin="planned",
        )
        return attempt, _canonical(
            render_batch_job(
                attempt,
                image,
                suite=context.suite,
                options=options,
                artifact_uri=inbound.artifact_uri or "",
            )
        )

    return case_id, case_inputs, build


def submit_case(
    con: sqlite3.Connection,
    case: Case,
    context: LaunchContext,
    *,
    inbound: Inbound = CONSUMES_NOTHING,
    repeat: bool = False,
) -> Attempt:
    """Journal one planned attempt of `case`, then create its job."""
    case_id, case_inputs, build = planned_attempt(case, context, inbound=inbound)
    attempt, request = journal_intent(
        con, case_id=case_id, case_inputs=case_inputs, build=build, repeat=repeat
    )
    _submit(con, attempt, request, context.options)
    return attempt


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
        return attempt, _canonical(request)

    attempt, request = journal_intent(
        con, case_id=previous.case_id, case_inputs=previous.case_inputs, build=build
    )
    _submit(con, attempt, request, options)
    return attempt


def _case_document(case: Case) -> dict[str, Any]:
    """One resolved row, as the JSON a slot's `known_inputs` carries."""
    return {
        **{
            name: getattr(case, name)
            for name in (
                "tool",
                "label",
                "mode",
                "purpose",
                "statistic",
                "auth_role",
                "reps",
                "timeout_s",
                "heap_percent",
            )
        },
        "resources": {
            name: getattr(case.resources, name)
            for name in ("vcpus", "memory_gb", "machine_type", "container_memory_gb")
        },
        "axes": [list(pair) for pair in case.axes],
        "config": [list(pair) for pair in case.config],
    }


def _case_from_document(document: Any) -> Case:
    try:
        return Case(
            **{
                name: document[name]
                for name in (
                    "tool",
                    "label",
                    "mode",
                    "purpose",
                    "statistic",
                    "auth_role",
                    "reps",
                    "timeout_s",
                    "heap_percent",
                )
            },
            resources=bench.Resources(**document["resources"]),
            axes=tuple((str(key), value) for key, value in document["axes"]),
            config=tuple((str(key), value) for key, value in document["config"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CampaignError(f"recorded case is not one this code understands: {exc}") from None


@dataclass(frozen=True)
class Step:
    """One unit of an expanded launch, and what it waits for.

    `waits_for` is the index of the step producing the artifact this one
    consumes, or `None` for a case that consumes nothing. The list contacts
    nothing — no ledger, no provider — which is what lets `--dry-run` print a
    whole expansion offline, and is the line between a bounded expansion and a
    graph discovered at run time (`architecture.md` § *Dependencies*).
    """

    case: Case
    waits_for: int | None


def producer_key(case: Case) -> str:
    """What a slot's producer spec comes to for a case of this launch.

    The spec's other fields — target, tool slice, platform slice — are one
    launch's constants, so within an expansion two cases produce the same
    artifact exactly when their tool and their resolved config agree.
    """
    return _canonical({"tool": case.tool, "config": dict(case.config)})


def expand_launch(
    cases: Iterable[Case], adapters: Mapping[str, LoadedCommandAdapter]
) -> tuple[Step, ...]:
    """Every plan row's declared chain, flattened, with prerequisites shared.

    Rows that differ only in a measurement axis share one preparation step: a
    preparation's identity carries neither the machine nor the consumer's config,
    so a sweep of three concurrencies names one preparation and does not build
    hints at three parallelisms (`identity.md` § *Two identities, two questions*).

    **A plan row that already runs what a prerequisite asks for is that
    prerequisite.** A slot is paid by any successful attempt of its producer's
    shape, so a plan carrying both `list` and `list-hinted` books one slot and no
    preparation, and lists the bucket once instead of twice with byte-identical
    argv. A plan with only hinted rows still mints its own preparation.

    Producer steps are emitted before the slots that consume them, whatever order
    the plan lists its rows in: a launch that died between booking a slot and
    journaling its candidate would otherwise leave a slot nothing in its group
    can ever pay.
    """
    rows = list(cases)
    chains: list[tuple[Case, ...]] = []
    for case in rows:
        adapter = adapters.get(case.tool)
        if adapter is None:
            raise CampaignError(f"{case.tool}: a case cannot be expanded without its capsule")
        chains.append(bench.expand_requirements(case, adapter))
    wanted = {producer_key(link) for chain in chains for link in chain[:-1]}
    steps: list[Step] = []
    shared: dict[str, int] = {}
    candidates: dict[str, int] = {}
    for case, links in sorted(
        zip(rows, chains, strict=True), key=lambda pair: producer_key(pair[0]) not in wanted
    ):
        previous: int | None = None
        for depth, link in enumerate(links[:-1]):
            # Only a root producer is matched by shape: a middle link's artifact
            # depends on what it consumed, which its config does not state.
            index = candidates.get(producer_key(link)) if depth == 0 else None
            if index is None:
                key = _canonical(
                    {
                        "tool": case.tool,
                        "chain": [[step.mode, dict(step.config)] for step in links[: depth + 1]],
                    }
                )
                index = shared.get(key)
                if index is None:
                    index = len(steps)
                    steps.append(Step(link, previous))
                    shared[key] = index
                    if depth == 0:
                        candidates.setdefault(producer_key(link), index)
            previous = index
        if len(links) == 1:
            # A row consuming nothing is shape-matchable; one consuming an
            # artifact is not, because its config does not state what it took.
            candidates.setdefault(producer_key(case), len(steps))
        # `reps: N` is N attempts of the one measurement, each its own job on its
        # own fresh machine; the preparations behind them are built once.
        steps.extend(Step(links[-1], previous) for _ in range(case.reps))
    return tuple(steps)


def produced_artifact(result_prefix: str, tool: str, mode: str) -> tuple[str, str]:
    """`(object uri, sha256)` of the artifact a producer's consumers named.

    The capsule says which of `mode`'s declared artifacts the chain takes from it
    (`capsule-contract.md` § *Declaring a prerequisite*), and that name is looked
    up in the evidence by the filename the mode publishes it under. Selecting the
    sink's only file instead held only while every producer happened to publish
    one — a listing that writes its product to a file publishes two, and picking
    the wrong one stages the wrong bytes under a correct-looking digest.

    The digest is the worker's own: `result.json` carries a content hash per
    native file, so nothing here re-reads bytes to answer a question the evidence
    already answered.
    """
    try:
        artifact, filename = bench.load_capsule(tool).consumed_artifact(mode)
    except (CommandAdapterError, bench.PlanError) as exc:
        raise CampaignError(f"{tool}: nothing consumes what mode {mode!r} makes: {exc}") from None
    marker = f"{result_prefix.rstrip('/')}/result.json"
    try:
        document = json.loads(gcs.download_bytes(marker))
    except (GoogleAPIError, OSError, ValueError) as exc:
        raise CampaignError(f"{marker}: evidence is unreadable: {exc}") from None
    manifest = document.get("native_manifest") if isinstance(document, dict) else None
    if not isinstance(manifest, dict) or filename not in manifest:
        published = sorted(manifest) if isinstance(manifest, dict) else "no manifest"
        raise CampaignError(
            f"{marker}: mode {mode!r} publishes {artifact} as {filename!r} and this attempt "
            f"published {published}"
        )
    digest = manifest[filename]
    if not isinstance(digest, str) or HEX64_RE.fullmatch(digest) is None:
        raise CampaignError(f"{marker}: artifact digest is not 64 lowercase hex digits")
    return f"{result_prefix.rstrip('/')}/native/{filename}", digest


def validate_artifact(tool: str, mode: str, uri: str) -> None:
    """Run the capsule's own structural check over the bytes this mode just produced.

    A digest proves an artifact is unchanged, not that it is usable
    (`capsule-contract.md` § *An artifact is validated*): s3-fast-list's empty
    first cut point hashes cleanly and turns a hinted listing into a full-range
    serial scan. A refusal fails the preparation, never the case that would have
    consumed it. The validator is looked up by producing mode, because a capsule
    with two producers has two structures and neither check reads the other's file;
    a mode with no entry produces bytes nothing structural can be said about.
    """
    validator = bench.load_capsule(tool).validate_artifact.get(mode)
    if validator is None:
        return
    with tempfile.TemporaryDirectory() as staging:
        path = Path(staging) / uri.rsplit("/", 1)[-1]
        path.write_bytes(gcs.download_bytes(uri))
        try:
            validator(path)
        except Exception as exc:
            raise CampaignError(f"{tool}: {uri} is not a usable artifact: {exc}") from None


def slot_reference(group_id: str, slot: int) -> str:
    """`<group_id>/<slot>` — what a slot waiting on a slot records in `awaiting`."""
    return f"{group_id}/{slot}"


def producer_spec(attempt: Attempt) -> str:
    """The canonical document a slot stores to say what an acceptable producer is.

    A shape, not a name. Any successful attempt of the same tool, mode, config,
    target and slices publishes the same bytes, so a retry — which settles under
    a new ordinal the slot never named — satisfies the slot its predecessor left
    owed, and a measurement arm's own listing satisfies a hinted arm's chain.
    Machine, vCPUs, memory, timeout, auth role and purpose are excluded: the
    exclusions `preparation_environment` already makes, because the bytes do not
    depend on them (`identity.md` § *Two identities, two questions*).

    `config` is carried as the column's own bytes rather than as a nested object,
    for the reason `case_inputs` is stored byte-exactly: the match is a string
    comparison against what the producer's row holds, and re-encoding it here
    would be a second answer to a settled question.
    """
    return _canonical(
        {
            "tool": attempt.tool,
            "mode": str(json.loads(attempt.config)["mode"]),
            "config": attempt.config,
            "target_bucket": attempt.target_bucket,
            "target_prefix": attempt.target_prefix,
            "target_region": attempt.target_region,
            "tool_slice_sha256": attempt.tool_slice_sha256,
            "platform_sha256": attempt.platform_sha256,
        }
    )


def book_slot(
    con: sqlite3.Connection,
    case: Case,
    context: LaunchContext,
    *,
    producer: str | None = None,
    awaiting: str | None = None,
) -> int:
    """Record a case the launch intends and cannot yet identify.

    `known_inputs` holds what has been resolved — the case, and the launch it
    belongs to. The identity document is not stored beside them because it is a
    pure function of the two, and a second answer to a settled question is how
    the two come to disagree.

    What is owed is stated one of two ways. A root producer is described by its
    spec, written here rather than derived when a poll pass rechecks the slot:
    deriving it later would let a capsule edited between launch and poll silently
    change what satisfies the slot, which is the frozen intent `LaunchContext`
    exists to hold. A mid-chain link cannot be described by shape at all — its
    own input digest is not knowable at booking — so it names the earlier slot
    and waits for that to become an attempt.
    """
    known_inputs = _canonical({"case": _case_document(case), "launch": context.document()})
    con.execute("BEGIN IMMEDIATE")
    try:
        last = con.execute(
            "SELECT max(slot) AS last FROM pending WHERE group_id=?", (context.group_id,)
        ).fetchone()["last"]
        slot = int(last or 0) + 1
        con.execute(
            "INSERT INTO pending (group_id, slot, tool, purpose, known_inputs, producer, "
            "awaiting, state, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'BLOCKED', ?)",
            (
                context.group_id,
                slot,
                case.tool,
                case.purpose,
                known_inputs,
                producer,
                awaiting,
                _now(),
            ),
        )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return slot


def _claim_slot(con: sqlite3.Connection, slot: sqlite3.Row, case_id: str) -> bool:
    """Take a BLOCKED slot for this pass, before anything is minted or submitted.

    The claim is the case identity the slot is about to become, written under one
    `BEGIN IMMEDIATE`: a second pass finds `became` already set and does nothing,
    and a pass that dies between claiming and resolving leaves the slot owed
    rather than submitting the same measurement twice. A claim is told from a
    resolution by the state beside it — `BLOCKED` with a `became` is claimed.
    """
    con.execute("BEGIN IMMEDIATE")
    try:
        claimed = (
            con.execute(
                "UPDATE pending SET became=? WHERE group_id=? AND slot=? AND state='BLOCKED' "
                "AND became IS NULL",
                (case_id, slot["group_id"], slot["slot"]),
            ).rowcount
            == 1
        )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return claimed


def _already_attempted(con: sqlite3.Connection, case_id: str, case: Case) -> Attempt | None:
    """The successful attempt of this exact case a slot may bind instead of re-running.

    Reuse within a launch is free and is what a shared preparation means, so a
    slot whose case has already succeeded binds that attempt rather than asking
    the ledger to journal a second one — which it refuses, deadlocking the slot.
    A preparation's artifact is re-digested here for the same reason
    :meth:`Launch.reuse` does it: the row's recorded digest and the evidence must
    still agree.
    """
    row = con.execute(
        "SELECT * FROM attempts WHERE case_id=? AND state='SUCCEEDED' ORDER BY attempt DESC "
        "LIMIT 1",
        (case_id,),
    ).fetchone()
    if row is None:
        return None
    if case.purpose == "preparation":
        _, digest = produced_artifact(row["result_prefix"], case.tool, case.mode)
        if row["artifact_sha256"] is not None and row["artifact_sha256"] != digest:
            raise CampaignError(
                f"{row['attempt_id']}: recorded artifact digest {row['artifact_sha256']} is not "
                f"the {digest} its evidence carries"
            )
    return Attempt.from_row(row)


def resolve_slot(
    con: sqlite3.Connection, slot: sqlite3.Row, inbound: Inbound, *, suite: str
) -> Attempt | None:
    """Mint the identity a slot was waiting for, submit it, and record what it became.

    `None` when another pass holds the claim: a slot resolves once, and a second
    pass over the same settled preparation does nothing rather than submitting a
    second job for one measurement.
    """
    reference = slot_reference(str(slot["group_id"]), int(slot["slot"]))
    document = json.loads(slot["known_inputs"])
    if not isinstance(document, dict):
        raise CampaignError(f"slot {reference}: known inputs are not an object")
    context = LaunchContext.from_document(
        document.get("launch"), suite=suite, group_id=str(slot["group_id"])
    )
    case = _case_from_document(document.get("case"))
    case_id, _, _ = planned_attempt(case, context, inbound=inbound)
    if not _claim_slot(con, slot, case_id):
        return None
    attempt = _already_attempted(con, case_id, case) or submit_case(
        con, case, context, inbound=inbound
    )
    con.execute(
        "UPDATE pending SET state='RESOLVED', became=?, settled_at=? WHERE group_id=? AND slot=?",
        (attempt.attempt_id, _now(), slot["group_id"], slot["slot"]),
    )
    # A slot may wait on a slot: whatever waited on this one now waits on the
    # attempt it became, so the thing that unblocks a slot is always an attempt
    # settling.
    con.execute(
        "UPDATE pending SET awaiting=? WHERE awaiting=? AND state='BLOCKED'",
        (attempt.attempt_id, reference),
    )
    return attempt


def _abandon(
    con: sqlite3.Connection, slot: sqlite3.Row, reason: str, abandoned: list[tuple[str, str]]
) -> None:
    con.execute(
        "UPDATE pending SET state='ABANDONED', settled_at=? WHERE group_id=? AND slot=?",
        (_now(), slot["group_id"], slot["slot"]),
    )
    abandoned.append((slot_reference(str(slot["group_id"]), int(slot["slot"])), reason))


def _cascade_abandon(
    con: sqlite3.Connection, group_id: str, abandoned: list[tuple[str, str]]
) -> None:
    """Carry an abandonment down the chain that hung off it.

    Only mid-chain links, which name the slot they wait on: a chain whose first
    link is gone owes every link after it, and no other slot in the group is
    made unpayable by this one going away.
    """
    progressed = True
    while progressed:
        progressed = False
        for slot in blocked_slots(con, group_id):
            if slot["awaiting"] is None or "/" not in str(slot["awaiting"]):
                continue
            reason = slot_owed_reason(con, slot)
            if reason is None:
                continue
            _abandon(con, slot, reason, abandoned)
            progressed = True


def abandon_dependents(
    con: sqlite3.Connection, group_id: str, *, accepted: str
) -> list[tuple[str, str]]:
    """Record the slots `accepted` was a candidate for as absent, with why.

    `ABANDONED` is what `ACCEPTED` says about an attempt, applied to a
    measurement that never got to exist.

    **Only slots that counted this attempt among their candidates.** Accepting
    one failure is a statement about that attempt, and a group holds slots owed
    by shapes it has nothing to do with; abandoning those too would take the
    deliberate step an owed slot exists to force and perform it on the operator's
    behalf, silently. Those slots stay owed and stay loud, and
    `accept-failure --slot` is how they are declared absent.

    Exhaustion is still evaluated per slot rather than off the accepted id: a
    slot is owed by a *shape*, so even a slot this attempt could have paid
    abandons only once every candidate of that shape has failed, been accepted,
    or published nothing usable.
    """
    abandoned: list[tuple[str, str]] = []
    for slot in blocked_slots(con, group_id):
        if all(row["attempt_id"] != accepted for row in slot_candidates(con, slot)):
            continue
        reason = slot_owed_reason(con, slot)
        if reason is None:
            continue
        _abandon(con, slot, reason, abandoned)
    _cascade_abandon(con, group_id, abandoned)
    return abandoned


def abandon_slot(con: sqlite3.Connection, reference: str) -> list[tuple[str, str]]:
    """Declare one named slot's measurement absent, and whatever hung off it.

    The operator affordance for the case `accept-failure --attempt` cannot
    reach: a slot whose only candidate SUCCEEDED and was disqualified has no
    failed attempt to accept, because the producer's timing is honest and stays
    `SUCCEEDED`. Refused while anything could still pay the slot — abandoning is
    for a measurement that cannot happen, not for one that is slow.
    """
    group_id, _, ordinal = reference.partition("/")
    if not ordinal.isdigit():
        raise CampaignError("accept-failure --slot takes <group>/<slot>")
    slot = con.execute(
        "SELECT * FROM pending WHERE group_id=? AND slot=?", (group_id, int(ordinal))
    ).fetchone()
    if slot is None:
        raise CampaignError(f"no slot {reference} in this ledger")
    if slot["state"] != "BLOCKED":
        raise CampaignError(f"slot {reference} is already {slot['state']}")
    reason = slot_owed_reason(con, slot)
    if reason is None:
        owed = slot["awaiting"] or producer_summary(str(slot["producer"]))
        raise CampaignError(f"slot {reference} can still be paid: it awaits {owed}")
    abandoned: list[tuple[str, str]] = []
    _abandon(con, slot, reason, abandoned)
    _cascade_abandon(con, slot["group_id"], abandoned)
    return abandoned


def _candidate_artifact(con: sqlite3.Connection, candidate: Attempt) -> Inbound | str:
    """What a successful attempt published for its consumers, or why it is unusable.

    The digest is recorded on the producer's row as it is read: `artifact_sha256`
    is what that attempt made, and it is what a consumer's `input_artifact_sha256`
    is copied from (`model.md`).
    """
    mode = str(json.loads(candidate.config)["mode"])
    try:
        uri, digest = produced_artifact(candidate.result_prefix, candidate.tool, mode)
    except CampaignError as exc:
        return str(exc)
    con.execute(
        "UPDATE attempts SET artifact_sha256=?, updated_at=? WHERE attempt_id=?",
        (digest, _now(), candidate.attempt_id),
    )
    try:
        validate_artifact(candidate.tool, mode, uri)
    except CampaignError as exc:
        return str(exc)
    return Inbound(artifact_sha256=digest, produced_by=candidate.attempt_id, artifact_uri=uri)


def _disqualify(
    con: sqlite3.Connection, slot: sqlite3.Row, candidate: Attempt, reason: str
) -> None:
    """Record against the slot why a settled candidate cannot pay it.

    Against the slot, never against the producer, when the producer is a
    measurement: it measured what it measured, and flipping an honest timing to
    FAILED to express an artifact complaint would falsify the number to make the
    bookkeeping tidy. A preparation is the other case — publishing the artifact
    is the whole of what it was for — so its refusal is its own, and FAILED keeps
    it retryable and abandonable.
    """
    if candidate.purpose == "preparation":
        set_state(con, candidate.attempt_id, "FAILED", reason[:500])
    recorded = con.execute(
        "SELECT disqualified FROM pending WHERE group_id=? AND slot=?",
        (slot["group_id"], slot["slot"]),
    ).fetchone()["disqualified"]
    against = json.loads(recorded) if recorded else {}
    if against.get(candidate.attempt_id) == reason[:200]:
        # Every poll pass re-examines the candidate, in case what was unreadable
        # was the bucket rather than the artifact. Saying so once is enough.
        return
    against[candidate.attempt_id] = reason[:200]
    con.execute(
        "UPDATE pending SET disqualified=? WHERE group_id=? AND slot=?",
        (_canonical(against), slot["group_id"], slot["slot"]),
    )
    print(
        f"campaign: {candidate.attempt_id} cannot pay slot "
        f"{slot['group_id']}/{slot['slot']}: {reason}",
        file=sys.stderr,
    )


def _slot_inbound(
    con: sqlite3.Connection, slot: sqlite3.Row, digested: dict[str, Inbound | str]
) -> Inbound | None:
    """The artifact a blocked slot may now consume, or `None` while it has none.

    The earliest-settled candidate wins. One that succeeded but published nothing
    usable is disqualified and stepped over rather than left to wedge the slot
    forever, which is the quiet failure a slot exists to prevent. `digested`
    carries one pass's answers, so a sweep of six sibling slots reads one
    producer's evidence once.
    """
    for row in slot_candidates(con, slot, state="SUCCEEDED"):
        candidate = Attempt.from_row(row)
        if candidate.attempt_id not in digested:
            digested[candidate.attempt_id] = _candidate_artifact(con, candidate)
        outcome = digested[candidate.attempt_id]
        if isinstance(outcome, Inbound):
            return outcome
        _disqualify(con, slot, candidate, outcome)
    return None


def resolve_blocked_slots(
    con: sqlite3.Connection,
    group_id: str,
    *,
    suite: str,
    digested: dict[str, Inbound | str] | None = None,
) -> None:
    """Ask every slot this group still owes whether an attempt now satisfies it.

    Slot-driven, not attempt-driven: the question is "does a satisfying attempt
    exist", not "which slots does this one attempt unblock". Nomination by
    attempt id could not answer the first, which is why a retry — settling under
    an ordinal nothing named — used to orphan its slot permanently.

    The sweep repeats while it makes progress, because a slot that resolves into
    an attempt which had already settled leaves whatever waited on *it* payable
    in the same pass.
    """
    answers: dict[str, Inbound | str] = {} if digested is None else digested
    progressed = True
    while progressed:
        progressed = False
        for slot in blocked_slots(con, group_id):
            reference = slot_reference(str(slot["group_id"]), int(slot["slot"]))
            inbound = _slot_inbound(con, slot, answers)
            if inbound is None:
                continue
            try:
                resolved = resolve_slot(con, slot, inbound, suite=suite)
            except (CampaignError, GoogleAPIError) as exc:
                # The slot stays owed, which is what a slot is for.
                print(f"campaign: slot {reference} could not be resolved: {exc}", file=sys.stderr)
                continue
            if resolved is None:
                continue
            progressed = True
            print(f"campaign: slot {reference} -> {resolved.attempt_id} {resolved.job_name}")


def settle_dependents(con: sqlite3.Connection, attempt: Attempt, state: str, *, suite: str) -> None:
    """Resolve or abandon whatever waited on an attempt that has just settled.

    A settled failure leaves its slots `BLOCKED`, because a retry may still pay
    them — and now genuinely can, since what a slot waits for is a shape rather
    than an ordinal. `accept-failure` is what declares the measurement absent.
    """
    if state == "ACCEPTED":
        for reference, reason in abandon_dependents(
            con, attempt.group_id, accepted=attempt.attempt_id
        ):
            print(f"campaign: slot {reference} ABANDONED: {reason}")
        return
    if state != "SUCCEEDED":
        return
    digested: dict[str, Inbound | str] = {}
    if attempt.purpose == "preparation":
        # A preparation is digested and validated whether or not anything is
        # waiting: `artifact_sha256` is what it made, written when it settled,
        # and a validator's verdict does not depend on who asked.
        outcome = _candidate_artifact(con, attempt)
        if isinstance(outcome, str):
            set_state(con, attempt.attempt_id, "FAILED", outcome[:500])
            print(
                f"campaign: {attempt.attempt_id} published no usable artifact: {outcome}",
                file=sys.stderr,
            )
            return
        digested[attempt.attempt_id] = outcome
    resolve_blocked_slots(con, attempt.group_id, suite=suite, digested=digested)


@dataclass
class Launch:
    """One `submit` pass: what it has already bound, and what it still owes.

    Reuse within a launch is free and falls out of the expansion — one step per
    preparation, bound by every consumer of it. Reuse *across* launches is a
    decision, so it is refused here unless the operator asks for it.
    """

    con: sqlite3.Connection
    suite: str
    group_id: str
    plan: Plan
    image_set: ImageSet
    results_bucket: str
    options: BatchOptions
    repeat: bool = False
    reuse_preparations: bool = False
    bound: dict[int, Inbound] = field(default_factory=dict)
    booked: int = 0
    """Slots this pass actually opened — what the launch still owes."""

    def context_for(self, tool: str) -> LaunchContext:
        return LaunchContext.for_tool(
            tool,
            suite=self.suite,
            group_id=self.group_id,
            plan=self.plan,
            image_set=self.image_set,
            results_bucket=self.results_bucket,
            options=self.options,
        )

    def run(self, steps: Iterable[Step]) -> None:
        for index, step in enumerate(steps):
            inbound = CONSUMES_NOTHING if step.waits_for is None else self.bound[step.waits_for]
            self.bound[index] = self.advance(step.case, inbound)

    def advance(self, case: Case, inbound: Inbound) -> Inbound:
        """Submit one step, or book it, and say what the next step consumes."""
        context = self.context_for(case.tool)
        if not inbound.mintable:
            slot = book_slot(
                self.con, case, context, producer=inbound.producer, awaiting=inbound.awaiting
            )
            self.booked += 1
            owed = inbound.awaiting or producer_summary(str(inbound.producer))
            print(f"campaign: slot {self.group_id}/{slot} {case.tool} {case.mode} awaiting {owed}")
            return Inbound(awaiting=slot_reference(self.group_id, slot))
        reused = self.reuse(case, context, inbound)
        if reused is not None:
            print(f"campaign: {case.tool} {case.mode} binds the artifact {reused.produced_by} made")
            return reused
        attempt = submit_case(self.con, case, context, inbound=inbound, repeat=self.repeat)
        print(f"campaign: {attempt.attempt_id} {attempt.job_name}")
        # What the next step waits for is this attempt's shape, not its name: a
        # retry of it, or any other attempt of the same shape, pays that slot too.
        return Inbound(producer=producer_spec(attempt))

    def reuse(self, case: Case, context: LaunchContext, inbound: Inbound) -> Inbound | None:
        """Bind an artifact a successful attempt of this exact case already made.

        Refused without `--reuse-preparations`, because the bytes being identical
        is precisely what cannot tell you the corpus moved: hints describe a key
        distribution, and the bucket it came from has been growing ever since
        (`identity.md` § *What identity cannot cover*).
        """
        if case.purpose != "preparation" or self.repeat:
            return None
        case_id, _, _ = planned_attempt(case, context, inbound=inbound)
        row = self.con.execute(
            "SELECT attempt_id, group_id, result_prefix, artifact_sha256 FROM attempts "
            "WHERE case_id=? AND state='SUCCEEDED' ORDER BY attempt DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        if row is None:
            return None
        if not self.reuse_preparations:
            raise CampaignError(
                f"{case_id} was already prepared by group {row['group_id']}; reusing a "
                "preparation across launches is a decision, not a default — pass "
                "--reuse-preparations, or --repeat to build it again"
            )
        uri, digest = produced_artifact(row["result_prefix"], case.tool, case.mode)
        if row["artifact_sha256"] is not None and row["artifact_sha256"] != digest:
            raise CampaignError(
                f"{row['attempt_id']}: recorded artifact digest {row['artifact_sha256']} is not "
                f"the {digest} its evidence carries"
            )
        return Inbound(artifact_sha256=digest, produced_by=str(row["attempt_id"]), artifact_uri=uri)


def render_launch(
    steps: Iterable[Step],
    *,
    suite: str,
    group_id: str,
    plan: Plan,
    image_set: ImageSet,
    results_bucket: str,
    options: BatchOptions,
) -> list[str]:
    """What a first launch of `steps` would do, rendered offline.

    A slot cannot be rendered — not being identifiable yet is what a slot *is* —
    so it prints its place in the chain instead, and a reviewer sees that a plan
    yields twenty-two attempts rather than eleven before anything is submitted.
    """
    expanded = list(steps)
    lines: list[str] = []
    booked: dict[int, str] = {}
    slot = 0
    for index, step in enumerate(expanded):
        if step.waits_for is None:
            context = LaunchContext.for_tool(
                step.case.tool,
                suite=suite,
                group_id=group_id,
                plan=plan,
                image_set=image_set,
                results_bucket=results_bucket,
                options=options,
            )
            _, _, build = planned_attempt(step.case, context)
            attempt, request = build(1)
            lines.append(f"{attempt.attempt_id} {attempt.job_name} {request}")
            continue
        slot += 1
        producer = expanded[step.waits_for]
        # Which step pays this slot, printed before anything is submitted: an
        # axis stated on a knob the producing mode ignores would otherwise
        # disqualify the candidate silently and duplicate the listing again.
        owed = booked.get(
            step.waits_for,
            f"step {step.waits_for + 1} ({producer.case.tool} {producer.case.mode})",
        )
        lines.append(
            f"slot {group_id}/{slot} {step.case.tool} {step.case.mode} "
            f"{step.case.purpose} awaiting {owed}"
        )
        booked[index] = slot_reference(group_id, slot)
    return lines


def poll_once(con: sqlite3.Connection, suite: str, *, client: batch_v1.BatchServiceClient) -> bool:
    """Write the provider's lifecycle through for every non-terminal row.

    Polling never invents a state: a describe that fails leaves the row untouched
    and the pass reports "not all terminal".
    """
    rows = [row for row in attempt_rows(con) if row["state"] not in TERMINAL_STATES]
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
    return BatchOptions(
        args.anonymous_worker_sa,
        args.authenticated_worker_sa,
        args.network,
        args.subnetwork,
        args.zone,
        args.provisioning,
        args.project,
        args.location,
        args.secret_resource,
    )


def cmd_submit(args: argparse.Namespace) -> int:
    suite = validate_suite(args.suite)
    loaded = Plan.load(Path(args.plan))
    image_set = load_image_set(args.image_set, {case.tool for case in loaded.cases})
    options = _options(args)
    # The capsules declare the chains, so what a plan comes to is knowable before
    # anything is contacted: N rows, M attempts, K slots.
    steps = expand_launch(loaded.cases, loaded.adapters)
    if args.dry_run:
        # Nothing is journaled and nothing is created, so every case renders at
        # the ordinal a first launch would give it.
        rendered = render_launch(
            steps,
            suite=suite,
            group_id=args.group or "dry-run",
            plan=loaded,
            image_set=image_set,
            results_bucket=args.results_bucket,
            options=options,
        )
        for line in rendered:
            print(line)
        _announce_shape(
            loaded.cases,
            steps,
            slots=sum(1 for step in steps if step.waits_for is not None),
        )
        return 0
    con = open_ledger(args.state, suite=suite)
    try:
        launch = Launch(
            con,
            suite,
            mint_group_id(con, args.group),
            loaded,
            image_set,
            args.results_bucket,
            options,
            repeat=args.repeat,
            reuse_preparations=args.reuse_preparations,
        )
        launch.run(steps)
        _announce_shape(loaded.cases, steps, slots=launch.booked)
        print(f"campaign: group {launch.group_id}")
    finally:
        con.close()
    return 0


def _announce_shape(cases: Iterable[Case], steps: Iterable[Step], *, slots: int) -> None:
    """What the launch came to: rows in, and the attempts and slots they became.

    `slots` is what was actually booked, not what declared a prerequisite: a step
    whose preparation was bound from an earlier attempt is identifiable now and
    is submitted rather than owed.
    """
    expanded = list(steps)
    print(
        f"campaign: {len(list(cases))} plan row(s) expand to {len(expanded) - slots} "
        f"attempt(s) and {slots} slot(s)"
    )


def cmd_poll(args: argparse.Namespace) -> int:
    con = open_ledger(args.state)
    client: batch_v1.BatchServiceClient | None = None
    try:
        client = batch_v1.BatchServiceClient()
        suite = ledger_suite(con)
        if not args.watch:
            poll_once(con, suite, client=client)
            return 0
        while not poll_once(con, suite, client=client):
            time.sleep(args.interval)
        return 0
    finally:
        try:
            if client is not None:
                batch_client._close_batch_client(client)
        finally:
            con.close()


def cmd_status(args: argparse.Namespace) -> int:
    con = open_ledger(args.state, readonly=True)
    try:
        for row in attempt_rows(con, group_id=args.group, case_id=args.case):
            print(
                f"{row['attempt_id']:<32} {row['state']:<12} {row['purpose']:<12} "
                f"{row['group_id']} {row['job_name']}"
            )
        # A group is not understood from its rows alone while it still owes a
        # measurement it cannot yet identify.
        for slot in pending_rows(con, group_id=args.group):
            owed = slot["awaiting"] or producer_summary(str(slot["producer"]))
            line = (
                f"slot {slot['group_id']}/{slot['slot']:<8} {slot['state']:<12} "
                f"{slot['purpose']:<12} awaiting {owed}"
            )
            # A slot nothing can pay is a measurement quietly absent, which is
            # the failure a slot exists to prevent: it says so here.
            reason = slot_owed_reason(con, slot)
            if reason is not None:
                line += f" -- OWED, nothing can pay it: {reason}"
            print(line)
    finally:
        con.close()
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    con = open_ledger(args.state)
    try:
        suite = ledger_suite(con)
        image_set = load_image_set(args.image_set, set())
        options = _options(args)
        for row in attempt_rows(con, group_id=args.group):
            if row["state"] not in RETRYABLE_STATES:
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
    finally:
        con.close()
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    con = open_ledger(args.state)
    client: batch_v1.BatchServiceClient | None = None
    try:
        client = batch_v1.BatchServiceClient()
        for row in attempt_rows(con, group_id=args.group):
            if row["state"] in TERMINAL_STATES:
                continue
            attempt = Attempt.from_row(row)
            project = str(json.loads(attempt.executor_env)["project"])
            batch_client.cancel_job(project, attempt.location, attempt.job_name, client=client)
            set_state(con, attempt.attempt_id, "CANCELLED", "cancelled by the operator")
    finally:
        try:
            if client is not None:
                batch_client._close_batch_client(client)
        finally:
            con.close()
    return 0


def cmd_accept_failure(args: argparse.Namespace) -> int:
    con = open_ledger(args.state)
    try:
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
    finally:
        con.close()
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    """Delete the evidence of attempts that settled unsuccessfully. Rows stay."""
    con = open_ledger(args.state, readonly=True)
    try:
        rows = [
            row
            for row in attempt_rows(con, group_id=args.group)
            if row["state"] in UNSUCCESSFUL_STATES
        ]
    finally:
        con.close()
    for row in rows:
        deleted = gcs.delete_prefix(row["result_prefix"])
        print(f"campaign: pruned {deleted} object(s) under {row['result_prefix']}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default=STATE_FILENAME)
    sub = parser.add_subparsers(dest="command", required=True)

    def provider(p: argparse.ArgumentParser) -> None:
        p.add_argument("--project", required=True)
        p.add_argument("--location", required=True)
        p.add_argument("--results-bucket", required=True)
        p.add_argument("--image-set", required=True)
        p.add_argument(
            "--secret-resource",
            metavar="projects/P/secrets/S/versions/V",
            help="Secret Manager version holding the authenticated stratum's "
            "KEY=VALUE credential payload. Required only when a case signs.",
        )
        p.add_argument("--anonymous-worker-sa", required=True)
        p.add_argument("--authenticated-worker-sa")
        p.add_argument("--network")
        p.add_argument("--subnetwork")
        p.add_argument("--zone")
        p.add_argument("--provisioning", choices=("SPOT", "STANDARD"), default="SPOT")

    submit = sub.add_parser("submit")
    provider(submit)
    submit.add_argument("--suite", required=True)
    submit.add_argument("--plan", required=True)
    submit.add_argument("--group", help="name this launch instead of minting a timestamp")
    submit.add_argument(
        "--repeat",
        action="store_true",
        help="submit a case that already has a successful attempt",
    )
    submit.add_argument(
        "--reuse-preparations",
        action="store_true",
        help="bind an artifact an earlier launch already produced, instead of "
        "refusing: free within one launch, a decision across them, because a "
        "digest cannot tell you the corpus moved",
    )
    submit.add_argument("--dry-run", action="store_true")
    submit.set_defaults(func=cmd_submit)

    retry = sub.add_parser("retry")
    provider(retry)
    retry.add_argument("--group", required=True)
    retry.set_defaults(func=cmd_retry)

    poll = sub.add_parser("poll")
    poll.add_argument("--watch", action="store_true")
    poll.add_argument("--interval", type=int, default=30)
    poll.set_defaults(func=cmd_poll)

    cancel = sub.add_parser("cancel")
    cancel.add_argument("--group", required=True)
    cancel.set_defaults(func=cmd_cancel)

    accept_failure = sub.add_parser("accept-failure")
    accepted = accept_failure.add_mutually_exclusive_group(required=True)
    accepted.add_argument("--attempt", help="a settled FAILED or NOT_CREATED attempt")
    accepted.add_argument(
        "--slot",
        metavar="GROUP/N",
        help="an owed slot nothing can pay -- the case a disqualified but "
        "SUCCEEDED producer leaves, where there is no failure to accept",
    )
    accept_failure.set_defaults(func=cmd_accept_failure)

    status = sub.add_parser("status")
    status.add_argument("--group")
    status.add_argument("--case")
    status.set_defaults(func=cmd_status)

    prune = sub.add_parser("prune")
    prune.add_argument("--group", required=True)
    prune.set_defaults(func=cmd_prune)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        return int(args.func(args))
    except (CampaignError, DefaultCredentialsError, GoogleAPIError, OSError, ValueError) as exc:
        print(f"campaign: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
