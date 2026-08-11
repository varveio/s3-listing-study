"""Owner-bound Temporal campaign retry and finalization commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import timedelta
from typing import Any

import google.cloud.storage as storage  # type: ignore[import-untyped]
from google.api_core.exceptions import GoogleAPIError
from google.auth.exceptions import DefaultCredentialsError
from temporalio.client import Client
from temporalio.envconfig import ClientConfig
from temporalio.exceptions import TemporalError

from s3_listing_study.common.argparse_utils import UniqueStoreAction
from s3_listing_study.manager.campaign import CampaignError, campaign_prefix
from s3_listing_study.manager.campaign.cli import (
    TEMPORAL_OWNER_MAX_BYTES,
    SubmissionError,
    TemporalScope,
    _parse_owner,
)
from s3_listing_study.manager.campaign.report import (
    ReportError,
    _load_manifest,
    _load_temporal_input,
    _required_blob,
)
from s3_listing_study.temporal import TASK_QUEUE
from s3_listing_study.temporal.models import RetryCaseRequest
from s3_listing_study.temporal.workflows import CampaignWorkflow


class ControlError(RuntimeError):
    """A requested mutation did not match the exact frozen campaign owner."""


def retry_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="s3-listing-study retry-case", allow_abbrev=False)
    parser.add_argument("--campaign", action=UniqueStoreAction, required=True)
    parser.add_argument("--results-bucket", action=UniqueStoreAction, required=True)
    parser.add_argument("--job-id", action=UniqueStoreAction, required=True)
    parser.add_argument("--submission", action=UniqueStoreAction, required=True)
    return parser


def finalize_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="s3-listing-study finalize-campaign", allow_abbrev=False)
    parser.add_argument("--campaign", action=UniqueStoreAction, required=True)
    parser.add_argument("--results-bucket", action=UniqueStoreAction, required=True)
    return parser


async def _owned_handle(campaign: str, results_bucket: str) -> tuple[Any, Any]:
    config = ClientConfig.load_client_connect_config()
    target_host = config.get("target_host")
    namespace = config.get("namespace")
    if (
        not isinstance(target_host, str)
        or not target_host
        or not isinstance(namespace, str)
        or not namespace
    ):
        raise ControlError("Temporal client config needs target_host and namespace")
    scope = TemporalScope(target_host, namespace)
    bucket = storage.Client().bucket(results_bucket)
    owner_name = f"{campaign_prefix(campaign)}/inputs/temporal-owner.json"
    try:
        owner = _parse_owner(
            _required_blob(bucket, owner_name, max_bytes=TEMPORAL_OWNER_MAX_BYTES),
            f"gs://{results_bucket}/{owner_name}",
        )
    except (ReportError, SubmissionError) as exc:
        raise ControlError(str(exc)) from None
    if owner.campaign != campaign or owner.scope != scope:
        raise ControlError("Temporal owner does not match the requested campaign and scope")
    manifest = _load_manifest(bucket, campaign)
    temporal_input = _load_temporal_input(bucket, campaign, manifest, owner, scope)
    client = await Client.connect(**config)
    handle = client.get_workflow_handle(owner.workflow_id, run_id=owner.run_id)
    description = await handle.describe()
    try:
        digest = await description.memo_value("campaign_digest", type_hint=str)
    except KeyError:
        digest = None
    if (
        description.id != owner.workflow_id
        or description.run_id != owner.run_id
        or description.namespace != scope.namespace
        or description.workflow_type != CampaignWorkflow.__name__
        or description.task_queue != TASK_QUEUE
        or digest != owner.campaign_digest
    ):
        raise ControlError("Temporal owner does not exactly match the retained Workflow Run")
    return handle, (manifest, temporal_input)


async def _retry(args: argparse.Namespace) -> dict[str, Any]:
    try:
        submission = int(args.submission)
    except (TypeError, ValueError):
        raise ControlError("--submission must be an integer") from None
    if submission < 2:
        raise ControlError("--submission must be at least 2")
    handle, frozen = await _owned_handle(args.campaign, args.results_bucket)
    manifest, temporal_input = frozen
    manifest_ids = [item.job_id for item in manifest.cases]
    if args.job_id not in manifest_ids or args.job_id not in {
        item.job_id for item in temporal_input.cases
    }:
        raise ControlError("--job-id is not an original frozen manifest job ID")
    result = await handle.execute_update(
        CampaignWorkflow.retry_case,
        RetryCaseRequest(args.job_id, submission),
        id=f"retry-{args.job_id}-s{submission}",
        rpc_timeout=timedelta(seconds=30),
    )
    return asdict(result)


async def _finalize(args: argparse.Namespace) -> list[dict[str, Any]]:
    handle, _frozen = await _owned_handle(args.campaign, args.results_bucket)
    result = await handle.execute_update(
        CampaignWorkflow.finalize_campaign,
        rpc_timeout=timedelta(seconds=30),
    )
    return [asdict(item) for item in result]


def _main(parser: argparse.ArgumentParser, runner: Any, argv: Sequence[str] | None) -> int:
    args = parser.parse_args(argv)
    try:
        print(json.dumps(asyncio.run(runner(args)), sort_keys=True, indent=2))
        return 0
    except (
        CampaignError,
        ControlError,
        DefaultCredentialsError,
        GoogleAPIError,
        OSError,
        ReportError,
        TemporalError,
    ) as exc:
        print(f"{parser.prog}: {exc}", file=sys.stderr)
        return 1


def retry_case_main(argv: Sequence[str] | None = None) -> int:
    return _main(retry_parser(), _retry, argv)


def finalize_campaign_main(argv: Sequence[str] | None = None) -> int:
    return _main(finalize_parser(), _finalize, argv)
