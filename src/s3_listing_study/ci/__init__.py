"""Continuous-integration logic for building and publishing the image chain.

Everything in this package used to live as ``python3 - <<'PY'`` heredocs inside
``.github/workflows/images-pr.yml``, where none of it could be unit-tested or
run on a laptop. A typo surfaced as a failed job eight minutes into a run.

The rule this package exists to enforce: **workflow YAML contains only
``checkout``, ``setup-*``, ``docker login``, ``docker buildx``, and
``s3-listing-study ci …``.** No heredocs, no ``awk``, no regexes in shell. Tag
derivation, existence probing, graph construction, and manifest assembly are
ordinary Python with ordinary tests.

The split inside the package is between pure and impure. :mod:`tags`,
:mod:`plan`, :mod:`bake`, and :mod:`publication` are pure functions over
validated inputs; :mod:`registry` is the only module that shells out. Tests
therefore cover the decisions exhaustively and stub the one boundary that
touches the network.
"""

from __future__ import annotations

from pathlib import Path

from s3_listing_study.common.build_selection import BuildSelectionError, validate_tool_slug


class CIError(RuntimeError):
    """A CI input is invalid, or an external command failed in a way we refuse to guess about."""


def buildable_tools(root: Path) -> tuple[str, ...]:
    """Every tool slug that registers a buildable image, discovered from the tree.

    The roster used to be written out four times — twice as a workflow matrix and
    twice as an ``expected`` set in a summary job — so adding a tool meant four
    edits and forgetting one failed late with "registrations are missing".
    A capsule is buildable exactly when it registers ``build/image.json``;
    ``pure-storage`` and ``s3-inventory`` are tracked subjects with no image, and
    they drop out of this list without anyone maintaining an exclusion.
    """
    tools_dir = root / "tools"
    try:
        entries = sorted(path.name for path in tools_dir.iterdir() if path.is_dir())
    except OSError as exc:
        raise CIError(f"cannot read the tool roster: {exc}") from exc
    roster: list[str] = []
    for name in entries:
        if not (tools_dir / name / "build" / "image.json").is_file():
            continue
        try:
            validate_tool_slug(name)
        except BuildSelectionError as exc:
            raise CIError(f"registered tool directory is not a valid slug: {exc}") from exc
        roster.append(name)
    if not roster:
        raise CIError("no buildable tools found under tools/")
    return tuple(roster)
