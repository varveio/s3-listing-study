"""Tests for the two repository-structure gates behind the packaged CLI.

Both gates read the working tree, so the interesting cases are synthetic: a
capsule fixture built in a tmp tree would not be this repo's tree, and running
either gate for real is CI's job. What is tested here is the seam — that
``s3-listing-study validate-capsule`` and ``s3-listing-study check-links`` reach
the modules that moved out of ``scripts/`` and return their exit codes — plus
the slug rules the two share, which are the part a broken link check would turn
into a false pass.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from s3_listing_study import cli
from s3_listing_study.repo import capsule, links, source_anchors

REPO = Path(__file__).resolve().parents[1]


def test_root_and_forwarded_command_help_are_normal(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as root_help:
        cli.main(["--help"])
    assert root_help.value.code == 0
    output = capsys.readouterr().out
    assert "validate-capsule" in output
    assert "check-source-anchors" in output


def test_capsule_and_links_agree_on_heading_slugs() -> None:
    text = "# Running the checks\n## `uv run` — the project env\n### Running the checks\n"
    ids = links.heading_ids(text)
    assert ids == {"running-the-checks", "uv-run--the-project-env", "running-the-checks-1"}
    assert capsule.heading_ids(text) == ids


def test_check_links_subcommand_reports_the_repo_surface(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["check-links"]) == 0
    assert "error(s)" in capsys.readouterr().out


def test_validate_capsule_subcommand_passes_a_committed_capsule(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["validate-capsule", "--tool", "s3-fast-list"]) == 0
    assert "current contract passed" in capsys.readouterr().out


def test_validate_capsule_reports_a_tool_that_has_no_directory(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["validate-capsule", "--tool", "not-a-tool"]) == 1
    assert "validate-capsule: missing" in capsys.readouterr().err


@pytest.mark.parametrize("provenance", [None, "source", []])
def test_malformed_provenance_is_a_validation_error_not_a_crash(provenance: object) -> None:
    document = json.loads((Path("tools/aws-cli/data/tool.json")).read_text())
    document["tested"]["version"]["provenance"] = provenance
    errors: list[str] = []
    capsule.validate_schema(document, Path("schemas/tool.schema.json"), "tool.json", errors)
    assert errors
    assert capsule.provenance_reference(document["tested"]["version"]) is None


def test_source_anchor_subcommand_runs_its_regression_self_test(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["check-source-anchors", "--self-test"]) == 0
    assert "self-test passed" in capsys.readouterr().out


def test_source_anchor_lines_are_coerced_from_canonical_json(tmp_path: Path) -> None:
    capsule = tmp_path / "fixture"
    (capsule / "data").mkdir(parents=True)
    (capsule / "data" / "claims.json").write_text(
        '{"tool":"fixture","claims":[{"id":"one","evidence":['
        '{"kind":"source","repository":"https://example.invalid/repo",'
        '"commit":"abcdef0","path":"source.py","lines":7}]}]}',
        encoding="utf-8",
    )
    errors: list[str] = []
    anchors = source_anchors.collect_json_anchors(capsule, errors)
    assert errors == []
    assert anchors[0].lines == "7"


def test_deleted_runner_paths_remain_only_in_frozen_evidence() -> None:
    """Living code and docs must not advertise retired executable paths."""
    deleted_names = tuple(
        bytes(points).decode("ascii")
        for points in (
            (115, 109, 111, 107, 101, 45, 114, 117, 110, 46, 115, 104),
            (114, 117, 110, 45, 97, 116, 116, 101, 109, 112, 116, 46, 115, 104),
            (97, 100, 97, 112, 116, 101, 114, 47, 114, 117, 110, 46, 115, 104),
        )
    )

    def static_string(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = static_string(node.left)
            right = static_string(node.right)
            if left is not None and right is not None:
                return left + right
        return None

    offenders: list[str] = []
    for path in REPO.rglob("*"):
        relative = path.relative_to(REPO)
        parts = relative.parts
        ignored_dirs = {
            ".git",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "__pycache__",
        }
        if not path.is_file() or ignored_dirs.intersection(parts):
            continue
        allowed = (
            len(parts) >= 3 and parts[0] == "tools" and parts[2] in {"receipts", "research"}
        ) or parts[:2] == ("tests", "fixtures")
        if allowed:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        emitted_strings: list[str] = []
        if path.suffix == ".py":
            try:
                tree = ast.parse(text, filename=str(relative))
            except SyntaxError:
                tree = None
            if tree is not None:
                emitted_strings = [
                    value for node in ast.walk(tree) if (value := static_string(node)) is not None
                ]
        if any(
            name in text or any(name in value for value in emitted_strings)
            for name in deleted_names
        ):
            offenders.append(str(relative))
    assert offenders == []


def test_gcp_module_matches_single_toolbox_and_upload_only_workers() -> None:
    module = REPO / "infra/terraform/modules/gcp/s3-listing-study"
    worker = (module / "worker.tf").read_text()
    authenticated = (module / "aws-credentials.tf").read_text()
    registry = (module / "image-registry.tf").read_text()
    readme = (module / "README.md").read_text()

    worker_roles = re.findall(r'\brole\s*=\s*"([^"]+)"', worker)
    authenticated_roles = re.findall(r'\brole\s*=\s*"([^"]+)"', authenticated)
    assert "roles/storage.objectCreator" in worker_roles
    assert "roles/storage.objectAdmin" not in worker_roles
    assert "roles/storage.objectCreator" in authenticated_roles
    assert "roles/storage.objectAdmin" not in authenticated_roles
    # Also scan raw HCL so objectAdmin cannot hide in a project-level toset or
    # another expression that is later assigned through ``role = each.value``.
    assert "roles/storage.objectAdmin" not in worker
    assert "roles/storage.objectAdmin" not in authenticated
    assert "single self-contained benchmark toolbox" in registry
    assert "this module does not publish it" in (module / "outputs.tf").read_text()
    for retired in (
        "derived attempt images",
        "docs/operating/runner-security.md",
        "strict local Docker profile",
        "required manager reconciler",
    ):
        assert retired not in readme
