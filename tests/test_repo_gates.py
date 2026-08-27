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

import json
import re
from pathlib import Path

import pytest

from s3_listing_study import cli
from s3_listing_study.repo import capsule, links, source_anchors

REPO = Path(__file__).resolve().parents[1]


def test_capsule_and_links_agree_on_heading_slugs() -> None:
    text = "# Running the checks\n## `uv run` — the project env\n### Running the checks\n"
    ids = links.heading_ids(text)
    assert ids == {"running-the-checks", "uv-run--the-project-env", "running-the-checks-1"}
    assert capsule.heading_ids(text) == ids


def test_check_links_subcommand_reports_the_repo_surface() -> None:
    # The committed Markdown tree must remain internally linked.
    assert cli.main(["check-links"]) == 0


def test_validate_capsule_subcommand_passes_a_committed_capsule() -> None:
    assert cli.main(["validate-capsule", "--tool", "s3-fast-list"]) == 0


@pytest.mark.parametrize("provenance", [None, "source", []])
def test_malformed_provenance_is_a_validation_error_not_a_crash(provenance: object) -> None:
    document = json.loads((Path("tools/aws-cli/data/tool.json")).read_text())
    document["tested"]["version"]["provenance"] = provenance
    errors: list[str] = []
    capsule.validate_schema(document, Path("schemas/tool.schema.json"), "tool.json", errors)
    assert errors
    assert capsule.provenance_reference(document["tested"]["version"]) is None


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


def test_gcp_worker_can_manage_evidence_objects() -> None:
    module = REPO / "infra/terraform/modules/gcp/s3-listing-study"
    worker = (module / "worker.tf").read_text()
    assert re.search(
        r'resource "google_storage_bucket_iam_member" "worker_write" \{.*?'
        r'role\s*=\s*"roles/storage\.objectAdmin"',
        worker,
        re.DOTALL,
    )
