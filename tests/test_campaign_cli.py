"""Campaign input validation and canonical local compilation."""

from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from s3_listing_study.common.build_selection import load_registered_selection
from s3_listing_study.manager.bench import plan as bench
from s3_listing_study.manager.campaign import cli as campaign_cli

ROOT = Path(__file__).resolve().parents[1]
DIGEST = "sha256:" + "a" * 64


def write_plan(path: Path, *, bucket: str = "example-bucket", tool: str = "aws-cli") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(
            f"""\
            spec_version: 2
            bucket: {bucket}
            region: us-east-1
            defaults:
              reps: 1
              timeout_s: 3600
              auth: anonymous
              vcpus: 2
              memory_gb: 4
            tools:
              {tool}:
            """
        ),
        encoding="utf-8",
    )
    return path


def write_image_set(path: Path, tools: set[str]) -> Path:
    images: dict[str, dict[str, Any]] = {}
    for index, tool in enumerate(sorted(tools)):
        selection = load_registered_selection(ROOT, tool)
        image = {
            "derived_image": DIGEST,
            "image_uri": f"registry.example/{tool}@{DIGEST}",
            "shared_base_digest": "sha256:" + "b" * 64,
            "shared_base_uri": "registry.example/base@sha256:" + "b" * 64,
            "shared_base_source_sha256": selection.shared_base_source_sha256,
            "tool_build_sha256": selection.tool_build_sha256,
            "tool_artifact": {
                "kind": selection.tool_artifact_kind,
                "locator": selection.tool_artifact_locator,
                "sha256": selection.tool_artifact_sha256,
            },
            "tool_version": selection.tool_version,
            "adapter_bundle_sha256": selection.adapter_bundle_sha256,
            "harness_revision": "a" * 40,
        }
        tool_digest = "sha256:" + format(index + 1, "x") * 64
        image.update(
            tool_image_digest=tool_digest,
            tool_image_uri=f"registry.example/{tool}@{tool_digest}",
            selection_sha256=selection.selection_sha256,
        )
        images[tool] = image
    path.write_text(json.dumps({"schema_version": 3, "images": images}))
    return path


def compile_arguments(plan: Path, image_set: Path, output: Path) -> list[str]:
    return [
        "--path",
        str(plan),
        "--campaign",
        "2026-08-10-first",
        "--image-set",
        str(image_set),
        "--results-bucket",
        "study-results",
        "--output",
        str(output),
    ]


def test_spot_is_the_default_provisioning_model(tmp_path: Path) -> None:
    parsed = campaign_cli.build_compile_parser().parse_args(
        compile_arguments(tmp_path / "plan.yaml", tmp_path / "images.json", tmp_path / "out.json")
    )
    assert parsed.provisioning == "SPOT"


def test_repeatable_canonical_buckets_form_one_campaign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    buckets = tmp_path / "buckets"
    write_plan(buckets / "first.yaml", bucket="first")
    write_plan(buckets / "second.yaml", bucket="second")
    images = write_image_set(tmp_path / "images.json", {"aws-cli"})
    monkeypatch.setattr(bench, "buckets_dir", lambda: buckets)
    monkeypatch.setattr(bench, "default_path", lambda bucket: buckets / f"{bucket}.yaml")
    monkeypatch.setattr(campaign_cli, "registered_tools", lambda: {"aws-cli"})
    monkeypatch.setattr(campaign_cli, "validate_registered_images", lambda _images: None)

    argv = [
        "--bucket",
        "first",
        "--bucket",
        "second",
        "--campaign",
        "2026-08-10-first",
        "--image-set",
        str(images),
        "--results-bucket",
        "study-results",
        "--output",
        str(tmp_path / "campaign.json"),
    ]
    assert campaign_cli.main(argv) == 0
    document = json.loads((tmp_path / "campaign.json").read_text())
    assert [plan["bucket"] for plan in document["plans"]] == ["first", "second"]
    assert [attempt["bucket"] for attempt in document["attempts"]] == ["first", "second"]
    assert json.loads(capsys.readouterr().out)["created"] is True


