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
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from google.api_core.exceptions import (
    AlreadyExists,
    BadRequest,
    FailedPrecondition,
    Forbidden,
    GoogleAPIError,
    NotFound,
    Unauthorized,
)
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import batch_v1
from google.protobuf.json_format import MessageToDict, ParseDict

from benchmark import gcs, identity
from benchmark import plan as bench
from benchmark.contract import CREDENTIAL_ENV_VAR, TOOLBOX_TOOLS
from benchmark.plan import Case, Plan

STATE_FILENAME = "campaign.db"

# Bumped whenever a file written by an older reader would be misread by this
# one. There is no migration: an unrecognised version is refused, so a command
# either fully understands the file it opened or does not open it.
SCHEMA_VERSION = 1

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

RETRYABLE_STATES = {"FAILED", "NOT_CREATED"}
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "NOT_CREATED", "CANCELLED", "ACCEPTED"}
# What `prune` may delete the evidence of: an attempt that settled without a
# measurement behind it.
UNSUCCESSFUL_STATES = TERMINAL_STATES - {"SUCCEEDED"}

N4_BOOT_DISK = {"type": "hyperdisk-balanced", "image": "batch-cos"}
PINNED_IMAGE_RE = re.compile(r"\A[^\s@]+@sha256:([0-9a-f]{64})\Z")
SECRET_RE = re.compile(r"\Aprojects/[^/]+/secrets/[^/]+/versions/[^/]+\Z")
HEX64_RE = re.compile(r"\A[0-9a-f]{64}\Z")
REVISION_RE = re.compile(r"\A[0-9a-f]{40}\Z")
SUITE_RE = re.compile(r"\A[a-z][a-z0-9-]{0,30}[a-z0-9]\Z")
GROUP_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,38}[a-z0-9]\Z")
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

SCHEMA = """
CREATE TABLE meta (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    suite          TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE attempts (
    case_id             TEXT NOT NULL,
    attempt             INTEGER NOT NULL,
    attempt_id          TEXT GENERATED ALWAYS AS (case_id || '.s' || attempt) VIRTUAL,
    case_inputs         TEXT NOT NULL,
    group_id            TEXT NOT NULL,
    tool                TEXT NOT NULL,

    auth_role           TEXT,
    executor            TEXT NOT NULL,
    location            TEXT NOT NULL,
    machine_type        TEXT NOT NULL,
    vcpus               INTEGER NOT NULL,
    memory_gb           INTEGER NOT NULL,
    container_memory_gb INTEGER,
    heap_percent        INTEGER NOT NULL,
    timeout_s           INTEGER NOT NULL,
    target_bucket       TEXT NOT NULL,
    target_region       TEXT NOT NULL,
    target_prefix       TEXT NOT NULL,

    config              TEXT NOT NULL,
    mode                TEXT    GENERATED ALWAYS AS (json_extract(config, '$.mode')) VIRTUAL,
    concurrency         INTEGER GENERATED ALWAYS AS (json_extract(config, '$.concurrency')) VIRTUAL,

    input_artifact_sha256 TEXT,
    produced_by           TEXT,
    artifact_sha256       TEXT,

    tool_slice_sha256   TEXT NOT NULL,
    platform_sha256     TEXT NOT NULL,
    image_uri           TEXT NOT NULL,
    image_set_sha256    TEXT NOT NULL,

    executor_env        TEXT NOT NULL,
    service_account     TEXT NOT NULL,
    secret_resource     TEXT,
    job_name            TEXT NOT NULL UNIQUE,
    result_prefix       TEXT NOT NULL,

    request_json        TEXT NOT NULL,
    purpose             TEXT NOT NULL
        CHECK (purpose IN ('measurement', 'preparation', 'canary', 'diagnostic')),
    statistic           TEXT NOT NULL CHECK (statistic IN ('timing', 'rate')),
    origin              TEXT NOT NULL CHECK (origin IN ('planned', 'retry')),
    state               TEXT NOT NULL,
    state_detail        TEXT,
    recorded_at         TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    settled_at          TEXT,

    PRIMARY KEY (case_id, attempt)
);

CREATE TABLE pending (
    group_id      TEXT NOT NULL,
    slot          INTEGER NOT NULL,
    tool          TEXT NOT NULL,
    purpose       TEXT NOT NULL
        CHECK (purpose IN ('measurement', 'preparation', 'canary', 'diagnostic')),
    known_inputs  TEXT NOT NULL,
    awaiting      TEXT NOT NULL,
    state         TEXT NOT NULL CHECK (state IN ('BLOCKED', 'RESOLVED', 'ABANDONED')),
    became        TEXT,
    recorded_at   TEXT NOT NULL,
    settled_at    TEXT,

    PRIMARY KEY (group_id, slot)
);
"""


