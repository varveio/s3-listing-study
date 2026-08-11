"""Manual local Temporal controller-plus-observer SDK test-server exercise."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from types import SimpleNamespace
from typing import Any, cast

from google.api_core.exceptions import PreconditionFailed
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from s3_listing_study.manager.campaign import campaign_prefix
from s3_listing_study.manager.campaign.cli import (
    TemporalOwner,
    TemporalScope,
    _canonical_json,
)
from s3_listing_study.manager.campaign.report import ReportError, _publish, observe_once
from s3_listing_study.temporal import TASK_QUEUE
from s3_listing_study.temporal.models import (
    BatchJobOutcome,
    BatchJobSpec,
    CampaignWorkflowInput,
)
from s3_listing_study.temporal.workflows import CampaignWorkflow, CaseWorkflow


class MemoryBlob:
    def __init__(self, content: bytes | None = None) -> None:
        self.content = content
        self.size = len(content) if content is not None else 0
        self.generation = 1

    def download_as_bytes(self, **_kwargs: Any) -> bytes:
        assert self.content is not None
        return self.content

    def upload_from_string(self, content: bytes, **kwargs: Any) -> None:
        assert kwargs["if_generation_match"] == 0
        if self.content is not None:
            error = cast(type[Exception], PreconditionFailed)
            raise error("create-only collision")
        self.content = content
        self.size = len(content)


class MemoryBucket:
    name = "local-results"

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = {name: MemoryBlob(content) for name, content in objects.items()}

    def get_blob(self, name: str) -> MemoryBlob | None:
        return self.objects.get(name)

    def blob(self, name: str) -> MemoryBlob:
        return self.objects.setdefault(name, MemoryBlob())

    def list_blobs(self, *, prefix: str, delimiter: str) -> Any:
        assert delimiter == "/"
        prefixes = {
            prefix + name.removeprefix(prefix).split("/", 1)[0] + "/"
            for name in self.objects
            if name.startswith(prefix) and "/" in name.removeprefix(prefix)
        }
        return SimpleNamespace(pages=(SimpleNamespace(prefixes=prefixes),))


def frozen_inputs(
    campaign: str, case: BatchJobSpec, scope: TemporalScope
) -> tuple[bytes, bytes, str]:
    run_prefix = f"{campaign_prefix(campaign)}/results/example-bucket/local/case/run-1"
    image = {
        "derived_image": "sha256:" + "b" * 64,
        "image_uri": "local.invalid/derived@sha256:" + "b" * 64,
        "shared_base_digest": "sha256:" + "8" * 64,
        "shared_base_uri": "local.invalid/base@sha256:" + "8" * 64,
        "shared_base_source_sha256": "6" * 64,
        "tool_build_sha256": "7" * 64,
        "tool_artifact": {
            "kind": "local-test",
            "locator": "local-controller-exercise",
            "sha256": "3" * 64,
        },
        "tool_version": "local-test",
        "adapter_bundle_sha256": "4" * 64,
        "harness_revision": "5" * 40,
        "tool_image_digest": "sha256:" + "1" * 64,
        "tool_image_uri": "local.invalid/tool@sha256:" + "1" * 64,
        "selection_sha256": "2" * 64,
    }
    manifest = {
        "schema_version": 3,
        "campaign": campaign,
        "results_bucket": MemoryBucket.name,
        "attempt_fingerprint_version": 3,
        "provisioning": "STANDARD",
        "zone": None,
        "plans": [],
        "images": {"local": image},
        "attempts": [
            {
                "job_id": case.job_id,
                "submission": 1,
                "run_ordinal": 1,
                "bucket": "example-bucket",
                "region": "us-east-1",
                "tool": "local",
                "case_id": "case",
                "mode": "local-mode",
                "auth": "anonymous",
                "case_fingerprint": "a" * 64,
                "derived_image": image["derived_image"],
                "fingerprint": "c" * 64,
                "attempt_fingerprint": "c" * 64,
                "resources": {
                    "machine_type": "local",
                    "vcpus": 1,
                    "memory_gb": 1,
                    "container_memory_gb": None,
                },
                "env": [],
                "reps": 1,
                "timeout_s": 30,
                "prefix": run_prefix,
            }
        ],
    }
    manifest_bytes = _canonical_json(manifest)
    temporal = {
        "schema_version": 1,
        "campaign": campaign,
        "campaign_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "workflow_type": "CampaignWorkflow",
        "task_queue": TASK_QUEUE,
        "temporal_scope": scope.document(),
        "cases": [
            {
                "project": case.project,
                "location": case.location,
                "job_id": case.job_id,
                "job": case.job,
                "controller_timeout_s": case.controller_timeout_s,
            }
        ],
    }
    temporal_bytes = _canonical_json(temporal)
    return manifest_bytes, temporal_bytes, hashlib.sha256(temporal_bytes).hexdigest()


async def exercise() -> None:
    # The first Activity failure is intentional. Keep this manual success path
    # concise without changing Temporal's actual retry behavior.
    logging.getLogger("temporalio").setLevel(logging.CRITICAL)
    suffix = "t" + uuid.uuid4().hex[:5]
    campaign_id = f"2026-08-11-{suffix}"
    job_id = f"local-case-{suffix}"
    scope = TemporalScope("local-sdk-test-server", "default")
    activity_started = asyncio.Event()
    activity_release = asyncio.Event()
    attempts: list[int] = []

    @activity.defn(name="run_batch_job")
    async def local_batch(spec: BatchJobSpec) -> BatchJobOutcome:
        attempt = activity.info().attempt
        attempts.append(attempt)
        if attempt == 1:
            raise ApplicationError("exercise retry", type="TransientLocalTest")
        activity.heartbeat({"job_name": spec.resource_name, "state": "RUNNING"})
        activity_started.set()
        await activity_release.wait()
        return BatchJobOutcome(spec.resource_name, "SUCCEEDED")

    case = BatchJobSpec(
        project="local-project",
        location="local-location",
        job_id=job_id,
        job={},
        controller_timeout_s=60,
    )
    manifest_bytes, temporal_bytes, digest = frozen_inputs(campaign_id, case, scope)
    bucket = MemoryBucket(
        {
            f"{campaign_prefix(campaign_id)}/campaign.json": manifest_bytes,
            f"{campaign_prefix(campaign_id)}/inputs/temporal.json": temporal_bytes,
        }
    )
    async with (
        await WorkflowEnvironment.start_time_skipping() as environment,
        Worker(
            environment.client,
            task_queue=TASK_QUEUE,
            workflows=[CampaignWorkflow, CaseWorkflow],
            activities=[local_batch],
        ),
    ):
        handle = await environment.client.start_workflow(
            CampaignWorkflow.run,
            CampaignWorkflowInput((case,), digest),
            id=campaign_id,
            task_queue=TASK_QUEUE,
            memo={"campaign_digest": digest},
        )
        pending = await handle.query(CampaignWorkflow.progress)
        assert pending[0].phase == "pending"
        assert not activity_started.is_set()

        run_id = handle.first_execution_run_id
        assert run_id is not None
        owner = TemporalOwner(
            campaign_id,
            digest,
            scope,
            campaign_id,
            run_id,
        )
        bucket.objects[f"{campaign_prefix(campaign_id)}/inputs/temporal-owner.json"] = MemoryBlob(
            _canonical_json(owner.document())
        )
        await handle.signal(CampaignWorkflow.claim, digest)
        await asyncio.wait_for(activity_started.wait(), timeout=30)
        retrying = await observe_once(
            temporal_client=environment.client,
            bucket=bucket,
            campaign=campaign_id,
            owner=owner,
            scope=scope,
        )
        assert attempts == [1, 2]
        assert retrying["cases"][0]["controller"]["phase"] == "retrying"
        assert retrying["cases"][0]["controller"]["activity_attempt"] == 2

        activity_release.set()
        result = await asyncio.wait_for(handle.result(), timeout=30)
        assert result[0].provider_resource_name == case.resource_name
        report = await observe_once(
            temporal_client=environment.client,
            bucket=bucket,
            campaign=campaign_id,
            owner=owner,
            scope=scope,
        )
        repeated = await observe_once(
            temporal_client=environment.client,
            bucket=bucket,
            campaign=campaign_id,
            owner=owner,
            scope=scope,
        )
        assert report == repeated
        assert report["controller_complete"]
        assert report["provider_settled"]
        assert report["report_final"]
        assert not report["operational_success"]
        assert report["cases"][0]["evidence"]["state"] == "missing"
        assert report["cases"][0]["provider_resource_name"] == case.resource_name

        _publish(bucket, campaign_id, report)
        _publish(bucket, campaign_id, report)
        changed = json.loads(json.dumps(report))
        changed["operational_success"] = True
        try:
            _publish(bucket, campaign_id, changed)
        except ReportError:
            pass
        else:
            raise AssertionError("different create-only report collision was accepted")


def main() -> None:
    asyncio.run(exercise())
    print("local Temporal controller-plus-observer exercise passed")


if __name__ == "__main__":
    main()
