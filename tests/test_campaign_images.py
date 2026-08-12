"""Derived-image publication and frozen image-set capture."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from s3_listing_study.common.build_selection import BuildSelection, load_registered_selection
from s3_listing_study.manager.campaign import images as image_publish

ROOT = Path(__file__).resolve().parents[1]
REVISION = "b" * 40
DIGEST = "sha256:" + "d" * 64
REPOSITORY = "us-east1-docker.pkg.dev/project/images"


def completed(
    argv: Sequence[str],
    returncode: int = 0,
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    responder: Callable[[tuple[str, ...]], subprocess.CompletedProcess[bytes]],
) -> tuple[list[tuple[str, ...]], list[str]]:
    calls: list[tuple[str, ...]] = []
    tags: list[str] = []
    monkeypatch.setattr(image_publish, "repo_root", lambda: ROOT)

    def fake_builder(
        root: Path, selection: BuildSelection, tag: str, shared_base: str
    ) -> tuple[str, ...]:
        assert root == ROOT
        assert selection.tool == "aws-cli"
        assert shared_base.endswith("@" + "sha256:" + "c" * 64)
        tags.append(tag)
        return "docker", "build", "--tag", tag, str(root)

    def fake_run(argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        rendered = tuple(argv)
        calls.append(rendered)
        if rendered[:4] == ("git", "-C", str(ROOT), "status"):
            return completed(rendered)
        if rendered[:4] == ("git", "-C", str(ROOT), "rev-parse"):
            return completed(rendered, stdout=(REVISION + "\n").encode())
        return responder(rendered)

    monkeypatch.setattr(image_publish, "derived_image_build_command", fake_builder)
    monkeypatch.setattr(image_publish, "_run", fake_run)
    return calls, tags


def successful(argv: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
    if argv[:2] == ("docker", "build") or argv[:2] == ("docker", "push"):
        return completed(argv)
    if argv[:3] == ("docker", "image", "inspect"):
        target = argv[-1].rsplit(":", 1)[0]
        return completed(argv, stdout=json.dumps([f"{target}@{DIGEST}"]).encode())
    raise AssertionError(argv)


def publish_args(path: Path) -> list[str]:
    return [
        "--tool",
        "aws-cli",
        "--repository",
        REPOSITORY,
        "--image-set",
        str(path),
        "--shared-base-image",
        "registry.example/shared-base@sha256:" + "a" * 64,
        "--tool-image",
        "registry.example/tool/aws-cli@sha256:" + "c" * 64,
    ]


def test_publisher_captures_pushed_digest_and_exact_registered_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "images.json"
    calls, tags = install_fakes(monkeypatch, successful)

    assert image_publish.publish_derived_image_main(publish_args(path)) == 0

    document = json.loads(path.read_text(encoding="utf-8"))
    image = document["images"]["aws-cli"]
    registration = json.loads((ROOT / "tools/aws-cli/build/image.json").read_text(encoding="utf-8"))
    assert document["schema_version"] == 3
    assert image == {
        "derived_image": DIGEST,
        "image_uri": f"{tags[0].rsplit(':', 1)[0]}@{DIGEST}",
        "shared_base_digest": "sha256:" + "a" * 64,
        "shared_base_uri": "registry.example/shared-base@sha256:" + "a" * 64,
        "tool_build_sha256": registration["tool_build_sha256"],
        "tool_image_digest": "sha256:" + "c" * 64,
        "tool_image_uri": "registry.example/tool/aws-cli@sha256:" + "c" * 64,
        "selection_sha256": load_registered_selection(ROOT, "aws-cli").selection_sha256,
        "tool_artifact": registration["tool_artifact"],
        "tool_version": registration["tool_version"],
        "adapter_bundle_sha256": registration["adapter_bundle_sha256"],
        "shared_base_source_sha256": registration["shared_base_source_sha256"],
        "harness_revision": REVISION,
    }
    assert calls[2] == ("docker", "build", "--tag", tags[0], str(ROOT))
    assert calls[5] == ("docker", "push", tags[0])
    assert calls[6] == (
        "docker",
        "image",
        "inspect",
        "--format={{json .RepoDigests}}",
        tags[0],
    )
    assert json.loads(capsys.readouterr().out)["image_uri"] == image["image_uri"]


@pytest.mark.parametrize("failed_action", ["build", "push"])
def test_build_or_push_failure_never_writes_an_image_set_or_prints_captured_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed_action: str,
) -> None:
    path = tmp_path / "images.json"
    secret = "DO-NOT-PRINT-THIS"

    def responder(argv: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        if argv[:2] == ("docker", failed_action):
            return completed(argv, 9, stdout=secret.encode())
        return successful(argv)

    calls, _tags = install_fakes(monkeypatch, responder)
    assert image_publish.publish_derived_image_main(publish_args(path)) == 1
    assert not path.exists()
    assert secret not in capsys.readouterr().err
    if failed_action == "build":
        assert not [call for call in calls if call[:2] == ("docker", "push")]


def test_malformed_digest_capture_refuses_to_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "images.json"

    def responder(argv: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        if argv[:3] == ("docker", "image", "inspect"):
            return completed(argv, stdout=b'["registry/image@sha256:not-a-digest"]')
        return completed(argv)

    install_fakes(monkeypatch, responder)
    assert image_publish.publish_derived_image_main(publish_args(path)) == 1
    assert not path.exists()


def registered_image(tool: str, digest_character: str) -> dict[str, Any]:
    registration = json.loads(
        (ROOT / "tools" / tool / "build" / "image.json").read_text(encoding="utf-8")
    )
    digest = "sha256:" + digest_character * 64
    return {
        "derived_image": digest,
        "image_uri": f"{REPOSITORY}/{tool}@{digest}",
        "shared_base_digest": "sha256:" + "a" * 64,
        "shared_base_uri": "registry.example/shared-base@sha256:" + "a" * 64,
        "tool_build_sha256": registration["tool_build_sha256"],
        "tool_image_digest": "sha256:" + "c" * 64,
        "tool_image_uri": f"{REPOSITORY}/tool/{tool}@sha256:" + "c" * 64,
        "selection_sha256": load_registered_selection(ROOT, tool).selection_sha256,
        "tool_artifact": registration["tool_artifact"],
        "tool_version": registration["tool_version"],
        "adapter_bundle_sha256": registration["adapter_bundle_sha256"],
        "shared_base_source_sha256": registration["shared_base_source_sha256"],
        "harness_revision": "a" * 40,
    }


def test_publisher_refuses_to_extend_historical_schema_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "images.json"
    rclone = registered_image("rclone", "e")
    path.write_text(
        json.dumps({"schema_version": 2, "images": {"rclone": rclone}}), encoding="utf-8"
    )
    calls, _tags = install_fakes(monkeypatch, successful)
    assert image_publish.publish_derived_image_main(publish_args(path)) == 1
    assert not calls


@pytest.mark.parametrize(
    "mismatch",
    ["shared_base_uri", "shared_base_digest", "shared_base_source_sha256"],
)
def test_publisher_refuses_an_existing_set_from_another_shared_base_before_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mismatch: str,
) -> None:
    path = tmp_path / "images.json"
    existing = registered_image("aws-cli", "e")
    if mismatch == "shared_base_uri":
        existing[mismatch] = "another.example/shared-base@sha256:" + "a" * 64
    elif mismatch == "shared_base_digest":
        existing[mismatch] = "sha256:" + "b" * 64
        existing["shared_base_uri"] = "registry.example/shared-base@sha256:" + "b" * 64
    else:
        existing[mismatch] = "b" * 64
    path.write_text(
        json.dumps({"schema_version": 3, "images": {"aws-cli": existing}}),
        encoding="utf-8",
    )

    calls, _tags = install_fakes(monkeypatch, successful)
    assert image_publish.publish_derived_image_main(publish_args(path)) == 1
    assert not calls
    assert mismatch in capsys.readouterr().err


def test_dirty_payload_is_refused_before_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "images.json"
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(image_publish, "repo_root", lambda: ROOT)
    monkeypatch.setattr(
        image_publish,
        "derived_image_build_command",
        lambda *_args: pytest.fail("dirty payload was built"),
    )

    def fake_run(argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        rendered = tuple(argv)
        calls.append(rendered)
        return completed(rendered, stdout=b" M src/twinstamp/publication.py\n")

    monkeypatch.setattr(image_publish, "_run", fake_run)
    assert image_publish.publish_derived_image_main(publish_args(path)) == 1
    assert len(calls) == 1
    assert not path.exists()