class CampaignError(RuntimeError):
    """Campaign input, ledger state, or provider state cannot be used safely."""


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


@dataclass(frozen=True)
class Attempt:
    """One attempt's identity and resolved environment: what a request renders from.

    Every field is a column of `attempts`, so a retry and a poll work from the
    recorded row rather than from a plan that may have been edited since.
    """

    case_id: str
    attempt: int
    case_inputs: str
    group_id: str
    tool: str
    auth_role: str | None
    executor: str
    location: str
    machine_type: str
    vcpus: int
    memory_gb: int
    container_memory_gb: int | None
    heap_percent: int
    timeout_s: int
    target_bucket: str
    target_region: str
    target_prefix: str
    config: str
    input_artifact_sha256: str | None
    produced_by: str | None
    tool_slice_sha256: str
    platform_sha256: str
    image_uri: str
    image_set_sha256: str
    executor_env: str
    service_account: str
    secret_resource: str | None
    job_name: str
    result_prefix: str
    purpose: str
    statistic: str
    origin: str

    @property
    def attempt_id(self) -> str:
        return identity.attempt_id(self.case_id, self.attempt)

    @property
    def visible_memory_gb(self) -> int:
        return self.container_memory_gb or self.memory_gb

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Attempt:
        return cls(**{field: row[field] for field in cls.__dataclass_fields__})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(document: Mapping[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def validate_suite(value: str) -> str:
    """The one value used as path segment, job label, and job-name prefix."""
    if SUITE_RE.fullmatch(value) is None:
        raise CampaignError("suite must be lowercase alphanumerics and hyphens, 2-32 characters")
    return value


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
        for field in (
            "tool_build_sha256",
            "tool_artifact_sha256",
            "recipe_sha256",
            "build_inputs_sha256",
            "adapter_bundle_sha256",
            "tool_slice_sha256",
            "platform_sha256",
        ):
            if HEX64_RE.fullmatch(image[field]) is None:
                raise CampaignError(f"{tool}: {field} must be 64 lowercase hex digits")
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


def render_batch_job(
    attempt: Attempt,
    image: Mapping[str, str],
    case_env: Iterable[tuple[str, str]],
    *,
    suite: str,
    options: BatchOptions,
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
    )
    commands = [item for pair in pairs for item in pair]
    for name, value in case_env:
        commands.extend(("--case-env", f"{name}={value}"))
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
        "maxRunDuration": f"{attempt.timeout_s + int(options.term_grace) + 300}s",
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


def _job_from_dict(document: dict[str, Any]) -> batch_v1.Job:
    protobuf = batch_v1.Job.pb(batch_v1.Job())
    ParseDict(document, protobuf)
    return cast(batch_v1.Job, batch_v1.Job.wrap(protobuf))


def _job_document(job: batch_v1.Job) -> dict[str, Any]:
    value = MessageToDict(batch_v1.Job.pb(job), preserving_proto_field_name=False)
    return {
        key: value[key]
        for key in ("labels", "taskGroups", "allocationPolicy", "logsPolicy")
        if key in value
    }


def _matches_intent(job: batch_v1.Job, resource_name: str, expected: dict[str, Any]) -> bool:
    if job.name != resource_name:
        return False
    actual = batch_v1.Job(job)
    for group in actual.task_groups:
        group.name = ""
    actual.allocation_policy.labels.pop("batch-job-id", None)
    # Batch resolves allowedLocations for itself: it echoes the enclosing region
    # back, and expands an unrestricted request into that region's zones. Neither
    # is a different job, so the check is that every location this launch asked
    # for survived, and the provider's own expansion is then left out of the
    # byte comparison on both sides.
    requested = expected.get("allocationPolicy", {}).get("location", {}).get("allowedLocations", [])
    actual_locations = list(actual.allocation_policy.location.allowed_locations)
    if not set(requested) <= set(actual_locations):
        return False
    batch_v1.AllocationPolicy.pb(actual.allocation_policy).ClearField("location")
    intended = _job_from_dict(expected)
    batch_v1.AllocationPolicy.pb(intended.allocation_policy).ClearField("location")
    return _job_document(intended) == _job_document(actual)


def _close_batch_client(client: batch_v1.BatchServiceClient) -> None:
    try:
        client.transport.close()  # type: ignore[no-untyped-call]
    except Exception as exc:
        raise CampaignError(f"could not close Batch client: {exc}") from exc


def ensure_job(
    project: str,
    location: str,
    job_name: str,
    request: dict[str, Any],
    *,
    client: batch_v1.BatchServiceClient | None = None,
) -> tuple[str, str | None]:
    """Create the job, or reconcile with one of that name, and say which state that is.

    `SUBMITTED` covers a job this run created and one of that name it found
    already matching the recorded request; `NOT_CREATED` covers a refusal and a
    job of that name that does not match. `model.md` § *The state column*.
    """
    owned = client is None
    selected = client or batch_v1.BatchServiceClient()
    parent = f"projects/{project}/locations/{location}"
    resource_name = f"{parent}/jobs/{job_name}"
    try:
        try:
            created = selected.create_job(
                parent=parent,
                job=_job_from_dict(request),
                job_id=job_name,
                retry=None,
                timeout=20,
            )
            if not _matches_intent(created, resource_name, request):
                raise CampaignError(
                    f"{job_name}: provider created a job that does not match intent"
                )
            return "SUBMITTED", None
        except AlreadyExists:
            existing = selected.get_job(name=resource_name, retry=None, timeout=20)
            if not _matches_intent(existing, resource_name, request):
                return "NOT_CREATED", f"{job_name}: existing job does not match recorded intent"
            return "SUBMITTED", f"{job_name}: adopted an existing job matching recorded intent"
        except (BadRequest, Forbidden, Unauthorized, FailedPrecondition, NotFound) as exc:
            return "NOT_CREATED", f"{type(exc).__name__}: {exc}"
        except GoogleAPIError as exc:
            try:
                existing = selected.get_job(name=resource_name, retry=None, timeout=20)
            except (NotFound, GoogleAPIError):
                raise CampaignError(f"{job_name}: create outcome is ambiguous: {exc}") from exc
            if not _matches_intent(existing, resource_name, request):
                raise CampaignError(f"{job_name}: ambiguous create found a colliding job") from exc
            return "SUBMITTED", f"{job_name}: ambiguous create found the intended job"
    finally:
        if owned:
            _close_batch_client(selected)


def describe_job(
    project: str, location: str, job_name: str, *, client: batch_v1.BatchServiceClient
) -> str:
    job = client.get_job(
        name=f"projects/{project}/locations/{location}/jobs/{job_name}", retry=None, timeout=20
    )
    return str(batch_v1.JobStatus.State(job.status.state).name)


def list_job_states(
    project: str, location: str, suite: str, *, client: batch_v1.BatchServiceClient
) -> dict[str, str]:
    """Job name -> provider state for this suite's jobs under the parent.

    One paginated call answers a whole polling pass, and because the label
    carries the suite the filter is exact rather than a narrowing over anything
    benchmark-shaped. Rows are still matched by job name afterwards.
    """
    request = {
        "parent": f"projects/{project}/locations/{location}",
        "filter": f"labels.suite={suite}",
    }
    return {
        job.name.rsplit("/", 1)[-1]: str(batch_v1.JobStatus.State(job.status.state).name)
        for job in client.list_jobs(request=request, retry=None, timeout=60)
    }


def cancel_job(
    project: str, location: str, job_name: str, *, client: batch_v1.BatchServiceClient
) -> None:
    operation = client.delete_job(
        name=f"projects/{project}/locations/{location}/jobs/{job_name}", retry=None, timeout=20
    )
    operation.result(timeout=60)  # type: ignore[no-untyped-call]


def open_ledger(
    path: str, *, suite: str | None = None, readonly: bool = False
) -> sqlite3.Connection:
    """Open `campaign.db`, creating it for `suite` when it does not exist yet.

    A file whose `schema_version` this code does not recognise is refused: a
    command that adapted to whatever columns it found would write rows that are
    quietly incomplete.
    """
    con = sqlite3.connect(
        f"file:{path}?mode=ro" if readonly else path, uri=readonly, isolation_level=None
    )
    con.row_factory = sqlite3.Row
    existing = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if existing is None:
        if readonly or suite is None:
            con.close()
            raise CampaignError(f"{path} is not a campaign ledger")
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        con.execute(
            "INSERT INTO meta (id, suite, schema_version, created_at) VALUES (1, ?, ?, ?)",
            (validate_suite(suite), SCHEMA_VERSION, _now()),
        )
        return con
    row = con.execute("SELECT suite, schema_version FROM meta WHERE id = 1").fetchone()
    if row is None or row["schema_version"] != SCHEMA_VERSION:
        version = None if row is None else row["schema_version"]
        con.close()
        raise CampaignError(
            f"{path} states schema_version {version!r}; this code writes {SCHEMA_VERSION} "
            "and does not migrate"
        )
    if suite is not None and row["suite"] != suite:
        con.close()
        raise CampaignError(f"{path} is the {row['suite']!r} suite, not {suite!r}")
    return con


def ledger_suite(con: sqlite3.Connection) -> str:
    return str(con.execute("SELECT suite FROM meta WHERE id = 1").fetchone()["suite"])


def mint_group_id(con: sqlite3.Connection, override: str | None = None) -> str:
    """`gYYYYMMDD-HHMMSS`, or the operator's own name, unique within the file.

    Assigned without a round trip and typeable at a prompt, because `retry`,
    `cancel` and `prune` all take it as their scope. Two launches in one second
    are suffixed rather than merged: a group is what was launched together.
    """
    if override is not None:
        if GROUP_RE.fullmatch(override) is None:
            raise CampaignError("group id must be lowercase alphanumerics and hyphens")
        if _group_exists(con, override):
            raise CampaignError(f"group {override} already exists in this ledger")
        return override
    base = datetime.now(UTC).strftime("g%Y%m%d-%H%M%S")
    candidate, ordinal = base, 1
    while _group_exists(con, candidate):
        ordinal += 1
        candidate = f"{base}-{ordinal}"
    return candidate


def _group_exists(con: sqlite3.Connection, group_id: str) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM attempts WHERE group_id=? UNION ALL "
            "SELECT 1 FROM pending WHERE group_id=? LIMIT 1",
            (group_id, group_id),
        ).fetchone()
        is not None
    )


