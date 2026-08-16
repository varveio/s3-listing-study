from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "simple"))
import build_image  # type: ignore[import-not-found]

SHARED_BASE_IMAGE = "registry/shared@sha256:" + "9" * 64


def selection(*, adapter_digest: str = "f" * 64) -> build_image.BuildSelection:
    return cast(
        build_image.BuildSelection,
        SimpleNamespace(
            tool="aws-cli",
            tool_version="1",
            tool_build_sha256="a" * 64,
            shared_base_source_sha256="c" * 64,
            subject_workdir="/aws",
            executable=("/usr/local/bin/aws",),
            adapter_bundle_sha256=adapter_digest,
        ),
    )


def parent_document(
    *, workdir: str = "/aws", digest: str = "a" * 64, base_digest: str = "c" * 64
) -> list[dict[str, object]]:
    return [
        {
            "Architecture": "amd64",
            "Os": "linux",
            "Config": {
                "Labels": {
                    build_image.TOOL_BUILD_LABEL: digest,
                    build_image.SHARED_BASE_LABEL: base_digest,
                },
                "WorkingDir": workdir,
            },
        }
    ]


def built_document(*, toolbox_digest: str = "d" * 64) -> list[dict[str, object]]:
    return [
        {
            "Architecture": "amd64",
            "Os": "linux",
            "Config": {
                "User": "10001:10001",
                "WorkingDir": "/home/s3study",
                "Entrypoint": ["/usr/bin/python3", "/opt/simple/measure.py"],
                "Labels": {
                    build_image.TOOLBOX_LABEL: toolbox_digest,
                    build_image.TOOL_BUILD_LABEL: "",
                    "io.varve.s3-listing-study.harness-revision": "e" * 40,
                },
            },
        }
    ]


def shared_base_document() -> list[dict[str, object]]:
    return [
        {
            "Architecture": "amd64",
            "Os": "linux",
            "Config": {
                "User": "10001:10001",
                "WorkingDir": "/home/s3study",
                "Labels": {build_image.SHARED_BASE_LABEL: "c" * 64},
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


def test_parent_config_rejects_wrong_shared_base() -> None:
    with pytest.raises(build_image.BuildError, match="shared-base label"):
        build_image.validate_parent_config(selection(), parent_document(base_digest="d" * 64))


def test_built_config_binds_runtime_and_aggregate_identity() -> None:
    build_image.validate_built_config(built_document(), "d" * 64, "e" * 40)
    document = built_document()
    config = document[0]["Config"]
    assert isinstance(config, dict)
    config["User"] = "root"
    with pytest.raises(build_image.BuildError, match="uid/gid 10001"):
        build_image.validate_built_config(document, "d" * 64, "e" * 40)


def test_shared_base_config_binds_platform_source_and_runtime() -> None:
    build_image.validate_shared_base_config("c" * 64, shared_base_document())
    document = shared_base_document()
    document[0]["Architecture"] = "arm64"
    with pytest.raises(build_image.BuildError, match="linux/amd64"):
        build_image.validate_shared_base_config("c" * 64, document)


def test_tool_parents_requires_exact_digest_pinned_roster(tmp_path: Path) -> None:
    parents = {tool: f"registry/{tool}@sha256:{'d' * 64}" for tool in build_image.PARENT_ARGUMENTS}
    path = tmp_path / "parents.json"
    path.write_text(
        json.dumps(
            {"schema_version": 1, "shared_base_image": SHARED_BASE_IMAGE, "parents": parents}
        )
    )
    assert build_image.load_build_inputs(path, set(parents)) == (SHARED_BASE_IMAGE, parents)
    parents.pop("swath")
    path.write_text(
        json.dumps(
            {"schema_version": 1, "shared_base_image": SHARED_BASE_IMAGE, "parents": parents}
        )
    )
    with pytest.raises(build_image.BuildError, match="roster mismatch"):
        build_image.load_build_inputs(path, set(build_image.PARENT_ARGUMENTS))


def test_adapter_change_does_not_change_toolbox_identity() -> None:
    parent = "registry/aws-cli@sha256:" + "d" * 64
    before = {"aws-cli": selection(adapter_digest="1" * 64)}
    after = {"aws-cli": selection(adapter_digest="2" * 64)}
    before_manifest, before_digest = build_image.toolbox_manifest(
        before, {"aws-cli": parent}, SHARED_BASE_IMAGE
    )
    after_manifest, after_digest = build_image.toolbox_manifest(
        after, {"aws-cli": parent}, SHARED_BASE_IMAGE
    )
    assert after_manifest == before_manifest
    assert after_digest == before_digest
    metadata = build_image.final_image_metadata(after_manifest, after, after_digest, "e" * 40)
    tools = metadata["tools"]
    assert isinstance(tools, dict)
    assert tools["aws-cli"]["adapter_bundle_sha256"] == "2" * 64


def test_built_image_uses_aggregate_label_and_clears_single_tool_label() -> None:
    digest = "7" * 64
    document = built_document(toolbox_digest=digest)
    build_image.validate_built_config(document, digest, "e" * 40)
    config = document[0]["Config"]
    assert isinstance(config, dict)
    labels = config["Labels"]
    assert isinstance(labels, dict)
    labels.pop(build_image.TOOLBOX_LABEL)
    with pytest.raises(build_image.BuildError, match="aggregate toolbox label"):
        build_image.validate_built_config(document, digest, "e" * 40)
