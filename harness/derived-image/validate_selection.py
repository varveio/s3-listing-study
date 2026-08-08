"""Fail a derived-image build when staged registration and adapter disagree."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/build/payload")

from s3_listing_study.build_selection import BuildSelectionError, load_staged_selection

try:
    selection = load_staged_selection(Path("/build/image.json"), Path("/build/tool"))
    if selection.subject_python != "/usr/bin/python3":
        raise BuildSelectionError("selected subject does not provide the shared Python runtime")
except BuildSelectionError as exc:
    raise SystemExit(f"derived-image selection refused: {exc}") from None