def attempt_rows(
    con: sqlite3.Connection, *, group_id: str | None = None, case_id: str | None = None
) -> list[sqlite3.Row]:
    """Every attempt, newest last, optionally scoped to one group or one case."""
    where, values = [], []
    if group_id is not None:
        where.append("group_id = ?")
        values.append(group_id)
    if case_id is not None:
        where.append("case_id = ?")
        values.append(case_id)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    return con.execute(
        f"SELECT * FROM attempts{clause} ORDER BY recorded_at, case_id, attempt", values
    ).fetchall()


def pending_rows(con: sqlite3.Connection, *, group_id: str | None = None) -> list[sqlite3.Row]:
    clause = " WHERE group_id = ?" if group_id is not None else ""
    values = [group_id] if group_id is not None else []
    return con.execute(f"SELECT * FROM pending{clause} ORDER BY group_id, slot", values).fetchall()


def journal_intent(
    con: sqlite3.Connection,
    *,
    case_id: str,
    case_inputs: str,
    build: Callable[[int], tuple[Attempt, str]],
    repeat: bool = False,
) -> tuple[Attempt, str]:
    """Allocate the next ordinal and write `SUBMITTING`, before any provider call.

    `build(ordinal) -> (attempt, request_json)` renders the request the row
    freezes, because the ordinal is part of the job name and the result prefix
    and is not known until this transaction holds the write lock. The ordinal is
    allocated inside it because groups may be submitted concurrently: the
    primary key makes a lost race an integrity error, the transaction is what
    stops the race.
    """
    con.execute("BEGIN IMMEDIATE")
    try:
        rows = con.execute(
            "SELECT attempt, case_inputs, state FROM attempts WHERE case_id=?", (case_id,)
        ).fetchall()
        for row in rows:
            if row["case_inputs"] != case_inputs:
                # 48 bits of hash, so this is a collision rather than a
                # coincidence: two cases filing evidence under one identity.
                raise CampaignError(
                    f"{case_id}: recorded case inputs differ from this case's — "
                    "two different cases hash to one case_id"
                )
        if not repeat and any(row["state"] == "SUCCEEDED" for row in rows):
            raise CampaignError(
                f"{case_id} already has a successful attempt; re-measuring is 'reps' "
                "in the plan or an explicit --repeat"
            )
        ordinal = max((int(row["attempt"]) for row in rows), default=0) + 1
        attempt, request = build(ordinal)
        now = _now()
        con.execute(
            f"""
            INSERT INTO attempts ({", ".join(_INSERT_COLUMNS)}, request_json, state,
                                  recorded_at, updated_at)
            VALUES ({", ".join(f":{name}" for name in _INSERT_COLUMNS)},
                    :request_json, 'SUBMITTING', :now, :now)
            """,
            {
                **{name: getattr(attempt, name) for name in _INSERT_COLUMNS},
                "request_json": request,
                "now": now,
            },
        )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return attempt, request