def test_image_set_must_cover_union_of_plan_tools(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = write_plan(tmp_path / "first.yaml", bucket="first")
    second = write_plan(tmp_path / "second.yaml", bucket="second", tool="s5cmd")
    images = write_image_set(tmp_path / "images.json", {"aws-cli"})
    argv = [
        "--path",
        str(first),
        "--path",
        str(second),
        *compile_arguments(first, images, tmp_path / "out.json")[2:],
    ]
    assert campaign_cli.main(argv) == 1
    assert "image set does not exactly cover the plans (missing s5cmd)" in capsys.readouterr().err


def test_duplicate_plan_bucket_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = write_plan(tmp_path / "one/same.yaml", bucket="same")
    duplicate = write_plan(tmp_path / "two/same.yaml", bucket="same")
    images = write_image_set(tmp_path / "images.json", {"aws-cli"})
    argv = [
        "--path",
        str(first),
        "--path",
        str(duplicate),
        *compile_arguments(first, images, tmp_path / "out.json")[2:],
    ]
    assert campaign_cli.main(argv) == 1
    assert "more than one plan for bucket 'same'" in capsys.readouterr().err


def test_image_set_refuses_retired_schema_two(tmp_path: Path) -> None:
    path = write_image_set(tmp_path / "images.json", {"aws-cli"})
    document = json.loads(path.read_text())
    document["schema_version"] = 2
    path.write_text(json.dumps(document))
    with pytest.raises(campaign_cli.SubmissionError, match="schema_version must be 3"):
        campaign_cli._read_image_set(path)


def test_current_image_set_requires_split_layer_identity(tmp_path: Path) -> None:
    path = write_image_set(tmp_path / "images.json", {"aws-cli"})
    document = json.loads(path.read_text())
    del document["images"]["aws-cli"]["selection_sha256"]
    path.write_text(json.dumps(document))
    with pytest.raises(campaign_cli.SubmissionError, match="missing selection_sha256"):
        campaign_cli._read_image_set(path)


def test_publication_manifest_converts_to_current_registration(tmp_path: Path) -> None:
    selection = load_registered_selection(ROOT, "aws-cli")
    execution_digest = "sha256:" + "d" * 64
    shared_digest = "sha256:" + "b" * 64
    tool_digest = "sha256:" + "c" * 64
    revision = "e" * 40
    publication = tmp_path / "publication.json"
    publication.write_text(
        json.dumps(
            {
                "kind": "github-container-image-publication",
                "format_version": 2,
                "checkout_revision": revision,
                "images": {
                    "aws-cli": {
                        "tool_name": "aws-cli",
                        "tool_version": selection.tool_version,
                        "selection_sha256": selection.selection_sha256,
                        "worker_revision": revision,
                        "shared": {
                            "digest": shared_digest,
                            "uri": f"registry.example/base@{shared_digest}",
                            "source_sha256": selection.shared_base_source_sha256,
                        },
                        "tool": {
                            "digest": tool_digest,
                            "uri": f"registry.example/tool@{tool_digest}",
                            "build_sha256": selection.tool_build_sha256,
                        },
                        "execution": {
                            "digest": execution_digest,
                            "uri": f"registry.example/run@{execution_digest}",
                        },
                    }
                },
            }
        )
    )
    images = campaign_cli._read_publication_images(publication, root=ROOT)
    assert images["aws-cli"]["selection_sha256"] == selection.selection_sha256
    assert images["aws-cli"]["image_uri"].endswith(f"@{execution_digest}")


def test_compile_freezes_canonical_local_bytes_and_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = write_plan(tmp_path / "example-bucket.yaml")
    images = write_image_set(tmp_path / "images.json", {"aws-cli"})
    output = tmp_path / "campaign.json"
    monkeypatch.setattr(campaign_cli, "validate_registered_images", lambda _images: None)
    argv = compile_arguments(plan, images, output)
    assert campaign_cli.main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert output.read_bytes().endswith(b"\n")
    assert campaign_cli.main(argv) == 0
    assert json.loads(capsys.readouterr().out) == {**first, "created": False}


def test_compile_refuses_to_replace_different_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = write_plan(tmp_path / "example-bucket.yaml")
    images = write_image_set(tmp_path / "images.json", {"aws-cli"})
    output = tmp_path / "campaign.json"
    output.write_bytes(b"different\n")
    monkeypatch.setattr(campaign_cli, "validate_registered_images", lambda _images: None)
    assert campaign_cli.main(compile_arguments(plan, images, output)) == 1
    assert "already exists with different content" in capsys.readouterr().err
    assert output.read_bytes() == b"different\n"
