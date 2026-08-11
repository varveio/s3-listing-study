"""Static and parser gates for the local smoke campaign shell entry point."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "harness" / "smoke-campaign.sh"


def test_smoke_campaign_help_names_the_required_image_inputs() -> None:
    result = subprocess.run(
        ("bash", str(SCRIPT), "--help"),
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0
    assert "--shared-base-image REGISTRY/...@sha256:<digest>" in result.stdout
    assert "--build-repository REGISTRY/REPOSITORY" in result.stdout


def test_smoke_campaign_never_selects_the_first_matching_local_tag() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "derived_image_tag, load_registered_selection" in text
    assert "build-tool-image" in text
    assert "docker image push '$tool_tag'" in text
    assert "--tool-image '$tool_image'" in text
    assert "--tag '$tag'" in text
    assert 'docker image inspect "$tag"' in text
    assert '    "$digest" \\\n' in text
    assert "docker images --filter" not in text
    assert "head -1" not in text
