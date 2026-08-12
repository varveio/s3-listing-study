"""Mechanical import independence for the sibling TwinStamp package."""

from __future__ import annotations

import ast
from pathlib import Path


def test_twinstamp_has_no_study_gcp_or_benchmark_imports() -> None:
    root = Path(__file__).parents[1] / "src" / "twinstamp"
    forbidden: list[tuple[Path, int, str]] = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = [node.module]
            else:
                continue
            for name in names:
                if (
                    name == "s3_listing_study"
                    or name.startswith("s3_listing_study.")
                    or name == "google"
                    or name.startswith("google.")
                    or name == "bench"
                    or name.startswith("bench.")
                ):
                    forbidden.append((path, node.lineno, name))
    assert forbidden == []
