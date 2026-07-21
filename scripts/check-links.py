#!/usr/bin/env python3
"""Check relative Markdown links on the repo's current-state surfaces.

Scope: root-level Markdown, docs/, harness/README.md, scripts/README.md,
tools/README.md, and the README-only contextual tool directories (those with
no data/ capsule, e.g. pure-storage and s3-inventory) — the pages a reader
navigates today. Capsule-internal pages are already covered by
validate-tool-capsule.py, and internal working notes (not published) are
dated history whose links describe the tree as it was, so neither is checked
here.

Checks that every relative link target exists and that every fragment resolves
to a real heading, using the same GitHub slug rules as the capsule validator.
External http(s)/mailto links are out of scope: this gate must not depend on
the network.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SURFACE_GLOBS = [
    "*.md",
    "docs/*.md",
    "harness/README.md",
    "scripts/README.md",
    "tools/README.md",
]

LINK = re.compile(r"\[[^]]*\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")


def heading_ids(text: str) -> set[str]:
    ids: set[str] = set()
    counts: dict[str, int] = {}
    for match in re.finditer(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
        label = re.sub(r"[`*~]", "", match.group(1)).strip().lower()
        slug = re.sub(r"[^\w\- ]", "", label, flags=re.UNICODE)
        slug = re.sub(r"\s", "-", slug).strip("-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        ids.add(slug if count == 0 else f"{slug}-{count}")
    ids.update(re.findall(r"<a\s+(?:name|id)=[\"']([^\"']+)", text, re.I))
    return ids


def contextual_tool_pages() -> list[Path]:
    pages = []
    for entry in sorted((REPO / "tools").iterdir()):
        readme = entry / "README.md"
        if entry.is_dir() and readme.is_file() and not (entry / "data").is_dir():
            pages.append(readme)
    return pages


def main() -> int:
    errors: list[str] = []
    pages = sorted(
        {p for g in SURFACE_GLOBS for p in REPO.glob(g) if p.is_file()}
        | set(contextual_tool_pages())
    )
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for raw_target in LINK.findall(text):
            if raw_target.startswith(("http://", "https://", "mailto:")):
                continue
            path_text, separator, fragment = raw_target.partition("#")
            resolved = page if not path_text else (page.parent / path_text).resolve()
            rel = page.relative_to(REPO)
            if not resolved.exists():
                errors.append(f"broken link in {rel}: {raw_target}")
                continue
            if separator and fragment:
                fragment_file = resolved / "README.md" if resolved.is_dir() else resolved
                if fragment_file.suffix.lower() != ".md" or not fragment_file.is_file():
                    errors.append(f"fragment target is not Markdown in {rel}: {raw_target}")
                elif fragment not in heading_ids(fragment_file.read_text(encoding="utf-8")):
                    errors.append(f"missing fragment in {rel}: {raw_target}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    print(f"check-links: {len(pages)} page(s), {len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
