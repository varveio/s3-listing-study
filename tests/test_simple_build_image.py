from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "simple"))
import build_image  # type: ignore[import-not-found]


def selection() -> SimpleNamespace:
    return SimpleNamespace(
        tool_build_sha256="a" * 64,
        subject_workdir="/aws",
    )


def parent_document(*, workdir: str = "/aws", digest: str = "a" * 64) -> list[dict[str, object]]:
    return [
        {
            "Architecture": "amd64",
            "Os": "linux",
            "Config": {
                "Labels": {build_image.TOOL_BUILD_LABEL: digest},
                "WorkingDir": workdir,
            },
        }
    ]


def test_parent_config_binds_build_and_workdir() -> None:
    document = parent_document()
    build_image.validate_parent_config(selection(), document)
    with pytest.raises(build_image.BuildError, match="working directory"):
        build_image.validate_parent_config(selection(), parent_document(workdir="/"))


def test_parent_config_rejects_wrong_tool_build() -> None:
    with pytest.raises(build_image.BuildError, match="tool-build label"):
        build_image.validate_parent_config(selection(), parent_document(digest="b" * 64))
