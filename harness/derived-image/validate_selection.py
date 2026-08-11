"""Fail a derived-image build when staged registration and adapter disagree."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SHARED_BASE_SOURCE_MARKER = Path("/opt/s3-listing-study/shared-base-source.sha256")
IMAGE_PROVENANCE = Path("/opt/s3-listing-study/image-provenance.json")


def validate_shared_base_marker(expected: str, marker_path: Path) -> None:
    """Require the final base to carry the selection's exact source identity."""
    try:
        actual = marker_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read shared-base source marker: {exc}") from exc
    if actual != expected.encode("ascii") + b"\n":
        raise ValueError("shared-base source marker does not match registered selection")


def main() -> None:
    sys.path.insert(0, "/opt/s3-listing-study/attempt.pyz")

    # Import the entry point before it is sealed into an image. A payload that
    # does not import — a missing dependency, a syntax error — then fails the
    # build instead of surfacing inside a shipped benchmark image.
    import s3_listing_study.worker.cli  # noqa: F401
    from s3_listing_study.common.build_selection import (
        BuildSelectionError,
        load_staged_selection,
    )

    try:
        selection = load_staged_selection(
            Path("/opt/s3-listing-study/selection.json"),
            Path("/opt/s3-listing-study/tool"),
        )
        validate_shared_base_marker(
            selection.shared_base_source_sha256,
            SHARED_BASE_SOURCE_MARKER,
        )
        provenance = json.loads(IMAGE_PROVENANCE.read_text(encoding="utf-8"))
        if provenance.get("schema_version") != 2:
            raise ValueError("image provenance does not use schema 2")
        if provenance.get("selection_sha256") != selection.selection_sha256:
            raise ValueError("image provenance selection digest does not match selection")
        worker_source_sha256 = provenance.get("worker_source_sha256")
        if (
            not isinstance(worker_source_sha256, str)
            or len(worker_source_sha256) != 64
            or any(character not in "0123456789abcdef" for character in worker_source_sha256)
        ):
            raise ValueError("image provenance has invalid worker source identity")
        tool_image = provenance.get("tool_image")
        if not isinstance(tool_image, dict) or set(tool_image) != {"digest", "uri"}:
            raise ValueError("image provenance has invalid tool-image fields")
        digest = tool_image["digest"]
        uri = tool_image["uri"]
        if (
            not isinstance(digest, str)
            or not isinstance(uri, str)
            or not uri.endswith("@" + digest)
        ):
            raise ValueError("image provenance tool-image URI does not match digest")
    except (BuildSelectionError, ValueError) as exc:
        raise SystemExit(f"derived-image selection refused: {exc}") from None


if __name__ == "__main__":
    main()
