"""Engine-neutral summary-only campaign reconciliation and immutable reporting."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import google.cloud.storage as storage  # type: ignore[import-untyped]
from google.api_core.exceptions import GoogleAPIError, PreconditionFailed
from google.auth.exceptions import DefaultCredentialsError

from s3_listing_study.common.argparse_utils import UniqueStoreAction
from s3_listing_study.manager.campaign import (
    CampaignError,
    campaign_prefix,
    controller,
    ledger,
    provider,
)
from s3_listing_study.manager.campaign.cli import IMAGE_SET_FIELDS, _canonical_json
from s3_listing_study.manager.campaign.models import CaseControllerProgress

MANIFEST_MAX_BYTES = 8_000_000
RESULT_MAX_BYTES = 1_000_000
REPORT_MAX_BYTES = 8_000_000
MAX_EXECUTION_LEAVES_PER_RUN = 256
DEFAULT_POLL_INTERVAL_S = 10.0
CONTROLLER_PHASES = ("pending", "running", "awaiting_retry", "terminal")
CONTROLLER_AGGREGATE_PHASES = (
    "pending",
    "running",
    "retrying",
    "awaiting_retry",
    "terminal",
)
EVIDENCE_STATES = ("pending", "missing", "recorded", "duplicate", "invalid", "unsealed")
SUBJECT_STATES = ("completed", "failed", "timed_out", "signaled", "harness_error", "unavailable")
PROVIDER_STATES = ("SUCCEEDED", "FAILED", "NOT_CREATED", "unavailable")


class ReportError(RuntimeError):
    """Frozen campaign, controller state, or result evidence is unsafe to report."""


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
    parser.add_argument("--ledger", "--ledger-path", action=UniqueStoreAction, required=True)
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


def _controller_view(item: CaseControllerProgress) -> dict[str, Any]:
    return {
        "phase": item.phase,
        "child_workflow_id": None,
        "child_run_id": None,
        "activity_attempt": None,
        "last_heartbeat": None,
        "terminal": (
            {
                "controller_state": "failed" if item.failure_type else "completed",
                "failure_type": item.failure_type,
                "provider_settled": item.provider_settled,
                "provider_state": item.provider_state,
                "provider_resource_name": item.provider_resource_name,
            }
            if item.phase in ("awaiting_retry", "terminal")
            else None
        ),
    }


def _validate_progress(
    progress: Sequence[CaseControllerProgress],
    cases: Sequence[ManifestCase],
    *,
    project: str,
    location: str,
) -> None:
    if [item.job_id for item in progress] != [case.job_id for case in cases]:
        raise ReportError("controller progress does not exactly match campaign.json jobs")
    expected = {case.job_id: case for case in cases}
    for item in progress:
        if item.phase not in CONTROLLER_PHASES:
            raise ReportError(f"controller progress for {item.job_id} has an invalid phase")
        case = expected[item.job_id]
        current_job_id = item.current_job_id or item.job_id
        if not _is_int(item.current_submission, minimum=1):
            raise ReportError(f"controller progress for {item.job_id} has invalid submission")
        stem, separator, original = item.job_id.rpartition("-s")
        wanted = (
            item.job_id if item.current_submission == 1 else f"{stem}-s{item.current_submission}"
        )
        if not separator or original != "1" or current_job_id != wanted:
            raise ReportError(f"controller progress for {item.job_id} has invalid current job")
        if item.phase in ("pending", "running"):
            expected_resource = f"projects/{project}/locations/{location}/jobs/{current_job_id}"
            pending_clean = item.phase == "pending" and all(
                value is None
                for value in (item.provider_state, item.failure_type, item.provider_resource_name)
            )
            running_valid = item.phase == "running" and (
                all(
                    value is None
                    for value in (
                        item.provider_state,
                        item.failure_type,
                        item.provider_resource_name,
                    )
                )
                or (
                    item.provider_state not in ("SUCCEEDED", "FAILED", "NOT_CREATED")
                    and item.failure_type in (None, "BatchJobCollision")
                    and item.provider_resource_name == expected_resource
                )
            )
            if item.provider_settled or not (pending_clean or running_valid):
                raise ReportError(f"controller progress for {item.job_id} settles prematurely")
            continue
        if item.phase == "awaiting_retry" and (
            not item.provider_settled
            or (item.provider_state == "SUCCEEDED" and item.failure_type is None)
        ):
            raise ReportError(f"controller progress for {item.job_id} has invalid retry wait")
        if not item.provider_settled:
            raise ReportError(f"terminal controller progress for {item.job_id} is unsettled")
        if item.provider_state in ("SUCCEEDED", "FAILED"):
            expected_resource = f"projects/{project}/locations/{location}/jobs/{current_job_id}"
            if item.provider_resource_name != expected_resource or item.failure_type not in (
                None,
                "BatchJobCollision",
            ):
                raise ReportError(f"controller progress for {item.job_id} has invalid outcome")
        elif item.provider_state == "NOT_CREATED":
            if item.provider_resource_name is not None or item.failure_type not in (
                "PermanentGoogleError",
                "BatchJobCollision",
            ):
                raise ReportError(
                    f"controller progress for {item.job_id} has invalid no-effect proof"
                )
        else:
            raise ReportError(f"controller progress for {item.job_id} has no settled outcome")
        if item.accepted_failure and item.phase != "terminal":
            raise ReportError(
                f"controller progress for {item.job_id} accepts a nonterminal failure"
            )
        if case.job_id != item.job_id:
            raise ReportError("controller case identity changed")


def observe_once(
    *,
    bucket: Any,
    campaign: str,
    ledger_path: Path,
    manifest_cache: ManifestSnapshot | None = None,
    evidence_cache: dict[str, EvidenceSnapshot] | None = None,
) -> dict[str, Any]:
    manifest = manifest_cache or _load_manifest(bucket, campaign)
    with ledger.open_ledger(ledger_path) as connection:
        owner = ledger.campaign_record(connection, campaign)
        controller_inputs = ledger.controller_inputs(connection, campaign)
    if (
        owner["results_bucket"] != bucket.name
        or owner["manifest_sha256"] != manifest.sha256
        or manifest.bucket != bucket.name
        or manifest.campaign != campaign
    ):
        raise ReportError("local controller owner does not match the frozen campaign")
    progress = controller.reconcile_once(ledger_path=ledger_path, campaign=campaign)
    _validate_progress(
        progress,
        manifest.cases,
        project=str(owner["project"]),
        location=str(owner["location"]),
    )
    by_id = {case.job_id: case for case in manifest.cases}
    progress_by_id = {item.job_id: item for item in progress}
    ordered_progress = tuple(progress_by_id[case.job_id] for case in manifest.cases)
    rows: list[dict[str, Any]] = []
    for item in ordered_progress:
        case = by_id[item.job_id]
        current_job_id = item.current_job_id or item.job_id
        provider_resource_name = (
            f"projects/{owner['project']}/locations/{owner['location']}/jobs/{current_job_id}"
        )
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
                "provider_resource_name": provider_resource_name,
                "controller_complete": item.phase == "terminal",
                "provider_settled": item.provider_settled,
                "controller": _controller_view(item),
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
    controller_counts = dict.fromkeys(CONTROLLER_AGGREGATE_PHASES, 0)
    evidence_counts = dict.fromkeys(EVIDENCE_STATES, 0)
    subject_counts = dict.fromkeys(SUBJECT_STATES, 0)
    provider_counts = dict.fromkeys(PROVIDER_STATES, 0)
    for row in rows:
        controller_counts[row["controller"]["phase"]] += 1
        evidence_counts[row["evidence"]["state"]] += 1
        subject = row["subject"]
        subject_counts[subject["status"] if subject else "unavailable"] += 1
        terminal = row["controller"]["terminal"]
        state = terminal["provider_state"] if terminal else "unavailable"
        provider_counts[state if state in PROVIDER_STATES[:-1] else "unavailable"] += 1
    controller_complete = all(item.phase == "terminal" for item in ordered_progress)
    provider_settled = all(item.provider_settled for item in ordered_progress)
    report_final = controller_complete and provider_settled
    operational_success = report_final and all(row["operational_success"] for row in rows)
    controller_input = {
        "schema_version": 1,
        "campaign": campaign,
        "campaign_manifest_sha256": manifest.sha256,
        "project": owner["project"],
        "location": owner["location"],
        "results_bucket": owner["results_bucket"],
        "cases": [
            {
                "base_job_id": item["base_job_id"],
                "job": json.loads(str(item["job_json"])),
                "controller_timeout_s": item["controller_timeout_s"],
            }
            for item in controller_inputs
        ],
    }
    campaign_digest = hashlib.sha256(_canonical_json(controller_input)).hexdigest()
    return {
        "schema_version": 3,
        "campaign": campaign,
        "campaign_manifest_sha256": manifest.sha256,
        "campaign_digest": campaign_digest,
        "controller_input_sha256": campaign_digest,
        "engine": {
            "name": "sqlite-gcp-batch",
            "execution_id": campaign,
            "run_id": None,
            "status": "completed" if controller_complete else "running",
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


def _run_report(args: argparse.Namespace) -> dict[str, Any]:
    try:
        poll_interval_s = float(args.poll_interval_s)
    except (TypeError, ValueError):
        raise ReportError("--poll-interval-s must be a number") from None
    if not math.isfinite(poll_interval_s) or poll_interval_s <= 0:
        raise ReportError("--poll-interval-s must be finite and positive")
    bucket = storage.Client().bucket(args.results_bucket)
    manifest_cache = _load_manifest(bucket, args.campaign)
    evidence_cache: dict[str, EvidenceSnapshot] = {}
    previous_progress: tuple[Any, ...] | None = None
    while True:
        report = observe_once(
            bucket=bucket,
            campaign=args.campaign,
            ledger_path=Path(args.ledger),
            manifest_cache=manifest_cache,
            evidence_cache=evidence_cache,
        )
        progress_key = (
            report["controller_complete"],
            report["provider_settled"],
            report["report_final"],
            report["operational_success"],
            tuple(
                (row["job_id"], row["controller"]["phase"], row["evidence"]["state"])
                for row in report["cases"]
            ),
        )
        if args.wait and progress_key != previous_progress:
            print(
                "report-campaign: finality["
                f"controller_complete={str(report['controller_complete']).lower()},"
                f"provider_settled={str(report['provider_settled']).lower()},"
                f"report_final={str(report['report_final']).lower()},"
                f"operational_success={str(report['operational_success']).lower()}]",
                file=sys.stderr,
            )
            previous_progress = progress_key
        if not args.wait or report["report_final"]:
            break
        time.sleep(poll_interval_s)
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
        report = _run_report(args)
        print(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False))
        return 0
    except (
        CampaignError,
        controller.ControllerError,
        DefaultCredentialsError,
        GoogleAPIError,
        OSError,
        provider.ProviderError,
        ReportError,
        ledger.LedgerError,
    ) as exc:
        print(f"report-campaign: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    return report_campaign_main(argv)
