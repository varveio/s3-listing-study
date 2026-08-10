"""Build, push, and atomically record one registered derived image."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from s3_listing_study.common.argparse_utils import UniqueStoreAction
from s3_listing_study.common.build_selection import (
    BuildSelection,
    BuildSelectionError,
    derived_image_build_command,
    derived_image_tag,
    load_registered_selection,
)
from s3_listing_study.manager.bench.cli import repo_root
from s3_listing_study.manager.campaign import DIGEST_RE
from s3_listing_study.manager.campaign.cli import (
    IMAGE_SET_SCHEMA_VERSION,
    SubmissionError,
    _read_image_set,
    validate_registered_images,
)

REVISION_RE = re.compile(r"\A[0-9a-f]{40}\Z")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="s3-listing-study publish-derived-image", allow_abbrev=False
    )
    parser.add_argument("--tool", action=UniqueStoreAction, required=True)
    parser.add_argument(
        "--repository",
        action=UniqueStoreAction,
        required=True,
        help="Artifact Registry Docker repository without an image name",
    )
    parser.add_argument("--image-set", action=UniqueStoreAction, required=True)
    parser.add_argument(
        "--shared-base-image",
        action=UniqueStoreAction,
        required=True,
        help="immutable registry URI of the once-built shared base",
    )
    parser.add_argument(
        "--tool-image",
        action=UniqueStoreAction,
        required=True,
        help="immutable URI of the separately built tool parent",
    )
    return parser


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(argv, capture_output=True, check=False)
    except OSError as exc:
        raise SubmissionError(f"cannot run {argv[0]}: {exc}") from None


def _failure(action: str, result: subprocess.CompletedProcess[bytes]) -> SubmissionError:
    detail = result.stderr.decode("utf-8", errors="replace").strip()
    return SubmissionError(f"{action} failed: {detail or f'exit {result.returncode}'}")


def _revision(root: Path, tool: str) -> str:
    payload_paths = (
        ".dockerignore",
        "src/s3_listing_study/__init__.py",
        "src/s3_listing_study/common",
        "src/s3_listing_study/worker",
        "harness/derived-image",
        "harness/shared-image",
        f"tools/{tool}/adapter",
        f"tools/{tool}/build",
    )
    status = _run(
        (
            "git",
            "-C",
            str(root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            *payload_paths,
        )
    )
    if status.returncode != 0:
        raise _failure("checking derived-image payload cleanliness", status)
    if status.stdout:
        raise SubmissionError(
            "derived-image payload has tracked or untracked changes; commit the exact bytes first"
        )
    revision = _run(("git", "-C", str(root), "rev-parse", "HEAD"))
    if revision.returncode != 0:
        raise _failure("reading harness revision", revision)
    value = revision.stdout.decode("ascii", errors="replace").strip()
    if REVISION_RE.fullmatch(value) is None:
        raise SubmissionError("git rev-parse HEAD did not return a full lowercase commit ID")
    return value


def _target_tag(repository: str, selection: BuildSelection) -> str:
    if (
        not repository
        or repository.startswith(("http://", "https://"))
        or "@" in repository
        or ":" in repository
        or any(character.isspace() for character in repository)
        or repository.rstrip("/").count("/") != 2
        or not repository.split("/", 1)[0].endswith("-docker.pkg.dev")
    ):
        raise SubmissionError("repository must be LOCATION-docker.pkg.dev/PROJECT/REPOSITORY")
    local_tag = derived_image_tag(selection)
    _namespace, separator, image_and_tag = local_tag.partition("/")
    if not separator:
        raise SubmissionError(f"derived image tag has no image component: {local_tag}")
    return f"{repository.rstrip('/')}/{image_and_tag}"


def _pushed_digest(target_tag: str, output: bytes) -> tuple[str, str]:
    try:
        references = json.loads(output)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SubmissionError("Docker did not report pushed repository digests as JSON") from None
    if not isinstance(references, list) or not all(isinstance(item, str) for item in references):
        raise SubmissionError("Docker reported malformed pushed repository digests")
    target_repository = target_tag.rsplit(":", 1)[0]
    matches: list[tuple[str, str]] = []
    for reference in references:
        repository, separator, digest = reference.rpartition("@")
        if separator and repository == target_repository and DIGEST_RE.fullmatch(digest):
            matches.append((digest, reference))
    if len(matches) != 1:
        raise SubmissionError(
            f"Docker reported {len(matches)} immutable digests for {target_repository}; expected 1"
        )
    return matches[0]


def _atomic_image_set(path: Path, images: Mapping[str, Mapping[str, Any]]) -> None:
    document = {"schema_version": IMAGE_SET_SCHEMA_VERSION, "images": images}
    content = (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _validate_existing_shared_base(
    images: Mapping[str, Mapping[str, Any]],
    *,
    uri: str,
    digest: str,
    source_sha256: str,
) -> None:
    """Refuse to extend or replace an image set from another shared base."""
    expected = {
        "shared_base_uri": uri,
        "shared_base_digest": digest,
        "shared_base_source_sha256": source_sha256,
    }
    for tool, image in images.items():
        mismatched = sorted(field for field, value in expected.items() if image.get(field) != value)
        if mismatched:
            raise SubmissionError(
                f"{tool}: existing image set disagrees with requested {', '.join(mismatched)}"
            )


def publish_derived_image_main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = repo_root().resolve(strict=True)
        selection = load_registered_selection(root, args.tool)
        target_tag = _target_tag(args.repository, selection)
        image_set_path = Path(args.image_set)
        existing = _read_image_set(image_set_path) if image_set_path.exists() else {}
        if getattr(existing, "schema_version", IMAGE_SET_SCHEMA_VERSION) == 2:
            raise SubmissionError("cannot extend a historical schema-version-2 image set")
        shared_repository, separator, shared_base_digest = args.shared_base_image.rpartition("@")
        if (
            not separator
            or not shared_repository
            or DIGEST_RE.fullmatch(shared_base_digest) is None
        ):
            raise SubmissionError("shared base image must be an immutable URI with sha256 digest")
        _validate_existing_shared_base(
            existing,
            uri=args.shared_base_image,
            digest=shared_base_digest,
            source_sha256=selection.shared_base_source_sha256,
        )
        validate_registered_images(existing, root=root, skip={selection.tool})
        harness_revision = _revision(root, selection.tool)
        tool_repository, separator, tool_image_digest = args.tool_image.rpartition("@")
        if not separator or not tool_repository or DIGEST_RE.fullmatch(tool_image_digest) is None:
            raise SubmissionError("tool image must be an immutable URI with sha256 digest")
        build = derived_image_build_command(root, selection, target_tag, args.tool_image)
        built = _run(build)
        if built.returncode != 0:
            raise _failure("derived-image build", built)
        if _revision(root, selection.tool) != harness_revision:
            raise SubmissionError("Git HEAD changed while the derived image was building")
        pushed = _run(("docker", "push", target_tag))
        if pushed.returncode != 0:
            raise _failure("derived-image push", pushed)
        inspected = _run(
            ("docker", "image", "inspect", "--format={{json .RepoDigests}}", target_tag)
        )
        if inspected.returncode != 0:
            raise _failure("pushed digest inspection", inspected)
        derived_image, image_uri = _pushed_digest(target_tag, inspected.stdout)
        registration = {
            "derived_image": derived_image,
            "image_uri": image_uri,
            "shared_base_digest": shared_base_digest,
            "shared_base_uri": args.shared_base_image,
            "shared_base_source_sha256": selection.shared_base_source_sha256,
            "tool_build_sha256": selection.tool_build_sha256,
            "tool_image_digest": tool_image_digest,
            "tool_image_uri": args.tool_image,
            "selection_sha256": selection.selection_sha256,
            "tool_artifact": {
                "kind": selection.tool_artifact_kind,
                "locator": selection.tool_artifact_locator,
                "sha256": selection.tool_artifact_sha256,
            },
            "tool_version": selection.tool_version,
            "adapter_bundle_sha256": selection.adapter_bundle_sha256,
            "harness_revision": harness_revision,
        }
        _atomic_image_set(image_set_path, {**existing, selection.tool: registration})
        print(json.dumps({"tool": selection.tool, "image_uri": image_uri}, sort_keys=True))
        return 0
    except (
        BuildSelectionError,
        OSError,
        SubmissionError,
    ) as exc:
        print(f"publish-derived-image: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    return publish_derived_image_main(argv)
