"""Freeze one campaign and start its Temporal controller Workflow."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from temporalio.api.common.v1 import Payloads
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.converter import DataConverter
from temporalio.envconfig import ClientConfig
from temporalio.exceptions import TemporalError, WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from s3_listing_study.common.argparse_utils import UniqueStoreAction
from s3_listing_study.common.build_selection import (
    BuildSelectionError,
    load_registered_selection,
)
from s3_listing_study.manager.bench import plan as bench
from s3_listing_study.manager.bench.cli import registered_tools, repo_root
from s3_listing_study.manager.campaign import (
    DIGEST_RE,
    CampaignError,
    attempts_for,
    campaign_prefix,
    manifest,
)
from s3_listing_study.manager.campaign.batch import BatchConfig, render_job
from s3_listing_study.temporal import TASK_QUEUE
from s3_listing_study.temporal.models import BatchJobSpec, CampaignWorkflowInput
from s3_listing_study.temporal.workflows import CampaignWorkflow

IMAGE_SET_FIELDS_V2 = {
    "derived_image",
    "image_uri",
    "shared_base_digest",
    "shared_base_uri",
    "shared_base_source_sha256",
    "tool_build_sha256",
    "tool_artifact",
    "tool_version",
    "adapter_bundle_sha256",
    "harness_revision",
}
IMAGE_SET_FIELDS = IMAGE_SET_FIELDS_V2 | {
    "tool_image_digest",
    "tool_image_uri",
    "selection_sha256",
}
IMAGE_SET_SCHEMA_VERSION = 3
TEMPORAL_WORKFLOW_INPUT_MAX_BYTES = 1_900_000
TEMPORAL_OWNER_MAX_BYTES = 4096
# Batch maxRunDuration covers the task itself. The controller also has to survive
# VM queue/provisioning delay and bounded create/get/poll control-plane work.
BATCH_QUEUE_CONTROL_ALLOWANCE_S = 3600


@dataclass(frozen=True)
class TemporalScope:
    target_host: str
    namespace: str

    def document(self) -> dict[str, str]:
        return {"target_host": self.target_host, "namespace": self.namespace}


@dataclass(frozen=True)
class TemporalOwner:
    campaign: str
    campaign_digest: str
    scope: TemporalScope
    workflow_id: str
    run_id: str

    def document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "campaign": self.campaign,
            "campaign_digest": self.campaign_digest,
            "temporal_scope": self.scope.document(),
            "workflow_type": CampaignWorkflow.__name__,
            "task_queue": TASK_QUEUE,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
        }


class ImageSet(dict[str, dict[str, Any]]):
    """Validated registrations retaining their on-disk schema generation."""

    def __init__(self, *args: Any, schema_version: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.schema_version = schema_version


class SubmissionError(RuntimeError):
    """Campaign inputs or a cloud command made submission unsafe."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="s3-listing-study submit-campaign", allow_abbrev=False)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--bucket",
        action="append",
        help="plan under bench/buckets (repeat for more plans)",
    )
    source.add_argument(
        "--path",
        action="append",
        help="path to a plan file (repeat for more plans)",
    )
    parser.add_argument(
        "--campaign", "--campaign-id", dest="campaign", action=UniqueStoreAction, required=True
    )
    parser.add_argument("--image-set", action=UniqueStoreAction, required=True)
    parser.add_argument("--project", action=UniqueStoreAction, required=True)
    parser.add_argument("--location", action=UniqueStoreAction, required=True)
    parser.add_argument("--results-bucket", action=UniqueStoreAction, required=True)
    parser.add_argument("--anonymous-worker-sa", action=UniqueStoreAction, required=True)
    parser.add_argument("--authenticated-worker-sa", "--auth-worker-sa", action=UniqueStoreAction)
    parser.add_argument("--secret-resource", "--aws-credential-secret", action=UniqueStoreAction)
    parser.add_argument("--network", action=UniqueStoreAction)
    parser.add_argument("--subnetwork", action=UniqueStoreAction)
    parser.add_argument(
        "--provisioning",
        action=UniqueStoreAction,
        choices=("STANDARD", "SPOT"),
        default="SPOT",
    )
    parser.add_argument("--zone", action=UniqueStoreAction)
    parser.add_argument(
        "--post-attempt-allowance-s",
        type=int,
        default=1800,
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionError(f"duplicate JSON key in image set: {key}")
        result[key] = value
    return result


def _read_image_set(path: Path) -> ImageSet:
    try:
        raw = path.read_text(encoding="utf-8")
        document = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SubmissionError(f"image set is not readable JSON: {path}: {exc}") from None
    if not isinstance(document, dict):
        raise SubmissionError("image set is not a JSON object")
    unknown_top = sorted(set(document) - {"schema_version", "images"})
    if unknown_top:
        raise SubmissionError(f"image set has unknown key(s): {', '.join(unknown_top)}")
    schema_version = document.get("schema_version")
    if schema_version not in (2, IMAGE_SET_SCHEMA_VERSION) or isinstance(schema_version, bool):
        raise SubmissionError("image set schema_version must be 2 or 3")
    fields = IMAGE_SET_FIELDS if schema_version == 3 else IMAGE_SET_FIELDS_V2
    images = document.get("images")
    if not isinstance(images, dict) or not images:
        raise SubmissionError("image set images must be a non-empty object")

    validated: dict[str, dict[str, Any]] = {}
    for tool, value in images.items():
        if not isinstance(tool, str) or not tool or not isinstance(value, dict):
            raise SubmissionError("each image must be a tool-named object")
        missing = sorted(fields - set(value))
        unknown = sorted(set(value) - fields)
        if missing or unknown:
            detail = []
            if missing:
                detail.append(f"missing {', '.join(missing)}")
            if unknown:
                detail.append(f"unknown {', '.join(unknown)}")
            raise SubmissionError(f"{tool}: invalid image fields ({'; '.join(detail)})")
        derived_image = value["derived_image"]
        if not isinstance(derived_image, str) or DIGEST_RE.fullmatch(derived_image) is None:
            raise SubmissionError(f"{tool}: derived_image is not a sha256 digest")
        image_uri = value["image_uri"]
        if not isinstance(image_uri, str) or not image_uri.endswith(f"@{derived_image}"):
            raise SubmissionError(f"{tool}: image_uri digest does not match derived_image")
        shared_digest = value["shared_base_digest"]
        shared_uri = value["shared_base_uri"]
        if not isinstance(shared_digest, str) or DIGEST_RE.fullmatch(shared_digest) is None:
            raise SubmissionError(f"{tool}: shared_base_digest is not a sha256 digest")
        if not isinstance(shared_uri, str) or not shared_uri.endswith(f"@{shared_digest}"):
            raise SubmissionError(f"{tool}: shared_base_uri digest does not match")
        for field in ("shared_base_source_sha256", "tool_build_sha256"):
            identity = value[field]
            if not isinstance(identity, str) or re.fullmatch(r"[0-9a-f]{64}", identity) is None:
                raise SubmissionError(f"{tool}: {field} is not 64 lowercase hex digits")
        if schema_version == 3:
            tool_digest = value["tool_image_digest"]
            tool_uri = value["tool_image_uri"]
            if not isinstance(tool_digest, str) or DIGEST_RE.fullmatch(tool_digest) is None:
                raise SubmissionError(f"{tool}: tool_image_digest is not a sha256 digest")
            if not isinstance(tool_uri, str) or not tool_uri.endswith(f"@{tool_digest}"):
                raise SubmissionError(f"{tool}: tool_image_uri digest does not match")
            selection_sha256 = value["selection_sha256"]
            if (
                not isinstance(selection_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", selection_sha256) is None
            ):
                raise SubmissionError(f"{tool}: selection_sha256 is not 64 lowercase hex digits")
        artifact = value["tool_artifact"]
        if not isinstance(artifact, dict) or set(artifact) != {"kind", "locator", "sha256"}:
            raise SubmissionError(f"{tool}: tool_artifact has invalid fields")
        if (
            not isinstance(artifact["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]) is None
        ):
            raise SubmissionError(f"{tool}: tool_artifact sha256 is invalid")
        adapter = value["adapter_bundle_sha256"]
        if (
            not isinstance(adapter, str)
            or len(adapter) != 64
            or any(character not in "0123456789abcdef" for character in adapter)
        ):
            raise SubmissionError(f"{tool}: adapter_bundle_sha256 is not 64 lowercase hex digits")
        for field in ("tool_version", "harness_revision"):
            field_value = value[field]
            if (
                not isinstance(field_value, str)
                or not field_value
                or any(character.isspace() for character in field_value)
            ):
                raise SubmissionError(f"{tool}: {field} must be a non-empty token")
        harness_revision = value["harness_revision"]
        if (
            not isinstance(harness_revision, str)
            or re.fullmatch(r"[0-9a-f]{40}", harness_revision) is None
        ):
            raise SubmissionError(f"{tool}: harness_revision must be a full lowercase commit ID")
        validated[tool] = dict(value)
    shared_inputs = {
        (image["shared_base_digest"], image["shared_base_source_sha256"])
        for image in validated.values()
    }
    if len(shared_inputs) != 1:
        raise SubmissionError(
            "image set must use one shared base digest and source identity for every tool"
        )
    return ImageSet(validated, schema_version=schema_version)


def validate_registered_images(
    images: Mapping[str, Mapping[str, Any]],
    *,
    root: Path | None = None,
    skip: set[str] | None = None,
) -> None:
    """Refuse component claims that disagree with the public capsule registration."""
    base = repo_root() if root is None else root
    if getattr(images, "schema_version", IMAGE_SET_SCHEMA_VERSION) == 2:
        return
    skipped = set() if skip is None else skip
    for tool, image in images.items():
        if tool in skipped:
            continue
        selection = load_registered_selection(base, tool)
        expected = {
            "tool_version": selection.tool_version,
            "shared_base_source_sha256": selection.shared_base_source_sha256,
            "tool_build_sha256": selection.tool_build_sha256,
            "tool_artifact": {
                "kind": selection.tool_artifact_kind,
                "locator": selection.tool_artifact_locator,
                "sha256": selection.tool_artifact_sha256,
            },
            "adapter_bundle_sha256": selection.adapter_bundle_sha256,
            "selection_sha256": selection.selection_sha256,
        }
        mismatched = sorted(field for field, value in expected.items() if image.get(field) != value)
        if mismatched:
            raise SubmissionError(
                f"{tool}: image set disagrees with registered {', '.join(mismatched)}"
            )


def _canonical_json(document: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _load_temporal_config() -> tuple[dict[str, Any], TemporalScope]:
    config = dict(ClientConfig.load_client_connect_config())
    target_host = config.get("target_host")
    namespace = config.get("namespace")
    if not isinstance(target_host, str) or not target_host:
        raise SubmissionError("Temporal client config has no non-empty target_host")
    if not isinstance(namespace, str) or not namespace:
        raise SubmissionError("Temporal client config has no non-empty namespace")
    return config, TemporalScope(target_host=target_host, namespace=namespace)


async def _workflow_input_size(request: CampaignWorkflowInput) -> int:
    payloads = await DataConverter.default.encode([request])
    return len(Payloads(payloads=payloads).SerializeToString())


def _preflight_workflow_input(request: CampaignWorkflowInput) -> None:
    encoded_size = asyncio.run(_workflow_input_size(request))
    if encoded_size > TEMPORAL_WORKFLOW_INPUT_MAX_BYTES:
        raise SubmissionError(
            "encoded Temporal Workflow input is "
            f"{encoded_size} bytes, above the {TEMPORAL_WORKFLOW_INPUT_MAX_BYTES}-byte "
            "safe request limit; split the campaign into smaller frozen campaigns"
        )


def _run(
    argv: Sequence[str], *, payload: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(argv, input=payload, capture_output=True, check=False)
    except OSError as exc:
        raise SubmissionError(f"cannot run {argv[0]}: {exc}") from None


def _already_exists(stderr: bytes) -> bool:
    message = stderr.decode("utf-8", errors="replace").lower()
    markers = ("already exists", "conditionnotmet", "precondition", "412")
    return any(token in message for token in markers)


def _not_found(stderr: bytes) -> bool:
    message = stderr.decode("utf-8", errors="replace").lower()
    markers = ("not found", "no urls matched", "does not exist", "404")
    return any(token in message for token in markers)


def _freeze(uri: str, content: bytes) -> None:
    created = _run(
        ("gcloud", "storage", "cp", "-", uri, "--if-generation-match=0"), payload=content
    )
    if created.returncode == 0:
        return
    if not _already_exists(created.stderr):
        detail = created.stderr.decode("utf-8", errors="replace").strip()
        raise SubmissionError(f"could not create {uri}: {detail or f'exit {created.returncode}'}")
    existing = _run(("gcloud", "storage", "cat", uri))
    if existing.returncode != 0:
        detail = existing.stderr.decode("utf-8", errors="replace").strip()
        reason = detail or f"exit {existing.returncode}"
        raise SubmissionError(f"could not read existing {uri}: {reason}")
    if existing.stdout != content:
        raise SubmissionError(f"{uri} already exists with different content")


def _read_optional_owner(uri: str) -> TemporalOwner | None:
    existing = _run(("gcloud", "storage", "cat", uri, f"--range=0-{TEMPORAL_OWNER_MAX_BYTES}"))
    if existing.returncode != 0:
        if _not_found(existing.stderr):
            return None
        detail = existing.stderr.decode("utf-8", errors="replace").strip()
        raise SubmissionError(
            f"could not read optional Temporal owner {uri}: "
            f"{detail or f'exit {existing.returncode}'}"
        )
    if len(existing.stdout) > TEMPORAL_OWNER_MAX_BYTES:
        raise SubmissionError(f"Temporal owner {uri} exceeds {TEMPORAL_OWNER_MAX_BYTES} bytes")
    try:
        document = json.loads(existing.stdout, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, SubmissionError) as exc:
        raise SubmissionError(f"Temporal owner {uri} is not valid JSON: {exc}") from None
    fields = {
        "schema_version",
        "campaign",
        "campaign_digest",
        "temporal_scope",
        "workflow_type",
        "task_queue",
        "workflow_id",
        "run_id",
    }
    if not isinstance(document, dict) or set(document) != fields:
        raise SubmissionError(f"Temporal owner {uri} has invalid fields")
    scope_document = document["temporal_scope"]
    if not isinstance(scope_document, dict) or set(scope_document) != {
        "target_host",
        "namespace",
    }:
        raise SubmissionError(f"Temporal owner {uri} has invalid scope")
    string_fields = (
        "campaign",
        "campaign_digest",
        "workflow_type",
        "task_queue",
        "workflow_id",
        "run_id",
    )
    if (
        document["schema_version"] != 1
        or any(
            not isinstance(document[field], str) or not document[field] for field in string_fields
        )
        or any(
            not isinstance(scope_document[field], str) or not scope_document[field]
            for field in ("target_host", "namespace")
        )
    ):
        raise SubmissionError(f"Temporal owner {uri} has invalid values")
    owner = TemporalOwner(
        campaign=document["campaign"],
        campaign_digest=document["campaign_digest"],
        scope=TemporalScope(
            target_host=scope_document["target_host"], namespace=scope_document["namespace"]
        ),
        workflow_id=document["workflow_id"],
        run_id=document["run_id"],
    )
    if existing.stdout != _canonical_json(owner.document()):
        raise SubmissionError(f"Temporal owner {uri} is not canonical")
    if (
        document["workflow_type"] != CampaignWorkflow.__name__
        or document["task_queue"] != TASK_QUEUE
    ):
        raise SubmissionError(f"Temporal owner {uri} names a different Temporal Workflow")
    return owner


def _freeze_owner(uri: str, owner: TemporalOwner) -> None:
    content = _canonical_json(owner.document())
    created = _run(
        ("gcloud", "storage", "cp", "-", uri, "--if-generation-match=0"), payload=content
    )
    if created.returncode == 0:
        return
    if not _already_exists(created.stderr):
        detail = created.stderr.decode("utf-8", errors="replace").strip()
        raise SubmissionError(f"could not create {uri}: {detail or f'exit {created.returncode}'}")
    existing = _read_optional_owner(uri)
    if existing is None:
        raise SubmissionError(f"Temporal owner {uri} disappeared after create collision")
    if existing != owner:
        raise SubmissionError(f"{uri} already exists with different content")


def _load_plans(args: argparse.Namespace) -> tuple[bench.Plan, ...]:
    paths = (
        [bench.default_path(bucket) for bucket in args.bucket]
        if args.bucket
        else [Path(path) for path in args.path]
    )
    loaded_plans: list[bench.Plan] = []
    seen_buckets: set[str] = set()
    for path in paths:
        loaded = bench.Plan.load(path)
        if path.resolve().parent == bench.buckets_dir().resolve():
            bench.check_roster(loaded, registered_tools())
        if loaded.bucket in seen_buckets:
            raise SubmissionError(
                f"campaign contains more than one plan for bucket {loaded.bucket!r}"
            )
        seen_buckets.add(loaded.bucket)
        loaded_plans.append(loaded)
    return tuple(loaded_plans)


PreparedCampaign = tuple[
    CampaignWorkflowInput,
    bytes,
    bytes,
    tuple[tuple[str, bytes], ...],
    dict[str, Any],
]


def _attempt_label(fingerprint: str) -> str:
    return base64.b32encode(bytes.fromhex(fingerprint)).decode().rstrip("=").lower()


def _prepare(args: argparse.Namespace, temporal_scope: TemporalScope) -> PreparedCampaign:
    loaded_plans = _load_plans(args)
    images = _read_image_set(Path(args.image_set))
    validate_registered_images(images)
    plan_tools = {tool for loaded in loaded_plans for tool in loaded.tools()}
    if set(images) != plan_tools:
        missing = sorted(plan_tools - set(images))
        extra = sorted(set(images) - plan_tools)
        detail = []
        if missing:
            detail.append(f"missing {', '.join(missing)}")
        if extra:
            detail.append(f"extra {', '.join(extra)}")
        raise SubmissionError(f"image set does not exactly cover the plans ({'; '.join(detail)})")

    generated = tuple(
        attempt
        for loaded in loaded_plans
        for attempt in attempts_for(loaded, campaign=args.campaign, images=images)
    )
    if not generated:
        raise SubmissionError("campaign contains no scheduled runs")
    job_ids = [attempt.job_id for attempt in generated]
    if len(job_ids) != len(set(job_ids)):
        raise SubmissionError("campaign contains duplicate Batch job IDs")

    plan_contents: list[bytes] = []
    for loaded in loaded_plans:
        plan_bytes = loaded.path.read_bytes()
        if hashlib.sha256(plan_bytes).hexdigest() != loaded.digest:
            raise SubmissionError(f"plan changed after it was resolved: {loaded.path}")
        plan_contents.append(plan_bytes)

    config = BatchConfig(
        results_bucket=args.results_bucket,
        anonymous_worker_service_account=args.anonymous_worker_sa,
        authenticated_worker_service_account=args.authenticated_worker_sa,
        aws_credential_secret=args.secret_resource,
        network=args.network,
        subnetwork=args.subnetwork,
        provisioning=args.provisioning,
        zone=args.zone,
        post_attempt_allowance_s=args.post_attempt_allowance_s,
    )
    jobs: list[dict[str, Any]] = []
    cases: list[BatchJobSpec] = []
    for attempt in generated:
        job = render_job(attempt, config)
        job["labels"] = {
            **job.get("labels", {}),
            "s3-study-attempt": _attempt_label(attempt.fingerprint),
        }
        jobs.append(job)
        controller_timeout_s = (
            attempt.case.timeout_s
            + config.term_grace_s
            + config.post_attempt_allowance_s
            + BATCH_QUEUE_CONTROL_ALLOWANCE_S
        )
        cases.append(
            BatchJobSpec(
                args.project,
                args.location,
                attempt.job_id,
                job,
                controller_timeout_s,
            )
        )

    campaign_document = manifest(
        campaign=args.campaign,
        plans=loaded_plans,
        images=images,
        attempts=generated,
        results_bucket=args.results_bucket,
        provisioning=args.provisioning,
        zone=args.zone,
    )
    manifest_bytes = _canonical_json(campaign_document)
    temporal_document = {
        "schema_version": 1,
        "campaign": args.campaign,
        "campaign_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "workflow_type": CampaignWorkflow.__name__,
        "task_queue": TASK_QUEUE,
        "temporal_scope": temporal_scope.document(),
        "cases": [
            {
                "project": case.project,
                "location": case.location,
                "job_id": case.job_id,
                "job": case.job,
                "controller_timeout_s": case.controller_timeout_s,
            }
            for case in cases
        ],
    }
    temporal_bytes = _canonical_json(temporal_document)
    campaign_digest = hashlib.sha256(temporal_bytes).hexdigest()
    plan_inputs = tuple(
        (str(record["path"]), content)
        for record, content in zip(campaign_document["plans"], plan_contents, strict=True)
    )
    dry_run = {
        "campaign.json": campaign_document,
        "temporal.json": temporal_document,
        "jobs": [
            {"job_id": attempt.job_id, "job": job}
            for attempt, job in zip(generated, jobs, strict=True)
        ],
    }
    return (
        CampaignWorkflowInput(tuple(cases), campaign_digest),
        manifest_bytes,
        temporal_bytes,
        plan_inputs,
        dry_run,
    )


def _freeze_campaign(
    *,
    campaign: str,
    results_bucket: str,
    manifest_bytes: bytes,
    temporal_bytes: bytes,
    plan_inputs: Sequence[tuple[str, bytes]],
) -> None:
    for path, content in plan_inputs:
        _freeze(f"gs://{results_bucket}/{path}", content)
    base = f"gs://{results_bucket}/{campaign_prefix(campaign)}"
    _freeze(f"{base}/campaign.json", manifest_bytes)
    _freeze(f"{base}/inputs/temporal.json", temporal_bytes)


async def _start_workflow(
    *,
    campaign: str,
    request: CampaignWorkflowInput,
    campaign_digest: str,
    client_config: Mapping[str, Any],
    temporal_scope: TemporalScope,
    owner_uri: str,
    owner: TemporalOwner | None,
) -> str:
    if request.campaign_digest != campaign_digest:
        raise SubmissionError("Workflow input digest does not match the frozen Temporal input")
    if owner is not None and owner != TemporalOwner(
        campaign=campaign,
        campaign_digest=campaign_digest,
        scope=temporal_scope,
        workflow_id=campaign,
        run_id=owner.run_id,
    ):
        raise SubmissionError("Temporal owner does not exactly match this campaign and scope")

    client = await Client.connect(**dict(client_config))
    if owner is None:
        try:
            handle = await client.start_workflow(
                CampaignWorkflow.run,
                request,
                id=campaign,
                task_queue=TASK_QUEUE,
                memo={"campaign_digest": campaign_digest},
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
        except WorkflowAlreadyStartedError:
            raise SubmissionError(
                "campaign Workflow is closed and its stable ID may not be reused"
            ) from None
    else:
        handle = client.get_workflow_handle(campaign, run_id=owner.run_id)

    try:
        description = await handle.describe()
    except RPCError as exc:
        if exc.status is RPCStatusCode.NOT_FOUND:
            raise SubmissionError("campaign Workflow history is missing or expired") from None
        raise
    try:
        recorded_digest = await description.memo_value("campaign_digest", type_hint=str)
    except KeyError:
        recorded_digest = None
    if description.status is not WorkflowExecutionStatus.RUNNING:
        raise SubmissionError("campaign Workflow is already closed")
    expected_run_id = owner.run_id if owner is not None else description.run_id
    if (
        description.id != campaign
        or description.run_id != expected_run_id
        or description.namespace != temporal_scope.namespace
        or description.workflow_type != CampaignWorkflow.__name__
        or description.task_queue != TASK_QUEUE
        or recorded_digest != campaign_digest
    ):
        raise SubmissionError("campaign ID belongs to a different Temporal Workflow")

    if owner is None:
        owner = TemporalOwner(
            campaign=campaign,
            campaign_digest=campaign_digest,
            scope=temporal_scope,
            workflow_id=campaign,
            run_id=description.run_id,
        )
        _freeze_owner(owner_uri, owner)
    await handle.signal(CampaignWorkflow.claim, campaign_digest)
    return str(handle.id)


def submit_campaign_main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client_config, temporal_scope = _load_temporal_config()
        request, manifest_bytes, temporal_bytes, plan_inputs, dry_run = _prepare(
            args, temporal_scope
        )
        _preflight_workflow_input(request)
        if args.dry_run:
            print(json.dumps(dry_run, sort_keys=True, indent=2, ensure_ascii=False))
            return 0

        _freeze_campaign(
            campaign=args.campaign,
            results_bucket=args.results_bucket,
            manifest_bytes=manifest_bytes,
            temporal_bytes=temporal_bytes,
            plan_inputs=plan_inputs,
        )
        owner_uri = (
            f"gs://{args.results_bucket}/{campaign_prefix(args.campaign)}"
            "/inputs/temporal-owner.json"
        )
        owner = _read_optional_owner(owner_uri)
        workflow_id = asyncio.run(
            _start_workflow(
                campaign=args.campaign,
                request=request,
                campaign_digest=request.campaign_digest,
                client_config=client_config,
                temporal_scope=temporal_scope,
                owner_uri=owner_uri,
                owner=owner,
            )
        )
        print(json.dumps({"campaign": args.campaign, "workflow_id": workflow_id}, sort_keys=True))
        return 0
    except (
        BuildSelectionError,
        CampaignError,
        SubmissionError,
        TemporalError,
        bench.PlanError,
        OSError,
    ) as exc:
        print(f"submit-campaign: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Compatibility entry point for direct campaign CLI use."""
    return submit_campaign_main(argv)
