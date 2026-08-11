"""Stateless Temporal observation and summary-only campaign reconciliation."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

import google.cloud.storage as storage  # type: ignore[import-untyped]
from google.api_core.exceptions import GoogleAPIError, PreconditionFailed
from google.auth.exceptions import DefaultCredentialsError
from temporalio.client import Client
from temporalio.envconfig import ClientConfig
from temporalio.exceptions import TemporalError

from s3_listing_study.common.argparse_utils import UniqueStoreAction
from s3_listing_study.manager.campaign import (
    JOB_ID_MAX,
    JOB_ID_RE,
    CampaignError,
    campaign_prefix,
)
from s3_listing_study.manager.campaign.cli import (
    IMAGE_SET_FIELDS,
    TEMPORAL_OWNER_MAX_BYTES,
    SubmissionError,
    TemporalOwner,
    TemporalScope,
    _canonical_json,
    _parse_owner,
)
from s3_listing_study.temporal import TASK_QUEUE
from s3_listing_study.temporal.models import CaseControllerProgress
from s3_listing_study.temporal.workflows import CampaignWorkflow

MANIFEST_MAX_BYTES = 8_000_000
TEMPORAL_INPUT_MAX_BYTES = 1_900_000
RESULT_MAX_BYTES = 1_000_000
REPORT_MAX_BYTES = 8_000_000
MAX_CONCURRENT_CHILD_DESCRIBES = 16
MAX_EXECUTION_LEAVES_PER_RUN = 256
DEFAULT_POLL_INTERVAL_S = 10.0
CONTROLLER_PHASES = ("pending", "running", "retrying", "awaiting_retry", "terminal")
EVIDENCE_STATES = ("pending", "missing", "recorded", "duplicate", "invalid", "unsealed")
SUBJECT_STATES = (
    "completed",
    "failed",
    "timed_out",
    "signaled",
    "harness_error",
    "unavailable",
)
PROVIDER_STATES = ("SUCCEEDED", "FAILED", "NOT_CREATED", "unavailable")


class ReportError(RuntimeError):
    """Frozen campaign, Temporal state, or result evidence is unsafe to report."""


@dataclass(frozen=True)
class ManifestCase:
    campaign: str
    job_id: str
    bucket: str
    tool: str
    case_id: str
    run_ordinal: int
    prefix: str
    attempt_fingerprint: str
    record: dict[str, Any]
    image: dict[str, Any]


@dataclass(frozen=True)
class ManifestSnapshot:
    bucket: str
    campaign: str
    generation: int
    content: bytes
    cases: tuple[ManifestCase, ...]
    sha256: str


@dataclass(frozen=True)
class TemporalCase:
    project: str
    location: str
    job_id: str
    resource_name: str


@dataclass(frozen=True)
class TemporalSnapshot:
    bucket: str
    campaign: str
    generation: int
    content: bytes
    sha256: str
    campaign_manifest_sha256: str
    scope: TemporalScope
    workflow_type: str
    task_queue: str
    cases: tuple[TemporalCase, ...]


@dataclass(frozen=True)
class EvidenceSnapshot:
    state: str
    leaves: tuple[dict[str, Any], ...]
    canonical_result_uri: str | None
    normalized: dict[str, Any] | None

    def document(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "leaves": list(self.leaves),
            "canonical_result_uri": self.canonical_result_uri,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="s3-listing-study report-campaign", allow_abbrev=False)
    parser.add_argument(
        "--campaign", "--campaign-id", dest="campaign", action=UniqueStoreAction, required=True
    )
    parser.add_argument("--results-bucket", action=UniqueStoreAction, required=True)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument(
        "--poll-interval-s", action=UniqueStoreAction, default=DEFAULT_POLL_INTERVAL_S
    )
    parser.add_argument("--publish", action="store_true")
    return parser


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReportError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json_object(content: bytes, *, label: str, max_bytes: int) -> dict[str, Any]:
    if len(content) > max_bytes:
        raise ReportError(f"{label} exceeds {max_bytes} bytes")
    try:
        document = json.loads(content, object_pairs_hook=_reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportError(f"{label} is not valid UTF-8 JSON: {exc}") from None
    if not isinstance(document, dict):
        raise ReportError(f"{label} is not a JSON object")
    return document


def _worker_json(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _download(blob: Any, *, label: str, max_bytes: int) -> bytes:
    size = blob.size
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ReportError(f"{label} has no valid object size")
    if size > max_bytes:
        raise ReportError(f"{label} exceeds {max_bytes} bytes")
    content = cast(bytes, blob.download_as_bytes(if_generation_match=blob.generation))
    if len(content) != size or len(content) > max_bytes:
        raise ReportError(f"{label} changed or exceeded its declared size while reading")
    return content


def _required_blob(bucket: Any, name: str, *, max_bytes: int) -> bytes:
    blob = bucket.get_blob(name)
    if blob is None:
        raise ReportError(f"required gs://{bucket.name}/{name} is missing")
    return _download(blob, label=f"gs://{bucket.name}/{name}", max_bytes=max_bytes)


def _is_int(value: Any, *, minimum: int = 0) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= minimum


def _is_hex_digest(value: Any, *, length: int = 64) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_image_registration(image: Any) -> bool:
    if not isinstance(image, dict) or set(image) != IMAGE_SET_FIELDS:
        return False
    derived = image.get("derived_image")
    shared_digest = image.get("shared_base_digest")
    tool_digest = image.get("tool_image_digest")
    artifact = image.get("tool_artifact")
    if not all(
        isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None
        for value in (derived, shared_digest, tool_digest)
    ):
        return False
    assert isinstance(derived, str)
    assert isinstance(shared_digest, str)
    assert isinstance(tool_digest, str)
    return not (
        not isinstance(image.get("image_uri"), str)
        or not image["image_uri"].endswith(f"@{derived}")
        or not isinstance(image.get("shared_base_uri"), str)
        or not image["shared_base_uri"].endswith(f"@{shared_digest}")
        or not isinstance(image.get("tool_image_uri"), str)
        or not image["tool_image_uri"].endswith(f"@{tool_digest}")
        or any(
            not _is_hex_digest(image.get(field))
            for field in (
                "shared_base_source_sha256",
                "tool_build_sha256",
                "adapter_bundle_sha256",
                "selection_sha256",
            )
        )
        or not isinstance(image.get("tool_version"), str)
        or not image["tool_version"]
        or any(character.isspace() for character in image["tool_version"])
        or not _is_hex_digest(image.get("harness_revision"), length=40)
        or not isinstance(artifact, dict)
        or set(artifact) != {"kind", "locator", "sha256"}
        or any(
            not isinstance(artifact.get(field), str) or not artifact[field]
            for field in ("kind", "locator")
        )
        or not _is_hex_digest(artifact.get("sha256"))
    )


def _parse_manifest(content: bytes, *, campaign: str, results_bucket: str) -> list[ManifestCase]:
    document = _json_object(content, label="campaign.json", max_bytes=MANIFEST_MAX_BYTES)
    if content != _canonical_json(document):
        raise ReportError("campaign.json is not canonical")
    if (
        document.get("schema_version") != 3
        or document.get("campaign") != campaign
        or document.get("results_bucket") != results_bucket
        or document.get("attempt_fingerprint_version") != 3
    ):
        raise ReportError("campaign.json identity does not match the requested campaign")
    attempts = document.get("attempts")
    images = document.get("images")
    if not isinstance(attempts, list) or not attempts or not isinstance(images, dict):
        raise ReportError("campaign.json has no valid attempts/images index")
    if not images or any(
        not isinstance(tool, str) or not tool or not _valid_image_registration(image)
        for tool, image in images.items()
    ):
        raise ReportError("campaign.json does not contain exact schema-3 image registrations")
    cases: list[ManifestCase] = []
    for raw in attempts:
        if not isinstance(raw, dict):
            raise ReportError("campaign.json attempt is not an object")
        required = (
            "job_id",
            "bucket",
            "tool",
            "case_id",
            "run_ordinal",
            "prefix",
            "attempt_fingerprint",
        )
        if any(field not in raw for field in required):
            raise ReportError("campaign.json attempt is missing identity fields")
        job_id, bucket_name, tool, case_id, prefix, fingerprint = (
            raw["job_id"],
            raw["bucket"],
            raw["tool"],
            raw["case_id"],
            raw["prefix"],
            raw["attempt_fingerprint"],
        )
        run_ordinal = raw["run_ordinal"]
        if any(
            not isinstance(value, str) or not value
            for value in (job_id, bucket_name, tool, case_id, prefix, fingerprint)
        ) or not _is_int(run_ordinal, minimum=1):
            raise ReportError("campaign.json attempt has invalid identity values")
        expected_prefix = (
            f"{campaign_prefix(campaign)}/results/{bucket_name}/{tool}/{case_id}/run-{run_ordinal}"
        )
        image = images.get(tool)
        if prefix != expected_prefix or not isinstance(image, dict):
            raise ReportError(f"campaign.json attempt {job_id} has inconsistent identity")
        cases.append(
            ManifestCase(
                campaign=campaign,
                job_id=job_id,
                bucket=bucket_name,
                tool=tool,
                case_id=case_id,
                run_ordinal=run_ordinal,
                prefix=prefix,
                attempt_fingerprint=fingerprint,
                record=dict(raw),
                image=dict(image),
            )
        )
    if len({case.job_id for case in cases}) != len(cases) or len(
        {case.prefix for case in cases}
    ) != len(cases):
        raise ReportError("campaign.json contains duplicate job IDs or run prefixes")
    return cases


def _load_manifest(bucket: Any, campaign: str) -> ManifestSnapshot:
    name = f"{campaign_prefix(campaign)}/campaign.json"
    blob = bucket.get_blob(name)
    if blob is None:
        raise ReportError(f"required gs://{bucket.name}/{name} is missing")
    generation = blob.generation
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise ReportError(f"gs://{bucket.name}/{name} has no valid object generation")
    content = _download(blob, label=f"gs://{bucket.name}/{name}", max_bytes=MANIFEST_MAX_BYTES)
    return ManifestSnapshot(
        bucket=bucket.name,
        campaign=campaign,
        generation=generation,
        content=content,
        cases=tuple(_parse_manifest(content, campaign=campaign, results_bucket=bucket.name)),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _load_temporal_input(
    bucket: Any,
    campaign: str,
    manifest: ManifestSnapshot,
    owner: TemporalOwner,
    scope: TemporalScope,
) -> TemporalSnapshot:
    name = f"{campaign_prefix(campaign)}/inputs/temporal.json"
    blob = bucket.get_blob(name)
    if blob is None:
        raise ReportError(f"required gs://{bucket.name}/{name} is missing")
    generation = blob.generation
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise ReportError(f"gs://{bucket.name}/{name} has no valid object generation")
    content = _download(
        blob, label=f"gs://{bucket.name}/{name}", max_bytes=TEMPORAL_INPUT_MAX_BYTES
    )
    document = _json_object(content, label="temporal.json", max_bytes=TEMPORAL_INPUT_MAX_BYTES)
    if content != _canonical_json(document):
        raise ReportError("temporal.json is not canonical")
    fields = {
        "schema_version",
        "campaign",
        "campaign_manifest_sha256",
        "workflow_type",
        "task_queue",
        "temporal_scope",
        "cases",
    }
    temporal_scope = document.get("temporal_scope")
    raw_cases = document.get("cases")
    digest = hashlib.sha256(content).hexdigest()
    if (
        set(document) != fields
        or document.get("schema_version") != 1
        or document.get("campaign") != campaign
        or document.get("campaign_manifest_sha256") != manifest.sha256
        or document.get("workflow_type") != CampaignWorkflow.__name__
        or document.get("task_queue") != TASK_QUEUE
        or temporal_scope != scope.document()
        or owner.scope != scope
        or digest != owner.campaign_digest
        or not isinstance(raw_cases, list)
    ):
        raise ReportError("temporal.json does not match the frozen campaign owner and manifest")
    cases: list[TemporalCase] = []
    for raw in raw_cases:
        if not isinstance(raw, dict) or set(raw) != {
            "project",
            "location",
            "job_id",
            "job",
            "controller_timeout_s",
        }:
            raise ReportError("temporal.json has an invalid case shape")
        project = raw["project"]
        location = raw["location"]
        job_id = raw["job_id"]
        if (
            any(not isinstance(value, str) or not value for value in (project, location, job_id))
            or any(
                re.fullmatch(r"[a-z0-9][a-z0-9-]*", value) is None for value in (project, location)
            )
            or len(job_id) > JOB_ID_MAX
            or JOB_ID_RE.fullmatch(job_id) is None
            or not isinstance(raw["job"], dict)
            or not _is_int(raw["controller_timeout_s"], minimum=1)
        ):
            raise ReportError("temporal.json has invalid case identity values")
        cases.append(
            TemporalCase(
                project=project,
                location=location,
                job_id=job_id,
                resource_name=f"projects/{project}/locations/{location}/jobs/{job_id}",
            )
        )
    if [item.job_id for item in cases] != [item.job_id for item in manifest.cases]:
        raise ReportError("temporal.json job order does not match campaign.json")
    return TemporalSnapshot(
        bucket=bucket.name,
        campaign=campaign,
        generation=generation,
        content=content,
        sha256=digest,
        campaign_manifest_sha256=manifest.sha256,
        scope=scope,
        workflow_type=CampaignWorkflow.__name__,
        task_queue=TASK_QUEUE,
        cases=tuple(cases),
    )


def _safe_heartbeat(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or set(value) != {"job_name", "state"}:
        return None
    if any(not isinstance(item, str) or not item for item in value.values()):
        return None
    return {"job_name": value["job_name"], "state": value["state"]}


async def _activity_view(client: Client, progress: CaseControllerProgress) -> dict[str, Any]:
    attempt: int | None = None
    heartbeat: dict[str, str] | None = None
    current_job_id = progress.current_job_id or progress.job_id
    if progress.child_run_id is not None and progress.phase in ("running", "pending"):
        child = client.get_workflow_handle(current_job_id, run_id=progress.child_run_id)
        description = await child.describe()
        pending = [
            item
            for item in description.raw_description.pending_activities
            if item.activity_type.name
            in ("ensure_batch_job", "wait_for_batch_job", "run_batch_job")
        ]
        if len(pending) > 1:
            raise ReportError(f"child Workflow {progress.job_id} has multiple Batch Activities")
        if pending:
            attempt = pending[0].attempt
            if not _is_int(attempt, minimum=1):
                raise ReportError(
                    f"child Workflow {progress.job_id} has an invalid Activity attempt"
                )
            if pending[0].heartbeat_details.payloads:
                decoded = await client.data_converter.decode(
                    pending[0].heartbeat_details.payloads, [dict[str, str]]
                )
                if decoded:
                    heartbeat = _safe_heartbeat(decoded[0])
    phase = progress.phase
    if phase == "running" and attempt is not None and attempt > 1:
        phase = "retrying"
    return {
        "phase": phase,
        "child_workflow_id": current_job_id,
        "child_run_id": progress.child_run_id,
        "activity_attempt": attempt,
        "last_heartbeat": heartbeat,
        "terminal": (
            {
                "controller_state": "failed" if progress.failure_type else "completed",
                "failure_type": progress.failure_type,
                "provider_settled": progress.provider_settled,
                "provider_state": progress.provider_state,
                "provider_resource_name": progress.provider_resource_name,
            }
            if progress.phase in ("awaiting_retry", "terminal")
            else None
        ),
    }


async def _activity_views(
    client: Client, progress: Sequence[CaseControllerProgress]
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHILD_DESCRIBES)

    async def bounded(item: CaseControllerProgress) -> dict[str, Any]:
        async with semaphore:
            return await _activity_view(client, item)

    return list(await asyncio.gather(*(bounded(item) for item in progress)))


def _validate_progress(
    progress: Sequence[CaseControllerProgress],
    cases: Sequence[ManifestCase],
    temporal_cases: Sequence[TemporalCase],
) -> None:
    if [item.job_id for item in progress] != [case.job_id for case in cases]:
        raise ReportError("Workflow progress does not exactly match campaign.json order")
    expected_cases = {item.job_id: item for item in temporal_cases}
    for item in progress:
        if item.phase not in ("pending", "running", "awaiting_retry", "terminal"):
            raise ReportError(f"Workflow progress for {item.job_id} has an invalid phase")
        current_job_id = item.current_job_id or item.job_id
        if not _is_int(item.current_submission, minimum=1):
            raise ReportError(f"Workflow progress for {item.job_id} has invalid submission")
        if item.current_submission == 1:
            if current_job_id != item.job_id:
                raise ReportError(f"Workflow progress for {item.job_id} has invalid current job")
        else:
            stem, separator, original = item.job_id.rpartition("-s")
            if (
                not separator
                or original != "1"
                or current_job_id != (f"{stem}-s{item.current_submission}")
            ):
                raise ReportError(f"Workflow progress for {item.job_id} has invalid retry job")
        temporal_case = expected_cases[item.job_id]
        expected_resource = (
            f"projects/{temporal_case.project}/locations/{temporal_case.location}/jobs/"
            f"{current_job_id}"
        )
        if item.phase in ("pending", "running") and any(
            value is not None
            for value in (item.provider_state, item.failure_type, item.provider_resource_name)
        ):
            raise ReportError(f"Workflow progress for {item.job_id} has premature terminal state")
        if item.phase in ("pending", "running") and item.provider_settled:
            raise ReportError(f"Workflow progress for {item.job_id} settles prematurely")
        if item.phase in ("pending", "running"):
            continue
        if item.phase == "awaiting_retry" and (
            not item.provider_settled
            or (item.provider_state == "SUCCEEDED" and item.failure_type is None)
        ):
            raise ReportError(f"Workflow progress for {item.job_id} has invalid retry wait")
        if item.provider_settled:
            if item.provider_state in ("SUCCEEDED", "FAILED"):
                if item.provider_resource_name != expected_resource or item.failure_type not in (
                    None,
                    "BatchJobCollision",
                ):
                    raise ReportError(
                        f"Workflow progress for {item.job_id} has an invalid settled outcome"
                    )
            elif item.provider_state == "NOT_CREATED":
                if item.provider_resource_name is not None or item.failure_type not in (
                    "PermanentGoogleError",
                    "BatchJobCollision",
                ):
                    raise ReportError(
                        f"Workflow progress for {item.job_id} has invalid no-effect proof"
                    )
            else:
                raise ReportError(
                    f"Workflow progress for {item.job_id} has no settled provider outcome"
                )
        elif (
            item.failure_type is None
            or item.provider_state is not None
            or item.provider_resource_name is not None
        ):
            raise ReportError(f"Workflow progress for {item.job_id} has invalid unsettled outcome")


def _list_leaves(bucket: Any, prefix: str) -> list[str]:
    found: set[str] = set()
    iterator = bucket.list_blobs(prefix=f"{prefix}/", delimiter="/")
    for page in iterator.pages:
        for child in page.prefixes:
            relative = child.removeprefix(f"{prefix}/").rstrip("/")
            if relative and "/" not in relative:
                found.add(relative)
                if len(found) > MAX_EXECUTION_LEAVES_PER_RUN:
                    raise ReportError(
                        f"run prefix exceeds {MAX_EXECUTION_LEAVES_PER_RUN} execution leaves"
                    )
    return sorted(found)


def _validated_result(
    bucket: Any,
    case: ManifestCase,
    leaf: str,
    *,
    current_job_id: str | None = None,
    current_submission: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    result_name = f"{case.prefix}/{leaf}/result.json"
    result_uri = f"gs://{bucket.name}/{result_name}"

    def leaf_record(state: str, reason: str | None) -> dict[str, Any]:
        return {
            "attempt_id": leaf,
            "result_uri": result_uri,
            "state": state,
            "reason": reason,
        }

    try:
        parsed = uuid.UUID(leaf)
    except ValueError:
        return leaf_record("invalid", "invalid_attempt_id"), None
    if str(parsed) != leaf or parsed.version != 4:
        return leaf_record("invalid", "invalid_attempt_id"), None
    blob = bucket.get_blob(result_name)
    if blob is None:
        return leaf_record("unsealed", "missing_result_commit"), None
    try:
        content = _download(blob, label=result_uri, max_bytes=RESULT_MAX_BYTES)
    except ReportError:
        return leaf_record("invalid", "invalid_result_size"), None
    try:
        document = _json_object(content, label=result_uri, max_bytes=RESULT_MAX_BYTES)
        if content != _worker_json(document):
            return leaf_record("invalid", "invalid_result_json"), None
    except ReportError:
        return leaf_record("invalid", "invalid_result_json"), None
    campaign = document.get("campaign")
    outcome = document.get("outcome")
    timing = document.get("timing")
    resources = document.get("resources")
    summary = document.get("summary")
    tool = document.get("tool")
    images = document.get("images")
    build_inputs = document.get("build_inputs")
    secret_scan = document.get("secret_scan")
    logical_request = document.get("logical_request")
    target = document.get("target")
    if not all(
        isinstance(value, dict)
        for value in (
            campaign,
            outcome,
            timing,
            resources,
            summary,
            tool,
            images,
            build_inputs,
            secret_scan,
            logical_request,
            target,
        )
    ):
        return leaf_record("invalid", "invalid_result_identity"), None
    assert isinstance(campaign, dict)
    assert isinstance(outcome, dict)
    assert isinstance(timing, dict)
    assert isinstance(resources, dict)
    assert isinstance(summary, dict)
    assert isinstance(tool, dict)
    assert isinstance(images, dict)
    assert isinstance(build_inputs, dict)
    assert isinstance(secret_scan, dict)
    assert isinstance(logical_request, dict)
    assert isinstance(target, dict)
    expected_campaign = {
        "campaign_id": case.campaign,
        "job_id": current_job_id or case.job_id,
        "case_id": case.case_id,
        "case_fingerprint": case.record.get("case_fingerprint"),
        "attempt_fingerprint": case.attempt_fingerprint,
        "run_ordinal": case.run_ordinal,
        "submission_number": (
            current_submission if current_submission is not None else case.record.get("submission")
        ),
        "declared_resources": case.record.get("resources"),
    }
    expected_artifact_uri = f"gs://{bucket.name}/{case.prefix}/{leaf}"
    if (
        document.get("schema_version") != 3
        or document.get("attempt_id") != leaf
        or campaign != expected_campaign
        or document.get("artifact_uri") != expected_artifact_uri
        or document.get("result_uri") != result_uri
        or tool.get("name") != case.tool
        or tool.get("version") != case.image.get("tool_version")
        or document.get("adapter_bundle_sha256") != case.image.get("adapter_bundle_sha256")
        or summary.get("adapter_bundle_sha256") != case.image.get("adapter_bundle_sha256")
    ):
        return leaf_record("invalid", "invalid_result_identity"), None
    expected_request = {
        "schema_version": 1,
        "operation": "list",
        "mode": case.record.get("mode"),
        "bucket": case.bucket,
        "region": case.record.get("region"),
        "prefix": "",
        "authentication": case.record.get("auth"),
        "concurrency": case.record.get("concurrency"),
    }
    expected_target = {
        "mode": case.record.get("mode"),
        "bucket": case.bucket,
        "region": case.record.get("region"),
        "prefix": "",
        "scope": "full",
    }
    if logical_request != expected_request or target != expected_target:
        return leaf_record("invalid", "invalid_result_request"), None
    result_tool_image = images.get("tool")
    result_shared_base = images.get("shared_base")
    result_build_shared = build_inputs.get("shared_base")
    result_build_tool = build_inputs.get("tool")
    if not all(
        isinstance(value, dict)
        for value in (
            result_tool_image,
            result_shared_base,
            result_build_shared,
            result_build_tool,
        )
    ):
        return leaf_record("invalid", "invalid_result_provenance"), None
    assert isinstance(result_tool_image, dict)
    assert isinstance(result_shared_base, dict)
    assert isinstance(result_build_shared, dict)
    assert isinstance(result_build_tool, dict)
    if (
        set(images) != {"derived", "tool", "shared_base"}
        or images.get("derived") != case.record.get("derived_image")
        or images.get("derived") != case.image.get("derived_image")
        or result_shared_base
        != {
            "digest": case.image.get("shared_base_digest"),
            "uri": case.image.get("shared_base_uri"),
        }
        or result_build_shared != {"source_sha256": case.image.get("shared_base_source_sha256")}
        or result_build_tool.get("build_sha256") != case.image.get("tool_build_sha256")
        or result_build_tool.get("artifact") != case.image.get("tool_artifact")
        or document.get("harness_revision") != case.image.get("harness_revision")
    ):
        return leaf_record("invalid", "invalid_result_provenance"), None
    if result_tool_image != {
        "digest": case.image.get("tool_image_digest"),
        "uri": case.image.get("tool_image_uri"),
    }:
        return leaf_record("invalid", "invalid_result_provenance"), None
    if result_build_tool.get("selection_sha256") != case.image.get("selection_sha256"):
        return leaf_record("invalid", "invalid_result_provenance"), None
    if set(result_build_tool) != {"build_sha256", "artifact", "selection_sha256"}:
        return leaf_record("invalid", "invalid_result_provenance"), None
    if secret_scan != {
        "status": "clean",
        "streams": {"stdout": "clean", "stderr": "clean"},
    }:
        return leaf_record("invalid", "invalid_result_secret_scan"), None
    status = outcome.get("status")
    exit_code = outcome.get("exit_code")
    subject_signal = outcome.get("signal")
    timed_out = outcome.get("timed_out")
    cleanup = outcome.get("cleanup")
    if (
        set(outcome) != {"status", "exit_code", "signal", "timed_out", "cleanup"}
        or status not in SUBJECT_STATES[:-1]
        or (exit_code is not None and not _is_int(exit_code))
        or (subject_signal is not None and not _is_int(subject_signal, minimum=1))
        or (exit_code is None) == (subject_signal is None)
        or not isinstance(timed_out, bool)
    ):
        return leaf_record("invalid", "invalid_result_outcome"), None
    assert isinstance(timed_out, bool)
    if not isinstance(cleanup, dict) or set(cleanup) != {
        "state",
        "term_sent",
        "kill_sent",
        "process_group_empty",
        "escaped_descendants",
    }:
        return leaf_record("invalid", "invalid_result_cleanup"), None
    term_sent = cleanup.get("term_sent")
    kill_sent = cleanup.get("kill_sent")
    group_empty = cleanup.get("process_group_empty")
    escaped = cleanup.get("escaped_descendants")
    if (
        not all(isinstance(value, bool) for value in (term_sent, kill_sent, group_empty))
        or not isinstance(escaped, list)
        or any(not _is_int(pid, minimum=1) for pid in escaped)
        or escaped != sorted(set(escaped))
    ):
        return leaf_record("invalid", "invalid_result_cleanup"), None
    assert isinstance(term_sent, bool)
    assert isinstance(kill_sent, bool)
    assert isinstance(group_empty, bool)
    expected_cleanup_state = (
        "failed"
        if not group_empty or escaped
        else "killed"
        if kill_sent
        else "terminated"
        if term_sent
        else "not_needed"
    )
    if cleanup.get("state") != expected_cleanup_state:
        return leaf_record("invalid", "invalid_result_cleanup"), None
    expected_status = (
        "harness_error"
        if escaped
        else "timed_out"
        if timed_out
        else "signaled"
        if subject_signal is not None
        else "completed"
        if exit_code == 0
        else "failed"
    )
    if status != expected_status:
        return leaf_record("invalid", "invalid_result_outcome"), None
    if not timed_out and cleanup != {
        "state": "failed" if escaped else "not_needed",
        "term_sent": False,
        "kill_sent": False,
        "process_group_empty": True,
        "escaped_descendants": escaped,
    }:
        return leaf_record("invalid", "invalid_result_cleanup"), None
    elapsed_ns = timing.get("elapsed_ns")
    rss_kb = resources.get("rusage_children_max_child_peak_rss_kb")
    row_count = summary.get("row_count")
    if not _is_int(elapsed_ns) or not _is_int(rss_kb):
        return leaf_record("invalid", "invalid_result_metrics"), None
    interpreter = summary.get("interpreter")
    duckdb_version = summary.get("duckdb_version")
    summary_status = summary.get("status")
    reason = summary.get("reason")
    error = summary.get("error")
    if (
        set(summary)
        != {
            "schema_version",
            "status",
            "row_count",
            "reason",
            "error",
            "adapter_bundle_sha256",
            "duckdb_version",
            "interpreter",
        }
        or summary.get("schema_version") != 2
        or (duckdb_version is not None and not isinstance(duckdb_version, str))
        or not isinstance(interpreter, dict)
        or set(interpreter)
        != {
            "architecture",
            "implementation",
            "libc",
            "package_manifest_sha256",
            "running_version",
            "source",
        }
        or any(value is not None and not isinstance(value, str) for value in interpreter.values())
    ):
        return leaf_record("invalid", "invalid_result_summary"), None
    if status != "completed":
        valid_summary = (
            summary_status == "skipped"
            and row_count is None
            and reason == f"tool_outcome_{status}"
            and error is None
        )
    elif summary_status == "counted":
        valid_summary = (
            _is_int(row_count)
            and reason is None
            and error is None
            and isinstance(duckdb_version, str)
            and bool(duckdb_version)
        )
    elif summary_status == "error":
        valid_summary = (
            row_count is None
            and reason is None
            and isinstance(error, dict)
            and set(error) == {"code", "type"}
            and error.get("code") == "row_count_failed"
            and isinstance(error.get("type"), str)
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", error["type"]) is not None
        )
    else:
        valid_summary = False
    if not valid_summary:
        return leaf_record("invalid", "invalid_result_summary"), None
    normalized = {
        "subject": {
            "status": status,
            "exit_code": exit_code,
            "signal": subject_signal,
            "timed_out": timed_out,
        },
        "metrics": {
            "elapsed_ns": elapsed_ns,
            "rusage_children_max_child_peak_rss_kb": rss_kb,
            "row_count": row_count,
        },
    }
    return leaf_record("recorded", None), normalized


def _evidence(
    bucket: Any,
    case: ManifestCase,
    *,
    terminal: bool,
    current_job_id: str | None = None,
    current_submission: int | None = None,
) -> EvidenceSnapshot:
    if not terminal:
        return EvidenceSnapshot("pending", (), None, None)
    leaf_names = _list_leaves(bucket, case.prefix)
    if not leaf_names:
        return EvidenceSnapshot("missing", (), None, None)
    checked: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for leaf in leaf_names:
        validated = _validated_result(
            bucket,
            case,
            leaf,
            current_job_id=current_job_id,
            current_submission=current_submission,
        )
        leaf_record, _normalized = validated
        if (
            current_submission is not None
            and current_submission > 1
            and leaf_record["reason"] == "invalid_result_identity"
        ):
            result_name = f"{case.prefix}/{leaf}/result.json"
            blob = bucket.get_blob(result_name)
            try:
                content = (
                    _download(
                        blob, label=str(leaf_record["result_uri"]), max_bytes=RESULT_MAX_BYTES
                    )
                    if blob is not None
                    else b""
                )
                document = _json_object(
                    content, label=str(leaf_record["result_uri"]), max_bytes=RESULT_MAX_BYTES
                )
                campaign = document.get("campaign")
            except ReportError:
                campaign = None
            if isinstance(campaign, dict):
                prior_submission = campaign.get("submission_number")
                prior_job_id = campaign.get("job_id")
                prior_number = cast(int, prior_submission)
                current_number = current_submission
                if _is_int(prior_submission, minimum=1) and prior_number < current_number:
                    stem, separator, original = case.job_id.rpartition("-s")
                    expected_prior_job = (
                        case.job_id
                        if prior_number == 1
                        else f"{stem}-s{prior_number}"
                        if separator and original == "1"
                        else None
                    )
                    if isinstance(prior_job_id, str) and prior_job_id == expected_prior_job:
                        prior_leaf, prior_normalized = _validated_result(
                            bucket,
                            case,
                            leaf,
                            current_job_id=prior_job_id,
                            current_submission=prior_number,
                        )
                        if prior_leaf["state"] == "recorded" and prior_normalized is not None:
                            prior_leaf = {
                                **prior_leaf,
                                "state": "historical",
                                "submission_number": prior_number,
                                "job_id": prior_job_id,
                            }
                            validated = (prior_leaf, None)
        checked.append(validated)
    leaves = tuple(item[0] for item in checked)
    current = [item for item in checked if item[0]["state"] != "historical"]
    if not current:
        return EvidenceSnapshot("missing", leaves, None, None)
    if len(current) > 1:
        return EvidenceSnapshot("duplicate", leaves, None, None)
    current_leaf, normalized = current[0]
    state = str(current_leaf["state"])
    return EvidenceSnapshot(
        state,
        leaves,
        str(current_leaf["result_uri"]) if state == "recorded" else None,
        normalized,
    )


async def observe_once(
    *,
    temporal_client: Client,
    bucket: Any,
    campaign: str,
    owner: TemporalOwner,
    scope: TemporalScope,
    manifest_cache: ManifestSnapshot | None = None,
    temporal_cache: TemporalSnapshot | None = None,
    evidence_cache: dict[str, EvidenceSnapshot] | None = None,
) -> dict[str, Any]:
    manifest = manifest_cache or _load_manifest(bucket, campaign)
    if manifest.bucket != bucket.name or manifest.campaign != campaign:
        raise ReportError("manifest cache does not match the requested bucket and campaign")
    temporal_input = temporal_cache or _load_temporal_input(
        bucket, campaign, manifest, owner, scope
    )
    if (
        temporal_input.bucket != bucket.name
        or temporal_input.campaign != campaign
        or temporal_input.sha256 != owner.campaign_digest
        or temporal_input.campaign_manifest_sha256 != manifest.sha256
        or temporal_input.scope != scope
        or temporal_input.workflow_type != CampaignWorkflow.__name__
        or temporal_input.task_queue != TASK_QUEUE
        or [item.job_id for item in temporal_input.cases]
        != [item.job_id for item in manifest.cases]
    ):
        raise ReportError("temporal input cache does not match the requested frozen chain")
    cases = manifest.cases
    handle = temporal_client.get_workflow_handle(owner.workflow_id, run_id=owner.run_id)
    description = await handle.describe()
    try:
        digest = await description.memo_value("campaign_digest", type_hint=str)
    except KeyError:
        digest = None
    if (
        owner.campaign != campaign
        or owner.workflow_id != campaign
        or owner.scope != scope
        or description.id != owner.workflow_id
        or description.run_id != owner.run_id
        or description.namespace != scope.namespace
        or description.workflow_type != CampaignWorkflow.__name__
        or description.task_queue != TASK_QUEUE
        or digest != owner.campaign_digest
    ):
        raise ReportError("Temporal owner does not exactly match the retained Workflow Run")
    progress = await handle.query(
        CampaignWorkflow.progress,
        rpc_timeout=timedelta(seconds=10),
    )
    _validate_progress(progress, cases, temporal_input.cases)
    controllers = await _activity_views(temporal_client, progress)
    rows: list[dict[str, Any]] = []
    for case, temporal_case, item, controller in zip(
        cases, temporal_input.cases, progress, controllers, strict=True
    ):
        current_job_id = item.current_job_id or item.job_id
        evidence_key = f"{case.job_id}:s{item.current_submission}"
        if item.provider_settled and evidence_cache is not None:
            evidence = evidence_cache.get(evidence_key)
            if evidence is None:
                evidence = _evidence(
                    bucket,
                    case,
                    terminal=True,
                    current_job_id=current_job_id,
                    current_submission=item.current_submission,
                )
                evidence_cache[evidence_key] = evidence
        else:
            evidence = _evidence(
                bucket,
                case,
                terminal=item.provider_settled,
                current_job_id=current_job_id,
                current_submission=item.current_submission,
            )
        normalized = evidence.normalized
        rows.append(
            {
                "job_id": case.job_id,
                "current_job_id": current_job_id,
                "current_submission": item.current_submission,
                "bucket": case.bucket,
                "tool": case.tool,
                "case_id": case.case_id,
                "run_ordinal": case.run_ordinal,
                "run_prefix": f"gs://{bucket.name}/{case.prefix}",
                "provider_resource_name": (
                    f"projects/{temporal_case.project}/locations/{temporal_case.location}/jobs/"
                    f"{current_job_id}"
                ),
                "controller_complete": item.phase == "terminal",
                "provider_settled": item.provider_settled,
                "controller": controller,
                "evidence": evidence.document(),
                "subject": normalized["subject"] if normalized else None,
                "metrics": normalized["metrics"] if normalized else None,
                "operational_success": (
                    item.provider_settled
                    and item.failure_type is None
                    and item.provider_state == "SUCCEEDED"
                    and evidence.state == "recorded"
                ),
            }
        )
    controller_counts = dict.fromkeys(CONTROLLER_PHASES, 0)
    evidence_counts = dict.fromkeys(EVIDENCE_STATES, 0)
    subject_counts = dict.fromkeys(SUBJECT_STATES, 0)
    provider_counts = dict.fromkeys(PROVIDER_STATES, 0)
    for row in rows:
        controller_counts[row["controller"]["phase"]] += 1
        evidence_counts[row["evidence"]["state"]] += 1
        subject = row["subject"]
        subject_counts[subject["status"] if subject else "unavailable"] += 1
        provider_state = row["controller"]["terminal"]
        provider_counts[
            provider_state["provider_state"]
            if provider_state and provider_state["provider_state"] in PROVIDER_STATES[:-1]
            else "unavailable"
        ] += 1
    status = description.status.name.lower() if description.status is not None else "unknown"
    all_terminal = all(item.phase == "terminal" for item in progress)
    if status == "completed" and not all_terminal:
        raise ReportError("completed campaign Workflow has nonterminal case progress")
    controller_complete = status == "completed" and all_terminal
    provider_settled = all(item.provider_settled for item in progress)
    report_final = controller_complete and provider_settled
    operational_success = report_final and all(row["operational_success"] for row in rows)
    return {
        "schema_version": 2,
        "campaign": campaign,
        "campaign_manifest_sha256": manifest.sha256,
        "campaign_digest": owner.campaign_digest,
        "temporal_input_sha256": temporal_input.sha256,
        "workflow": {
            "workflow_id": owner.workflow_id,
            "run_id": owner.run_id,
            "status": status,
        },
        "controller_complete": controller_complete,
        "provider_settled": provider_settled,
        "report_final": report_final,
        "operational_success": operational_success,
        "aggregate": {
            "cases_total": len(rows),
            "controller": controller_counts,
            "provider": provider_counts,
            "evidence": evidence_counts,
            "subject": subject_counts,
        },
        "cases": rows,
    }


def _publish(bucket: Any, campaign: str, report: Mapping[str, Any]) -> None:
    if report.get("report_final") is not True:
        raise ReportError("refusing to publish a report without provider settlement")
    content = _canonical_json(report)
    if len(content) > REPORT_MAX_BYTES:
        raise ReportError(f"final campaign report exceeds {REPORT_MAX_BYTES} bytes")
    name = f"{campaign_prefix(campaign)}/report.json"
    blob = bucket.blob(name)
    try:
        blob.upload_from_string(content, content_type="application/json", if_generation_match=0)
    except PreconditionFailed:
        existing = bucket.get_blob(name)
        if (
            existing is None
            or _download(existing, label=f"gs://{bucket.name}/{name}", max_bytes=REPORT_MAX_BYTES)
            != content
        ):
            raise ReportError(
                f"gs://{bucket.name}/{name} already exists with different content"
            ) from None


async def _run_report(args: argparse.Namespace) -> dict[str, Any]:
    try:
        poll_interval_s = float(args.poll_interval_s)
    except (TypeError, ValueError):
        raise ReportError("--poll-interval-s must be a number") from None
    if not math.isfinite(poll_interval_s) or poll_interval_s <= 0:
        raise ReportError("--poll-interval-s must be finite and positive")
    prefix = campaign_prefix(args.campaign)
    connect_config = ClientConfig.load_client_connect_config()
    target_host = connect_config.get("target_host")
    namespace = connect_config.get("namespace")
    if (
        not isinstance(target_host, str)
        or not target_host
        or not isinstance(namespace, str)
        or not namespace
    ):
        raise ReportError("Temporal client config needs target_host and namespace")
    scope = TemporalScope(target_host, namespace)
    storage_client = storage.Client()
    bucket = storage_client.bucket(args.results_bucket)
    owner_name = f"{prefix}/inputs/temporal-owner.json"
    owner_content = _required_blob(bucket, owner_name, max_bytes=TEMPORAL_OWNER_MAX_BYTES)
    try:
        owner = _parse_owner(owner_content, f"gs://{bucket.name}/{owner_name}")
    except SubmissionError as exc:
        raise ReportError(str(exc)) from None
    temporal_client = await Client.connect(**connect_config)
    manifest_cache = _load_manifest(bucket, args.campaign)
    temporal_cache = _load_temporal_input(bucket, args.campaign, manifest_cache, owner, scope)
    evidence_cache: dict[str, EvidenceSnapshot] = {}
    previous_progress: tuple[Any, ...] | None = None
    while True:
        report = await observe_once(
            temporal_client=temporal_client,
            bucket=bucket,
            campaign=args.campaign,
            owner=owner,
            scope=scope,
            manifest_cache=manifest_cache,
            temporal_cache=temporal_cache,
            evidence_cache=evidence_cache,
        )
        workflow_status = str(report["workflow"]["status"])
        case_state = tuple(
            (
                row["job_id"],
                row["controller"]["phase"],
                row["controller"]["activity_attempt"],
                (
                    row["controller"]["last_heartbeat"]["state"]
                    if row["controller"]["last_heartbeat"]
                    else None
                ),
                row["evidence"]["state"],
            )
            for row in report["cases"]
        )
        progress_key = (
            workflow_status,
            report["controller_complete"],
            report["provider_settled"],
            report["report_final"],
            report["operational_success"],
            case_state,
        )
        if args.wait and progress_key != previous_progress:
            controller = report["aggregate"]["controller"]
            evidence = report["aggregate"]["evidence"]
            print(
                "report-campaign: "
                f"workflow={workflow_status} "
                "finality["
                f"controller_complete={str(report['controller_complete']).lower()},"
                f"provider_settled={str(report['provider_settled']).lower()},"
                f"report_final={str(report['report_final']).lower()},"
                f"operational_success={str(report['operational_success']).lower()}"
                "] "
                "controller["
                + ",".join(f"{key}={value}" for key, value in controller.items())
                + "] evidence["
                + ",".join(f"{key}={value}" for key, value in evidence.items())
                + "]",
                file=sys.stderr,
            )
            previous_progress = progress_key
        if not args.wait or report["report_final"]:
            break
        if report["controller_complete"] and not report["provider_settled"]:
            unsettled = [row["job_id"] for row in report["cases"] if not row["provider_settled"]]
            preview = ", ".join(unsettled[:8])
            suffix = "" if len(unsettled) <= 8 else f" (+{len(unsettled) - 8} more)"
            raise ReportError(
                "case controllers completed without provider settlement for "
                f"{preview}{suffix}; immutable publication remains prohibited; inspect the "
                "deterministic Batch resources and retain this campaign for recovery"
            )
        if workflow_status not in ("running", "completed"):
            raise ReportError(
                f"owned campaign Workflow closed with status {workflow_status} "
                "before completing all case controllers"
            )
        await asyncio.sleep(poll_interval_s)
    if args.publish:
        if not report["report_final"]:
            raise ReportError(
                "refusing to publish before every provider effect has settled; use --wait"
            )
        _publish(bucket, args.campaign, report)
    return report


def report_campaign_main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = asyncio.run(_run_report(args))
        print(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False))
        return 0
    except (
        CampaignError,
        DefaultCredentialsError,
        GoogleAPIError,
        OSError,
        ReportError,
        TemporalError,
    ) as exc:
        print(f"report-campaign: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    return report_campaign_main(argv)
