"""Freeze and submit one resolved campaign to GCP Batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from s3_listing_study.common.argparse_utils import UniqueStoreAction
from s3_listing_study.common.build_selection import (
    BuildSelectionError,
    load_registered_selection,
)
from s3_listing_study.manager.bench import plan as bench
from s3_listing_study.manager.bench.cli import registered_tools, repo_root
from s3_listing_study.manager.campaign import (
    DIGEST_RE,
    Attempt,
    CampaignCompilation,
    CampaignError,
    campaign_prefix,
    compile_campaign,
    ledger,
)
from s3_listing_study.manager.campaign.batch import BatchConfig, render_job

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


class ImageSet(dict[str, dict[str, Any]]):
    """Validated registrations retaining their on-disk schema generation."""

    def __init__(self, *args: Any, schema_version: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.schema_version = schema_version


class SubmissionError(RuntimeError):
    """Campaign inputs or a cloud command made submission unsafe."""


def _add_campaign_source(parser: argparse.ArgumentParser) -> None:
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


def _add_image_source(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image-set", action=UniqueStoreAction)
    source.add_argument(
        "--publication-manifest",
        action=UniqueStoreAction,
        help="sealed schema-2 current-image publication ledger",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="s3-listing-study submit-campaign", allow_abbrev=False)
    _add_campaign_source(parser)
    parser.add_argument(
        "--campaign", "--campaign-id", dest="campaign", action=UniqueStoreAction, required=True
    )
    _add_image_source(parser)
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
        type=int,
        default=1800,
    )
    parser.add_argument("--ledger", "--ledger-path", action=UniqueStoreAction, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def build_compile_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="s3-listing-study compile-campaign", allow_abbrev=False)
    _add_campaign_source(parser)
    parser.add_argument(
        "--campaign", "--campaign-id", dest="campaign", action=UniqueStoreAction, required=True
    )
    _add_image_source(parser)
    parser.add_argument("--results-bucket", action=UniqueStoreAction, required=True)
    parser.add_argument(
        "--provisioning",
        action=UniqueStoreAction,
        choices=("STANDARD", "SPOT"),
        default="SPOT",
    )
    parser.add_argument("--zone", action=UniqueStoreAction)
    parser.add_argument("--output", action=UniqueStoreAction, required=True)
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


def _publication_reference(value: Any, *, label: str) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise SubmissionError(f"publication {label} is not an object")
    digest = value.get("digest")
    uri = value.get("uri")
    if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
        raise SubmissionError(f"publication {label} digest is invalid")
    if not isinstance(uri, str) or not uri.endswith(f"@{digest}"):
        raise SubmissionError(f"publication {label} URI is not pinned to its digest")
    return digest, uri


def _read_publication_images(path: Path, *, root: Path | None = None) -> ImageSet:
    """Convert one sealed current-image ledger to validated campaign registrations."""
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SubmissionError(f"publication manifest is not readable JSON: {path}: {exc}") from None
    if not isinstance(document, dict):
        raise SubmissionError("publication manifest is not a JSON object")
    if document.get("kind") != "github-container-image-publication":
        raise SubmissionError("publication manifest has the wrong kind")
    if document.get("format_version") != 2:
        raise SubmissionError("publication manifest format_version must be 2")
    revision = document.get("checkout_revision")
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise SubmissionError("publication checkout_revision must be a full SHA-1 commit ID")
    published_images = document.get("images")
    if not isinstance(published_images, dict) or not published_images:
        raise SubmissionError("publication images must be a non-empty object")
    base = repo_root() if root is None else root
    registrations: dict[str, dict[str, Any]] = {}
    for tool, published in published_images.items():
        if not isinstance(tool, str) or not isinstance(published, dict):
            raise SubmissionError("publication images must be tool-named objects")
        if published.get("tool_name") != tool:
            raise SubmissionError(f"publication {tool}: tool_name does not match its key")
        if published.get("worker_revision") != revision:
            raise SubmissionError(f"publication {tool}: worker_revision does not match checkout")
        selection = load_registered_selection(base, tool)
        expected = {
            "tool_version": selection.tool_version,
            "selection_sha256": selection.selection_sha256,
        }
        mismatched = sorted(key for key, value in expected.items() if published.get(key) != value)
        shared = published.get("shared")
        tool_image = published.get("tool")
        if not isinstance(shared, dict) or not isinstance(tool_image, dict):
            raise SubmissionError(f"publication {tool}: shared and tool must be objects")
        if shared.get("source_sha256") != selection.shared_base_source_sha256:
            mismatched.append("shared.source_sha256")
        if tool_image.get("build_sha256") != selection.tool_build_sha256:
            mismatched.append("tool.build_sha256")
        if mismatched:
            raise SubmissionError(
                f"publication {tool} disagrees with registered {', '.join(sorted(mismatched))}"
            )
        derived_image, image_uri = _publication_reference(
            published.get("execution"), label=f"{tool} execution"
        )
        shared_base_digest, shared_base_uri = _publication_reference(shared, label=f"{tool} shared")
        tool_image_digest, tool_image_uri = _publication_reference(tool_image, label=f"{tool} tool")
        registrations[tool] = {
            "derived_image": derived_image,
            "image_uri": image_uri,
            "shared_base_digest": shared_base_digest,
            "shared_base_uri": shared_base_uri,
            "shared_base_source_sha256": selection.shared_base_source_sha256,
            "tool_build_sha256": selection.tool_build_sha256,
            "tool_image_digest": tool_image_digest,
            "tool_image_uri": tool_image_uri,
            "selection_sha256": selection.selection_sha256,
            "tool_artifact": {
                "kind": selection.tool_artifact_kind,
                "locator": selection.tool_artifact_locator,
                "sha256": selection.tool_artifact_sha256,
            },
            "tool_version": selection.tool_version,
            "adapter_bundle_sha256": selection.adapter_bundle_sha256,
            "harness_revision": revision,
        }
    images = ImageSet(registrations, schema_version=IMAGE_SET_SCHEMA_VERSION)
    validate_registered_images(images, root=base)
    return images


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


def _freeze_local(path: Path, content: bytes) -> bool:
    """Create ``path`` once, accepting an existing byte-identical freeze."""
    try:
        with path.open("xb") as destination:
            destination.write(content)
    except FileExistsError:
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise SubmissionError(f"could not read existing {path}: {exc}") from None
        if existing != content:
            raise SubmissionError(f"{path} already exists with different content") from None
        return False
    except OSError as exc:
        raise SubmissionError(f"could not create {path}: {exc}") from None
    path.chmod(0o444)
    return True


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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _ledger_attempt(connection: sqlite3.Connection, attempt: Attempt) -> tuple[str, bool] | None:
    row = connection.execute(
        "SELECT campaign, state, case_json FROM attempts WHERE job_id = ?", (attempt.job_id,)
    ).fetchone()
    if row is None:
        return None
    try:
        recorded = json.loads(row["case_json"])
    except (TypeError, json.JSONDecodeError):
        return str(row["state"]), False
    exact = row["campaign"] == attempt.campaign and recorded == attempt.as_dict()
    return str(row["state"]), exact


def _batch_already_exists(result: subprocess.CompletedProcess[bytes]) -> bool:
    message = (result.stdout + b"\n" + result.stderr).decode("utf-8", errors="replace").lower()
    markers = ("already_exists", "already exists", "code=409", "http 409")
    return result.returncode != 0 and any(marker in message for marker in markers)


def _submit_jobs(
    attempts: Sequence[Attempt],
    jobs: Sequence[Mapping[str, Any]],
    *,
    project: str,
    location: str,
    ledger_path: Path,
) -> tuple[list[dict[str, Any]], bool]:
    statuses: list[dict[str, Any]] = []
    failed = False
    with ledger.open_ledger(ledger_path) as connection:
        for attempt, job in zip(attempts, jobs, strict=True):
            existing = _ledger_attempt(connection, attempt)
            preexisting_intent = existing is not None
            if existing is None:
                ledger.record_intent(
                    connection,
                    attempt=attempt.as_dict(),
                    campaign=attempt.campaign,
                    now=_utc_now(),
                )
            else:
                state, exact = existing
                if not exact:
                    failed = True
                    statuses.append({"job_id": attempt.job_id, "state": "ledger-mismatch"})
                    continue
                if state in ("submitted", "running", "succeeded"):
                    statuses.append({"job_id": attempt.job_id, "state": state})
                    continue
                if state in ("failed", "abandoned"):
                    failed = True
                    statuses.append({"job_id": attempt.job_id, "state": state})
                    continue
                if state != "submitting":
                    failed = True
                    statuses.append(
                        {"job_id": attempt.job_id, "state": f"unknown-ledger-state:{state}"}
                    )
                    continue
            try:
                result = _run(
                    (
                        "gcloud",
                        "batch",
                        "jobs",
                        "submit",
                        attempt.job_id,
                        "--project",
                        project,
                        "--location",
                        location,
                        "--config=-",
                        "--quiet",
                    ),
                    payload=_canonical_json(job),
                )
            except SubmissionError as exc:
                failed = True
                error_detail = {"error": str(exc)}
                ledger.record_state(
                    connection,
                    job_id=attempt.job_id,
                    state="failed",
                    now=_utc_now(),
                    detail=error_detail,
                )
                statuses.append({"job_id": attempt.job_id, "state": "failed"})
                continue
            recovered = preexisting_intent and _batch_already_exists(result)
            if recovered:
                result = subprocess.CompletedProcess(result.args, 0, result.stdout, result.stderr)
            state = "submitted" if result.returncode == 0 else "failed"
            detail: dict[str, Any] = {"returncode": result.returncode}
            if recovered:
                detail["recovered_from_already_exists"] = True
            if result.returncode != 0:
                failed = True
                message = result.stderr.decode("utf-8", errors="replace").strip()
                if message:
                    detail["stderr"] = message
            ledger.record_state(
                connection,
                job_id=attempt.job_id,
                state=state,
                now=_utc_now(),
                detail=detail,
            )
            statuses.append({"job_id": attempt.job_id, "state": state})
    return statuses, failed


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


def _compile_from_args(args: argparse.Namespace) -> CampaignCompilation:
    loaded_plans = _load_plans(args)
    if args.image_set:
        images = _read_image_set(Path(args.image_set))
        validate_registered_images(images)
    else:
        images = _read_publication_images(Path(args.publication_manifest))
    return compile_campaign(
        campaign=args.campaign,
        plans=loaded_plans,
        images=images,
        results_bucket=args.results_bucket,
        provisioning=args.provisioning,
        zone=args.zone,
    )


def compile_campaign_main(argv: Sequence[str] | None = None) -> int:
    args = build_compile_parser().parse_args(argv)
    try:
        compiled = _compile_from_args(args)
        output = Path(args.output)
        created = _freeze_local(output, compiled.content)
        print(
            json.dumps(
                {
                    "campaign": args.campaign,
                    "created": created,
                    "path": str(output),
                    "sha256": compiled.sha256,
                },
                sort_keys=True,
            )
        )
        return 0
    except (BuildSelectionError, CampaignError, SubmissionError, bench.PlanError, OSError) as exc:
        print(f"compile-campaign: {exc}", file=sys.stderr)
        return 1


def submit_campaign_main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        compiled = _compile_from_args(args)
        loaded_plans = compiled.plans
        generated = compiled.attempts
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
        jobs = [render_job(attempt, config) for attempt in generated]
        campaign_document = compiled.document
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
        _freeze(f"{base}/campaign.json", _canonical_json(campaign_document))
        statuses, failed = _submit_jobs(
            generated,
            jobs,
            project=args.project,
            location=args.location,
            ledger_path=Path(args.ledger),
        )
        print(json.dumps({"campaign": args.campaign, "submissions": statuses}, sort_keys=True))
        return 1 if failed else 0
    except (
        BuildSelectionError,
        CampaignError,
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
