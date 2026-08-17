"""Small, plan-driven GCP Batch campaign controller with durable submission intent."""

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

from benchmark import verify
from benchmark.contract import (
    CREDENTIAL_ENV_VAR,
    EXIT_DRIFT,
    EXIT_FAIL,
    EXIT_PASS,
    TOOLBOX_TOOLS,
)
from benchmark.plan import Case, Plan

STATE_FILENAME = "campaign.db"
RETRYABLE_STATES = {"FAILED", "NOT_CREATED", "COLLISION"}
ACCEPTED_FAILURE_STATES = {f"ACCEPTED_{state}" for state in RETRYABLE_STATES}
TERMINAL_STATES = {
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "NOT_CREATED",
    "COLLISION",
    *ACCEPTED_FAILURE_STATES,
}
N4_BOOT_DISK = {"type": "hyperdisk-balanced", "image": "batch-cos"}
# Anchored to the repository so verification does not depend on where the
# operator is standing when they run it.
DEFAULT_ADAPTER_ROOT = str(Path(__file__).resolve().parents[3] / "tools")
# Every rendered job carries a benchmark-intent label, so one server-side
# filtered listing covers a polling pass for a project this study shares.
BENCHMARK_JOB_FILTER = "labels.benchmark-intent:*"
PINNED_IMAGE_RE = re.compile(r"\A[^\s@]+@sha256:([0-9a-f]{64})\Z")
SECRET_RE = re.compile(r"\Aprojects/[^/]+/secrets/[^/]+/versions/[^/]+\Z")
HEX64_RE = re.compile(r"\A[0-9a-f]{64}\Z")
REVISION_RE = re.compile(r"\A[0-9a-f]{40}\Z")
CAMPAIGN_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}-[a-z][a-z0-9-]*\Z")
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
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    base_job_id TEXT NOT NULL,
    submission INTEGER NOT NULL,
    job_id TEXT NOT NULL UNIQUE,
    campaign_id TEXT NOT NULL,
    project TEXT NOT NULL,
    location TEXT NOT NULL,
    tool TEXT NOT NULL,
    mode TEXT NOT NULL,
    case_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    rep INTEGER NOT NULL,
    bucket TEXT NOT NULL,
    region TEXT NOT NULL,
    image_uri TEXT NOT NULL,
    image_set_sha256 TEXT NOT NULL,
    destination TEXT NOT NULL,
    job_json TEXT NOT NULL,
    state TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (base_job_id, submission)
)
"""


class CampaignError(RuntimeError):
    """Campaign input or provider state cannot be used safely."""


class JobCollisionError(CampaignError):
    """The requested provider name belongs to a different immutable job."""


@dataclass(frozen=True)
class ImageSet:
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
    # The Secret Manager version holding the authenticated stratum's credential
    # payload. Only an authenticated case's job carries it, and only the
    # authenticated worker identity can read it.
    aws_credential_secret: str | None = None
    term_grace: float = 5.0


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")


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
    if document["schema_version"] != 4 or not isinstance(document["tools"], dict):
        raise CampaignError("image set schema_version must be 4 and tools must be an object")
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


def validate_campaign_id(value: str) -> str:
    if CAMPAIGN_RE.fullmatch(value) is None:
        raise CampaignError("campaign id must look like YYYY-MM-DD-name")
    return value


def job_id_for(
    case: Case,
    rep: int,
    *,
    campaign_id: str,
    bucket: str,
    region: str,
    image_uri: str,
) -> str:
    identity = json.dumps(
        [campaign_id, bucket, region, case.tool, case.fingerprint, image_uri, rep],
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
    readable = _slug(f"{campaign_id}-{case.tool}-{case.case_id}")[:43].rstrip("-")
    # The namespace is intentionally disjoint from every retired controller.
    # AlreadyExists reconciliation can therefore adopt only a job created by
    # this benchmark implementation with this exact immutable request.
    return f"benchmark-{readable}-{digest}"[:63].rstrip("-")


def planned_job_ids(plan: Plan, campaign_id: str, image_set: ImageSet) -> list[str]:
    ids = [
        job_id_for(
            case,
            rep,
            campaign_id=campaign_id,
            bucket=plan.bucket,
            region=plan.region,
            image_uri=image_set.image_uri,
        )
        for case in plan.cases
        for rep in range(1, case.reps + 1)
    ]
    if len(ids) != len(set(ids)):
        raise CampaignError("generated Batch job IDs collide")
    return ids


def submission_job_id(base_job_id: str, submission: int) -> str:
    if submission < 1:
        raise CampaignError("submission number must be positive")
    if submission == 1:
        return base_job_id
    digest = hashlib.sha256(f"{base_job_id}:{submission}".encode()).hexdigest()[:12]
    suffix = f"-r{submission}-{digest}"
    readable = base_job_id[: 63 - len(suffix)].rstrip("-")
    return f"{readable}{suffix}"


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
    case: Case,
    destination: str,
    image: dict[str, str],
    image_set_sha256: str,
    *,
    campaign_id: str,
    job_id: str,
    rep: int,
    submission: int,
    bucket: str,
    region: str,
    options: BatchOptions,
) -> dict[str, Any]:
    _validate_batch_options(options)
    secret_variables: dict[str, str] = {}
    if case.auth_role is not None:
        if not options.authenticated_worker_sa:
            raise CampaignError(
                f"case signs with role {case.auth_role} and requires a signing worker "
                "service account"
            )
        secret = options.aws_credential_secret
        if not secret or SECRET_RE.fullmatch(secret) is None:
            raise CampaignError(
                f"case signs with role {case.auth_role} and requires a Secret Manager "
                "version resource"
            )
        # One variable, whose payload the worker parses. A case that lists
        # unsigned has no environment block at all, so the role a case resolved
        # to is what decides whether a credential is present.
        secret_variables = {CREDENTIAL_ENV_VAR: secret}
        service_account = options.authenticated_worker_sa
    else:
        service_account = options.anonymous_worker_sa

    container_memory = case.resources.container_memory_gb
    pairs = (
        ("--tool", case.tool),
        ("--mode", case.mode),
        ("--bucket", bucket),
        ("--region", region),
        *(() if case.auth_role is None else (("--auth-role", case.auth_role),)),
        ("--prefix", ""),
        ("--output", "/tmp/attempt"),
        ("--destination", destination),
        ("--timeout", str(case.timeout_s)),
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
        ("--image-set-sha256", image_set_sha256),
        ("--campaign-id", campaign_id),
        ("--job-id", job_id),
        ("--case-id", case.case_id),
        ("--case-fingerprint", case.fingerprint),
        ("--run-ordinal", str(rep)),
        ("--submission-number", str(submission)),
        ("--machine-type", case.resources.machine_type),
        ("--vcpus", str(case.resources.vcpus)),
        ("--memory-gb", str(case.resources.memory_gb)),
        ("--container-memory-gb", "none" if container_memory is None else str(container_memory)),
    )
    commands = [item for pair in pairs for item in pair]
    for name, value in case.env:
        commands.extend(("--case-env", f"{name}={value}"))
    container: dict[str, Any] = {"imageUri": image["image_uri"], "commands": commands}
    if case.resources.docker_options:
        container["options"] = shlex.join(case.resources.docker_options)
    task_spec: dict[str, Any] = {
        "runnables": [{"container": container}],
        "computeResource": {
            "cpuMilli": str(case.resources.cpu_milli),
            "memoryMib": str(case.resources.memory_mib),
        },
        "maxRetryCount": 0,
        "maxRunDuration": f"{case.timeout_s + int(options.term_grace) + 300}s",
    }
    if secret_variables:
        task_spec["environment"] = {"secretVariables": secret_variables}
    policy: dict[str, Any] = {
        "machineType": case.resources.machine_type,
        "provisioningModel": options.provisioning,
    }
    if case.resources.machine_type.startswith("n4-"):
        policy["bootDisk"] = dict(N4_BOOT_DISK)
    allocation: dict[str, Any] = {
        "instances": [{"policy": policy}],
        "serviceAccount": {"email": service_account},
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
        "labels": {"benchmark-intent": hashlib.sha256(job_id.encode()).hexdigest()[:32]},
        "taskGroups": [{"taskCount": "1", "parallelism": "1", "taskSpec": task_spec}],
        "allocationPolicy": allocation,
        "logsPolicy": {"destination": "CLOUD_LOGGING"},
    }


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


def _adoption_exact(
    job: batch_v1.Job, resource_name: str, expected: dict[str, Any], location: str
) -> bool:
    if job.name != resource_name:
        return False
    actual = batch_v1.Job(job)
    for group in actual.task_groups:
        group.name = ""
    actual.allocation_policy.labels.pop("batch-job-id", None)
    # Batch resolves allowedLocations for itself: it echoes the enclosing region
    # back, and expands an unrestricted request into that region's zones. Neither
    # is a different job, so the check is that every location this campaign asked
    # for survived, and the provider's own expansion is then left out of the
    # byte comparison on both sides.
    del location
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
    job_id: str,
    job_dict: dict[str, Any],
    *,
    client: batch_v1.BatchServiceClient | None = None,
) -> str:
    owned = client is None
    selected = client or batch_v1.BatchServiceClient()
    parent = f"projects/{project}/locations/{location}"
    resource_name = f"{parent}/jobs/{job_id}"
    try:
        try:
            created = selected.create_job(
                parent=parent,
                job=_job_from_dict(job_dict),
                job_id=job_id,
                retry=None,
                timeout=20,
            )
            if not _adoption_exact(created, resource_name, job_dict, location):
                raise CampaignError(f"{job_id}: provider created a job that does not match intent")
            return "SUBMITTED"
        except AlreadyExists:
            existing = selected.get_job(name=resource_name, retry=None, timeout=20)
            if not _adoption_exact(existing, resource_name, job_dict, location):
                raise JobCollisionError(
                    f"{job_id}: existing Batch job collides with recorded intent"
                ) from None
            return "ADOPTED"
        except (BadRequest, Forbidden, Unauthorized, FailedPrecondition, NotFound):
            return "NOT_CREATED"
        except GoogleAPIError as exc:
            try:
                existing = selected.get_job(name=resource_name, retry=None, timeout=20)
            except (NotFound, GoogleAPIError):
                raise CampaignError(f"{job_id}: create outcome is ambiguous: {exc}") from exc
            if not _adoption_exact(existing, resource_name, job_dict, location):
                raise CampaignError(f"{job_id}: ambiguous create found a colliding job") from exc
            return "ADOPTED"
    finally:
        if owned:
            _close_batch_client(selected)


def describe_job(
    project: str,
    location: str,
    job_id: str,
    *,
    client: batch_v1.BatchServiceClient,
) -> str:
    job = client.get_job(
        name=f"projects/{project}/locations/{location}/jobs/{job_id}", retry=None, timeout=20
    )
    return str(batch_v1.JobStatus.State(job.status.state).name)


def list_job_states(
    project: str,
    location: str,
    *,
    client: batch_v1.BatchServiceClient,
) -> dict[str, str]:
    """Return job ID -> provider state for benchmark-owned jobs under the parent.

    One paginated call answers a whole polling pass, so a campaign's cost is a
    request per pass rather than a request per submission. The label every
    rendered job carries is what keeps a shared project's unrelated Batch work
    out of the response; rows are still matched by exact job ID afterwards, so
    the filter is a narrowing, never the identity.
    """
    request = {"parent": f"projects/{project}/locations/{location}", "filter": BENCHMARK_JOB_FILTER}
    return {
        job.name.rsplit("/", 1)[-1]: str(batch_v1.JobStatus.State(job.status.state).name)
        for job in client.list_jobs(request=request, retry=None, timeout=60)
    }


def cancel_job(
    project: str,
    location: str,
    job_id: str,
    *,
    client: batch_v1.BatchServiceClient,
) -> None:
    operation = client.delete_job(
        name=f"projects/{project}/locations/{location}/jobs/{job_id}", retry=None, timeout=20
    )
    operation.result(timeout=60)  # type: ignore[no-untyped-call]


def open_db(path: str, *, readonly: bool = False) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro" if readonly else path, uri=readonly)
    if not readonly:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(SCHEMA)
        columns = {row[1] for row in con.execute("PRAGMA table_info(submissions)")}
        for name in ("project", "location"):
            if name not in columns:
                con.execute(f"ALTER TABLE submissions ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        con.commit()
    con.row_factory = sqlite3.Row
    return con


def record_intent(
    con: sqlite3.Connection,
    *,
    base_job_id: str,
    submission: int,
    job_id: str,
    campaign_id: str,
    project: str,
    location: str,
    case: Case,
    rep: int,
    bucket: str,
    region: str,
    image_uri: str,
    image_set_sha256: str,
    destination: str,
    job_dict: dict[str, Any],
) -> None:
    now = _now()
    try:
        con.execute(
            """
            INSERT INTO submissions (
                base_job_id, submission, job_id, campaign_id, project, location,
                tool, mode, case_id, fingerprint, rep, bucket, region,
                image_uri, image_set_sha256, destination, job_json, state,
                submitted_at, updated_at
            ) VALUES (
                :base_job_id, :submission, :job_id, :campaign_id, :project, :location,
                :tool, :mode, :case_id, :fingerprint, :rep, :bucket, :region,
                :image_uri, :image_set_sha256, :destination, :job_json, :state,
                :submitted_at, :updated_at
            )
            """,
            {
                "base_job_id": base_job_id,
                "submission": submission,
                "job_id": job_id,
                "campaign_id": campaign_id,
                "project": project,
                "location": location,
                "tool": case.tool,
                "mode": case.mode,
                "case_id": case.case_id,
                "fingerprint": case.fingerprint,
                "rep": rep,
                "bucket": bucket,
                "region": region,
                "image_uri": image_uri,
                "image_set_sha256": image_set_sha256,
                "destination": destination,
                "job_json": json.dumps(job_dict, sort_keys=True),
                "state": "SUBMITTING",
                "submitted_at": now,
                "updated_at": now,
            },
        )
        con.commit()
    except sqlite3.IntegrityError as exc:
        raise CampaignError(f"{job_id}: submission intent already exists") from exc


def update_submission_state(con: sqlite3.Connection, job_id: str, state: str) -> None:
    con.execute(
        "UPDATE submissions SET state=?, updated_at=? WHERE job_id=?", (state, _now(), job_id)
    )
    con.commit()


def all_submissions(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM submissions ORDER BY base_job_id, submission").fetchall()


def latest_submissions(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT s.* FROM submissions s JOIN "
        "(SELECT base_job_id, max(submission) submission FROM submissions GROUP BY base_job_id) x "
        "ON s.base_job_id=x.base_job_id AND s.submission=x.submission ORDER BY s.base_job_id"
    ).fetchall()


def retry_job_document(
    previous: dict[str, Any], *, job_id: str, destination: str, submission: int
) -> dict[str, Any]:
    """Rewrite only the three retry identities in a frozen Batch request."""
    document = copy.deepcopy(previous)
    try:
        commands = document["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]["commands"]
        if not isinstance(commands, list):
            raise TypeError
        replacements = {
            "--job-id": job_id,
            "--destination": destination,
            "--submission-number": str(submission),
        }
        seen: set[str] = set()
        for index in range(0, len(commands), 2):
            name = commands[index]
            if name in replacements:
                commands[index + 1] = replacements[name]
                seen.add(name)
        if seen != set(replacements):
            raise ValueError
        document["labels"]["benchmark-intent"] = hashlib.sha256(job_id.encode()).hexdigest()[:32]
    except (IndexError, KeyError, TypeError, ValueError):
        raise CampaignError("recorded Batch request is not safely retryable") from None
    return document


def _options(args: argparse.Namespace) -> BatchOptions:
    return BatchOptions(
        args.anonymous_worker_sa,
        args.authenticated_worker_sa,
        args.network,
        args.subnetwork,
        args.zone,
        args.provisioning,
        args.secret_resource,
    )


def _submit_one(
    con: sqlite3.Connection,
    args: argparse.Namespace,
    plan: Plan,
    case: Case,
    image_set: ImageSet,
    rep: int,
    submission: int,
    base: str,
    previous_job_json: dict[str, Any] | None = None,
) -> None:
    job_id = submission_job_id(base, submission)
    destination = (
        f"gs://{args.results_bucket}/campaigns/{args.campaign_id}/results/{plan.bucket}/"
        f"{case.tool}/{case.case_id}/run-{rep}/submission-{submission}/"
    )
    image = image_set.image_for(case.tool)
    job_dict = render_batch_job(
        case,
        destination,
        image,
        image_set.sha256,
        campaign_id=args.campaign_id,
        job_id=job_id,
        rep=rep,
        submission=submission,
        bucket=plan.bucket,
        region=plan.region,
        options=_options(args),
    )
    if previous_job_json is not None:
        expected_retry = retry_job_document(
            previous_job_json,
            job_id=job_id,
            destination=destination,
            submission=submission,
        )
        if job_dict != expected_retry:
            raise CampaignError("retry changes the recorded immutable Batch request policy")
    record_intent(
        con,
        base_job_id=base,
        submission=submission,
        job_id=job_id,
        campaign_id=args.campaign_id,
        project=args.project,
        location=args.location,
        case=case,
        rep=rep,
        bucket=plan.bucket,
        region=plan.region,
        image_uri=image["image_uri"],
        image_set_sha256=image_set.sha256,
        destination=destination,
        job_dict=job_dict,
    )
    try:
        state = ensure_job(args.project, args.location, job_id, job_dict)
    except JobCollisionError:
        update_submission_state(con, job_id, "COLLISION")
        raise
    except CampaignError:
        update_submission_state(con, job_id, "AMBIGUOUS")
        raise
    update_submission_state(con, job_id, state)


def cmd_submit(args: argparse.Namespace) -> int:
    plan = Plan.load(Path(args.plan))
    validate_campaign_id(args.campaign_id)
    image_set = load_image_set(args.image_set, {case.tool for case in plan.cases})
    planned_job_ids(plan, args.campaign_id, image_set)
    if args.dry_run:
        options = _options(args)
        for case in plan.cases:
            for rep in range(1, case.reps + 1):
                image = image_set.image_for(case.tool)
                base = job_id_for(
                    case,
                    rep,
                    campaign_id=args.campaign_id,
                    bucket=plan.bucket,
                    region=plan.region,
                    image_uri=image["image_uri"],
                )
                destination = (
                    f"gs://{args.results_bucket}/campaigns/{args.campaign_id}/results/"
                    f"{plan.bucket}/{case.tool}/{case.case_id}/run-{rep}/submission-1/"
                )
                rendered = render_batch_job(
                    case,
                    destination,
                    image,
                    image_set.sha256,
                    campaign_id=args.campaign_id,
                    job_id=base,
                    rep=rep,
                    submission=1,
                    bucket=plan.bucket,
                    region=plan.region,
                    options=options,
                )
                print(f"{base} {json.dumps(rendered, sort_keys=True, separators=(',', ':'))}")
        return 0
    con = open_db(args.state)
    try:
        for case in plan.cases:
            for rep in range(1, case.reps + 1):
                base = job_id_for(
                    case,
                    rep,
                    campaign_id=args.campaign_id,
                    bucket=plan.bucket,
                    region=plan.region,
                    image_uri=image_set.image_uri,
                )
                existing = con.execute(
                    "SELECT * FROM submissions WHERE job_id=?", (base,)
                ).fetchone()
                if existing:
                    if existing["project"] != args.project or existing["location"] != args.location:
                        raise CampaignError(
                            "recorded submission belongs to a different provider parent"
                        )
                    if existing["state"] not in {"SUBMITTING", "AMBIGUOUS"}:
                        print(f"campaign: skipping already-tracked job {base}")
                        continue
                    state = ensure_job(
                        args.project, args.location, base, json.loads(existing["job_json"])
                    )
                    update_submission_state(con, base, state)
                    continue
                _submit_one(con, args, plan, case, image_set, rep, 1, base)
    finally:
        con.close()
    return 0


def poll_once(
    project: str,
    location: str,
    con: sqlite3.Connection,
    rows: list[sqlite3.Row],
    *,
    client: batch_v1.BatchServiceClient,
) -> bool:
    pending = [row for row in rows if row["state"] not in TERMINAL_STATES]
    if not pending:
        return True
    try:
        listed = list_job_states(project, location, client=client)
    except GoogleAPIError as exc:
        # A listing that fails costs the pass nothing: every row below simply
        # falls back to the point read it would have done anyway.
        print(f"campaign: job listing failed, describing each submission: {exc}", file=sys.stderr)
        listed = {}
    all_terminal = True
    for row in pending:
        state = listed.get(row["job_id"])
        if state is None:
            # The listing did not account for this submission. Ask for it by
            # name so a job the provider does not have is a reported error
            # rather than an absence indistinguishable from a narrowed filter.
            try:
                state = describe_job(project, location, row["job_id"], client=client)
            except GoogleAPIError as exc:
                print(f"campaign: describe failed for {row['job_id']}: {exc}", file=sys.stderr)
                all_terminal = False
                continue
        update_submission_state(con, row["job_id"], state)
        all_terminal &= state in TERMINAL_STATES
    return all_terminal


def cmd_poll(args: argparse.Namespace) -> int:
    con = open_db(args.state)
    client: batch_v1.BatchServiceClient | None = None
    try:
        client = batch_v1.BatchServiceClient()
        rows_for = all_submissions if args.all else latest_submissions
        rows = rows_for(con)
        validate_provider_parent(rows, args.project, args.location)
        if not args.watch:
            poll_once(args.project, args.location, con, rows, client=client)
            return 0
        while not poll_once(args.project, args.location, con, rows_for(con), client=client):
            time.sleep(args.interval)
        return 0
    finally:
        try:
            if client is not None:
                _close_batch_client(client)
        finally:
            con.close()


def cmd_status(args: argparse.Namespace) -> int:
    con = open_db(args.state, readonly=True)
    try:
        for row in latest_submissions(con):
            print(f"{row['job_id']:<63} {row['state']:<12} {row['tool']}")
    finally:
        con.close()
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    plan = Plan.load(Path(args.plan))
    validate_campaign_id(args.campaign_id)
    image_set = load_image_set(args.image_set, {case.tool for case in plan.cases})
    cases = {(case.tool, case.case_id): case for case in plan.cases}
    con = open_db(args.state)
    try:
        for row in latest_submissions(con):
            if args.job_id and row["job_id"] != args.job_id:
                continue
            if row["state"] not in RETRYABLE_STATES:
                if args.job_id:
                    raise CampaignError("only a settled failed submission is retryable")
                continue
            evidence_state = retry_evidence_state(row["destination"])
            if evidence_state == "COMPLETE":
                if args.job_id:
                    raise CampaignError("submission already has a complete recorded outcome")
                print(f"campaign: not retrying recorded outcome {row['job_id']}")
                continue
            if evidence_state == "AMBIGUOUS":
                raise CampaignError("submission attempt leaves are ambiguous; refusing retry")
            case = cases.get((row["tool"], row["case_id"]))
            if case is None or row["campaign_id"] != args.campaign_id:
                raise CampaignError("retry plan/campaign does not match recorded intent")
            if (
                row["fingerprint"] != case.fingerprint
                or row["bucket"] != plan.bucket
                or row["region"] != plan.region
            ):
                raise CampaignError("retry plan case or target does not match recorded intent")
            if (
                row["image_set_sha256"] != image_set.sha256
                or row["image_uri"] != image_set.image_uri
            ):
                raise CampaignError("retry image set does not match the frozen campaign")
            if row["project"] != args.project or row["location"] != args.location:
                raise CampaignError("retry provider parent does not match the frozen campaign")
            expected_destination_root = (
                f"gs://{args.results_bucket}/campaigns/{args.campaign_id}/results/{plan.bucket}/"
            )
            if not row["destination"].startswith(expected_destination_root):
                raise CampaignError("retry results bucket does not match the frozen campaign")
            _submit_one(
                con,
                args,
                plan,
                case,
                image_set,
                row["rep"],
                row["submission"] + 1,
                row["base_job_id"],
                previous_job_json=json.loads(row["job_json"]),
            )
    finally:
        con.close()
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    con = open_db(args.state)
    client: batch_v1.BatchServiceClient | None = None
    try:
        client = batch_v1.BatchServiceClient()
        rows = all_submissions(con)
        validate_provider_parent(rows, args.project, args.location)
        for row in rows:
            if row["state"] not in TERMINAL_STATES:
                cancel_job(args.project, args.location, row["job_id"], client=client)
                update_submission_state(con, row["job_id"], "CANCELLED")
    finally:
        try:
            if client is not None:
                _close_batch_client(client)
        finally:
            con.close()
    return 0


def cmd_accept_failure(args: argparse.Namespace) -> int:
    con = open_db(args.state)
    try:
        matches = [row for row in latest_submissions(con) if row["job_id"] == args.job_id]
        if len(matches) != 1 or matches[0]["state"] not in RETRYABLE_STATES:
            raise CampaignError("accept-failure requires one latest settled failed submission")
        state = f"ACCEPTED_{matches[0]['state']}"
        update_submission_state(con, args.job_id, state)
        print(f"campaign: {args.job_id} marked {state}")
    finally:
        con.close()
    return 0


def validate_provider_parent(rows: list[sqlite3.Row], project: str, location: str) -> None:
    if any(row["project"] != project or row["location"] != location for row in rows):
        raise CampaignError("CLI provider parent does not match the recorded campaign")


def aggregate_verify_exit(exit_codes: list[int]) -> int:
    """FAIL/refusal dominates DRIFT, which deliberately remains nonzero."""
    if not exit_codes:
        return EXIT_FAIL
    if any(code not in {EXIT_PASS, EXIT_DRIFT} for code in exit_codes):
        return EXIT_FAIL
    if EXIT_DRIFT in exit_codes:
        return EXIT_DRIFT
    return EXIT_PASS


def plan_binding_errors(plan: Plan, rows: list[sqlite3.Row]) -> list[str]:
    """Bind the current latest-submission roster back to the supplied plan."""
    expected = {
        (case.tool, case.case_id, rep): case
        for case in plan.cases
        for rep in range(1, case.reps + 1)
    }
    actual = {(row["tool"], row["case_id"], row["rep"]): row for row in rows}
    errors = []
    if len(actual) != len(rows):
        errors.append("duplicate ledger rows name the same plan case/run")
    for field in ("campaign_id", "image_set_sha256", "project", "location"):
        if rows and field not in tuple(rows[0].keys()):
            errors.append(f"ledger schema omits {field}")
        elif len({row[field] for row in rows}) > 1:
            errors.append(f"ledger rows disagree on {field}")
    for key in sorted(expected.keys() - actual.keys()):
        errors.append(f"missing ledger case {key}")
    for key in sorted(actual.keys() - expected.keys()):
        errors.append(f"unexpected ledger case {key}")
    for key in sorted(expected.keys() & actual.keys()):
        case, row = expected[key], actual[key]
        if row["fingerprint"] != case.fingerprint or row["mode"] != case.mode:
            errors.append(f"changed plan case {key}")
        if row["bucket"] != plan.bucket or row["region"] != plan.region:
            errors.append(f"changed plan target {key}")
    return errors


def retry_evidence_state(destination: str) -> str:
    leaves = verify.list_leaves(destination)
    if not leaves:
        return "ABSENT"
    completed = [leaf for leaf in leaves if verify.has_result_marker(leaf)]
    if len(leaves) == 1 and not completed:
        return "INCOMPLETE"
    if len(leaves) == 1 and len(completed) == 1:
        return "COMPLETE"
    return "AMBIGUOUS"


def cmd_verify(args: argparse.Namespace) -> int:
    plan = Plan.load(Path(args.plan))
    con = open_db(args.state, readonly=True)
    try:
        rows = latest_submissions(con)
    finally:
        con.close()
    binding_errors = plan_binding_errors(plan, rows)
    if binding_errors:
        print(
            "campaign: ledger does not match supplied plan: " + "; ".join(binding_errors),
            file=sys.stderr,
        )
        return EXIT_FAIL
    matches = [row for row in rows if row["case_id"] == args.reference_case]
    if len(matches) != 1 or matches[0]["state"] != "SUCCEEDED":
        print("campaign: reference case is missing, ambiguous, or unsuccessful", file=sys.stderr)
        return EXIT_FAIL
    reference = matches[0]
    exits: list[int] = []
    for row in rows:
        if row["job_id"] == reference["job_id"]:
            continue
        if row["state"] != "SUCCEEDED":
            exits.append(EXIT_FAIL)
            print(f"{row['job_id']}: refused -- Batch state is {row['state']}", file=sys.stderr)
            continue
        code, output = verify.verify_leaves(
            tool=row["tool"],
            bucket=plan.bucket,
            prefix="",
            mode=row["mode"],
            actual_destination=row["destination"],
            reference_destination=reference["destination"],
            adapter_root=args.adapter_root,
            expected_actual={
                "campaign_id": row["campaign_id"],
                "job_id": row["job_id"],
                "case_id": row["case_id"],
                "case_fingerprint": row["fingerprint"],
                "image": row["image_uri"],
                "image_set_sha256": row["image_set_sha256"],
            },
            expected_reference={
                "campaign_id": reference["campaign_id"],
                "job_id": reference["job_id"],
                "case_id": reference["case_id"],
                "case_fingerprint": reference["fingerprint"],
                "image": reference["image_uri"],
                "image_set_sha256": reference["image_set_sha256"],
            },
        )
        exits.append(code)
        print(f"{row['job_id']}: {output.get('verdict', 'REFUSED')} (exit {code})")
    return aggregate_verify_exit(exits)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default=STATE_FILENAME)
    sub = parser.add_subparsers(dest="command", required=True)

    def batch_target(p: argparse.ArgumentParser) -> None:
        p.add_argument("--project", required=True)
        p.add_argument("--location", required=True)

    def submission(p: argparse.ArgumentParser) -> None:
        p.add_argument("--plan", required=True)
        p.add_argument("--campaign-id", required=True)
        p.add_argument("--results-bucket", required=True)
        p.add_argument("--image-set", required=True)
        p.add_argument(
            "--secret-resource",
            metavar="projects/P/secrets/S/versions/V",
            help="Secret Manager version holding the authenticated stratum's "
            "KEY=VALUE credential payload. Required only when the plan has an "
            "authenticated case; anonymous cases never receive it.",
        )
        p.add_argument("--anonymous-worker-sa", required=True)
        p.add_argument("--authenticated-worker-sa")
        p.add_argument("--network")
        p.add_argument("--subnetwork")
        p.add_argument("--zone")
        p.add_argument("--provisioning", choices=("SPOT", "STANDARD"), default="SPOT")

    submit = sub.add_parser("submit")
    batch_target(submit)
    submission(submit)
    submit.add_argument("--dry-run", action="store_true")
    submit.set_defaults(func=cmd_submit)
    retry = sub.add_parser("retry")
    batch_target(retry)
    submission(retry)
    retry.add_argument("--job-id")
    retry.set_defaults(func=cmd_retry)
    poll = sub.add_parser("poll")
    batch_target(poll)
    poll.add_argument("--watch", action="store_true")
    poll.add_argument("--all", action="store_true")
    poll.add_argument("--interval", type=int, default=30)
    poll.set_defaults(func=cmd_poll)
    cancel = sub.add_parser("cancel")
    batch_target(cancel)
    cancel.set_defaults(func=cmd_cancel)
    accept_failure = sub.add_parser("accept-failure")
    accept_failure.add_argument("--job-id", required=True)
    accept_failure.set_defaults(func=cmd_accept_failure)
    status = sub.add_parser("status")
    status.set_defaults(func=cmd_status)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--plan", required=True)
    verify_parser.add_argument("--reference-case", required=True)
    verify_parser.add_argument("--adapter-root", default=DEFAULT_ADAPTER_ROOT)
    verify_parser.set_defaults(func=cmd_verify)
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