_INSERT_COLUMNS = tuple(Attempt.__dataclass_fields__)


def set_state(
    con: sqlite3.Connection, attempt_id: str, state: str, detail: str | None = None
) -> None:
    """Write one attempt's state, stamping `settled_at` when it settles."""
    now = _now()
    con.execute(
        "UPDATE attempts SET state=?, state_detail=?, updated_at=?, "
        "settled_at=CASE WHEN ? THEN ? ELSE settled_at END WHERE attempt_id=?",
        (state, detail, now, state in TERMINAL_STATES, now, attempt_id),
    )


def _heap_env(
    tool: str, *, visible_memory_gb: int, heap_percent: int
) -> tuple[tuple[str, str], ...]:
    """What a managed runtime is told about its own memory, from the shared table.

    Derived rather than stored: the row carries `heap_percent` and the ceiling,
    and `benchmark/plans/tools.yaml` says which variable each runtime reads. A
    retry re-derives it and the frozen-request diff refuses if the table moved.
    """
    policies = bench.load_heap_config(bench.bench_dir() / "tools.yaml").policies
    policy = policies.get(tool)
    if policy is None:
        return ()
    return (policy.render(percent=heap_percent, visible_memory_gb=visible_memory_gb),)


def _submit(con: sqlite3.Connection, attempt: Attempt, request: str, options: BatchOptions) -> str:
    """Call the provider for an already-journaled row and record what came back."""
    try:
        state, detail = ensure_job(
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
    *,
    suite: str,
    group_id: str,
    plan: Plan,
    image_set: ImageSet,
    results_bucket: str,
    options: BatchOptions,
) -> tuple[str, str, Callable[[int], tuple[Attempt, str]]]:
    """Mint one plan case's identity and return what builds its nth attempt.

    Split from :func:`submit_case` because the ordinal is not known until the
    journaling transaction holds the lock, and because `--dry-run` renders the
    same request without a ledger to allocate one from.
    """
    image = image_set.image_for(case.tool)
    case_id, case_inputs = case_identity(
        case,
        auth_role=case.auth_role,
        target_bucket=plan.bucket,
        target_region=plan.region,
        location=options.location,
        tool_slice=image["tool_slice_sha256"],
        platform=image["platform_sha256"],
    )
    config = _canonical(dict(case.config))
    env = _heap_env(
        case.tool,
        visible_memory_gb=case.resources.visible_memory_gb,
        heap_percent=case.heap_percent,
    )

    def build(ordinal: int) -> tuple[Attempt, str]:
        attempt = Attempt(
            case_id=case_id,
            attempt=ordinal,
            case_inputs=case_inputs,
            group_id=group_id,
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
            target_bucket=plan.bucket,
            target_region=plan.region,
            target_prefix=TARGET_PREFIX,
            config=config,
            input_artifact_sha256=None,
            produced_by=None,
            tool_slice_sha256=image["tool_slice_sha256"],
            platform_sha256=image["platform_sha256"],
            image_uri=image["image_uri"],
            image_set_sha256=image_set.sha256,
            executor_env=options.executor_env(),
            service_account=options.service_account_for(case.auth_role),
            secret_resource=options.secret_for(case.auth_role),
            job_name=job_name_for(suite, case_id, ordinal),
            result_prefix=result_prefix_for(
                results_bucket, suite, plan.bucket, identity.attempt_id(case_id, ordinal)
            ),
            purpose=case.purpose,
            statistic=case.statistic,
            origin="planned",
        )
        return attempt, _canonical(
            render_batch_job(attempt, image, env, suite=suite, options=options)
        )

    return case_id, case_inputs, build


