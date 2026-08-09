"""Fail a derived-image build when staged registration and adapter disagree."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/build/payload")

# Import the entry point before it is sealed into an image. A payload that does
# not import — a missing dependency, a syntax error — then fails the build
# against the exact name, instead of surfacing inside a benchmark attempt on an
# image that has already shipped.
import s3_listing_study.worker.cli  # noqa: F401
from s3_listing_study.common.build_selection import BuildSelectionError, load_staged_selection
from s3_listing_study.common.python_runtime import running_libc

try:
    selection = load_staged_selection(Path("/build/image.json"), Path("/build/tool"))
    # This script runs on the interpreter the build bound, so its own libc is
    # the one the attempt engine will run under. A registration that declares
    # the other one produces an interpreter that loads on the build host and
    # dies inside the subject, which is exactly what this build must not ship.
    libc = running_libc()
    if libc != selection.python_libc:
        raise BuildSelectionError(
            f"the bound interpreter is {libc or 'of an unidentifiable libc'}, "
            f"but this capsule registers python_libc {selection.python_libc}"
        )
except BuildSelectionError as exc:
    raise SystemExit(f"derived-image selection refused: {exc}") from None
