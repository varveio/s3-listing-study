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

import pytest

from s3_listing_study import capsule, cli, links


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
