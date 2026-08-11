"""Compile resolved plans and frozen images into one canonical campaign."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
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
    CampaignCompilation,
    CampaignError,
    compile_campaign,
)

IMAGE_SET_FIELDS = {
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
    "tool_image_digest",
    "tool_image_uri",
    "selection_sha256",
}


class SubmissionError(RuntimeError):
    """Campaign inputs cannot be validated or frozen safely."""


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
        help="sealed current CI image-publication ledger",
    )


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


def _read_image_set(path: Path) -> dict[str, dict[str, Any]]:
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
    if schema_version != 3 or isinstance(schema_version, bool):
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
    return validated


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


def _read_publication_images(
    path: Path, *, root: Path | None = None
) -> dict[str, dict[str, Any]]:
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
    validate_registered_images(registrations, root=base)
    return registrations


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


def main(argv: Sequence[str] | None = None) -> int:
    """Compile one immutable campaign for a workflow engine."""
    return compile_campaign_main(argv)
