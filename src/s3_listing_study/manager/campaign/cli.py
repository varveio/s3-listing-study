"""Freeze and submit one resolved campaign to GCP Batch."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from google.auth.exceptions import DefaultCredentialsError

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
    ledger,
    manifest,
)
from s3_listing_study.manager.campaign.batch import BatchConfig, render_job
from s3_listing_study.manager.campaign.controller import ControllerError, start_campaign

IMAGE_SET_FIELDS = {
    "adapter_bundle_sha256",
    "derived_image",
    "harness_revision",
    "image_uri",
    "selection_sha256",
    "shared_base_digest",
    "shared_base_source_sha256",
    "shared_base_uri",
    "tool_artifact",
    "tool_build_sha256",
    "tool_image_digest",
    "tool_image_uri",
    "tool_version",
}
IMAGE_SET_SCHEMA_VERSION = 3


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
        action=UniqueStoreAction,
        default=1800,
    )
    parser.add_argument("--ledger", "--ledger-path", action=UniqueStoreAction, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--poll-interval-s", action=UniqueStoreAction, default=10.0)
    parser.add_argument("--publish-report", action="store_true")
    return parser


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionError(f"duplicate JSON key in image set: {key}")
        result[key] = value
    return result


def _digest(tool: str, value: Any, field: str) -> str:
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        raise SubmissionError(f"{tool}: {field} is not a sha256 digest")
    return value


def _hex(tool: str, value: Any, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise SubmissionError(f"{tool}: {field} is not 64 lowercase hex digits")
    return value


def _token(tool: str, value: Any, field: str) -> None:
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise SubmissionError(f"{tool}: {field} must be a non-empty token")


def _pinned_uri(tool: str, value: Mapping[str, Any], digest_field: str, uri_field: str) -> None:
    digest = _digest(tool, value[digest_field], digest_field)
    if not isinstance(value[uri_field], str) or not value[uri_field].endswith(f"@{digest}"):
        label = "derived_image" if uri_field == "image_uri" else ""
        raise SubmissionError(f"{tool}: {uri_field} digest does not match {label}".rstrip())


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
    if schema_version != IMAGE_SET_SCHEMA_VERSION or isinstance(schema_version, bool):
        raise SubmissionError("image set schema_version must be 3")
    images = document.get("images")
    if not isinstance(images, dict) or not images:
        raise SubmissionError("image set images must be a non-empty object")

    validated: dict[str, dict[str, Any]] = {}
    for tool, value in images.items():
        if not isinstance(tool, str) or not tool or not isinstance(value, dict):
            raise SubmissionError("each image must be a tool-named object")
        missing = sorted(IMAGE_SET_FIELDS - set(value))
        unknown = sorted(set(value) - IMAGE_SET_FIELDS)
        if missing or unknown:
            detail = []
            if missing:
                detail.append(f"missing {', '.join(missing)}")
            if unknown:
                detail.append(f"unknown {', '.join(unknown)}")
            raise SubmissionError(f"{tool}: invalid image fields ({'; '.join(detail)})")
        _pinned_uri(tool, value, "derived_image", "image_uri")
        _pinned_uri(tool, value, "shared_base_digest", "shared_base_uri")
        for field in ("shared_base_source_sha256", "tool_build_sha256"):
            _hex(tool, value[field], field)
        _pinned_uri(tool, value, "tool_image_digest", "tool_image_uri")
        _hex(tool, value["selection_sha256"], "selection_sha256")
        artifact = value["tool_artifact"]
        if not isinstance(artifact, dict) or set(artifact) != {"kind", "locator", "sha256"}:
            raise SubmissionError(f"{tool}: tool_artifact has invalid fields")
        _hex(tool, artifact["sha256"], "tool_artifact sha256")
        _hex(tool, value["adapter_bundle_sha256"], "adapter_bundle_sha256")
        _token(tool, value["tool_version"], "tool_version")
        if (
            not isinstance(value["harness_revision"], str)
            or re.fullmatch(r"[0-9a-f]{40}", value["harness_revision"]) is None
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


def _attempt_label(fingerprint: str) -> str:
    return base64.b32encode(bytes.fromhex(fingerprint)).decode().rstrip("=").lower()


def submit_campaign_main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        try:
            post_attempt_allowance_s = int(args.post_attempt_allowance_s)
        except (TypeError, ValueError):
            raise SubmissionError("--post-attempt-allowance-s must be an integer") from None
        try:
            poll_interval_s = float(args.poll_interval_s)
        except (TypeError, ValueError):
            raise SubmissionError("--poll-interval-s must be a number") from None
        if not math.isfinite(poll_interval_s) or poll_interval_s <= 0:
            raise SubmissionError("--poll-interval-s must be finite and positive")
        if args.publish_report and not args.wait and not args.dry_run:
            raise SubmissionError("--publish-report requires --wait")
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
            mismatch = "; ".join(detail)
            raise SubmissionError(f"image set does not exactly cover the plans ({mismatch})")

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
        config = BatchConfig(
            results_bucket=args.results_bucket,
            anonymous_worker_service_account=args.anonymous_worker_sa,
            authenticated_worker_service_account=args.authenticated_worker_sa,
            aws_credential_secret=args.secret_resource,
            network=args.network,
            subnetwork=args.subnetwork,
            provisioning=args.provisioning,
            zone=args.zone,
            post_attempt_allowance_s=post_attempt_allowance_s,
        )
        jobs = []
        controller_timeouts = []
        for attempt in generated:
            job = render_job(attempt, config)
            job["labels"] = {
                **job.get("labels", {}),
                "s3-study-attempt": _attempt_label(attempt.fingerprint),
            }
            jobs.append(job)
            controller_timeouts.append(
                attempt.case.timeout_s
                + config.term_grace_s
                + config.post_attempt_allowance_s
                + 3600
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
        dry_run = {
            "campaign.json": campaign_document,
            "jobs": [
                {"job_id": attempt.job_id, "job": job}
                for attempt, job in zip(generated, jobs, strict=True)
            ],
        }
        if args.dry_run:
            print(json.dumps(dry_run, sort_keys=True, indent=2, ensure_ascii=False))
            return 0
        plan_contents: list[bytes] = []
        for loaded in loaded_plans:
            plan_bytes = loaded.path.read_bytes()
            if hashlib.sha256(plan_bytes).hexdigest() != loaded.digest:
                raise SubmissionError(f"plan changed after it was resolved: {loaded.path}")
            plan_contents.append(plan_bytes)
        base = f"gs://{args.results_bucket}/{campaign_prefix(args.campaign)}"
        for plan_record, plan_bytes in zip(campaign_document["plans"], plan_contents, strict=True):
            plan_uri = f"gs://{args.results_bucket}/{plan_record['path']}"
            _freeze(plan_uri, plan_bytes)
        manifest_content = _canonical_json(campaign_document)
        _freeze(f"{base}/campaign.json", manifest_content)
        statuses = start_campaign(
            ledger_path=Path(args.ledger),
            campaign=args.campaign,
            project=args.project,
            location=args.location,
            results_bucket=args.results_bucket,
            manifest_sha256=hashlib.sha256(manifest_content).hexdigest(),
            attempts=[attempt.as_dict() for attempt in generated],
            jobs=jobs,
            controller_timeouts=controller_timeouts,
        )
        if args.wait:
            from s3_listing_study.manager.campaign.report import report_campaign_main

            forwarded = [
                "--campaign",
                args.campaign,
                "--results-bucket",
                args.results_bucket,
                "--ledger",
                args.ledger,
                "--poll-interval-s",
                str(poll_interval_s),
            ]
            forwarded.append("--wait")
            if args.publish_report:
                forwarded.append("--publish")
            return report_campaign_main(forwarded)
        print(json.dumps({"campaign": args.campaign, "submissions": statuses}, sort_keys=True))
        return 0
    except (
        BuildSelectionError,
        CampaignError,
        ControllerError,
        DefaultCredentialsError,
        SubmissionError,
        bench.PlanError,
        ledger.LedgerError,
        OSError,
    ) as exc:
        print(f"submit-campaign: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Compatibility entry point for direct campaign CLI use."""

    return submit_campaign_main(argv)