def submit_case(
    con: sqlite3.Connection,
    case: Case,
    *,
    suite: str,
    group_id: str,
    plan: Plan,
    image_set: ImageSet,
    results_bucket: str,
    options: BatchOptions,
    repeat: bool = False,
) -> Attempt:
    """Journal one planned attempt of `case`, then create its job."""
    case_id, case_inputs, build = planned_attempt(
        case,
        suite=suite,
        group_id=group_id,
        plan=plan,
        image_set=image_set,
        results_bucket=results_bucket,
        options=options,
    )
    attempt, request = journal_intent(
        con, case_id=case_id, case_inputs=case_inputs, build=build, repeat=repeat
    )
    _submit(con, attempt, request, options)
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
    env = _heap_env(
        previous.tool,
        visible_memory_gb=previous.visible_memory_gb,
        heap_percent=previous.heap_percent,
    )

    def build(ordinal: int) -> tuple[Attempt, str]:
        attempt_id = identity.attempt_id(previous.case_id, ordinal)
        attempt = Attempt(
            **{
                **{name: getattr(previous, name) for name in _INSERT_COLUMNS},
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
        request = render_batch_job(attempt, image, env, suite=suite, options=options)
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
                listed[parent] = list_job_states(project, attempt.location, suite, client=client)
            except GoogleAPIError as exc:
                # A listing that fails costs the pass nothing: every row below
                # falls back to the point read it would have done anyway.
                print(f"campaign: job listing failed: {exc}", file=sys.stderr)
                listed[parent] = {}
        state = listed[parent].get(attempt.job_name)
        if state is None:
            try:
                state = describe_job(project, attempt.location, attempt.job_name, client=client)
            except GoogleAPIError as exc:
                print(f"campaign: describe failed for {attempt.job_name}: {exc}", file=sys.stderr)
                all_terminal = False
                continue
        set_state(con, attempt.attempt_id, state, row["state_detail"])
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
    if args.dry_run:
        # Nothing is journaled and nothing is created, so every case renders at
        # the ordinal a first launch would give it.
        for case in loaded.cases:
            _, _, build = planned_attempt(
                case,
                suite=suite,
                group_id=args.group or "dry-run",
                plan=loaded,
                image_set=image_set,
                results_bucket=args.results_bucket,
                options=options,
            )
            attempt, request = build(1)
            print(f"{attempt.attempt_id} {attempt.job_name} {request}")
        return 0
    con = open_ledger(args.state, suite=suite)
    try:
        group_id = mint_group_id(con, args.group)
        for case in loaded.cases:
            # `reps: N` is N planned attempts of one case, each its own job on
            # its own fresh machine.
            for _ in range(case.reps):
                attempt = submit_case(
                    con,
                    case,
                    suite=suite,
                    group_id=group_id,
                    plan=loaded,
                    image_set=image_set,
                    results_bucket=args.results_bucket,
                    options=options,
                    repeat=args.repeat,
                )
                print(f"campaign: {attempt.attempt_id} {attempt.job_name}")
        print(f"campaign: group {group_id}")
    finally:
        con.close()
    return 0


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
                _close_batch_client(client)
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
            print(
                f"slot {slot['group_id']}/{slot['slot']:<8} {slot['state']:<12} "
                f"{slot['purpose']:<12} awaiting {slot['awaiting']}"
            )
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
            attempt = retry_attempt(
                con,
                row,
                suite=suite,
                image_set=image_set,
                results_bucket=args.results_bucket,
                options=options,
            )
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
            cancel_job(project, attempt.location, attempt.job_name, client=client)
            set_state(con, attempt.attempt_id, "CANCELLED", "cancelled by the operator")
    finally:
        try:
            if client is not None:
                _close_batch_client(client)
        finally:
            con.close()
    return 0


def cmd_accept_failure(args: argparse.Namespace) -> int:
    con = open_ledger(args.state)
    try:
        row = con.execute(
            "SELECT state, state_detail FROM attempts WHERE attempt_id=?", (args.attempt,)
        ).fetchone()
        if row is None or row["state"] not in RETRYABLE_STATES:
            raise CampaignError("accept-failure requires one settled failed attempt")
        # An absent measurement, recorded as absent: the detail keeps which
        # failure was accepted, since ACCEPTED itself does not say.
        detail = f"accepted {row['state']}"
        if row["state_detail"]:
            detail = f"{detail}: {row['state_detail']}"
        set_state(con, args.attempt, "ACCEPTED", detail)
        print(f"campaign: {args.attempt} marked ACCEPTED ({row['state']})")
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
    accept_failure.add_argument("--attempt", required=True)
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
