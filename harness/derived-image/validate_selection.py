"""Fail a derived-image build when staged registration and adapter disagree."""

from __future__ import annotations

import sys
from pathlib import Path

SHARED_BASE_SOURCE_MARKER = Path("/opt/s3-listing-study/shared-base-source.sha256")


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
    except (BuildSelectionError, ValueError) as exc:
        raise SystemExit(f"derived-image selection refused: {exc}") from None


if __name__ == "__main__":
    main()
