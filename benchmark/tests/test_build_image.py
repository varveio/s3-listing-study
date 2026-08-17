from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmark import build_image
from benchmark.contract import TOOLBOX_TOOLS

ROOT = Path(__file__).parents[2]


def built_document(*, toolbox_digest: str = "d" * 64) -> list[dict[str, object]]:
    return [
        {
            "Architecture": "amd64",
            "Os": "linux",
            "Config": {
                "User": "10001:10001",
                "WorkingDir": "/home/s3study",
                "Entrypoint": ["/usr/bin/python3", "/opt/benchmark/benchmark/measure.py"],
                "Labels": {
                    build_image.TOOLBOX_LABEL: toolbox_digest,
                    build_image.TOOLBOX_RECIPE_LABEL: "c" * 64,
                    "io.varve.s3-listing-study.harness-revision": "e" * 40,
                },
            },
        }
    ]


def test_registered_recipes_form_the_exact_toolbox() -> None:
    selections = build_image.registered_selections(ROOT)
    assert set(selections) == TOOLBOX_TOOLS
    manifest, digest = build_image.toolbox_manifest(selections, ROOT)
    assert len(digest) == 64
    assert manifest["schema_version"] == 2
    assert (
        manifest["toolbox_recipe_sha256"]
        == hashlib.sha256((ROOT / "benchmark/build/Dockerfile").read_bytes()).hexdigest()
    )
    tools = manifest["tools"]
    assert isinstance(tools, dict)
    assert all("recipe_sha256" in value for value in tools.values())
    assert all("build_inputs_sha256" in value for value in tools.values())
    metadata = build_image.final_image_metadata(manifest, selections, digest, "e" * 40)
    assert metadata["schema_version"] == 4
    assert metadata["toolbox_manifest_sha256"] == digest


def test_built_config_binds_runtime_and_aggregate_identity() -> None:
    build_image.validate_built_config(built_document(), "d" * 64, "c" * 64, "e" * 40)
    wrong = built_document(toolbox_digest="a" * 64)
    with pytest.raises(build_image.BuildError, match="toolbox label"):
        build_image.validate_built_config(wrong, "d" * 64, "c" * 64, "e" * 40)


def test_dockerfile_is_self_contained_and_checksum_pinned() -> None:
    source = (ROOT / "benchmark/build/Dockerfile").read_text()
    assert "_PARENT" not in source
    assert "SHARED_BASE" not in source
    assert source.count("ADD --checksum=sha256:") == 11
    for stage in (
        "aws_cli_install",
        "minio_mc_install",
        "ps3_install",
        "rclone_install",
        "s3_fast_list_build",
        "s3kor_install",
        "s3p_install",
        "s4cmd_install",
        "s5cmd_install",
        "s7cmd_install",
        "swath_install",
    ):
        assert f"AS {stage}" in source
    final_stage = source.index("FROM runtime_base AS toolbox")
    assert "ADD --checksum" not in source[final_stage:]


def test_toolbox_context_contains_only_runtime_adapters_and_exact_build_inputs() -> None:
    policy = (ROOT / "benchmark/build/Dockerfile.dockerignore").read_text().splitlines()
    assert policy == [
        "*",
        # Only the importable package and the one build input the image needs;
        # plans/, tests/, and the README stay out of the worker image.
        "!benchmark/src/benchmark/",
        "!benchmark/src/benchmark/**",
        "!benchmark/build/requirements-worker.txt",
        "!tools/",
        "!tools/*/",
        "!tools/*/adapter/",
        "!tools/*/adapter/*.py",
        "!tools/s3-fast-list/build/",
        "!tools/s3-fast-list/build/Cargo.lock",
        "!tools/s3p/build/",
        "!tools/s3p/build/package.json",
        "!tools/s3p/build/package-lock.json",
        "!tools/s4cmd/build/",
        "!tools/s4cmd/build/requirements.txt",
        "**/__pycache__",
        "**/*.pyc",
    ]
    required_inputs = {
        "tools/s3-fast-list/build/Cargo.lock",
        "tools/s3p/build/package.json",
        "tools/s3p/build/package-lock.json",
        "tools/s4cmd/build/requirements.txt",
    }
    assert all((ROOT / path).is_file() for path in required_inputs)
    assert all((ROOT / "tools" / tool / "adapter").is_dir() for tool in TOOLBOX_TOOLS)


def test_final_stage_validates_metadata_roster_paths_and_manifest() -> None:
    source = (ROOT / "benchmark/build/Dockerfile").read_text()
    final_stage = source[source.index("FROM runtime_base AS toolbox") :]
    for marker in (
        'metadata["schema_version"] != 4',
        "set(tools) != expected_tools",
        "adapter_roster != expected_tools",
        "Path(workdir).is_dir()",
        "os.access(command, os.X_OK)",
        'metadata["toolbox_recipe_sha256"] != os.environ["TOOLBOX_RECIPE_SHA256"]',
        'computed_manifest != os.environ["TOOLBOX_MANIFEST_SHA256"]',
        'metadata["harness_revision"] != os.environ["HARNESS_REVISION"]',
    ):
        assert marker in final_stage

    workflow = (ROOT / ".github/workflows/benchmark-toolbox.yml").read_text()
    assert "set(tools) != expected_tools" in workflow
    assert "workdir.is_dir()" in workflow
    assert "os.access(command, os.X_OK)" in workflow


def test_metadata_is_canonical_json() -> None:
    selections = build_image.registered_selections(ROOT)
    manifest, digest = build_image.toolbox_manifest(selections, ROOT)
    metadata = build_image.final_image_metadata(manifest, selections, digest, "f" * 40)
    assert json.loads(json.dumps(metadata, sort_keys=True)) == metadata


def test_consolidated_recipe_and_s3p_lock_are_manifest_inputs(tmp_path: Path) -> None:
    selections = build_image.registered_selections(ROOT)
    manifest, _ = build_image.toolbox_manifest(selections, ROOT)
    assert (
        manifest["toolbox_recipe_sha256"]
        == hashlib.sha256((ROOT / "benchmark/build/Dockerfile").read_bytes()).hexdigest()
    )

    copied: list[Path] = []
    for relative in (
        "tools/s3p/build/image.json",
        "tools/s3p/build/Dockerfile",
        "tools/s3p/build/package.json",
        "tools/s3p/build/package-lock.json",
    ):
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        copied.append(destination)
    before = build_image._input_digest(tmp_path, copied)
    lock = copied[-1]
    lock.write_bytes(lock.read_bytes() + b"\n")
    assert build_image._input_digest(tmp_path, copied) != before


def test_declared_artifact_must_appear_in_executed_stage() -> None:
    selections = build_image.registered_selections(ROOT)
    source = (ROOT / "benchmark/build/Dockerfile").read_text()
    broken = source.replace(selections["aws-cli"].tool_artifact_locator, "https://invalid.test/aws")
    with pytest.raises(build_image.BuildError, match="aws-cli"):
        build_image.validate_executed_sources(selections, ROOT, broken)
