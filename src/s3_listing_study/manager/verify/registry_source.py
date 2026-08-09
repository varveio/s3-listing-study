"""Which registry a verdict is checked against, and how it is identified.

Two properties, decided in ``tests/differential/README.md`` § "The fixture is
markdown, and stays markdown":

* **Identity is the raw bytes.** ``--registry PATH`` is digested as
  ``sha256(file bytes)``, format-agnostically. All 73 committed ``run.meta``
  cite ``254c8cfe…`` — the sha of a *markdown* registry — and the verifier
  refuses to verify a run against a registry it never saw. Digesting bytes
  rather than a parse is what lets the committed fixture reproduce that digest
  naturally; nothing short of a preimage could make the TOML do it.
* **Field parsing dispatches on file type.** ``.toml`` goes through
  :mod:`s3_listing_study.manager.registry`. Anything else is read by the narrow legacy
  markdown reader below, retained *solely* so historical receipts can be
  replayed against the registry they were made against. It is not a second
  supported format and nothing new may be written in it.

There is deliberately no environment override: redirection is an explicit
argument at the call site.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ..registry import Registry, RegistryError
from .errors import VerifierError

_SECTION = re.compile(r"^## ", re.M)
_CODE = re.compile(r"`([^`]*)`")


@dataclass(frozen=True)
class RegistrySource:
    """A registry file: its bytes' digest, and the fields the verifier reads."""

    path: Path
    digest: str
    _text: str

    @classmethod
    def load(cls, path: Path) -> RegistrySource:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise VerifierError(f"registry not readable: {path} ({exc.strerror})") from None
        text = "" if path.suffix == ".toml" else raw.decode("utf-8", errors="replace")
        return cls(path=path, digest=hashlib.sha256(raw).hexdigest(), _text=text)

    def harness_image(self) -> str:
        """The digest-pinned harness client image."""
        image = self._toml_image() if self.path.suffix == ".toml" else self._markdown_image()
        if "@sha256:" not in image:
            raise VerifierError(f"harness image is not digest-pinned: {image}")
        return image

    def _toml_image(self) -> str:
        try:
            return Registry.load(self.path).harness_image
        except RegistryError as exc:
            raise VerifierError(str(exc)) from None

    def _markdown_image(self) -> str:
        section = _section(self._text, "## Harness client")
        if not section:
            raise VerifierError(f"no '## Harness client' section in {self.path}")
        value = _row_value(section, "Image", self.path)
        match = _CODE.search(value)
        if not match:
            raise VerifierError(f"no Image row under '## Harness client' in {self.path}")
        return match.group(1)


def _section(text: str, heading: str) -> list[str]:
    body: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith(heading):
            inside = True
            continue
        if _SECTION.match(line):
            inside = False
        if inside:
            body.append(line)
    return body


def _row_value(section: list[str], label: str, path: Path) -> str:
    """The value cell of ``| Label | value |``. More than one match is fatal.

    The legacy resolver hand-rolled this refusal because a duplicated row
    silently resolving to whichever came first would put a plausible wrong
    value in a receipt — worse than no receipt at all.
    """
    hits = []
    for line in section:
        if not line.startswith("|"):
            continue
        body = line[1:].lstrip()
        head, sep, tail = body.partition("|")
        if not sep:
            continue
        if head.strip().lower() == label.lower():
            hits.append(tail.rstrip().removesuffix("|").strip())
    if len(hits) > 1:
        raise VerifierError(
            f"row '{label}' appears {len(hits)} times in this section of {path} — "
            "registry is ambiguous, refusing to guess"
        )
    return hits[0] if hits else ""
