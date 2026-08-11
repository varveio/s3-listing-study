from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
from dataclasses import replace
from pathlib import Path

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.envconfig import ClientConfig
from temporalio.service import RPCError, RPCStatusCode

from s3_listing_study.manager.bench.plan import Plan
from s3_listing_study.manager.campaign import attempts_for, campaign_prefix, manifest
from s3_listing_study.manager.campaign import cli as campaign_cli
from s3_listing_study.manager.campaign.batch import BatchConfig, render_job
from s3_listing_study.temporal import MAX_CASES, TASK_QUEUE, models
from s3_listing_study.temporal.workflows import CampaignWorkflow

PreparedCampaign = tuple[models.CampaignWorkflowInput, bytes, tuple[tuple[str, bytes], ...]]
TEMPORAL_ROOT = "temporal"


def _attempt_label(fingerprint: str) -> str:
    return base64.b32encode(bytes.fromhex(fingerprint)).decode().rstrip("=").lower()


def prepare(args: argparse.Namespace) -> PreparedCampaign:
    plans = tuple(Plan.load(Path(path)) for path in args.path)
    buckets = [plan.bucket for plan in plans]
    if len(buckets) != len(set(buckets)):
        raise RuntimeError("campaign contains duplicate plan buckets")
    plan_inputs: list[tuple[str, bytes]] = []
    for plan in plans:
        content = plan.path.read_bytes()
        if hashlib.sha256(content).hexdigest() != plan.digest:
            raise RuntimeError(f"plan changed after resolution: {plan.path}")
        plan_inputs.append((plan.bucket, content))
    images = campaign_cli._read_image_set(Path(args.image_set))
    campaign_cli.validate_registered_images(images)
    if set(images) != {tool for plan in plans for tool in plan.tools()}:
        raise RuntimeError("image set must exactly cover the selected plan tools")
    generated = tuple(
        replace(attempt, prefix=f"{TEMPORAL_ROOT}/{attempt.prefix}")
        for plan in plans
        for attempt in attempts_for(plan, campaign=args.campaign, images=images)
    )
    if not generated or len(generated) > MAX_CASES:
        raise RuntimeError(f"Temporal spike requires between 1 and {MAX_CASES} cases")
    job_ids = [attempt.job_id for attempt in generated]
    if len(job_ids) != len(set(job_ids)):
        raise RuntimeError("campaign contains duplicate Batch job IDs")
    if any(attempt.case.auth != "anonymous" for attempt in generated):
        raise RuntimeError("Temporal spike accepts anonymous cases only")
    config = BatchConfig(
        results_bucket=args.results_bucket,
        anonymous_worker_service_account=args.anonymous_worker_sa,
        network=args.network,
        subnetwork=args.subnetwork,
        provisioning=args.provisioning,
        zone=args.zone,
    )
    cases: list[models.BatchJobSpec] = []
    for attempt in generated:
        job = render_job(attempt, config)
        identity = _attempt_label(attempt.fingerprint)
        job["labels"] = {**job.get("labels", {}), "s3-study-attempt": identity}
        cases.append(models.BatchJobSpec(args.project, args.location, attempt.job_id, job))
    document = manifest(
        campaign=args.campaign,
        plans=plans,
        images=images,
        attempts=generated,
        results_bucket=args.results_bucket,
        provisioning=args.provisioning,
        zone=args.zone,
    )
    for plan_record in document["plans"]:
        plan_record["path"] = f"{TEMPORAL_ROOT}/{plan_record['path']}"
    request = models.CampaignWorkflowInput(tuple(cases))
    return request, campaign_cli._canonical_json(document), tuple(plan_inputs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="s3-listing-study-temporal-start", allow_abbrev=False)
    parser.add_argument("--path", action="append", required=True)
    required: tuple[str, ...] = ("campaign", "image-set", "project", "location")
    required += ("results-bucket", "anonymous-worker-sa")
    for name in required:
        parser.add_argument(f"--{name}", required=True)
    for name in ("network", "subnetwork"):
        parser.add_argument(f"--{name}")
    parser.add_argument("--zone")
    parser.add_argument("--provisioning", choices=("STANDARD", "SPOT"), default="SPOT")
    return parser


async def start(args: argparse.Namespace) -> str:
    request, campaign_bytes, plan_inputs = prepare(args)
    campaign_digest = hashlib.sha256(campaign_bytes).hexdigest()
    prefix = f"{TEMPORAL_ROOT}/{campaign_prefix(args.campaign)}"
    base = f"gs://{args.results_bucket}/{prefix}"
    for bucket, content in plan_inputs:
        campaign_cli._freeze(f"{base}/inputs/plans/{bucket}.yaml", content)
    created = campaign_cli._freeze(f"{base}/campaign.json", campaign_bytes)
    client = await Client.connect(**ClientConfig.load_client_connect_config())
    if not created:
        handle = client.get_workflow_handle(args.campaign)
        try:
            description = await handle.describe()
        except RPCError as exc:
            if exc.status is RPCStatusCode.NOT_FOUND:
                raise RuntimeError("reserved campaign Workflow is unstarted or expired") from None
            raise
        try:
            recorded_digest = await description.memo_value("campaign_digest", type_hint=str)
        except KeyError:
            recorded_digest = None
        if (
            description.status is not WorkflowExecutionStatus.RUNNING
            or description.workflow_type != CampaignWorkflow.__name__
            or description.task_queue != TASK_QUEUE
            or recorded_digest != campaign_digest
        ):
            raise RuntimeError("reserved campaign ID belongs to a different Temporal Workflow")
        return str(handle.id)
    handle = await client.start_workflow(
        CampaignWorkflow.run,
        request,
        id=args.campaign,
        task_queue=TASK_QUEUE,
        memo={"campaign_digest": campaign_digest},
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
    )
    return str(handle.id)


def main() -> None:
    print(asyncio.run(start(build_parser().parse_args())))
