"""Strict summary evidence, historical leaves, duplicates, and publication."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from google.api_core.exceptions import PreconditionFailed

from s3_listing_study.manager.campaign import cli, controller, ledger, report
from s3_listing_study.manager.campaign.models import CaseControllerProgress

CAMPAIGN = "2026-08-11-report"
BUCKET = "study-results"
UUIDS = (
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
)


class FakeBlob:
    def __init__(self, content: bytes, *, collision: bool = False) -> None:
        self.content = content
        self.size = len(content)
        self.generation = 1
        self.collision = collision
        self.uploads: list[tuple[bytes, dict[str, Any]]] = []

    def download_as_bytes(self, **_kwargs: Any) -> bytes:
        return self.content

    def upload_from_string(self, content: bytes, **kwargs: Any) -> None:
        if self.collision:
            raise PreconditionFailed("exists")  # type: ignore[no-untyped-call]
        self.uploads.append((content, kwargs))
        self.content = content
        self.size = len(content)


class FakeBucket:
    name = BUCKET

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = {name: FakeBlob(content) for name, content in objects.items()}
        self.listed: list[tuple[str, str]] = []

    def get_blob(self, name: str) -> FakeBlob | None:
        return self.objects.get(name)

    def blob(self, name: str) -> FakeBlob:
        return self.objects.setdefault(name, FakeBlob(b""))

    def list_blobs(self, *, prefix: str, delimiter: str) -> Any:
        self.listed.append((prefix, delimiter))
        prefixes = {
            prefix + name.removeprefix(prefix).split("/", 1)[0] + "/"
            for name in self.objects
            if name.startswith(prefix) and "/" in name.removeprefix(prefix)
        }
        return SimpleNamespace(pages=(SimpleNamespace(prefixes=prefixes),))


def frozen() -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    image = {
        "derived_image": "sha256:" + "f" * 64,
        "image_uri": "registry.example/derived@sha256:" + "f" * 64,
        "shared_base_digest": "sha256:" + "b" * 64,
        "shared_base_uri": "registry.example/base@sha256:" + "b" * 64,
        "shared_base_source_sha256": "c" * 64,
        "tool_build_sha256": "e" * 64,
        "tool_image_digest": "sha256:" + "1" * 64,
        "tool_image_uri": "registry.example/tool@sha256:" + "1" * 64,
        "selection_sha256": "2" * 64,
        "tool_artifact": {
            "kind": "url",
            "locator": "https://example.invalid",
            "sha256": "3" * 64,
        },
        "tool_version": "2.31.0",
        "adapter_bundle_sha256": "a" * 64,
        "harness_revision": "4" * 40,
    }
    case = {
        "job_id": "job-s1",
        "submission": 1,
        "run_ordinal": 1,
        "bucket": "example-bucket",
        "region": "us-east-1",
        "tool": "aws-cli",
        "case_id": "case-1",
        "mode": "s3api-v2-text",
        "auth": "anonymous",
        "case_fingerprint": "a" * 64,
        "derived_image": image["derived_image"],
        "fingerprint": "d" * 64,
        "attempt_fingerprint": "d" * 64,
        "resources": {
            "machine_type": "n4-highcpu-2",
            "vcpus": 2,
            "memory_gb": 4,
            "container_memory_gb": 2,
        },
        "env": [],
        "reps": 1,
        "timeout_s": 3600,
        "prefix": f"campaigns/{CAMPAIGN}/results/example-bucket/aws-cli/case-1/run-1",
    }
    document = {
        "schema_version": 3,
        "campaign": CAMPAIGN,
        "results_bucket": BUCKET,
        "attempt_fingerprint_version": 3,
        "provisioning": "SPOT",
        "zone": None,
        "plans": [],
        "images": {"aws-cli": image},
        "attempts": [case],
    }
    return cli._canonical_json(document), case, image


def result(
    case: dict[str, Any],
    image: dict[str, Any],
    leaf: str,
    *,
    job_id: str,
    submission: int,
) -> bytes:
    artifact_uri = f"gs://{BUCKET}/{case['prefix']}/{leaf}"
    document = {
        "schema_version": 3,
        "attempt_id": leaf,
        "tool": {"name": case["tool"], "version": image["tool_version"]},
        "images": {
            "derived": case["derived_image"],
            "tool": {
                "digest": image["tool_image_digest"],
                "uri": image["tool_image_uri"],
            },
            "shared_base": {
                "digest": image["shared_base_digest"],
                "uri": image["shared_base_uri"],
            },
        },
        "build_inputs": {
            "shared_base": {"source_sha256": image["shared_base_source_sha256"]},
            "tool": {
                "build_sha256": image["tool_build_sha256"],
                "artifact": image["tool_artifact"],
                "selection_sha256": image["selection_sha256"],
            },
        },
        "harness_revision": image["harness_revision"],
        "adapter_bundle_sha256": image["adapter_bundle_sha256"],
        "campaign": {
            "campaign_id": CAMPAIGN,
            "job_id": job_id,
            "case_id": case["case_id"],
            "case_fingerprint": case["case_fingerprint"],
            "attempt_fingerprint": case["attempt_fingerprint"],
            "run_ordinal": 1,
            "submission_number": submission,
            "declared_resources": case["resources"],
        },
        "artifact_uri": artifact_uri,
        "result_uri": f"{artifact_uri}/result.json",
        "outcome": {
            "status": "completed",
            "exit_code": 0,
            "signal": None,
            "timed_out": False,
            "cleanup": {
                "state": "not_needed",
                "term_sent": False,
                "kill_sent": False,
                "process_group_empty": True,
                "escaped_descendants": [],
            },
        },
        "timing": {"elapsed_ns": 123},
        "resources": {"rusage_children_max_child_peak_rss_kb": 456},
        "logical_request": {
            "schema_version": 1,
            "operation": "list",
            "mode": case["mode"],
            "bucket": case["bucket"],
            "region": case["region"],
            "prefix": "",
            "authentication": case["auth"],
            "concurrency": None,
        },
        "target": {
            "mode": case["mode"],
            "bucket": case["bucket"],
            "region": case["region"],
            "prefix": "",
            "scope": "full",
        },
        "summary": {
            "schema_version": 2,
            "status": "counted",
            "row_count": 789,
            "reason": None,
            "error": None,
            "adapter_bundle_sha256": image["adapter_bundle_sha256"],
            "duckdb_version": "1.5.5",
            "interpreter": {
                "architecture": "x86_64",
                "implementation": "CPython",
                "libc": "glibc-2.41",
                "package_manifest_sha256": "5" * 64,
                "running_version": "3.13.5",
                "source": "image-marker",
            },
        },
        "secret_scan": {"status": "clean", "streams": {"stdout": "clean", "stderr": "clean"}},
    }
    return report._worker_json(document)


def selected_case() -> tuple[report.ManifestCase, dict[str, Any], dict[str, Any]]:
    content, case, image = frozen()
    [parsed] = report._parse_manifest(content, campaign=CAMPAIGN, results_bucket=BUCKET)
    return parsed, case, image


def test_nonterminal_evidence_is_pending_without_any_listing() -> None:
    parsed, _case, _image = selected_case()
    bucket = FakeBucket({})
    evidence = report._evidence(bucket, parsed, terminal=False)
    assert evidence.state == "pending"
    assert bucket.listed == []


def test_strict_result_rejects_provenance_mismatch() -> None:
    parsed, case, image = selected_case()
    bad = dict(image)
    bad["selection_sha256"] = "9" * 64
    name = f"{case['prefix']}/{UUIDS[0]}/result.json"
    bucket = FakeBucket({name: result(case, bad, UUIDS[0], job_id=case["job_id"], submission=1)})
    evidence = report._evidence(bucket, parsed, terminal=True)
    assert evidence.state == "invalid"
    assert evidence.leaves[0]["reason"] == "invalid_result_provenance"


def test_duplicate_current_results_select_no_canonical_leaf() -> None:
    parsed, case, image = selected_case()
    objects = {
        f"{case['prefix']}/{leaf}/result.json": result(
            case, image, leaf, job_id=case["job_id"], submission=1
        )
        for leaf in UUIDS
    }
    evidence = report._evidence(FakeBucket(objects), parsed, terminal=True)
    assert evidence.state == "duplicate"
    assert evidence.canonical_result_uri is None
    assert len(evidence.leaves) == 2


def test_prior_submission_is_historical_and_does_not_compete_with_current() -> None:
    parsed, case, image = selected_case()
    current_job = "job-s2"
    objects = {
        f"{case['prefix']}/{UUIDS[0]}/result.json": result(
            case, image, UUIDS[0], job_id=case["job_id"], submission=1
        ),
        f"{case['prefix']}/{UUIDS[1]}/result.json": result(
            case, image, UUIDS[1], job_id=current_job, submission=2
        ),
    }
    evidence = report._evidence(
        FakeBucket(objects),
        parsed,
        terminal=True,
        current_job_id=current_job,
        current_submission=2,
    )
    assert evidence.state == "recorded"
    assert [leaf["state"] for leaf in evidence.leaves] == ["historical", "recorded"]
    assert evidence.leaves[0]["submission_number"] == 1


def test_publication_is_create_only_and_idempotent_only_for_identical_bytes() -> None:
    payload = {
        "schema_version": 3,
        "campaign": CAMPAIGN,
        "report_final": True,
    }
    bucket = FakeBucket({})
    report._publish(bucket, CAMPAIGN, payload)
    published = bucket.objects[f"campaigns/{CAMPAIGN}/report.json"]
    assert published.uploads[0][1]["if_generation_match"] == 0

    content = cli._canonical_json(payload)
    identical = FakeBucket({f"campaigns/{CAMPAIGN}/report.json": content})
    identical.objects[f"campaigns/{CAMPAIGN}/report.json"].collision = True
    report._publish(identical, CAMPAIGN, payload)

    different = FakeBucket({f"campaigns/{CAMPAIGN}/report.json": b"{}\n"})
    different.objects[f"campaigns/{CAMPAIGN}/report.json"].collision = True
    with pytest.raises(report.ReportError, match="different content"):
        report._publish(different, CAMPAIGN, payload)


def test_report_retains_deterministic_resource_for_not_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content, case, _image = frozen()
    snapshot = report.ManifestSnapshot(
        bucket=BUCKET,
        campaign=CAMPAIGN,
        generation=1,
        content=content,
        cases=tuple(report._parse_manifest(content, campaign=CAMPAIGN, results_bucket=BUCKET)),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    path = tmp_path / "campaign.sqlite3"
    with ledger.open_ledger(path) as connection:
        ledger.register_campaign(
            connection,
            campaign=CAMPAIGN,
            project="study",
            location="us-east1",
            results_bucket=BUCKET,
            manifest_sha256=snapshot.sha256,
            cases=[
                {
                    "base_job_id": case["job_id"],
                    "job": {},
                    "controller_timeout_s": 1,
                }
            ],
            now="2026-08-11T12:00:00Z",
        )
    progress = [
        CaseControllerProgress(
            job_id=case["job_id"],
            phase="terminal",
            provider_state="NOT_CREATED",
            failure_type="PermanentGoogleError",
            provider_settled=True,
            current_job_id=case["job_id"],
            accepted_failure=True,
        )
    ]
    monkeypatch.setattr(controller, "reconcile_once", lambda **_kwargs: progress)

    rendered = report.observe_once(
        bucket=FakeBucket({}),
        campaign=CAMPAIGN,
        ledger_path=path,
        manifest_cache=snapshot,
    )

    assert rendered["report_final"] is True
    assert rendered["schema_version"] == 3
    assert rendered["operational_success"] is False
    assert rendered["campaign_digest"] != snapshot.sha256
    assert rendered["controller_input_sha256"] == rendered["campaign_digest"]
    assert rendered["engine"] == {
        "name": "sqlite-gcp-batch",
        "execution_id": CAMPAIGN,
        "run_id": None,
        "status": "completed",
    }
    assert set(rendered) == {
        "schema_version",
        "campaign",
        "campaign_manifest_sha256",
        "campaign_digest",
        "controller_input_sha256",
        "engine",
        "controller_complete",
        "provider_settled",
        "report_final",
        "operational_success",
        "aggregate",
        "cases",
    }
    assert "accepted_failure" not in rendered["cases"][0]
    assert rendered["aggregate"]["controller"] == {
        "pending": 0,
        "running": 0,
        "retrying": 0,
        "awaiting_retry": 0,
        "terminal": 1,
    }
    assert rendered["cases"][0]["provider_resource_name"] == (
        f"projects/study/locations/us-east1/jobs/{case['job_id']}"
    )
    assert rendered["cases"][0]["controller"]["terminal"]["provider_resource_name"] is None
