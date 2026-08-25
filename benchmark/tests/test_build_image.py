from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from benchmark import build_image, campaign
from benchmark.contract import TOOLBOX_TOOLS

ROOT = Path(__file__).parents[2]
RECIPE = (ROOT / "benchmark/build/Dockerfile").read_text()


def slices(source: str) -> tuple[dict[str, str], str]:
    """Slice digests over one recipe text, with the capsule facts held constant."""
    facts = {
        tool: {name: f"{tool}:{name}" for name in build_image.TOOL_SLICE_FACTS}
        for tool in build_image.TOOL_STAGES
    }
    return build_image.slice_digests(
        build_image.attribute_recipe(source), facts, "a" * 64, "e" * 40
    )


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
    manifest, digest = build_image.toolbox_manifest(selections, ROOT, "e" * 40)
    assert len(digest) == 64
    assert manifest["schema_version"] == 3
    assert (
        manifest["toolbox_recipe_sha256"]
        == hashlib.sha256((ROOT / "benchmark/build/Dockerfile").read_bytes()).hexdigest()
    )
    tools = manifest["tools"]
    assert isinstance(tools, dict)
    assert all("recipe_sha256" in value for value in tools.values())
    assert all("build_inputs_sha256" in value for value in tools.values())
    metadata = build_image.final_image_metadata(manifest, selections, digest, "e" * 40)
    assert metadata["schema_version"] == 5
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
    assert source.count("ADD --checksum=sha256:") == 10
    assert (
        "FROM ghcr.io/varveio/swath@sha256:"
        "a13adef049de8c11c053861918005aaaae6c8576797df48867d1c5efdbcfc88b "
        "AS swath_install"
    ) in source
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
        'metadata["schema_version"] != 5',
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
    manifest, digest = build_image.toolbox_manifest(selections, ROOT, "f" * 40)
    metadata = build_image.final_image_metadata(manifest, selections, digest, "f" * 40)
    assert json.loads(json.dumps(metadata, sort_keys=True)) == metadata


def test_consolidated_recipe_and_s3p_lock_are_manifest_inputs(tmp_path: Path) -> None:
    selections = build_image.registered_selections(ROOT)
    manifest, _ = build_image.toolbox_manifest(selections, ROOT, "e" * 40)
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

    broken_swath = source.replace(
        selections["swath"].tool_artifact_sha256,
        "0" * 64,
    )
    with pytest.raises(build_image.BuildError, match="swath"):
        build_image.validate_executed_sources(selections, ROOT, broken_swath)


def test_closure_follows_from_edges_past_the_first_hop() -> None:
    recipe = build_image.attribute_recipe(RECIPE)
    assert set(recipe.tool_stages["swath"]) == {"swath_install"}
    assert "ghcr.io/varveio/swath@sha256:" in recipe.tool_stages["swath"]["swath_install"]


def test_a_pinned_base_digest_moves_only_the_slice_that_reaches_it() -> None:
    match = re.search(r"^FROM (?P<base>\S+) AS swath_install$", RECIPE, re.MULTILINE)
    assert match is not None
    base = match.group("base")
    bumped = RECIPE.replace(base, f"{base.split('@')[0]}@sha256:{'0' * 64}")
    before, before_platform = slices(RECIPE)
    after, after_platform = slices(bumped)
    assert after["swath"] != before["swath"]
    assert after_platform == before_platform
    assert {tool: after[tool] for tool in after if tool != "swath"} == {
        tool: before[tool] for tool in before if tool != "swath"
    }


def test_a_stage_more_than_one_tool_reaches_is_platform() -> None:
    recipe = build_image.attribute_recipe(RECIPE)
    assert "runtime_base" in recipe.platform_stages
    assert all("runtime_base" not in stages for stages in recipe.tool_stages.values())


def test_an_unmarked_tool_line_lands_in_the_platform() -> None:
    line = "RUN install -d -o 10001 -g 10001 /home/s7cmd"
    assert line in build_image.attribute_recipe(RECIPE).tool_lines["s7cmd"]
    recipe = build_image.attribute_recipe(RECIPE.replace("# slice: s7cmd\n", ""))
    assert line in recipe.platform_lines
    assert line not in recipe.tool_lines["s7cmd"]


def test_a_marker_naming_an_unregistered_tool_is_refused() -> None:
    with pytest.raises(build_image.BuildError, match="unregistered tool"):
        build_image.attribute_recipe(RECIPE.replace("# slice: s7cmd", "# slice: s8cmd"))


def test_a_copy_from_an_unknown_stage_is_refused() -> None:
    broken = RECIPE.replace("COPY --from=s5cmd_install", "COPY --from=absent_stage")
    with pytest.raises(build_image.BuildError, match="unknown stage"):
        build_image.attribute_recipe(broken)


def test_a_marker_on_a_self_attributing_copy_is_refused() -> None:
    broken = RECIPE.replace(
        "COPY --from=s5cmd_install", "# slice: s5cmd\nCOPY --from=s5cmd_install"
    )
    with pytest.raises(build_image.BuildError, match="attributes itself"):
        build_image.attribute_recipe(broken)


def test_a_tool_whose_stage_is_unreachable_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(build_image.TOOL_STAGES, "s5cmd", "absent_stage")
    with pytest.raises(build_image.BuildError, match="unreachable"):
        build_image.attribute_recipe(RECIPE)


def test_a_twelfth_tool_leaves_the_platform_and_every_slice_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before, before_platform = slices(RECIPE)
    extended = RECIPE.replace(
        "FROM runtime_base AS toolbox",
        "FROM runtime_base AS twelfth_install\n"
        "RUN install -m 0755 /bin/true /usr/local/bin/twelfth\n\n"
        "FROM runtime_base AS toolbox",
    ).replace(
        "COPY --from=aws_cli_install",
        "COPY --from=twelfth_install /usr/local/bin/twelfth /usr/local/bin/twelfth\n"
        "COPY --from=aws_cli_install",
        1,
    )
    monkeypatch.setitem(build_image.TOOL_STAGES, "twelfth", "twelfth_install")
    after, after_platform = slices(extended)
    assert after_platform == before_platform
    assert {tool: after[tool] for tool in before} == before


def test_schema_5_image_set_round_trips_through_the_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The build emits the controller's set, so nothing hand-filters it.

    `image-metadata.json` carries `executable` and an image set carrying it is
    refused — the projection is the build's job, and this is the round trip that
    says so.
    """
    monkeypatch.setattr(build_image, "assert_clean_revision", lambda root, revision: None)
    uri = f"us-docker.pkg.dev/p/r/toolbox@sha256:{'1' * 64}"
    document = build_image.image_set_document(ROOT, "e" * 40, uri)
    tools = document["tools"]
    assert isinstance(tools, dict)
    assert not any("executable" in facts for facts in tools.values())
    path = tmp_path / "images.json"
    path.write_text(json.dumps(document))
    image_set = campaign.load_image_set(path, set(TOOLBOX_TOOLS))
    assert image_set.image_uri == uri
    assert set(image_set.tools) == TOOLBOX_TOOLS
    assert len({image["platform_sha256"] for image in image_set.tools.values()}) == 1
    assert len({image["tool_slice_sha256"] for image in image_set.tools.values()}) == len(
        TOOLBOX_TOOLS
    )
